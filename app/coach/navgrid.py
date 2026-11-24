"""Por dónde se puede andar en la Grieta y rutas que rodean los muros (para reconstruir los movimientos).

La rejilla (128 × 128, ~117 unidades por celda) sale del minimapa de Data Dragon (``map11.png``: negro = muro; caminos,
río y bases = caminable) y se comprobó con las posiciones reales de las partidas del usuario: el 96 % caen en una celda
caminable y el 99,9 % a una celda o menos. Está guardada aquí (1 bit por celda) para no depender de la imagen.

Las rutas se buscan con A* (8 vecinos) solo cuando la línea recta atraviesa un muro, y se suavizan (de un punto se salta
al más lejano que se ve en línea recta). Los campeones que cruzan muros (Kayn, Zac, Talon, Nidalee, Kindred…) pueden
atravesarlos pagando más, en vez de rodearlos siempre.
"""

from __future__ import annotations

import base64
import heapq
import math
from functools import lru_cache

N = 128
MIN_X, MIN_Y, MAX_X, MAX_Y = -120, -120, 14870, 14980
CELL_X = (MAX_X - MIN_X) / N
CELL_Y = (MAX_Y - MIN_Y) / N
_PACKED = (
    "AAAAAAAAAAAAAAAAAAAH/wAAAAAAAAAAAAAAAAAAD/8AAAAAAAAAAAAAAAAAAA//AAAAAAAAAAAAAAAAAAAP/wAAAAAAAAAAAAAA"
    "P/////8AAAAAAAAAAAAAAH//////AAAAAf///////////////wAAAB////////////////8AAAD/////////////////AAAD////"
    "/////////////wAfD/////////////////8Af9//////////////////AH///////////////////AD7//////////////////AA"
    "+//////////////////wAOf//AAeAHgAef//////8ADv//gAHgB4AHn///////AAf//wAH4H+AA4///////wAH//+AP///8AOP//"
    "////8AA///gH////gHj///////AAP//8D////8D4///////wAD///B///+P/8P//////8AB///4f/+fj/+D///////AAf//+Hh8D"
    "w//A///////wAP///x+AAYP/wH//////8AD///8f+AAD/+B///////AA////n/4AA/Pwf//////wAf////7/A//A8H//////8AH/"
    "///8H4//wHj///////AB/////Af//+A7///////wAf8///gD/x/wP///////8AH/D//gAf4/+D////////AD/wP/gAD8P/w+H///"
    "///wA/4A/wDwfH/8PA//////8AP+AH+B/Hw/vDwH//////AD/gB/g/58P558A//////wA/4AP8P+fB8efAP/////8AP+AD/j/jwA"
    "HvwD//////AD/g+f4/48AD/4B//////wA/4f3/P+PgB/8B//////4AP+H8/7/D4D//A//////8AD/h/v//gf//3x///////AA/4+"
    "9//gH//8////////wAP+fP//wA///v//8////8AD//5//+AH//7//+H////AA//+f//8D/n+f//AP///wAP/nnz//9/4/g//gA//"
    "/8AD/x58f///+Pwf/wAD///AA/4cfB////h4P/4AA8B/wAP+HD4P///+AH/8f4fAf8AD/hw/A//8P4D////HwH/AA/48H4D//g/B"
    "////58B/wAP+PB/gf/8D4//z8/Pgf8AD/nwP////gff/4+f54H/AA/78B////+D//+fH+PB/wAP+/g+H///wf//nj/zwf8AD/v//"
    "g/3/+H//74/88H/AA/5//+P8//z//++H/PB/wAP+Af/j/D/////Pg/zwf8AD/wH/4fwf////78A88H/AA//g/+H8D///4+/gfPB/"
    "wAP/8Pvh/Af//8Hn8P/wf8AD//nwAfwD///A5///8H/AA/5/4Af8A///wH////h/wAP+H///3AP//8B/8Af+f8AD/g///84D///A"
    "f8AHn//AA/4H/4/HA///4D/D34//wAP+Bz4D54f///A/x/+D/8AD/gc8AePP///4P8f/gP/AA/4PP+Hj/////D/H/4B/wAP+Dz/x"
    "4/////4/x//+f8AD/g8/8eP//n//P8P//3/AA/4PH/Hj//4//7/B8H9/wAP+Bw/x4///H/////A/f8AD/geP4+f//4/////wP3/A"
    "A/4Hh8fn/+fH//8/+B//wAP+A8H////D8f/+B/wf/8AD/gPA////gPz//4H8H//AA/4DwAB//wB////gfh//wAP/h8AAf/4eH///"
    "8D8cf8AD///AAP/8Px////wfPH/AA///8AH/+H+f+f/+Dzx/wAP///wD//h/n/Af/we8f8AD////h//+f//gB/+HvH/AA////8//"
    "/3//8AH/w7h/wAP///////+///gB/+P4f8AD///////H///8H//j+H/AA//////8D//APD/f5/h/wAf/////+B//ADw/z//4f8AP"
    "//////AffAA+f8f//H/AD//////gPnwAPn/D/79/wA//////wD58eD4/wf8f/8AP/////+B8fPw+P8H/D//AD//////wfH3+Ph+A"
    "/wf/wA//////+Hw//j8GAP8D/8AP//////j8P/5/gAD/AP/AD////////B/8/4AP/wB/gA////////wP/f/AH/+A/4AP///////+"
    "B///8D//wf+AD///////Dgf/+f4//+P/gA///////w8H/+D/+H///4AP//////8H/8AAf/A///8AD///////B//AAA/wD///AA//"
    "/////4P/wcAA+Af//wAP//////+B/8Pg/HwH//4AD///////g//H9//+B//+AA///////4fD5////gf//AAP//////+PAf////8H"
    "//wAD///////jwD//4P/D//8AA///////54Af/+A/w///gAP//////+eAB/zwA+f//4AD///////jgAOAeAHv//nAA///////44A"
    "DgHgB///xwAP/////////////////58AD/////////////////+fAA///////////////////gA/////////////////+/4A////"
    "//////////////D4AP/////////////////AAAD/////////////////AAAA////////////////+AAAAP///////////////4AA"
    "AAD//////gAAAAAAAAAAAAAA//////wAAAAAAAAAAAAAAP/4AAAAAAAAAAAAAAAAAAD/8AAAAAAAAAAAAAAAAAAA//AAAAAAAAAA"
    "AAAAAAAAAP/wAAAAAAAAAAAAAAAAAAA="
)
_BITS = base64.b64decode("".join(_PACKED))
WALK = [[bool(_BITS[(y * N + x) // 8] & (1 << (7 - (y * N + x) % 8))) for x in range(N)] for y in range(N)]
# Saltan o atraviesan muros con una habilidad básica: cruzar un muro les cuesta poco más que rodearlo.
WALL_HOPPERS = {
    # Cruzan muros a voluntad con una habilidad de enfriamiento corto (o los recorren): casi como andar.
    141: 1.5,  # Kayn (E: atraviesa muros)
    221: 1.5,  # Zeri (E: salta muros y cornisas)
    154: 2.0,  # Zac (E)
    91: 2.0,  # Talon (E: escala muros)
    163: 2.0,  # Taliyah (R: cabalga el muro; W/E no)
    121: 2.0,  # Kha'Zix (E)
    76: 2.5,  # Nidalee (W)
    203: 2.5,  # Kindred (Q)
    18: 2.5,  # Tristana (W)
    105: 2.5,  # Fizz (Q a un objetivo, E)
    164: 2.5,  # Camille (E: se engancha al muro)
    60: 2.5,  # Elise (E araña: rappel)
    # Saltan muros finos con un desplazamiento (o con un ward/objetivo al otro lado).
    64: 3.0,  # Lee Sin (W a un ward)
    81: 3.0,  # Ezreal (E)
    35: 3.0,  # Shaco (Q)
    104: 3.0,  # Graves (E)
    92: 3.0,  # Riven (Q3 y E)
    245: 3.0,  # Ekko (E)
    38: 3.0,  # Kassadin (R)
    42: 3.0,  # Corki (W)
    236: 3.0,  # Lucian (E)
    24: 3.0,  # Jax (Q a un ward)
    55: 3.0,  # Katarina (E a una daga o ward)
    23: 3.0,  # Tryndamere (E)
    150: 3.0,  # Gnar (E)
    266: 3.0,  # Aatrox (E)
    84: 3.0,  # Akali (E)
    246: 3.0,  # Qiyana (W al terreno)
    200: 3.0,  # Bel'Veth (Q)
    7: 3.0,  # LeBlanc (W)
    238: 3.0,  # Zed (W: sombra)
    59: 3.0,  # Jarvan IV (E + Q)
    429: 3.0,  # Kalista (Q + salto del pasivo)
    517: 3.0,  # Sylas (E)
    107: 3.0,  # Rengar (salto desde un arbusto)
}
WALL_COST = 50.0  # resto de campeones: solo si no hay otra (Destello en un muro fino)


def cell(p) -> tuple[int, int]:
    x = int((p[0] - MIN_X) / CELL_X)
    y = int((MAX_Y - p[1]) / CELL_Y)  # la fila 0 es arriba (rojo): y del juego invertida
    return min(N - 1, max(0, x)), min(N - 1, max(0, y))


def center(c: tuple[int, int]) -> tuple[float, float]:
    return MIN_X + (c[0] + 0.5) * CELL_X, MAX_Y - (c[1] + 0.5) * CELL_Y


def walkable(p) -> bool:
    x, y = cell(p)
    return WALK[y][x]


@lru_cache(maxsize=200_000)
def _line_clear(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """¿Se ve b desde a sin cruzar un muro? (muestreo cada media celda). Con caché: las rutas de una partida y de otras
    repiten muchos tramos (el análisis de cada partida hacía ~10.000 comprobaciones)."""
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    steps = max(abs(dx), abs(dy)) * 2
    free = _FREE
    for i in range(1, steps):
        f = i / steps
        if not free[round(ay + dy * f) * N + round(ax + dx * f)]:
            return False
    return True


def clear(p, q) -> bool:
    return _line_clear(cell(p), cell(q))


def _nearest_walkable(c: tuple[int, int]) -> tuple[int, int]:
    if WALK[c[1]][c[0]]:
        return c
    for r in range(1, 6):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                x, y = c[0] + dx, c[1] + dy
                if 0 <= x < N and 0 <= y < N and WALK[y][x]:
                    return x, y
    return c


_FREE = [WALK[i // N][i % N] for i in range(N * N)]
_STEPS = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414))
# A* ponderado: la heurística (octil) pesa un 30 % más. Explora unas 5 veces menos celdas y las rutas salen como mucho
# un 30 % más largas que la óptima (en la práctica, casi iguales tras el suavizado). Con ~6.500 tramos por partida, el
# análisis pasaba de 0,02 a 0,24 s; así no frena al recolector.
HEURISTIC_WEIGHT = 2.0


@lru_cache(maxsize=50_000)
def _route(a: tuple[int, int], b: tuple[int, int], wall: float) -> tuple[tuple[int, int], ...]:
    """Celdas de a a b (A*), suavizadas. wall: coste de pisar un muro (alto = rodearlo)."""
    a, b = _nearest_walkable(a), _nearest_walkable(b)
    if a == b or _line_clear(a, b):
        return (a, b)
    path = _astar(a, b, wall)
    if path is None:
        return (a, b)
    return _smooth(path)


def _heuristic(x: int, y: int, bx: int, by: int) -> float:
    """Distancia octil de (x, y) a (bx, by), con el peso del A* ponderado."""
    dx, dy = abs(bx - x), abs(by - y)
    return HEURISTIC_WEIGHT * (max(dx, dy) + 0.414 * min(dx, dy))


def _astar(a: tuple[int, int], b: tuple[int, int], wall: float) -> list[tuple[int, int]] | None:
    """Celdas del camino de a a b con A* (8 vecinos), o None si b no se alcanza."""
    bx, by = b
    start, goal = a[1] * N + a[0], by * N + bx
    openq = [(_heuristic(a[0], a[1], bx, by), 0.0, start)]
    came: dict[int, int] = {}
    cost = {start: 0.0}
    pop, inf = heapq.heappop, math.inf
    while openq:
        _, g, cur = pop(openq)
        if cur == goal:
            break
        if g > cost.get(cur, inf):
            continue
        _expand(cur, g, wall, b, openq, came, cost)
    if goal not in came:
        return None
    idx = [goal]
    while idx[-1] != start:
        idx.append(came[idx[-1]])
    return [(i % N, i // N) for i in reversed(idx)]


def _expand(cur: int, g: float, wall: float, b: tuple[int, int], openq: list, came: dict[int, int], cost: dict[int, float]) -> None:
    """Abre los 8 vecinos de la celda ``cur`` del A*: apunta los que mejoran su coste y los mete en la cola."""
    bx, by = b
    free, push, inf = _FREE, heapq.heappush, math.inf
    cy, cx = divmod(cur, N)
    for dx, dy, step in _STEPS:
        nx, ny = cx + dx, cy + dy
        if not (0 <= nx < N and 0 <= ny < N):
            continue
        nxt = ny * N + nx
        # En diagonal no se cortan esquinas: las dos celdas de al lado también tienen que ser suelo.
        ok = free[nxt] and (dx == 0 or dy == 0 or (free[cy * N + nx] and free[ny * N + cx]))
        ng = g + (step if ok else step * wall)
        if ng < cost.get(nxt, inf):
            cost[nxt] = ng
            came[nxt] = cur
            push(openq, (ng + _heuristic(nx, ny, bx, by), ng, nxt))


def _smooth(path: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Suavizado: de cada punto, avanza mientras el siguiente se vea en línea recta."""
    out = [path[0]]
    i = 0
    while i < len(path) - 1:
        j = i + 1
        while j + 1 < len(path) and _line_clear(path[i], path[j + 1]):
            j += 1
        out.append(path[j])
        i = j
    return tuple(out)


def route(p, q, champ: int | None = None) -> list[tuple[float, float]]:
    """Puntos de paso de p a q rodeando muros (incluye p y q). Línea recta si no hay muro en medio."""
    a, b = cell(p), cell(q)
    if a == b or _line_clear(a, b):
        return [p, q]
    cells = _route(a, b, WALL_HOPPERS.get(champ or 0, WALL_COST))
    mid = [center(c) for c in cells[1:-1]]
    return [p, *mid, q]


def length(points: list[tuple[float, float]]) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def along(points: list[tuple[float, float]], f: float) -> tuple[float, float]:
    """Punto a la fracción f (0-1) del recorrido."""
    total = length(points)
    if total <= 0:
        return points[0]
    target = f * total
    for i in range(len(points) - 1):
        seg = math.dist(points[i], points[i + 1])
        if target <= seg or i == len(points) - 2:
            g = 0 if seg == 0 else min(1.0, target / seg)
            return points[i][0] + (points[i + 1][0] - points[i][0]) * g, points[i][1] + (points[i + 1][1] - points[i][1]) * g
        target -= seg
    return points[-1]


ANCHOR_ROUTE = 7  # punto de paso de una ruta (rodeando muros), añadido entre dos puntos conocidos


def route_track(anchors: list[list], champ: int | None = None) -> list[list]:
    """Puntos de paso de la repetición (``[segundo, x, y, tipo]``) con las rutas que rodean muros entre cada dos.

    No se tocan los tramos «quieto y salta» (3 muerte, 4 vuelta a base o teletransporte): ahí no se anda. A cada punto
    nuevo se le da su hora repartiendo el tiempo del tramo según la distancia (velocidad constante)."""
    out: list[list] = []
    for i, a in enumerate(anchors):
        out.append(list(a))
        if i + 1 >= len(anchors) or a[3] in (3, 4):
            continue
        b = anchors[i + 1]
        if b[0] <= a[0]:
            continue
        pts = route((a[1], a[2]), (b[1], b[2]), champ)
        if len(pts) <= 2:
            continue
        total = length(pts)
        if total <= 0:
            continue
        done = 0.0
        for j in range(1, len(pts) - 1):
            done += math.dist(pts[j - 1], pts[j])
            out.append([round(a[0] + (b[0] - a[0]) * done / total, 1), int(pts[j][0]), int(pts[j][1]), ANCHOR_ROUTE])
    return out


def route_replay(replay: dict, champs: dict[str, int]) -> dict:
    """La repetición con rutas que rodean muros (para la vista 2D)."""
    tracks = {}
    for pid, tr in (replay.get("tracks") or {}).items():
        tracks[pid] = {**tr, "a": route_track(tr.get("a") or [], champs.get(pid))}
    return {**replay, "tracks": tracks, "routed": True}
