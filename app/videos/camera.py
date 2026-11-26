"""Vistazos de cámara de una partida en vídeo: cuántas veces mira el jugador otra zona del mapa y vuelve.

Un vistazo es la cámara saltando más de ``JUMP`` unidades (fuera de las bases) en una muestra y volviendo a menos de
``BACK`` del punto de partida en ``RETURN_S`` segundos o menos. Con una muestra cada 2 s, los de menos de 2 s no se ven:
es un mínimo. Validado el 26-11-2026 con 223 partidas de junglas de elo alto: el 58 % de los vistazos caen junto a un
campeón visible, frente al 16 % de un punto al azar (ver macro/macro_internet_por_elo.md, C1c).
"""

from __future__ import annotations

import bisect

from app.coach import geometry as geo

JUMP = 3_000
BACK = 1_500
RETURN_S = 10
MAX_GAP_S = 3  # entre dos muestras seguidas de la cámara
CLOCK_SLACK_S = 15  # una lectura del reloj vale para las muestras de hasta 15 s después
PHASES = ((0, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 30), (30, 99))  # minutos de partida
# Junglas de elo alto en vídeo (223 partidas; ~100 h): vistazos por minuto en cada tramo de PHASES.
HIGH_ELO = (1.47, 1.45, 1.13, 0.88, 0.72, 0.62, 0.56)
MIN_PHASE_MIN = 2.0  # minutos con cámara en un tramo para dar su cifra


def _game_clock(frames: dict):
    rel = frames.get("reloj") or []
    ts = [r[0] for r in rel]

    def at(t: float) -> float | None:
        i = bisect.bisect_right(ts, t) - 1
        return rel[i][1] + (t - ts[i]) if i >= 0 and t - ts[i] <= CLOCK_SLACK_S else None

    return at


def _phase(game_s: float) -> int:
    minute = game_s / 60
    for i, (a, b) in enumerate(PHASES):
        if a <= minute < b:
            return i
    return len(PHASES) - 1


def _in_base(p) -> bool:
    return geo.zone(p).startswith("base")


def _return_index(cam: list, i: int, anchor) -> int | None:
    t1 = cam[i][0]
    for j in range(i + 1, len(cam)):
        if cam[j][0] - t1 > RETURN_S:
            return None
        if geo.dist(cam[j][2], anchor) < BACK:
            return j
    return None


def glances(frames: dict) -> dict | None:
    """``{"por_minuto": [...], "minutos": [...], "elo_alto": [...], "total": n, "zonas": {...}}`` o ``None`` sin datos."""
    if not frames or not frames.get("partida") or frames.get("hud") != "jugador" or not frames.get("reloj_ok"):
        return None
    clock = _game_clock(frames)
    cam = [(t, clock(t), (x, y)) for t, x, y in frames.get("camara") or []]
    cam = [c for c in cam if c[1] is not None]
    if len(cam) < 30:
        return None
    minutes = [0.0] * len(PHASES)
    counts = [0] * len(PHASES)
    zones: dict[str, int] = {}
    for a, b in zip(cam, cam[1:]):
        if b[0] - a[0] <= MAX_GAP_S and 0 < b[1] - a[1] <= MAX_GAP_S + 1:
            minutes[_phase(b[1])] += (b[1] - a[1]) / 60
    i = 1
    while i < len(cam):
        t0, _, anchor = cam[i - 1]
        t1, g1, p = cam[i]
        back = None
        if t1 - t0 <= MAX_GAP_S and geo.dist(anchor, p) > JUMP and not _in_base(p) and not _in_base(anchor):
            back = _return_index(cam, i, anchor)
        if back is None:
            i += 1
            continue
        counts[_phase(g1)] += 1
        zone = geo.zone(p)
        zone = "jungla" if zone.startswith("jungle_") else zone
        zones[zone] = zones.get(zone, 0) + 1
        i = back
    rates = [round(c / m, 2) if m >= MIN_PHASE_MIN else None for c, m in zip(counts, minutes)]
    early_min = sum(minutes[:3])
    return {
        "por_minuto": rates,
        "minutos": [round(m, 1) for m in minutes],
        "tramos": [list(p) for p in PHASES],
        "elo_alto": list(HIGH_ELO),
        "antes_del_15": round(sum(counts[:3]) / early_min, 2) if early_min >= MIN_PHASE_MIN else None,
        "elo_alto_antes_del_15": round(sum(HIGH_ELO[:3]) / 3, 2),
        "total": sum(counts),
        "zonas": zones,
    }
