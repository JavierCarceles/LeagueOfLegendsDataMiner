"""¿Reconstruye bien las posiciones el analizador? Validación con partidas reales (``python -m app.coach.track_check``).

El timeline de Riot da la posición de cada jugador una vez por minuto y en cada evento (kills, objetivos, torres). Para
medir la reconstrucción se quita cada posición real de un evento, se reconstruye con el resto de datos de la partida y
se mide a qué distancia queda de la real. Se compara con la línea recta entre puntos (lo que se hacía antes). También se
esconde la posición de los junglas en un minuto (con su CS de jungla) y se cruza el recorrido de los laners con su CS de
súbditos y su experiencia de cada minuto, que solo se ganan cerca de una línea.

Usa tus partidas de ``data/mis_partidas`` (traen la partida y el timeline en bruto).
"""

from __future__ import annotations

import gzip
import json
import statistics
import sys
from pathlib import Path

from app.coach import analyzer as A
from app.coach import geometry as geo

FAR = 4_000  # «fallo grande»: más lejos que esto de donde estaba de verdad (el mapa mide ~15.000)


DEDUCED = (A.ANCHOR_CAMP, A.ANCHOR_FIGHT, A.ANCHOR_WARD)  # se deducen otra vez sin el punto quitado


def cases(match: dict, timeline: dict, pid: int, catalog=None, camps: bool = True, fights: bool = True):
    """(fuente, puntos sin el quitado, segundo, posición real) por cada posición de un evento de cada jugador.

    Los campamentos de jungla y las peleas sin muerte se deducen otra vez sin el punto quitado (si no, el minuto nunca
    estaría «limpio»)."""
    g = A._Game(match, timeline, A.find_me(match, pid=pid), catalog)
    base = {p: [a for a in raw if a[3] not in DEDUCED] for p, raw in g.raw_anchors.items()}
    junglers = {p for p, info in g.players.items() if info.get("smite") or info.get("role") == "JUNGLE"}
    for p, raw in base.items():
        fountain = geo.FOUNTAIN.get(g.players[p]["team"])
        for i, (t, x, y, kind) in enumerate(raw):
            if kind != 1 or i in (0, len(raw) - 1):
                continue
            rest = raw[:i] + raw[i + 1:]
            if any(a[0] == t for a in rest):
                continue  # otro punto en el mismo instante: sería hacer trampa
            if camps and p in junglers:
                rest = sorted(rest + A.infer_camps(g, {**base, p: rest}).get(p, []))
            if fights:
                rest = sorted(rest + A.infer_fights(g, {**base, p: rest}).get(p, []))
                rest = sorted(rest + A.infer_ward_kills(g, {**base, p: rest}).get(p, []))
            yield fountain, rest, t, (x, y)


def stats(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0}
    s = sorted(errors)
    return {
        "n": len(s),
        "median": round(statistics.median(s)),
        "mean": round(sum(s) / len(s)),
        "p90": round(s[int(0.9 * (len(s) - 1))]),
        "far_pct": round(100 * sum(e > FAR for e in s) / len(s), 1),
    }


def validate(files: list[Path], catalog=None, camps: bool = True, fights: bool = True) -> dict:
    linear: list[float] = []
    rebuilt: list[float] = []
    for f in files:
        try:
            d = json.load(gzip.open(f, "rt", encoding="utf-8"))
        except (OSError, ValueError):
            continue
        match, timeline, pid = d.get("match"), d.get("timeline"), (d.get("analysis") or {}).get("me")
        if not match or not timeline or not pid:
            continue
        try:
            for fountain, rest, t, truth in cases(match, timeline, pid, catalog, camps, fights):
                old = A.interpolate([a for a in rest if a[3] in (0, 1, 2, 3)], t)
                new = A.interpolate(A._reconstruct(rest, fountain), t)
                if old and new:
                    linear.append(geo.dist(old, truth))
                    rebuilt.append(geo.dist(new, truth))
        except A.NotInMatch:
            continue
    return {"linear": stats(linear), "reconstructed": stats(rebuilt)}


