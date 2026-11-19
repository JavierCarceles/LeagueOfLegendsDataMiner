"""Geometría de la Grieta del Invocador en coordenadas del juego.

El timeline de match-v5 da la posición ``{x, y}`` de cada jugador al final
de cada minuto y la de muchos eventos (kills, objetivos, torres). Aquí se
traducen esas coordenadas a zonas con sentido para un jugador: base, cada
línea, río y los cuatro cuadrantes de jungla.

* Origen abajo a la izquierda (base azul); la base roja está arriba a la
  derecha. El mapa va de (-120, -120) a (14870, 14980).
* Es casi simétrico respecto a un punto: ``(x, y) -> (MX - x, MY - y)``.
  Por eso solo se definen las posiciones del lado azul y el resto se refleja.
* El río sigue la antidiagonal ``x + y ≈ 14900`` (del barón al dragón).
"""

from __future__ import annotations

import math

MAP_MIN = (-120, -120)
MAP_MAX = (14870, 14980)
MX, MY = 14800, 14900  # centro de simetría ×2
RIVER_SUM = 14900
RIVER_HALF = 1250  # |x + y - RIVER_SUM| ≤ RIVER_HALF  (≈ 880 unidades perpendiculares)
BASE_RADIUS = 4300
FOUNTAIN_RADIUS = 1400

FOUNTAIN = {100: (400, 420), 200: (14340, 14390)}


def mirror(p: tuple[float, float]) -> tuple[float, float]:
    return (MX - p[0], MY - p[1])


# Líneas como polilíneas (del lado azul al rojo) y su semiancho.
TOP_LANE = [(1100, 4300), (1250, 11800), (1900, 13000), (3000, 13700), (10500, 13650)]
BOT_LANE = [mirror(p) for p in reversed(TOP_LANE)]
MID_LANE = [(3700, 3700), (11100, 11200)]
LANES = {"top": (TOP_LANE, 1000), "mid": (MID_LANE, 800), "bot": (BOT_LANE, 1000)}

# Línea de cada rol (ADC y support comparten la de abajo).
ROLE_LANE = {"TOP": "top", "MID": "mid", "ADC": "bot", "SUPPORT": "bot"}

OUTER_TURRETS = {
    100: {"top": (981, 10441), "mid": (5846, 6396), "bot": (10504, 1029)},
    200: {"top": (4318, 13875), "mid": (8955, 8510), "bot": (13866, 4505)},
}

_BLUE_CAMPS = {
    "blue": (3821, 8101),
    "gromp": (2288, 8448),
    "wolves": (3780, 6443),
    "raptors": (6823, 5508),
    "red": (7765, 4020),
    "krugs": (8394, 2641),
}
CAMPS: dict[tuple[int, str], tuple[float, float]] = {}
for _name, _pos in _BLUE_CAMPS.items():
    CAMPS[(100, _name)] = _pos
    CAMPS[(200, _name)] = mirror(_pos)
SCUTTLE = {"top": (4400, 9600), "bot": mirror((4400, 9600))}
PITS = {"baron": (5007, 10471), "dragon": (9866, 4414)}

CAMP_LABELS = {
    "blue": "azul",
    "gromp": "Gromp",
    "wolves": "lobos",
    "raptors": "aves",
    "red": "rojo",
    "krugs": "Krugs",
}
ZONE_LABELS = {
    "top": "top",
    "mid": "mid",
    "bot": "bot",
    "river": "el río",
    "base_100": "la base azul",
    "base_200": "la base roja",
}


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dist_segment(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def dist_polyline(p, points) -> float:
    return min(_dist_segment(p, points[i], points[i + 1]) for i in range(len(points) - 1))


def side_of(p) -> int:
    """Mitad del mapa (por el río): 100 = lado azul, 200 = lado rojo."""
    return 100 if p[0] + p[1] < RIVER_SUM else 200


def half_of(p) -> str:
    """Mitad por la diagonal de mid: ``top`` (lado del barón) o ``bot`` (lado del dragón)."""
    return "top" if p[1] > p[0] else "bot"


def in_fountain(team: int, p) -> bool:
    return dist(p, FOUNTAIN[team]) <= FOUNTAIN_RADIUS


def zone(p) -> str:
    """``base_100`` · ``base_200`` · ``top`` · ``mid`` · ``bot`` · ``river`` · ``jungle_{100|200}_{top|bot}``."""
    for team, fountain in FOUNTAIN.items():
        if dist(p, fountain) <= BASE_RADIUS:
            return f"base_{team}"
    best: tuple[str, float] | None = None
    for lane, (points, half) in LANES.items():
        d = dist_polyline(p, points)
        if d <= half and (best is None or d < best[1]):
            best = (lane, d)
    if best:
        return best[0]
    if abs(p[0] + p[1] - RIVER_SUM) <= RIVER_HALF:
        return "river"
    return f"jungle_{side_of(p)}_{half_of(p)}"


def is_lane(z: str) -> bool:
    return z in ("top", "mid", "bot")


def is_jungle_of(z: str, team: int) -> bool:
    return z.startswith(f"jungle_{team}_")


def nearest_camp(p, max_distance: float = 1300) -> tuple[int, str] | None:
    best = None
    for key, pos in CAMPS.items():
        d = dist(p, pos)
        if d <= max_distance and (best is None or d < best[1]):
            best = (key, d)
    return best[0] if best else None


def zone_label(z: str, team: int) -> str:
    """Texto desde el punto de vista del equipo ``team``."""
    if z.startswith("base_"):
        return "tu base" if z == f"base_{team}" else "la base rival"
    if z in ZONE_LABELS:
        return ZONE_LABELS[z]
    if z.startswith("jungle_"):
        _, owner, half = z.split("_")
        whose = "tu jungla" if int(owner) == team else "la jungla rival"
        return f"{whose} ({'lado del barón' if half == 'top' else 'lado del dragón'})"
    return z


OVEREXTEND_MARGIN = 0.85  # justo en el centro de la línea no es estar adelantado


def overextended(team: int, lane: str, p, margin: float = OVEREXTEND_MARGIN) -> bool:
    """Claramente más cerca de la torre exterior rival que de la propia (en esa línea)."""
    if team not in OUTER_TURRETS or (300 - team) not in OUTER_TURRETS or lane not in OUTER_TURRETS[team]:
        return False
    own = OUTER_TURRETS[team][lane]
    enemy = OUTER_TURRETS[300 - team][lane]
    return dist(p, enemy) < margin * dist(p, own)
