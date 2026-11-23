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


def cases(match: dict, timeline: dict, pid: int, catalog=None):
    """(fuente, puntos sin el quitado, segundo, posición real) por cada posición de un evento de cada jugador."""
    g = A._Game(match, timeline, A.find_me(match, pid=pid), catalog)
    for p, raw in g.raw_anchors.items():
        fountain = geo.FOUNTAIN.get(g.players[p]["team"])
        for i, (t, x, y, kind) in enumerate(raw):
            if kind != 1 or i in (0, len(raw) - 1):
                continue
            rest = raw[:i] + raw[i + 1:]
            if any(a[0] == t for a in rest):
                continue  # otro punto en el mismo instante: sería hacer trampa
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


def validate(files: list[Path], catalog=None) -> dict:
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
            for fountain, rest, t, truth in cases(match, timeline, pid, catalog):
                old = A.interpolate([a for a in rest if a[3] in (0, 1, 2, 3)], t)
                new = A.interpolate(A._reconstruct(rest, fountain), t)
                if old and new:
                    linear.append(geo.dist(old, truth))
                    rebuilt.append(geo.dist(new, truth))
        except A.NotInMatch:
            continue
    return {"linear": stats(linear), "reconstructed": stats(rebuilt)}


def main() -> int:
    from app.config import settings

    files = sorted((Path(settings.data_dir) / "mis_partidas").glob("*/*/*.json.gz"))
    if not files:
        print("No hay partidas en data/mis_partidas.", file=sys.stderr)
        return 1
    out = validate(files)
    for name, label in (("linear", "Línea recta"), ("reconstructed", "Reconstruido")):
        s = out[name]
        print(f"{label:13s} {s['n']} casos · error mediano {s['median']} u. · medio {s['mean']} · p90 {s['p90']} · "
              f"más de {FAR} u.: {s['far_pct']} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