def validate_jungle(files: list[Path], catalog=None, max_minute: int = 15) -> dict:
    """Se esconde la posición del jungla en un minuto (su CS de ese minuto sí se sabe) y se reconstruye con la de antes,
    la de después y los campamentos que encajan con su CS. Solo cuenta los casos en que se dedujo algún campamento."""
    without: list[float] = []
    with_camps: list[float] = []
    spans = misses = 0
    for f in files:
        g = _load_game(f, catalog)
        if g is None:
            continue
        spans += getattr(g, "camp_minutes", 0)
        misses += getattr(g, "camp_misses", 0)
        base = {p: [a for a in raw if a[3] != A.ANCHOR_CAMP] for p, raw in g.raw_anchors.items()}
        for p, info in g.players.items():
            if not info.get("smite"):
                continue
            for error_without, error_camps in _jungle_errors(g, base, p, geo.FOUNTAIN.get(info["team"]), max_minute):
                without.append(error_without)
                with_camps.append(error_camps)
    return {"without": stats(without), "with_camps": stats(with_camps), "spans": spans, "misses": misses}


def _load_game(f: Path, catalog=None):
    """Partida de ``data/mis_partidas`` lista para reconstruir (None si el fichero no sirve)."""
    try:
        d = json.load(gzip.open(f, "rt", encoding="utf-8"))
        match, timeline, me = d["match"], d["timeline"], d["analysis"]["me"]
        return A._Game(match, timeline, A.find_me(match, pid=me), catalog)
    except (OSError, ValueError, KeyError, TypeError, A.NotInMatch):
        return None


def _jungle_errors(g, base: dict, p: int, fountain, max_minute: int):
    """(error sin campamentos, error con ellos) de cada minuto en que se esconde la posición del jungla ``p``."""
    for i in range(2, min(len(g.ts) - 1, max_minute)):
        t, truth = g.ts[i], g.frame_pos(p, i)
        if truth is None or g.is_dead(p, t):
            continue
        rest = [a for a in base[p] if not (a[0] == t and a[3] == 0)]
        if any(a[0] == t for a in rest):
            continue
        camps = A.infer_camps(g, {**base, p: rest}, frozenset({(p, i)})).get(p, [])
        if not any(abs(c[0] - t) < 60_000 for c in camps):
            continue
        error_without = geo.dist(A.interpolate(A._reconstruct(rest, fountain), t), truth)
        yield error_without, geo.dist(A.interpolate(A._reconstruct(sorted(rest + camps), fountain), t), truth)


def validate_fights(files: list[Path], catalog=None) -> dict:
    """Se esconde la posición del minuto (la de Riot) de quien hizo daño a campeones en ese minuto o en el siguiente sin
    kill, asistencia ni muerte que lo explique, y se reconstruye con y sin las peleas sin muerte deducidas."""
    return _validate_hidden(files, A.ANCHOR_FIGHT, A.infer_fights, _dealt_damage, catalog)


def validate_ward_kills(files: list[Path], catalog=None) -> dict:
    """Lo mismo con quien rompió un ward rival en ese minuto o en el siguiente: con y sin colocarle junto al ward."""
    return _validate_hidden(files, A.ANCHOR_WARD, A.infer_ward_kills, _broke_ward, catalog)


def _dealt_damage(g, p: int, i: int) -> bool:
    return any(A._minute_delta(g, p, j, "totalDamageDoneToChampions") >= A.FIGHT_DMG for j in (i, i + 1))


def _broke_ward(g, p: int, i: int) -> bool:
    t0, t1 = g.ts[i - 1], g.ts[min(i + 1, len(g.ts) - 1)]
    return any(k == p and t0 < t <= t1 for t, k, _ in g.ward_kill_events)


def _validate_hidden(files: list[Path], kind: int, infer, applies, catalog=None) -> dict:
    """Esconde la posición del frame de cada jugador en los minutos en que ``applies`` y reconstruye con y sin ``infer``."""
    without: list[float] = []
    with_it: list[float] = []
    for f in files:
        g = _load_game(f, catalog)
        if g is None:
            continue
        base = {p: [a for a in raw if a[3] != kind] for p, raw in g.raw_anchors.items()}
        for p, info in g.players.items():
            fountain = geo.FOUNTAIN.get(info["team"])
            for i in range(1, len(g.ts) - 1):
                errors = _hidden_errors(g, base, p, i, fountain, infer) if applies(g, p, i) else None
                if errors is not None:
                    without.append(errors[0])
                    with_it.append(errors[1])
    return {"without": stats(without), "with": stats(with_it)}


