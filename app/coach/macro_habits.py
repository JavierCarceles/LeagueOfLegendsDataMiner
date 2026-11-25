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
- **C-3 Peleas tardías concentradas**: cuánto baja la dispersión de las kills de la partida (de los dos equipos) del
  0-10 al 20+ (la «SDist» del estudio de Riot por elo: en Oro baja la mitad que en Aspirante). Es de equipo.
- **C-7 Muerte ante el pico de poder del rival**: tu muerte ante tu rival de línea (solo o con un aliado suyo) en los
  60 s siguientes a que subiera a 6, 11 o 16 (por su experiencia de cada minuto: sus objetos no se guardan).
- **C-8 Placas mientras el rival roamea**: minutos (del 3 al 14) en que tu rival de top o mid está lejos de su línea;
  ¿te quedas en la tuya farmeando?
- **C-9 Muerte cara antes de un objetivo**: muertes en las que reapareces después de que salga el dragón, el ancestral o
  el barón (con la tabla de reaparición y los relojes de ``clocks``), y cuántas acaban con el objetivo para el rival.
- **C-10 Por detrás**: desde el 14, con tu equipo 3.000 de oro o más por detrás (cuando suele haber recompensas de
  objetivo), qué parte de los objetivos que caen cogéis y cuánto mueres.
- **C-12 Solo tras perder la primera torre**: tus muertes sin ningún aliado en la jugada en los 3 min siguientes.
- **C-13 ADC sin desplazamientos en una lateral**: con Jinx, Aphelios, Ashe… tus muertes en top o bot pasado el río
  tras caer la primera torre.

C-2 (larvas o dragón según la prioridad de las líneas a las 8:00) necesita la posición de los 10 jugadores a esa hora,
que los dossiers no guardan: va en el análisis (``analyzer``) y se valida con las partidas nuevas.

Entradas (``Game``) como las de ``route_macro.features``: kills ``[segundo, matador, víctima, x, y, asistentes]``,
objetivos ``[segundo, tipo, equipo que lo coge, x, y]``, estructuras ``[segundo, equipo dueño, tipo, línea, x, y]``, CS
por minuto (índice = minuto) y vueltas a base ``{"t", "dead", …}``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from app.coach import clocks as clk
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

# C-3
SPREAD_EARLY_S = 600
SPREAD_LATE_S = 1_200
SPREAD_MIN_KILLS = 4
SPREAD_MIN_MINUTES = 25
# C-7: experiencia total para llegar a cada nivel (comprobada con los frames de las partidas guardadas)
SPIKE_XP = {6: 2_400, 11: 7_300, 16: 14_700}
SPIKE_WINDOW_S = 60
# C-8
PUNISH_ROLES = ("TOP", "MID")
OPP_ROAM_FAR = 4_000
PUNISH_MINUTES = (3, 14)
PUNISH_LANE = 2_500  # «en tu línea»: a menos de esto de ella
PUNISH_CS = 4  # CS ese minuto para contar que te quedaste farmeando
# C-9
DEAD_SPAWN_BACK_S = 60  # de la fuente al foso: si lo cogen antes de que llegues, lo perdiste por esa muerte
# C-10
BEHIND_FROM_MIN = 14
BEHIND_GOLD = 3_000
BEHIND_MIN_MINUTES = 3
BEHIND_OBJECTIVES = ("dragon", "elder", "herald", "baron")
# C-12
TOWER_AFTER_S = 180
ALONE_WINDOW_S = 15  # una jugada: kills a menos de esto…
ALONE_NEAR = 2_500  # …y de esta distancia de tu muerte
# C-13: ADC sin saltos ni desplazamientos (Sivir, Draven y Twitch solo tienen velocidad o camuflaje)
IMMOBILE_ADCS = frozenset({15, 21, 22, 29, 50, 96, 110, 115, 119, 147, 202, 222, 235, 498, 523, 800})

KEYS = (
    "rm_obj_convert_pct", "rm_jg_clear_min", "rm_first_back_min", "rm_jg_idle_min", "rm_farsight10", "rm_dive_extra",
    "rm_fight_spread_drop", "rm_spike_deaths", "rm_punish_roam_pct", "rm_dead_spawn", "rm_dead_spawn_lost",
    "rm_behind_obj_pct", "rm_behind_deaths10", "rm_alone_after_tower", "rm_adc_side_deaths",
)


@dataclass
class Game:
    """Lo que hace falta de una partida (tuya o del dossier de un OTP) para medir estos hábitos."""

    me: int
    team: int | None
    role: str | None
    kills: list
    pos: Callable[[int], tuple | None]
    dead: list[tuple[int, int]] = field(default_factory=list)
    objectives: list | None = None
    buildings: list | None = None
    cs: list | None = None
    backs: list | None = None
    wards: list | None = None
    minutes: float | None = None
    champ: int | None = None
    clocks: dict | None = None  # relojes de la temporada, solo si cuadran con esta partida (C-9)
    opp: int | None = None  # tu rival de línea (participante)
    opp_frames: list | None = None  # su posición y su experiencia cada minuto: (x, y, xp), None donde falte
    gold_diff: list | None = None  # oro de tu equipo menos el del rival, cada minuto


