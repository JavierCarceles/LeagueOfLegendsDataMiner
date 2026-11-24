"""Auditoría de la calibración: de lo que sale igual en todos los elos, ¿es que todos lo hacen igual o falla el detector?

Para cada rol y cada métrica de «Mi progreso» (movimiento ``mv_*``, recorridos y visión ``rm_*``, reglas ``rl_*``) y cada
tipo de error, con las partidas de ``data/otps`` y ``data/partidas_campeon``:

1. **¿Separa elos?** El elo más bajo con muestra frente al más alto: diferencia en errores típicos (``z``). «separa»,
   «igual» (con muestra de sobra: de verdad no cambia) o «pocos datos».
2. **¿Lo tapa la reconstrucción?** Las posiciones entre dos puntos reales (el frame de cada minuto, una kill, una compra)
   se reconstruyen. Se repite la comparación solo con la mitad de partidas con la posición más segura (menos tiempo
   lejos de un punto real antes del 14). Si esa mitad separa y la otra no, el ruido de la reconstrucción lo iguala.
3. **¿Mide lo que dice?** Se cruza con la estadística oficial de Riot que le corresponde (``challenges``), partida a
   partida (correlación). Casi 0: el detector no mide eso.

``python -m app.coach.calibration_audit [carpeta de datos] [salida.json]`` lo imprime y lo guarda.
"""

from __future__ import annotations

import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from app.coach import geometry as geo
from app.coach import route_macro, rule_metrics
from app.coach.elo_profile import MOVE_KEYS, game_mistakes
from app.coach.insights import lens_on_cooldown, reliable

GROUP_OF = {
    "IRON": "BRONZE", "BRONZE": "BRONZE", "SILVER": "GOLD", "GOLD": "GOLD", "PLATINUM": "PLATINUM",
    "EMERALD": "EMERALD", "DIAMOND": "DIAMOND", "MASTER": "MASTER", "GRANDMASTER": "MASTER", "CHALLENGER": "CHALLENGER",
}
GROUPS = ("BRONZE", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "CHALLENGER")
MIN_GAMES = 100  # partidas de un elo para compararlo
SEPARATES_Z = 3.0
SAME_Z = 1.5
EXACT_ANCHORS = (0, 1, 2, 3)  # frame, evento propio, fuente, muerte (el resto se deducen)
FAR_FROM_REAL_S = 20  # «posición insegura»: a más de esto de un punto real
SURE_GAP_S = 10  # una detección «segura»: las posiciones que usó, a menos de esto de un punto real
EARLY_S = 14 * 60

