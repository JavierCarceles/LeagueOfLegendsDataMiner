"""¿Reconstruye bien las posiciones el analizador? Validación con partidas reales (``python -m app.coach.track_check``).

El timeline de Riot da la posición de cada jugador una vez por minuto y en cada evento (kills, objetivos, torres). Para
medir la reconstrucción se quita cada posición real de un evento, se reconstruye con el resto de datos de la partida y
se mide a qué distancia queda de la real. Se compara con la línea recta entre puntos (lo que se hacía antes).

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
