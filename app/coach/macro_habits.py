"""Hábitos de macro de ``macro/macro_internet_por_elo.md`` (investigación del 25-11-2026) que se pueden medir con lo que
ya guarda el análisis de cada partida. Como los roams de ``route_macro``, no son errores sueltos: se comparan con lo que
hace cada elo en «Cómo te mueves por el mapa» y ninguno se marca como error sin validarlo antes por elo.

- **C-1 Objetivos que acaban en estructuras**: tras un dragón, unas larvas, el heraldo o el barón de tu equipo, ¿cae una
  torre o un inhibidor rival en los 90 s siguientes (3 min tras el barón)? No cuenta si tu equipo pierde a dos o más en
  ese rato. Es lo que más separa los elos según las guías de Diamante → Maestro («encadenar objetivos»).
- **C-4 Ritmo del jungla**: minuto en que llegas a 24 de CS (el primer full clear: 6 campamentos de 4) y de tu primera
  vuelta a base sin haber muerto antes. Medianas de roflstats: full clear 3:24 en Hierro y 2:56 en Aspirante; primera
  vuelta 4:42 y 3:54.
- **C-5 Farsight**: el trinket azul cada 10 min (roflstats: 0,02 en Hierro → 0,5 en Aspirante).
- **C-6 Jungla parado tras el full clear**: minutos del 4 al 14 sin CS, sin kill ni asistencia, sin objetivo de tu
  equipo, vivo y fuera de base: el fallo que señalan las guías de jungla de 2026 («lo grave no es el full clear, sino
  quedarse 30 s sin hacer nada después»).
- **C-14 El «disparo de más» en un dive**: tu muerte bajo una torre rival en pie en los 10 s siguientes a una kill de tu
  equipo en el mismo sitio (ya estaba la kill y alguien se come un disparo de más). Medido el 25-11-2026: no separa
  elos (0,06-0,11 por partida en todos, algo más en elo alto, que divea más): se calcula, pero no se enseña.

Entradas como las de ``route_macro.features``: kills ``[segundo, matador, víctima, x, y, asistentes]``, objetivos
``[segundo, tipo, equipo que lo coge, x, y]``, estructuras ``[segundo, equipo dueño, tipo, línea, x, y]``, CS por
minuto (índice = minuto) y vueltas a base ``{"t", "dead", …}``.
"""

from __future__ import annotations

from app.coach import geometry as geo

EPIC = ("dragon", "elder", "grubs", "herald", "baron")
CONVERT_S = 90
CONVERT_BARON_S = 180
CONVERT_MAX_LOSSES = 2  # perder a dos o más en la ventana: la jugada salió mal, no es «no convertir»
GRUBS_MERGE_S = 60  # las larvas caen seguidas: una sola jugada
STRUCTURES = ("tower", "inhibitor")
FULL_CLEAR_CS = 24
FULL_CLEAR_BY_MIN = 8  # sin llegar a 24 de CS en el minuto 8 no hubo full clear (invasión, ganks, peleas)
JG_IDLE_MINUTES = (4, 14)
FARSIGHT = "BLUE_TRINKET"
MIN_MINUTES = 15
DIVE_TOWER_RANGE = 900  # alcance de una torre (~750) más el margen de la posición
DIVE_AFTER_KILL_S = 10
DIVE_SAME_PLAY = 1_500
TOWER_SAME = 300  # una torre caída es «esa torre» si cayó a menos de esto de su sitio

# Torres de cada equipo (exteriores, interiores, de inhibidor y de nexo), comprobadas con las estructuras caídas de
# las partidas guardadas.
TURRETS = {
    100: ((981, 10441), (1512, 6699), (1169, 4287), (5846, 6396), (5048, 4812), (3651, 3696),
          (10504, 1029), (6919, 1483), (4281, 1253), (1748, 2270), (2177, 1807)),
    200: ((4318, 13875), (7943, 13411), (10481, 13650), (8955, 8510), (9767, 10113), (11134, 11207),
          (13866, 4505), (13327, 8226), (13624, 10572), (12611, 13084), (13052, 12612)),
}

KEYS = ("rm_obj_convert_pct", "rm_jg_clear_min", "rm_first_back_min", "rm_jg_idle_min", "rm_farsight10", "rm_dive_extra")