def team_of(pid) -> int | None:
    """Equipo de un participante de Riot (1-5 azul, 6-10 rojo); ``None`` para torres, súbditos o monstruos."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if 1 <= pid <= 5:
        return 100
    return 200 if 6 <= pid <= 10 else None


def habits(g: Game) -> dict:
    """Todas las métricas de la partida que se pueden calcular con lo que hay (las demás no salen)."""
    out: dict = {}
    if g.team:
        out.update(objective_conversion(g.objectives, g.buildings, g.kills, g.team))
        out.update(dive_extra(g.kills, g.buildings, g.me, g.team))
        out.update(dead_at_spawn(g))
        out.update(behind(g))
        out.update(alone_after_tower(g))
    out.update(farsight(g.wards, g.minutes))
    out.update(fight_spread(g.kills, g.minutes))
    out.update(spike_deaths(g))
    if g.role in PUNISH_ROLES:
        out.update(punish_roams(g))
    if g.role == "ADC" and g.champ in IMMOBILE_ADCS:
        out.update(adc_side_deaths(g))
    if g.role == "JUNGLE":
        out.update(jungle_pace(g.cs, g.backs))
        if g.team:
            out.update(jungle_idle(g.cs, g.kills, g.me, g.team, g.objectives, g.pos, g.dead))
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


# ------------------------------------------------------------------ C-3
def _spread(points: list[tuple]) -> float:
    """Dispersión de unas posiciones: distancia cuadrática media a su centro."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return math.sqrt(sum((p[0] - cx) ** 2 + (p[1] - cy) ** 2 for p in points) / len(points))


def fight_spread(kills: list, minutes: float | None) -> dict:
    """% que baja la dispersión de las kills de la partida (de los dos equipos) del 0-10 al 20+ (partidas de 25 min o más)."""
    if not minutes or minutes < SPREAD_MIN_MINUTES:
        return {}
    early = [(k[3], k[4]) for k in kills if k[0] < SPREAD_EARLY_S and k[3] is not None]
    late = [(k[3], k[4]) for k in kills if k[0] >= SPREAD_LATE_S and k[3] is not None]
    if len(early) < SPREAD_MIN_KILLS or len(late) < SPREAD_MIN_KILLS:
        return {}
    first = _spread(early)
    return {"rm_fight_spread_drop": round(100 * (first - _spread(late)) / first, 1)} if first > 0 else {}


# ------------------------------------------------------------------ C-7
def _spikes(frames: list) -> list[float]:
    """Segundo en que tu rival llega a 6, 11 y 16 (interpolando su experiencia de cada minuto)."""
    xp = [f[2] if f else None for f in frames]
    out = []
    for target in SPIKE_XP.values():
        minute = _reach_minute(xp, target, len(xp) - 1)
        if minute is not None:
            out.append(minute * 60)
    return out


def spike_deaths(g: Game) -> dict:
    """Tus muertes ante tu rival de línea (solo o con un aliado suyo) en los 60 s siguientes a que subiera a 6, 11 o 16."""
    if not g.opp or not g.opp_frames:
        return {}
    spikes = _spikes(g.opp_frames)
    n = sum(
        1 for k in g.kills
        if k[2] == g.me and k[1] == g.opp and len(k[5] or []) <= 1 and any(0 <= k[0] - s <= SPIKE_WINDOW_S for s in spikes)
    )
    return {"rm_spike_deaths": n}


# ------------------------------------------------------------------ C-8
def _gained(cs: list, m: int) -> float:
    a, b = cs[m], cs[m + 1]
    return 0 if a is None or b is None else b - a


def _opp_away(frame, points) -> bool:
    """¿Tu rival está lejos de su línea en ese minuto (y fuera de su base)?"""
    if not frame or frame[0] is None:
        return False
    p = (frame[0], frame[1])
    return not geo.zone(p).startswith("base") and geo.dist_polyline(p, points) > OPP_ROAM_FAR


def punish_roams(g: Game) -> dict:
    """% de los minutos (del 3 al 14) con tu rival lejos de su línea en los que tú sigues en la tuya farmeando."""
    lane = geo.ROLE_LANE.get(g.role or "")
    if not lane or not g.opp_frames or not g.cs:
        return {}
    points, _ = geo.LANES[lane]
    first, last = PUNISH_MINUTES
    roams = punished = 0
    for m in range(first, min(last, len(g.opp_frames), len(g.cs) - 1)):
        p = g.pos(m * 60_000)
        if p is None or not _opp_away(g.opp_frames[m], points):
            continue  # estabas muerto o tu rival no se había ido
        roams += 1
        if geo.dist_polyline(p, points) <= PUNISH_LANE and _gained(g.cs, m) >= PUNISH_CS:
            punished += 1
    return {"rm_punish_roam_pct": round(100 * punished / roams, 1)} if roams else {}