# Métricas que salen del recorrido reconstruido (las demás salen de eventos exactos de Riot).
TRACK_BASED = {
    "mv_lane_pre14", "mv_river_pre14", "mv_enemy_jungle_pre14", "mv_base_pct", "mv_grouped_late", "mv_side_alone_late",
    "rm_roams", "rm_roam_success_pct", "rm_roam_cs_cost", "rm_counter_near_pct", "rm_mid_with_jg_pct", "rm_adc_alone_deaths",
    "rm_roam_lane", "rm_roam_river", "rm_roam_own_jg", "rm_roam_enemy_jg", "rm_ward_lane_pct", "rm_ward_river_pct",
    "rm_ward_enemy_jg_pct", "rm_ward_enemy_jg10", "rm_pink_obj", "rm_obj_ward_pct",
}
# Estadística oficial de Riot con la que debería ir (misma partida): (métrica, challenge, roles).
OFFICIAL = [
    ("rm_roam_lane", "killsOnOtherLanesEarlyJungleAsLaner", ("TOP", "MID", "ADC", "SUPPORT")),
    ("rm_roam_success_pct", "killsOnOtherLanesEarlyJungleAsLaner", ("TOP", "MID", "ADC", "SUPPORT")),
    ("mv_enemy_jungle_pre14", "enemyJungleMonsterKills", ("JUNGLE",)),
    ("mv_enemy_jungle_pre14", "moreEnemyJungleThanOpponent", ("JUNGLE",)),
    ("pinks_rio_mitad_rival_pct", "controlWardTimeCoverageInRiverOrEnemyHalf", ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")),
    ("rm_ward_enemy_jg_pct", "controlWardTimeCoverageInRiverOrEnemyHalf", ("JUNGLE", "SUPPORT")),
]


def _certainty(track: dict) -> float | None:
    """Fracción del juego antes del 14 con la posición a más de ``FAR_FROM_REAL_S`` de un punto real (0 = toda segura)."""
    real = sorted(a[0] for a in track.get("a") or [] if a[3] in EXACT_ANCHORS and a[0] <= EARLY_S + 60)
    if len(real) < 3:
        return None
    far = 0
    samples = range(90, EARLY_S, 5)
    import bisect

    for t in samples:
        i = bisect.bisect_left(real, t)
        gap = min(abs(t - real[j]) for j in (i - 1, i) if 0 <= j < len(real))
        far += gap > FAR_FROM_REAL_S
    return far / len(samples)


def _pinks_river_enemy_half(d: dict) -> float | None:
    """% de tus pinks puestas en el río o en la mitad rival (con tu recorrido), para cruzar con Riot."""
    a = d.get("analysis") or {}
    team = a.get("team")
    track = d.get("track") or {}
    anchors = [(int(x[0] * 1000), x[1], x[2], x[3]) for x in track.get("a") or []]
    if not team or len(anchors) < 5:
        return None
    from app.coach.analyzer import interpolate

    times = [x[0] for x in anchors]
    spots = []
    for w in (d.get("events") or {}).get("wards_placed") or []:
        if w[1] == "CONTROL_WARD" and (p := interpolate(anchors, int(w[0] * 1000), times)) is not None:
            spots.append(p)
    if len(spots) < 2:
        return None
    enemy_half = (lambda p: p[0] + p[1] > 15000) if team == 100 else (lambda p: p[0] + p[1] < 15000)
    return 100 * sum(1 for p in spots if geo.zone(p) == "river" or enemy_half(p)) / len(spots)


def mistake_of(moment: dict, role: str) -> str | None:
    from app.coach.insights import mistake_type

    mt = mistake_type(moment.get("kind"), set(moment.get("tags") or []), role)
    return mt["id"] if mt else None


def _folder_dossiers(data_dir: Path):
    for src in ("otps", "partidas_campeon"):
        for f in Path(data_dir, src).glob("*/*/*/partidas.jsonl.gz"):
            try:
                with gzip.open(f, "rt", encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
            except (OSError, EOFError):
                continue


def _dossiers(data_dir: Path):
    """Cada partida de cada jugador: carpetas de OTPs y de jugadores y, una vez, los 10 de la búsqueda por partidas."""
    from app.ingest import patterns

    seen: set[tuple] = set()
    for d in _folder_dossiers(data_dir):
        seen.add((d.get("match_id"), d.get("team"), d.get("champ")))
        yield d
    for _, d in patterns.iter_dossiers(data_dir):
        if (d.get("match_id"), d.get("team"), d.get("champ")) not in seen:
            yield d


def _values(d: dict, a: dict) -> dict:
    """Métricas de «Mi progreso» de una partida (movimiento, recorridos, reglas y pinks en el río o la mitad rival)."""
    values = {k: (a.get("metrics") or {}).get(k) for k in MOVE_KEYS}
    try:
        values.update(route_macro.from_dossier(d))
        values.update(rule_metrics.from_dossier(d))
    except Exception:  # noqa: BLE001
        pass
    values["pinks_rio_mitad_rival_pct"] = _pinks_river_enemy_half(d)
    return {k: float(v) for k, v in values.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _by_certainty(moments: list[dict], role: str) -> tuple[set, set, bool]:
    """Errores decididos con posiciones seguras / inseguras (``ev`` de cada momento; solo partidas nuevas)."""
    sure_m, unsure_m, with_ev = set(), set(), False
    for mo in moments:
        ev = mo.get("ev")
        if ev is None or mo.get("severity") not in ("high", "medium", "low"):
            continue
        with_ev = True
        mt = mistake_of(mo, role)
        if mt is None:
            continue
        gap = max((v for v in ev.values() if isinstance(v, (int, float))), default=0.0)
        (sure_m if gap <= SURE_GAP_S else unsure_m).add(mt)
    return sure_m, unsure_m, with_ev


def records(data_dir: Path):
    """Una fila por partida: rol, elo, certeza del recorrido, métricas, errores y estadísticas oficiales."""
    for d in _dossiers(data_dir):
        a = d.get("analysis") or {}
        group = GROUP_OF.get((d.get("tier") or "").upper())
        role = d.get("role") or a.get("role")
        if not group or not role or not a.get("has_timeline") or not reliable(a):
            continue
        moments = lens_on_cooldown(a.get("moments") or [], [w[0] for w in (d.get("events") or {}).get("wards_killed") or []])
        sure_m, unsure_m, with_ev = _by_certainty(moments, role)
        yield {
            "role": role, "group": group, "certainty": _certainty(d.get("track") or {}),
            "with_ev": with_ev, "sure_mistakes": sure_m, "unsure_mistakes": unsure_m,
            "values": _values(d, a),
            "mistakes": {m for m, _ in game_mistakes(moments, role, d.get("champ"))},
            "official": {k: float(v) for k, v in (d.get("challenges") or {}).items() if isinstance(v, (int, float))},
        }


def _stats(vals: list[float]) -> tuple[int, float, float]:
    n = len(vals)
    if n < 2:
        return n, (vals[0] if vals else 0.0), 0.0
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    return n, mean, math.sqrt(var / n)


def _verdict(z: float) -> str:
    """Veredicto de la comparación según su z: «separa», «igual» o «dudoso»."""
    if abs(z) >= SEPARATES_Z:
        return "separa"
    return "igual" if abs(z) < SAME_Z else "dudoso"


def compare(by_group: dict[str, list[float]]) -> dict:
    """El elo más bajo con muestra frente al más alto: medias, z y veredicto."""
    present = [g for g in GROUPS if len(by_group.get(g, [])) >= MIN_GAMES]
    if len(present) < 2:
        return {"veredicto": "pocos datos", "elos": {g: len(by_group.get(g, [])) for g in GROUPS if by_group.get(g)}}
    lo, hi = present[0], present[-1]
    n1, m1, se1 = _stats(by_group[lo])
    n2, m2, se2 = _stats(by_group[hi])
    se = math.sqrt(se1**2 + se2**2) or 1e-9
    z = (m2 - m1) / se
    verdict = _verdict(z)
    return {
        "veredicto": verdict, "z": round(z, 1), "bajo": lo, "alto": hi,
        "media_bajo": round(m1, 3), "media_alto": round(m2, 3), "n_bajo": n1, "n_alto": n2,
        "medias": {g: round(_stats(by_group[g])[1], 3) for g in present},
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 50:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not sx or not sy:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy), 2)


def _by_position_certainty(item: dict, rows: list[dict], k: str) -> None:
    """¿La mitad con la posición más segura sí separa? Entonces lo iguala el ruido de la reconstrucción."""
    sure: dict[str, list[float]] = defaultdict(list)
    unsure: dict[str, list[float]] = defaultdict(list)
    # Por orden (no por valor: con empates, la mitad «segura» se lo llevaba todo).
    ranked = sorted((r for r in rows if k in r["values"] and r["certainty"] is not None), key=lambda r: r["certainty"])
    for i, r in enumerate(ranked):
        (sure if i < len(ranked) // 2 else unsure)[r["group"]].append(r["values"][k])
    s, u = compare(sure), compare(unsure)
    item["posicion_segura"] = {x: s.get(x) for x in ("veredicto", "z", "medias")}
    item["posicion_insegura"] = {x: u.get(x) for x in ("veredicto", "z", "medias")}
    if s["veredicto"] == "separa" and u["veredicto"] != "separa":
        item["diagnostico"] = "lo iguala el ruido de la reconstrucción"


def _vs_official(item: dict, rows: list[dict], k: str, official: str) -> None:
    """Correlación, partida a partida, con la estadística oficial de Riot que le corresponde (casi 0: no mide eso)."""
    pairs = [(r["values"][k], r["official"][official]) for r in rows if k in r["values"] and official in r["official"]]
    rho = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
    item.setdefault("riot", {})[official] = rho
    if rho is not None and abs(rho) < 0.1 and "diagnostico" not in item:
        item["diagnostico"] = f"no va con la estadística oficial de Riot ({official}, r = {rho}): no mide lo que dice"


def _audit_metric(role: str, rows: list[dict], k: str, median: float | None) -> dict:
    """Una métrica de un rol entre elos, con su diagnóstico (reconstrucción, estadística de Riot o de verdad igual)."""
    groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if k in r["values"]:
            groups[r["group"]].append(r["values"][k])
    item = compare(groups)
    if item["veredicto"] != "separa" and k in TRACK_BASED and median is not None:
        _by_position_certainty(item, rows, k)
    for metric, official, roles in OFFICIAL:
        if metric == k and role in roles:
            _vs_official(item, rows, k, official)
    if item["veredicto"] == "igual" and "diagnostico" not in item:
        item["diagnostico"] = "igual en todos los elos con muestra de sobra: de verdad no cambia"
    return item


def _by_detection_certainty(item: dict, ev_rows: list[dict], m: str) -> None:
    """Las detecciones seguras / inseguras de un error: si solo separan las seguras, lo iguala el ruido."""
    sure, unsure = defaultdict(list), defaultdict(list)
    for r in ev_rows:
        sure[r["group"]].append(100.0 if m in r["sure_mistakes"] else 0.0)
        unsure[r["group"]].append(100.0 if m in r["unsure_mistakes"] else 0.0)
    s_, u_ = compare(sure), compare(unsure)
    item["deteccion_segura"] = {x: s_.get(x) for x in ("veredicto", "z", "medias")}
    item["deteccion_insegura"] = {x: u_.get(x) for x in ("veredicto", "z", "medias")}
    if s_["veredicto"] == "separa" and u_["veredicto"] != "separa" and item["veredicto"] != "separa":
        item["diagnostico"] = "lo iguala el ruido: las detecciones con posiciones seguras sí separan"


def _audit_mistake(rows: list[dict], m: str) -> dict:
    """Un tipo de error de un rol entre elos (% de partidas con el error), con su diagnóstico."""
    groups = defaultdict(list)
    for r in rows:
        groups[r["group"]].append(100.0 if m in r["mistakes"] else 0.0)
    item = compare(groups)
    # Solo las detecciones seguras / inseguras (partidas analizadas ya con ``ev``).
    ev_rows = [r for r in rows if r.get("with_ev")]
    if ev_rows:
        _by_detection_certainty(item, ev_rows, m)
    if item["veredicto"] == "igual" and "diagnostico" not in item:
        item["diagnostico"] = "igual en todos los elos (% de partidas con el error)"
    return item


def _audit_role(role: str, rows: list[dict]) -> dict:
    """La auditoría de un rol: cada métrica y cada tipo de error."""
    certs = sorted(r["certainty"] for r in rows if r["certainty"] is not None)
    median = certs[len(certs) // 2] if certs else None
    res: dict = {"partidas": len(rows), "posicion_insegura_mediana": round(median, 2) if median is not None else None,
                 "metricas": {}, "errores": {}}
    keys = sorted({k for r in rows for k in r["values"]})
    for k in keys:
        res["metricas"][k] = _audit_metric(role, rows, k, median)
    mistake_ids = sorted({m for r in rows for m in r["mistakes"]})
    for m in mistake_ids:
        res["errores"][m] = _audit_mistake(rows, m)
    return res


def audit(recs: list[dict]) -> dict:
    by_role: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_role[r["role"]].append(r)
    out: dict = {}
    for role, rows in sorted(by_role.items()):
        out[role] = _audit_role(role, rows)
    return out


def _summary_extra(item: dict) -> str:
    """Lo que se añade a la línea de una métrica o un error: diagnóstico, posición segura y correlación con Riot."""
    extra = f" · {item['diagnostico']}" if item.get("diagnostico") else ""
    seg = item.get("posicion_segura")
    if seg:
        extra += f" · con posición segura: {seg.get('veredicto')} (z {seg.get('z')})"
    if item.get("riot"):
        extra += " · Riot: " + ", ".join(f"{o} r={v}" for o, v in item["riot"].items())
    return extra


def summary_lines(report: dict) -> list[str]:
    lines = []
    for role, res in report.items():
        lines.append(f"== {role}: {res['partidas']} partidas · posición insegura (mediana) {res['posicion_insegura_mediana']}")
        for kind in ("metricas", "errores"):
            for k, item in res[kind].items():
                if item["veredicto"] in ("separa", "pocos datos"):
                    continue
                extra = _summary_extra(item)
                lines.append(f"  {kind[:-1]:7s} {k:28s} {item['veredicto']:7s} z {item.get('z')} {item.get('medias')}{extra}")
    return lines


def main(argv: list[str]) -> int:
    data_dir = Path(argv[1]) if len(argv) > 1 else Path("data")
    report = audit(list(records(data_dir)))
    for line in summary_lines(report):
        print(line)
    if len(argv) > 2:
        Path(argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