def _hidden_errors(g, base: dict, p: int, i: int, fountain, infer) -> tuple[float, float] | None:
    """(error sin lo deducido, error con ello) escondiendo la posición del frame ``i`` de ``p`` (``None`` si no toca)."""
    t, truth = g.ts[i], g.frame_pos(p, i)
    if truth is None or g.is_dead(p, t):
        return None
    rest = [a for a in base[p] if not (a[0] == t and a[3] == 0)]
    if any(a[0] == t for a in rest):
        return None
    deduced = infer(g, {**base, p: rest}).get(p, [])
    if not any(abs(a[0] - t) < 60_000 for a in deduced):
        return None  # nada deducido cerca: las dos reconstrucciones serían iguales
    error_without = geo.dist(A.interpolate(A._reconstruct(rest, fountain), t), truth)
    return error_without, geo.dist(A.interpolate(A._reconstruct(sorted(rest + deduced), fountain), t), truth)


LANE_NEAR = 1600  # radio de experiencia de los súbditos: a más de esto de la línea no la ganas
LANE_CS_MIN = 6  # CS de súbditos en un minuto que exige haber pasado buena parte de él en una línea


def _lane_d(p) -> float:
    return min(geo.dist_polyline(p, pts) for pts, _ in geo.LANES.values())


def validate_lane_xp(files: list[Path], catalog=None, max_minute: int = 15) -> dict:
    """Con la experiencia y el CS de súbditos de cada minuto (que Riot sí da): un laner que en un minuto hizo ≥ 6 CS de
    súbditos sin pelear ni volver a base tuvo que estar en una línea buena parte de él. Cuenta los minutos en que el
    recorrido reconstruido (y la línea recta de antes) lo pone fuera de todas las líneas ≥ 75 % del tiempo, y la CS y
    experiencia medias según el tiempo que lo pone en su línea (más tiempo en línea, más de ambas)."""
    out = {"minutes": 0, "rec_bad": 0, "old_bad": 0, "bins": {}}
    bins: dict[str, list[tuple[float, float]]] = {"0-25": [], "25-50": [], "50-75": [], "75-100": []}
    for f in files:
        g = _load_game(f, catalog)
        if g is None:
            continue
        fights = _fight_times(g)
        for p, info in g.players.items():
            lane = geo.ROLE_LANE.get(info.get("role"))
            if info.get("role") not in ("TOP", "MID", "ADC") or not lane:
                continue
            for minute in _lane_minutes(g, p, fights, max_minute):
                _count_lane_minute(out, bins, lane, minute)
    out["bins"] = {
        k: {"n": len(v), "cs": round(sum(c for c, _ in v) / len(v), 1), "xp": round(sum(x for _, x in v) / len(v))}
        for k, v in bins.items() if v
    }
    return out


def _fight_times(g) -> dict[int, list[int]]:
    """Instantes (ms) de las kills en que participó cada jugador: como asesino, víctima o asistente."""
    fights: dict[int, list[int]] = {}
    for k in g.kills:
        for p in (k["killer"], k["victim"], *k["assists"]):
            fights.setdefault(p, []).append(k["t"])
    return fights


def _lane_minutes(g, p: int, fights: dict, max_minute: int):
    """Los minutos útiles del laner ``p`` (los que no descarta ``_lane_minute``), en orden."""
    frames = [a for a in g.raw_anchors[p] if a[3] == 0]
    frame_t = [a[0] for a in frames]
    for i in range(2, min(len(g.ts), max_minute)):
        minute = _lane_minute(g, p, i, fights, frames, frame_t)
        if minute is not None:
            yield minute


def _lane_minute(g, p: int, i: int, fights: dict, frames: list, frame_t: list):
    """(CS, experiencia, puntos reconstruidos, puntos en línea recta) del minuto ``i`` del laner ``p``; None si no sirve
    (muerto, peleando o volviendo a base, sin sus datos de ese minuto o sin puntos del recorrido)."""
    t0, t1 = g.ts[i - 1], g.ts[i]
    if g.dead_overlap(p, t0, t1) or any(t0 <= t <= t1 for t in fights.get(p, ())):
        return None
    if any(a[3] == 2 and t0 - 30_000 <= a[0] <= t1 for a in g.raw_anchors[p]):
        return None  # minuto con vuelta a base
    v = [g.fval(p, j, k) for j in (i - 1, i) for k in ("minionsKilled", "jungleMinionsKilled", "xp")]
    if None in v or v[4] != v[1]:
        return None
    cs, xp = v[3] - v[0], v[5] - v[2]
    ts = range(t0 + 2_500, t1, 5_000)
    rec = [q for t in ts if (q := g.pos_at(p, t))]
    old = [q for t in ts if (q := A.interpolate(frames, t, frame_t))]
    if not rec or not old:
        return None
    return cs, xp, rec, old