# ------------------------------------------------------------------ C-9
def _lost_after(objs: list[dict], hit: tuple[int, str], team: int | None, until: float) -> bool:
    """¿El rival se llevó ese objetivo (el primero de ese foso tras salir) antes de ``until``?"""
    spawn, kind = hit
    pit = ("baron",) if kind == "baron" else ("dragon", "elder")
    taken = next((o for o in objs if o["kind"] in pit and o["t"] >= spawn), None)
    return taken is not None and taken["team"] != team and taken["t"] <= until


def dead_at_spawn(g: Game) -> dict:
    """Muertes en las que reapareces después de que salga el dragón, el ancestral o el barón, y en cuántas se lo lleva
    el rival antes de que puedas llegar (60 s tras reaparecer)."""
    if not g.clocks or not g.minutes:
        return {}
    objs = [{"kind": o[1], "team": o[2], "t": int(o[0] * 1000)} for o in sorted(g.objectives or [], key=lambda o: o[0])]
    spawns = clk.big_spawns(objs, g.clocks, int(g.minutes * 60_000))
    if spawns is None:
        return {}
    n = lost = 0
    for start, end in g.dead:
        hit = next(((s, kind) for s, kind in spawns if start < s <= end), None)
        if hit is None:
            continue
        n += 1
        if _lost_after(objs, hit, g.team, end + DEAD_SPAWN_BACK_S * 1000):
            lost += 1
    return {"rm_dead_spawn": n, "rm_dead_spawn_lost": lost}


# ------------------------------------------------------------------ C-10
def behind(g: Game) -> dict:
    """Desde el 14, con tu equipo 3.000 de oro o más por detrás: % de los objetivos que caen que cogéis vosotros y tus
    muertes por cada 10 min así."""
    diff = g.gold_diff or []
    minutes = {m for m in range(BEHIND_FROM_MIN, len(diff)) if diff[m] is not None and diff[m] <= -BEHIND_GOLD}
    if len(minutes) < BEHIND_MIN_MINUTES:
        return {}
    taken = [o[2] == g.team for o in g.objectives or [] if o[1] in BEHIND_OBJECTIVES and int(o[0] // 60) in minutes]
    deaths = sum(1 for k in g.kills if k[2] == g.me and int(k[0] // 60) in minutes)
    out = {"rm_behind_deaths10": round(10 * deaths / len(minutes), 2)}
    if taken:
        out["rm_behind_obj_pct"] = round(100 * sum(taken) / len(taken), 1)
    return out


# ------------------------------------------------------------------ C-12
def _died_alone(kills: list, k: list, me: int, team: int | None) -> bool:
    """¿Ningún aliado mató, murió ni asistió en esa jugada (15 s y 2.500 alrededor de tu muerte)?"""
    t, p = k[0], (k[3], k[4])
    for j in kills:
        if j is k or abs(j[0] - t) > ALONE_WINDOW_S or j[3] is None or geo.dist(p, (j[3], j[4])) > ALONE_NEAR:
            continue
        if any(team_of(x) == team and x != me for x in (j[1], j[2], *(j[5] or []))):
            return False
    return True


def alone_after_tower(g: Game) -> dict:
    """Tus muertes sin ningún aliado en la jugada en los 3 min siguientes a perder vuestra primera torre."""
    if g.buildings is None:
        return {}
    first = min((b[0] for b in g.buildings if b[1] == g.team and b[2] == "tower"), default=None)
    if first is None:
        return {}
    n = sum(
        1 for k in g.kills
        if k[2] == g.me and k[3] is not None and first <= k[0] <= first + TOWER_AFTER_S and _died_alone(g.kills, k, g.me, g.team)
    )
    return {"rm_alone_after_tower": n}


# ------------------------------------------------------------------ C-13
def adc_side_deaths(g: Game) -> dict:
    """Con un ADC sin desplazamientos: tus muertes en top o bot pasado el río después de caer la primera torre."""
    if g.buildings is None or not g.team:
        return {}
    first = min((b[0] for b in g.buildings if b[2] == "tower"), default=None)
    if first is None:
        return {}
    n = sum(
        1 for k in g.kills
        if k[2] == g.me and k[0] >= first and k[3] is not None
        and geo.zone((k[3], k[4])) in ("top", "bot") and geo.side_of((k[3], k[4])) != g.team
    )
    return {"rm_adc_side_deaths": n}
