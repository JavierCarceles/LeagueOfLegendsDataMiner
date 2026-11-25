"""Recorridos típicos: cómo se aparta la gente de la línea recta entre dos puntos conocidos de su recorrido.

Riot da la posición de cada jugador una vez por minuto y en algunos eventos; entre dos de esos puntos el analizador
traza una línea recta (rodeando muros). Pero nadie va en línea recta: el jungla que va de su jungla al río pasa por los
campamentos, el support que sale de su línea entra por el río... Esta tabla guarda, aprendido de las partidas
guardadas de todos los elos (``data/otps`` y ``data/partidas_campeon``: ~1,1 millones de posiciones de minuto exactas,
escondidas una a una y reconstruidas sin ellas), cuánto se aparta de media la posición real de la reconstruida en mitad
de cada tramo, según el rol, la fase de la partida y las zonas de salida y de llegada.

Validado el 25-11-2026 (``python -m app.coach.track_check``) con las 55 partidas del usuario, que no se usan para
aprender la tabla: posición del minuto escondida, error mediano 1.337 → 1.314, más de 4.000 unidades 11,6 % → 11,2 %;
los laners, más tiempo en su línea en los minutos en que farmean (1.291 → 1.317 minutos de 1.385 con ≥ 75 % en ella);
posición de un evento quitada, igual (1.113 → 1.126 de mediana, 6,6 % → 6,4 % de fallos grandes).

Probado y descartado:
* Aprenderla de las kills, asistencias y muertes: acertaba más en esos momentos (1.113 → 1.075) pero empeoraba las
  posiciones normales (1.352 → 1.493): en las peleas la gente se aparta de su camino; el resto del tiempo, no tanto.
* Aprenderla aparte para cada elo: no mejoraba nada (1.177 → 1.181 de error mediano con 226.474 casos de prueba). Cuánto
  roamea cada elo cambia mucho (supports fuera de su línea antes del 14: 23 % en Hierro-Bronce, 52 % en Aspirante), pero
  por dónde va la gente entre dos puntos conocidos, no.

Se genera con ``python -m app.coach.typical_paths`` (lee las partidas guardadas) y va con la app
(``typical_paths.json``): la reconstrucción no depende de lo que haya en disco en cada momento.
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from app.coach import geometry as geo

TABLE_FILE = Path(__file__).with_name("typical_paths.json")
VERSION = 1
MIN_SAMPLES = 30  # casos de una casilla para fiarse de su media
EARLY_MS = 14 * 60_000
EXACT_KINDS = (0, 1, 2, 3, 6, 8)  # puntos con los que se aprende: frames, eventos, base, muertes, campamentos, línea


def blue_side(p, team: int):
    """Todo en el lado azul: un jugador rojo se ve como si jugara de azul (simetría del mapa)."""
    return p if team == 100 else geo.mirror(p)


def key(role: str, t_ms: int, start, end, f: float) -> str:
    """Casilla de la tabla: rol, fase, zona de salida y de llegada (lado azul) y por dónde del tramo (0, 0,5 o 1)."""
    phase = "early" if t_ms < EARLY_MS else "late"
    return f"{role}|{phase}|{geo.zone(start)}|{geo.zone(end)}|{round(f * 2) / 2}"


@lru_cache(maxsize=1)
def table() -> dict[str, tuple[float, float]]:
    """La tabla que va con la app (``{}`` si no está)."""
    try:
        data = json.loads(TABLE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if data.get("version") != VERSION:
        return {}
    return {k: (v[0], v[1]) for k, v in (data.get("offsets") or {}).items()}


def offset(role: str | None, team: int, t_ms: int, start, end, f: float) -> tuple[float, float] | None:
    """Cuánto se aparta de media la posición real de la reconstruida en ese punto del tramo (en coordenadas del mapa)."""
    if not role or team not in (100, 200):
        return None
    off = table().get(key(role, t_ms, blue_side(start, team), blue_side(end, team), f))
    if off is None:
        return None
    return off if team == 100 else (-off[0], -off[1])


# ---------------------------------------------------------------- aprenderla
def _samples(d: dict, frames: bool = False):
    """(casilla, desvío) de cada kill, asistencia o muerte de una partida guardada (o, con ``frames``, de cada posición
    de minuto): se quita y se reconstruye sin ella."""
    from app.coach import analyzer as A

    role, team = d.get("role"), d.get("team")
    track = (d.get("track") or {}).get("a") or []
    if not role or team not in (100, 200) or not track:
        return
    fountain = geo.FOUNTAIN.get(team)
    points = [(int(a[0] * 1000), int(a[1]), int(a[2]), int(a[3])) for a in track if a[3] in EXACT_KINDS]
    wanted = (0,) if frames else (1, 3)
    for j in range(1, len(points) - 1):
        t, x, y, kind = points[j]
        if kind not in wanted or (frames and t < 120_000):
            continue
        rest = points[:j] + points[j + 1:]
        prev, nxt = points[j - 1], points[j + 1]
        if prev[3] == 3 or any(a[0] == t for a in rest):
            continue
        rebuilt = A.interpolate(A._reconstruct(rest, fountain), t)
        if rebuilt is None:
            continue
        f = (t - prev[0]) / max(1, nxt[0] - prev[0])
        start, end = blue_side((prev[1], prev[2]), team), blue_side((nxt[1], nxt[2]), team)
        real, guess = blue_side((x, y), team), blue_side(rebuilt, team)
        yield key(role, t, start, end, f), (real[0] - guess[0], real[1] - guess[1])


def build(data_dir: Path, frames: bool = True) -> dict:
    """La tabla con las partidas guardadas en ``data_dir`` (``otps`` y ``partidas_campeon``)."""
    sums: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    games = 0
    for src in ("otps", "partidas_campeon"):
        for f in sorted((data_dir / src).glob("*/*/*/partidas*.jsonl.gz")):
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    games += 1
                    for k, (dx, dy) in _samples(json.loads(line), frames):
                        s = sums[k]
                        s[0] += 1
                        s[1] += dx
                        s[2] += dy
    offsets = {k: [round(s[1] / s[0]), round(s[2] / s[0]), int(s[0])] for k, s in sorted(sums.items()) if s[0] >= MIN_SAMPLES}
    return {"version": VERSION, "games": games, "samples": int(sum(s[0] for s in sums.values())), "offsets": offsets}


def main() -> int:
    from app.config import settings

    out = build(Path(settings.data_dir))
    if not out["offsets"]:
        print("No hay partidas guardadas con las que aprender la tabla.", file=sys.stderr)
        return 1
    TABLE_FILE.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    table.cache_clear()
    print(f"Recorridos típicos: {len(out['offsets'])} casillas con {out['samples']} casos de {out['games']} partidas → {TABLE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
