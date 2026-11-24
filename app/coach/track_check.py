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


def cases(match: dict, timeline: dict, pid: int, catalog=None, camps: bool = True):
    """(fuente, puntos sin el quitado, segundo, posición real) por cada posición de un evento de cada jugador.

    Los campamentos de jungla se deducen otra vez sin el punto quitado (si no, el minuto nunca estaría «limpio»)."""
    g = A._Game(match, timeline, A.find_me(match, pid=pid), catalog)
    base = {p: [a for a in raw if a[3] != A.ANCHOR_CAMP] for p, raw in g.raw_anchors.items()}
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


def validate(files: list[Path], catalog=None, camps: bool = True) -> dict:
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
            for fountain, rest, t, truth in cases(match, timeline, pid, catalog, camps):
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
        try:
            d = json.load(gzip.open(f, "rt", encoding="utf-8"))
            match, timeline, me = d["match"], d["timeline"], d["analysis"]["me"]
            g = A._Game(match, timeline, A.find_me(match, pid=me), catalog)
        except (OSError, ValueError, KeyError, TypeError, A.NotInMatch):
            continue
        spans += getattr(g, "camp_minutes", 0)
        misses += getattr(g, "camp_misses", 0)
        base = {p: [a for a in raw if a[3] != A.ANCHOR_CAMP] for p, raw in g.raw_anchors.items()}
        for p, info in g.players.items():
            if not info.get("smite"):
                continue
            fountain = geo.FOUNTAIN.get(info["team"])
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
                without.append(geo.dist(A.interpolate(A._reconstruct(rest, fountain), t), truth))
                with_camps.append(geo.dist(A.interpolate(A._reconstruct(sorted(rest + camps), fountain), t), truth))
    return {"without": stats(without), "with_camps": stats(with_camps), "spans": spans, "misses": misses}


LANE_NEAR = 1600  # radio de experiencia de los súbditos: a más de esto de la línea no la ganas
LANE_CS_MIN = 6  # CS de súbditos en un minuto que exige haber pasado buena parte de él en una línea


def _lane_d(p) -> float:
    return min(geo.dist_polyline(p, pts) for pts, _half in geo.LANES.values())


def validate_lane_xp(files: list[Path], catalog=None, max_minute: int = 15) -> dict:
    """Con la experiencia y el CS de súbditos de cada minuto (que Riot sí da): un laner que en un minuto hizo ≥ 6 CS de
    súbditos sin pelear ni volver a base tuvo que estar en una línea buena parte de él. Cuenta los minutos en que el
    recorrido reconstruido (y la línea recta de antes) lo pone fuera de todas las líneas ≥ 75 % del tiempo, y la CS y
    experiencia medias según el tiempo que lo pone en su línea (más tiempo en línea, más de ambas)."""
    out = {"minutes": 0, "rec_bad": 0, "old_bad": 0, "bins": {}}
    bins: dict[str, list[tuple[float, float]]] = {"0-25": [], "25-50": [], "50-75": [], "75-100": []}
    for f in files:
        try:
            d = json.load(gzip.open(f, "rt", encoding="utf-8"))
            match, timeline, me = d["match"], d["timeline"], d["analysis"]["me"]
            g = A._Game(match, timeline, A.find_me(match, pid=me), catalog)
        except (OSError, ValueError, KeyError, TypeError, A.NotInMatch):
            continue
        fights: dict[int, list[int]] = {}
        for k in g.kills:
            for p in (k["killer"], k["victim"], *k["assists"]):
                fights.setdefault(p, []).append(k["t"])
        for p, info in g.players.items():
            lane = geo.ROLE_LANE.get(info.get("role"))
            if info.get("role") not in ("TOP", "MID", "ADC") or not lane:
                continue
            frames = [a for a in g.raw_anchors[p] if a[3] == 0]
            frame_t = [a[0] for a in frames]
            for i in range(2, min(len(g.ts), max_minute)):
                t0, t1 = g.ts[i - 1], g.ts[i]
                if g.dead_overlap(p, t0, t1) or any(t0 <= t <= t1 for t in fights.get(p, ())):
                    continue
                if any(a[3] == 2 and t0 - 30_000 <= a[0] <= t1 for a in g.raw_anchors[p]):
                    continue  # minuto con vuelta a base
                v = [g.fval(p, j, k) for j in (i - 1, i) for k in ("minionsKilled", "jungleMinionsKilled", "xp")]
                if None in v or v[4] != v[1]:
                    continue
                cs, xp = v[3] - v[0], v[5] - v[2]
                ts = range(t0 + 2_500, t1, 5_000)
                rec = [q for t in ts if (q := g.pos_at(p, t))]
                old = [q for t in ts if (q := A.interpolate(frames, t, frame_t))]
                if not rec or not old:
                    continue
                out["minutes"] += 1
                own = sum(geo.dist_polyline(q, geo.LANES[lane][0]) <= LANE_NEAR for q in rec) / len(rec)
                key = "0-25" if own < 0.25 else "25-50" if own < 0.5 else "50-75" if own < 0.75 else "75-100"
                bins[key].append((cs, xp))
                if cs >= LANE_CS_MIN:
                    out["rec_bad"] += sum(_lane_d(q) <= LANE_NEAR for q in rec) / len(rec) < 0.25
                    out["old_bad"] += sum(_lane_d(q) <= LANE_NEAR for q in old) / len(old) < 0.25
    out["bins"] = {
        k: {"n": len(v), "cs": round(sum(c for c, _ in v) / len(v), 1), "xp": round(sum(x for _, x in v) / len(v))}
        for k, v in bins.items() if v
    }
    return out


def main() -> int:
    from app.config import settings

    files = sorted((Path(settings.data_dir) / "mis_partidas").glob("*/*/*.json.gz"))
    if not files:
        print("No hay partidas en data/mis_partidas.", file=sys.stderr)
        return 1
    out = validate(files)
    print("1) Posición real de un evento quitada y reconstruida con el resto:")
    for name, label in (("linear", "Línea recta"), ("reconstructed", "Reconstruido")):
        s = out[name]
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