def team_of(pid) -> int | None:
    """Equipo de un participante de Riot (1-5 azul, 6-10 rojo); ``None`` para torres, súbditos o monstruos."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if 1 <= pid <= 5:
        return 100
    return 200 if 6 <= pid <= 10 else None


def habits(
    *,
    me: int,
    team: int | None,
    role: str | None,
    kills: list,
    objectives: list | None,
    buildings: list | None,
    cs: list | None,
    backs: list | None,
    wards: list | None,
    minutes: float | None,
    pos,
    dead: list[tuple[int, int]],
) -> dict:
    """Todas las métricas de la partida que se pueden calcular con lo que hay (las demás no salen)."""
    out: dict = {}
    if team:
        out.update(objective_conversion(objectives, buildings, kills, team))
        out.update(dive_extra(kills, buildings, me, team))
    out.update(farsight(wards, minutes))
    if role == "JUNGLE":
        out.update(jungle_pace(cs, backs))
        if team:
            out.update(jungle_idle(cs, kills, me, team, objectives, pos, dead))
    return out


# ------------------------------------------------------------------ C-1
def _epic_events(objectives: list | None, team: int) -> list[tuple[float, str]]:
    """(segundo, tipo) de los objetivos de tu equipo; las larvas seguidas cuentan como una jugada (la última)."""
    out: list[tuple[float, str]] = []
    for o in sorted(objectives or [], key=lambda o: o[0]):
        if o[1] not in EPIC or o[2] != team:
            continue
        if o[1] == "grubs" and out and out[-1][1] == "grubs" and o[0] - out[-1][0] <= GRUBS_MERGE_S:
            out[-1] = (o[0], "grubs")
            continue
        out.append((o[0], o[1]))
    return out


def objective_conversion(objectives: list | None, buildings: list | None, kills: list, team: int) -> dict:
    """% de los objetivos de tu equipo tras los que cae una torre o un inhibidor rival (sin perder a dos o más)."""
    if buildings is None:
        return {}
    considered = converted = 0
    for t, kind in _epic_events(objectives, team):
        end = t + (CONVERT_BARON_S if kind == "baron" else CONVERT_S)
        if sum(1 for k in kills if t < k[0] <= end and team_of(k[2]) == team) >= CONVERT_MAX_LOSSES:
            continue
        considered += 1
        if any(t < b[0] <= end and b[1] != team and b[2] in STRUCTURES for b in buildings):
            converted += 1
    return {"rm_obj_convert_pct": round(100 * converted / considered, 1)} if considered else {}


# ------------------------------------------------------------------ C-4
def _reach_minute(series: list, target: float, by_min: int) -> float | None:
    """Minuto (con decimales, interpolando entre los de Riot) en que la serie llega a ``target``; ``None`` si no llega
    antes de ``by_min`` o falta algún valor."""
    for m in range(1, min(len(series), by_min + 1)):
        a, b = series[m - 1], series[m]
        if a is None or b is None:
            return None
        if b >= target:
            return m - 1 + (target - a) / (b - a) if b > a else float(m)
    return None


def _first_back(backs: list | None) -> float | None:
    """Minuto de la primera vuelta a base tras salir (sin la compra inicial); ``None`` si antes moriste."""
    for b in backs or []:
        t = b.get("t") or 0
        if t <= 60:
            continue  # la compra inicial
        return None if b.get("dead") else t / 60
    return None


def jungle_pace(cs: list | None, backs: list | None) -> dict:
    out: dict = {}
    clear = _reach_minute(cs or [], FULL_CLEAR_CS, FULL_CLEAR_BY_MIN)
    if clear is not None:
        out["rm_jg_clear_min"] = round(clear, 2)
    back = _first_back(backs)
    if back is not None:
        out["rm_first_back_min"] = round(back, 2)
    return out


# ------------------------------------------------------------------ C-5
def farsight(wards: list | None, minutes: float | None) -> dict:
    """Farsight (trinket azul) que pones cada 10 min."""
    if wards is None or not minutes or minutes < MIN_MINUTES:
        return {}
    return {"rm_farsight10": round(10 * sum(1 for w in wards if w[1] == FARSIGHT) / minutes, 2)}


# ------------------------------------------------------------------ C-6
def _busy_minute(lo: float, hi: float, me: int, team: int, kills: list, objectives: list | None) -> bool:
    """¿Hiciste algo en ese minuto? Kill o asistencia tuya, o un objetivo de tu equipo."""
    if any(lo <= k[0] < hi and (k[1] == me or me in (k[5] or [])) for k in kills):
        return True
    return any(lo <= o[0] < hi and o[2] == team for o in objectives or [])


def jungle_idle(cs: list | None, kills: list, me: int, team: int, objectives: list | None, pos, dead: list[tuple[int, int]]) -> dict:
    """Minutos del 4 al 14 sin CS, sin kill ni asistencia, sin objetivo de tu equipo, vivo y fuera de base."""
    first, last = JG_IDLE_MINUTES
    if not cs or len(cs) <= last or any(v is None for v in cs[first : last + 1]):
        return {}
    idle = 0
    for m in range(first, last):
        lo, hi = m * 60, (m + 1) * 60
        if cs[m + 1] > cs[m] or _busy_minute(lo, hi, me, team, kills, objectives):
            continue
        if any(s < hi * 1000 and e > lo * 1000 for s, e in dead):
            continue
        p = pos((lo + 30) * 1000)
        if p is not None and not geo.zone(p).startswith("base"):
            idle += 1
    return {"rm_jg_idle_min": idle}


# ------------------------------------------------------------------ C-14
def _standing_tower(p: tuple, t: float, owner: int, buildings: list | None) -> bool:
    """¿Hay una torre de ``owner`` en pie a tiro de ``p`` en el segundo ``t``?"""
    for tower in TURRETS[owner]:
        if geo.dist(p, tower) > DIVE_TOWER_RANGE:
            continue
        fallen = any(b[0] < t and b[1] == owner and b[2] == "tower" and geo.dist((b[4], b[5]), tower) <= TOWER_SAME for b in buildings or [])
        if not fallen:
            return True
    return False


def dive_extra(kills: list, buildings: list | None, me: int, team: int) -> dict:
    """Tus muertes bajo una torre rival en pie en los 10 s siguientes a una kill de tu equipo en el mismo sitio."""
    if team not in TURRETS:
        return {}
    enemy = 300 - team
    count = 0
    for k in kills:
        if k[2] != me or k[3] is None:
            continue
        t, p = k[0], (k[3], k[4])
        if not _standing_tower(p, t, enemy, buildings):
            continue
        if any(
            j is not k and t - DIVE_AFTER_KILL_S <= j[0] <= t and team_of(j[1]) == team and j[3] is not None
            and geo.dist(p, (j[3], j[4])) <= DIVE_SAME_PLAY
            for j in kills
        ):
            count += 1
    return {"rm_dive_extra": count}