def _lane_bin(own: float) -> str:
    """Tramo (en %) del minuto que el recorrido reconstruido pasa en su línea."""
    if own < 0.25:
        return "0-25"
    if own < 0.5:
        return "25-50"
    if own < 0.75:
        return "50-75"
    return "75-100"


def _count_lane_minute(out: dict, bins: dict, lane: str, minute: tuple) -> None:
    """Suma un minuto útil de un laner a los recuentos de ``validate_lane_xp``."""
    cs, xp, rec, old = minute
    out["minutes"] += 1
    own = sum(geo.dist_polyline(q, geo.LANES[lane][0]) <= LANE_NEAR for q in rec) / len(rec)
    bins[_lane_bin(own)].append((cs, xp))
    if cs >= LANE_CS_MIN:
        out["rec_bad"] += sum(_lane_d(q) <= LANE_NEAR for q in rec) / len(rec) < 0.25
        out["old_bad"] += sum(_lane_d(q) <= LANE_NEAR for q in old) / len(old) < 0.25


def main() -> int:
    from app.config import settings

    files = sorted((Path(settings.data_dir) / "mis_partidas").glob("*/*/*.json.gz"))
    if not files:
        print("No hay partidas en data/mis_partidas.", file=sys.stderr)
        return 1
    out = validate(files)
    plain = validate(files, fights=False)["reconstructed"]
    print("1) Posición real de un evento quitada y reconstruida con el resto:")
    for label, s in (("Línea recta", out["linear"]), ("Sin peleas", plain), ("Reconstruido", out["reconstructed"])):
        print(f"   {label:13s} {s['n']} casos · error mediano {s['median']} u. · medio {s['mean']} · p90 {s['p90']} · "
              f"más de {FAR} u.: {s['far_pct']} %")
    for title, out_h, labels in (
        ("1b) Posición del minuto escondida a quien pegó a campeones sin kill que lo explique (peleas sin muerte):",
         validate_fights(files), ("Sin peleas", "Con peleas")),
        ("1c) Posición del minuto escondida a quien rompió un ward rival:", validate_ward_kills(files), ("Sin wards", "Con wards")),
    ):
        print(title)
        for name, label in zip(("without", "with"), labels):
            s = out_h[name]
            if s.get("n"):
                print(f"   {label:13s} {s['n']} casos · error mediano {s['median']} u. · medio {s['mean']} · p90 {s['p90']} · "
                      f"más de {FAR} u.: {s['far_pct']} %")
    jg = validate_jungle(files)
    print("2) Posición del jungla escondida en un minuto (su CS sí se sabe), con lo de antes y lo de después:")
    for name, label in (("without", "Sin campamentos"), ("with_camps", "Con campamentos")):
        s = jg[name]
        if s.get("n"):
            print(f"   {label:16s} {s['n']} casos · error mediano {s['median']} u. · medio {s['mean']} · más de {FAR} u.: {s['far_pct']} %")
    if jg["spans"]:
        print(f"   Tramos de jungla sin otros datos: {jg['spans']}; no cuadraban con el CS (no se inventa nada): {jg['misses']} "
              f"({round(100 * jg['misses'] / jg['spans'])} %).")
    xp = validate_lane_xp(files)
    print("3) Experiencia y CS de súbditos de cada minuto frente al recorrido (laners, sin pelear ni volver a base):")
    print(f"   {xp['minutes']} minutos. Fuera de todas las líneas ≥ 75 % del minuto con ≥ {LANE_CS_MIN} CS de súbditos (imposible): "
          f"reconstruido {xp['rec_bad']}, línea recta {xp['old_bad']}.")
    for k, b in xp["bins"].items():
        print(f"   En su línea {k} % del minuto: {b['n']} minutos · {b['cs']} CS · {b['xp']} de experiencia de media")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
