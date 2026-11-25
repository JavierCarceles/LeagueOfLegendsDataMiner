"""Análisis de una partida propia a partir de match-v5 y su timeline.

No hace falta grabar la repetición: el timeline de Riot trae la posición de
los 10 jugadores al final de cada minuto y todos los eventos con su
coordenada (kills, objetivos, torres, compras, wards, subidas de nivel).
Con eso se reconstruye por dónde se movió cada uno y se detectan errores
concretos, **teniendo en cuenta el contexto**:

* **Muertes** clasificadas: gank (sobre todo del jungla rival), 1 contra 1,
  adelantado en la línea, sin ward en los 2 minutos previos, con oro que
  podías gastar en tu build, justo antes de un objetivo rival, en la jungla
  rival o aislado. Se mira cómo acabó la pelea (si la ganasteis, no es lo
  mismo) y si tu equipo cogió algo a cambio.
* **Oro**: se reconstruye tu inventario. Solo es «oro parado» el que te daba
  para algo de tu build y con hueco para comprarlo. Con el inventario lleno
  ahorrando para un objeto completo, con la build terminada, en pleno
  objetivo o en el empujón final, no es un error.
* **Objetivos**: si el rival coge uno y estabas lejos, se mide qué sacó tu
  equipo a cambio (kills, torres, placas, su jungla, otro objetivo). Un buen
  cambio es una buena decisión; regalarlo sin sacar nada, no.
* **Build**: orden y minutos de tus objetos, compras por vuelta a base,
  inventario lleno de componentes, la R a tiempo, Heridas graves contra
  curanderos y resistencias frente al daño que recibiste.
* **Tiempo**: minutos vacíos y desconexiones (AFK), que no cuentan como errores
  de juego.
* **Pathing**, **visión**, **objetivos** y **fase de líneas** (CS, oro y
  experiencia frente a tu rival a los 10 y 15 minutos).

La función principal es pura (sin BD ni red):
``analyze_game(match, timeline, pid=…, items=ItemCatalog)``. Sin catálogo de
objetos (o con objetos que no conoce, o de otra temporada) no se juzgan el oro
ni la build. Si aún no hay timeline se calcula lo que permite el resumen de la
partida.

Regla general: nunca se marca algo como error (ni como acierto) si los datos no
lo respaldan con claridad. Si no se puede comprobar (rival de línea desconocido,
desconexiones, objetos desconocidos…), se omite el veredicto.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from itertools import permutations

from app.coach import clocks as clk
from app.coach import geometry as geo
from app.coach import navgrid
from app.coach import rule_metrics
from app.coach import typical_paths
from app.coach.items import Inventory, ItemCatalog, season
from app.riot.routing import QUEUES, TEAM_POSITION_TO_ROLE

# 7: macro con los relojes de 2026 (vuelta a base a destiempo, llegar tarde, pelear con uno menos, pink sin usar,
#    barón y peleas ganadas sin convertir, cazado en su mitad, recompensa regalada) y objetivos del otro lado hasta el 20;
#    auditoría: gank solo en tu línea, 1 contra 1 sin muerte mutua, ganks rivales en mid no son «la otra mitad», pinks
#    destruidas asignadas con prudencia, una ganancia no paga dos objetivos, AFK de Riot, objetos que llegan sin compra;
# 6: el resumen guarda cada error (para «Errores que repites»); 5: placas 2026 (120, sin doble conteo), las botas nunca
#    «sobran», intercambios explicados por ambos lados
# 8: calibrado con 704 partidas de junglas Aspirante (OTPs de Hecarim y Lee Sin, noviembre de 2026): una muerte en una
#    pelea perdida por varios no pesa como una muerte suelta antes de un objetivo; al jungla, estar en la jungla rival
#    (su trabajo) no le suma gravedad ni le cambia el título si era una pelea; la R solo se exige a nivel 6 (a 11/16 los
#    Aspirante la retrasan a propósito en el 11 % de partidas); sin aviso de botas al jungla (1 de cada 3 Aspirante
#    no las tiene mejoradas al 20).
# 9: con 240 partidas de OTPs Esmeralda frente a 438 de Aspirante (mismo campeón): los dragones normales cedidos salen
#    igual en los dos elos (10 % y 11 % de partidas), así que bajan a leve (heraldo, larvas y barón sí distinguen: 1,6-2,8
#    veces más en Esmeralda); «inventario lleno de componentes» salía más en Aspirante (6 % frente a 2,5 %): solo si se
#    repite.
# 10: con 1.315 partidas de junglas OTP (560 Esmeralda, 755 Maestro+): morir en una pelea perdida por varios justo
#     antes de un objetivo pasa casi igual en los dos elos (0,48 y 0,40 por partida) y era la mayor parte de las muertes
#     «a mejorar»: pesa 0,3 (leve) en vez de 0,5; morir aislado o en 1 contra 1 sí distingue (2-3 veces más en
#     Esmeralda) y se queda igual. «El jungla rival ganqueó en la otra mitad» salía más en Aspirante: leve.
# 11: duelo en desventaja (reglas de macro/macro_por_rol.txt: «compara nivel, objetos y definitivas antes de pelear»):
#     morir 1 contra 1 con 2 niveles menos, con un legendario menos (y ≥ 1.000 de oro de diferencia) o sin la R contra
#     un rival que ya la tiene, sin ir tú por delante en lo otro. 8 de 25 muertes en duelo en 51 partidas del usuario.
# 12: prioridad de línea (CS de las dos líneas junto al objetivo frente a sus rivales). Con 1.818 partidas de junglas
#     OTP: con mid y bot ≥ +5 CS el equipo coge el dragón el 68 % (Challenger) y el 76 % (Esmeralda); con los dos ≤ −5,
#     el 44 % y el 38 %. Un dragón normal cedido sin prioridad deja de ser error (info, como en inferioridad numérica);
#     la prioridad se explica en los objetivos y en las muertes junto al foso. Jugar del lado de las líneas que ganan
#     NO se juzga: los junglas de todos los elos pasan el 47 % del early arriba sea cual sea el estado de las líneas.
# 13: relojes: el heraldo (y las larvas) que alguien está pegando a su hora de irse aguanta hasta morir. 6 de 1.818
#     partidas con el heraldo muerto entre 19:51 y 19:55 se quedaban sin relojes (sin juzgar la preparación de objetivos).
# 14: posiciones reconstruidas (vuelta a base con su canalización en vez de cruzar el mapa andando, saltos imposibles
#     andando = teletransporte, quien hizo o recibió daño en cada muerte y las kills especiales) y movimiento de los 10
#     jugadores cada 5 s (línea, río, junglas, base; agrupado o solo en una línea lateral pasado el 14).
ANALYSIS_VERSION = 14

EARLY_MS = 14 * 60_000
IDLE_FROM_MS = 15 * 60_000
HOARD_GOLD = 1500  # «mucho oro» (ahorro con el inventario lleno o la build terminada)
USEFUL_SPEND = 1000  # compra mínima de tu build que justifica volver a base
GENERIC_COMPONENT = 1100  # sin saber tu siguiente objeto: lo que cuesta un componente medio
HOARD_GRACE_MS = 45_000  # margen para acabar la oleada o el campamento y volver
FINAL_PUSH_MS = 90_000
WARD_WINDOW_MS = 120_000
OBJECTIVE_WINDOW_MS = 90_000
TRADE_BEFORE_MS = 75_000
TRADE_AFTER_MS = 40_000
FIGHT_GAP_MS = 20_000
FIGHT_RADIUS = 3200
NEAR_OBJECTIVE = 2600
FAR_FROM_OBJECTIVE = 5500
OBJ_FAR_SURE_GAP_S = 20  # tu posición a más de esto de un punto real: «lejos» con margen…
OBJ_FAR_UNSURE_MARGIN = 2000  # …de esto (el error de la reconstrucción crece con el tiempo sin datos)
ISOLATED_RADIUS = 2200
AFK_MIN_FRAMES = 2
SMITE = 11
CONTROL_WARD = 2055
HEAVY_HEAL_MIN = 650  # curación por minuto (propia + aliados) para considerar a un rival «curandero»
VISION_NEAR = 4000  # un ward de un aliado cuenta si lo puso a esta distancia de donde moriste
WARD_LIFE_MS = {"CONTROL_WARD": 360_000, "BLUE_TRINKET": 180_000, "SIGHT_WARD": 150_000}  # el resto, WARD_WINDOW_MS
# Wards de cada equipo con su sitio (donde estaba quien lo puso: el ward se pone a menos de ~600) y su vida
# (``_ward_map``). Riot no da ni la posición del ward ni cuál cae en un WARD_KILL: el que cae es el del tipo y equipo
# rival más cerca de quien lo quita. Trinket amarillo: 90-120 s según el nivel; ward de support: 150 s; pink: hasta que la
# quitan. Cada jugador: 1 pink y 3 wards sigilosos a la vez (el siguiente quita el más antiguo).
STEALTH_WARDS = ("YELLOW_TRINKET", "SIGHT_WARD")
WARD_MAX_ALIVE = {"CONTROL_WARD": 1, "STEALTH": 3}
WARD_KILL_MATCH = 2500  # quien quita un ward estaba a menos de esto de él (si no, no se sabe cuál fue)
TRINKETS = {3340: "ward", 3363: "farsight", 3364: "lens"}
SEEN_BY_WARD = 1100  # visión de un ward (~900) y algo de margen por la posición estimada
SEEN_BEFORE_MS = 15_000  # te vio en los 15 s antes de morir
# La lente se recarga 90-60 s según el nivel: si quitaste un ward en los 90 s anteriores, seguramente estaba en recarga y
# no es «llevabas la lente sin usar» (pasaba en el 28-47 % de esas muertes, 24-11-2026).
LENS_COOLDOWN_MS = 90_000
ENEMY_PINK_LONG_MS = 180_000  # pink rival en tu jungla que vive 3 min o más…
ENEMY_PINK_PASS = 1200  # …y pasaste a su lado (la pink se ve: no hace falta lente)…
ENEMY_PINK_AFTER_MS = 60_000  # …y siguió viva 1 min o más después
ENEMY_PINK_SETTLE_MS = 20_000  # pasar justo cuando la ponen (en plena pelea) no cuenta
OBJ_RELEVANT_RADIUS = 6000  # una muerte influye en un objetivo de su zona del mapa
OBJ_SETUP_MS = 120_000  # colocarse y preparar visión antes de un objetivo no es tiempo vacío
OBJ_SETUP_RADIUS = 3500
SUPPORT_NEAR_ALLY = 1500  # el support acompañando a un aliado está jugando aunque no farmee ni pegue
RESPAWN_BUY_MS = 30_000  # comprar al reaparecer no es «volver a base»
AFK_AFTER_RESPAWN_MS = 75_000  # muerte seguida de quedarse AFK al reaparecer: no se juzga
TRADE_GAIN_BEFORE_MS = 5_000  # lo que tu equipo cogió antes de tu muerte no lo «compró» tu muerte
CROSS_MAP_MS = 60_000
CONTEXT_OK = ("fight_won", "traded", "game_end", "afk")  # muertes que no cuentan como error en las métricas
R_LATE_S = 30
NO_R_CHECK = {43, 60, 76, 77, 126, 910}  # Karma, Elise, Nidalee, Udyr, Jayce, Hwei: su R no sigue la regla 6/11/16
NO_BOOTS = {69, 350}  # Cassiopeia y Yuumi no pueden comprar botas (Yuumi: 261 de 265 partidas sin ellas, 24-11-2026)
BUYS_ANYWHERE = {516}  # Ornn compra en cualquier sitio: sus compras no son vueltas a base
LANE_PHASE_END_MS = 20 * 60_000  # hasta el 20, top, ADC y support no van a los objetivos del otro lado (salvo que cayera su torre exterior)

# Macro con los relojes de cada temporada (``clocks.py``): S = aparece el objetivo, T = muere.
CONTEST_WINDOW_MS = 120_000  # disputado: muere ≤ 2 min después de aparecer…
CONTEST_DEATH_RADIUS = 3000  # …con 2 aliados cerca o un aliado caído junto al foso
RECALL_LATE_FROM_MS = 30_000  # volver a base desde S − 30 s (o en los 75 s previos a T) ya no deja llegar
RECALL_LATE_LAST_MS = 75_000
FORCED_RECALL_HP = 0.5  # con menos vida, volver a base es obligado
FORCED_RECALL_FIGHT_MS = 40_000  # o si acababas de pelear
FORCED_RECALL_TAKEN = 800  # o si recibiste mucho daño en ese minuto
LATE_FAR_BEFORE = 7000  # llegar tarde: un minuto antes de aparecer estabas a más de esto del foso…
OBJ_FIGHT_RADIUS = 3000  # pelear con uno menos «junto al objetivo»
OUTNUMBERED_DEAD_MS = 10_000  # el aliado llevaba al menos esto muerto 10 s antes de la primera kill
PINK_BEFORE_MS = 90_000  # llevar una pink de S − 90 s a S + 30 s sin ponerla, cerca del foso
PINK_AFTER_MS = 30_000
PINK_NEAR = 4000
PINK_ALIVE_MS = 270_000  # una pink tuya puesta poco antes puede seguir viva: no hace falta otra
BARON_WINDOW_MS = 180_000
WON_FIGHT_OBJECTIVE = 7000  # pelea ganada: objetivo vivo a esta distancia…
WON_FIGHT_TOWER = 5000  # …o una torre rival atacable
WON_FIGHT_RESPAWN_MARGIN = 15_000
WON_FIGHT_MIN_WINDOW = 30_000  # ventaja numérica mínima (hasta que reaparece el 2.º rival) para exigir convertirla
COLLAPSED_RADIUS = 3000
GANK_LANE_RADIUS = 2500  # morir en el río junto a tu línea también es un gank
FAR_GANK = 5000  # ganks del jungla rival «en la otra mitad»: además, a más de esta distancia de ti
BOUNTY_GIFT = 500  # recompensa (shutdown) que regalas muriendo solo; Riot la limita a 700
DUEL_LEVEL_GAP = 2  # duelo en desventaja: el rival te sacaba al menos 2 niveles…
DUEL_GOLD_GAP = 1000  # …o un legendario más y al menos este oro total de diferencia (no un objeto recién cerrado)
ULT_LEVEL = 6
DUEL_BEFORE_MS = 3_000  # nivel y objetos de cada uno al empezar el duelo, no tras tu muerte
PRIO_CS = 5  # prioridad: las dos líneas junto al objetivo con al menos esta ventaja (o desventaja) de CS
PRIO_LANES = {"dragon": ("mid", "bot"), "elder": ("mid", "bot"), "baron": ("top", "mid"), "herald": ("top", "mid"), "grubs": ("top", "mid")}
LANE_ROLE = {"top": "TOP", "mid": "MID", "bot": "ADC"}
LANE_NAME = {"top": "top", "mid": "mid", "bot": "ADC"}

# Tiempo base de reaparición por nivel (s) y factor por minuto de partida.
BRW = [10, 10, 12, 12, 14, 16, 20, 25, 28, 32.5, 35, 37.5, 40, 42.5, 45, 47.5, 50, 52.5]

MONSTERS = {"DRAGON": "dragon", "BARON_NASHOR": "baron", "RIFTHERALD": "herald", "HORDE": "grubs", "ATAKHAN": "atakhan"}
OBJ_LABEL = {
    "dragon": "el dragón",
    "elder": "el dragón ancestral",
    "baron": "el barón",
    "herald": "el heraldo",
    "grubs": "las larvas",
    "atakhan": "Atakhan",
}
SOUL_LABEL = "el alma"  # así empieza la etiqueta del alma del dragón (``_obj_label``)
# Valor aproximado en oro de equipo (para comparar cambios de objetivos).
OBJ_VALUE = {"dragon": 1000, "elder": 3000, "baron": 3000, "herald": 900, "grubs": 250, "atakhan": 1500}
# Desde la temporada 2026 (parche 16.1) las placas no caen al 14: hay placas toda la partida, también en las
# torres interiores y de inhibidor (que reparten su oro placa a placa), y cada una da 120 de oro. Una torre
# destruida ya incluye el valor de sus placas: si caen en la misma ventana, no se suman dos veces.
BUILDING_VALUE = {"tower": 650, "inhibitor": 900, "plate": 120}
SAME_TURRET = 600  # las placas se registran en la posición de su torre
ENEMY_JUNGLE_CS_VALUE = 25
GOOD_TRADE = 0.85
FAIR_TRADE = 0.45
ROLE_LABEL = {"TOP": "top", "JUNGLE": "jungla", "MID": "mid", "ADC": "ADC", "SUPPORT": "support"}
ROLE_SIDE = {"TOP": "top", "ADC": "bot", "SUPPORT": "bot"}  # lado del mapa de cada línea en la fase de líneas
# Claves de ``damageStats`` en los frames: daño a campeones, daño total (súbditos, monstruos, estructuras) y recibido.
DAMAGE_KEYS = {"dmg": "totalDamageDoneToChampions", "dmg_all": "totalDamageDone", "taken": "totalDamageTaken"}
SKILL_KEYS = {1: "Q", 2: "W", 3: "E", 4: "R"}


class NotInMatch(ValueError):
    """El jugador del perfil no aparece en la partida."""


def time_impact(minutes: float) -> float:
    """Aumento del tiempo de muerte según el minuto (0 % antes del 15, tope 50 %)."""
    if minutes < 15:
        return 0.0
    if minutes <= 30:
        return 0.00425 * (minutes - 15) * 2
    if minutes <= 45:
        return 0.1275 + 0.003 * (minutes - 30) * 2
    return min(0.5, 0.2175 + 0.0145 * (minutes - 45) * 2)


def respawn_seconds(level: int, minutes: float) -> float:
    return BRW[max(0, min(17, level - 1))] * (1 + time_impact(minutes))


def duration_seconds(info: dict) -> int:
    duration = _num(info.get("gameDuration"))
    if duration <= 0:  # sin gameDuration: por las marcas de inicio y fin
        start, end = _num(info.get("gameStartTimestamp")), _num(info.get("gameEndTimestamp"))
        return (end - start) // 1000 if start and end > start else 0
    if "gameEndTimestamp" not in info and duration > 10_000:  # partidas anteriores a 11.20 (ms)
        duration //= 1000
    return duration


def player_role(p: dict) -> str | None:
    """Rol interno (TOP, JUNGLE, MID, ADC, SUPPORT) según ``teamPosition``."""
    return TEAM_POSITION_TO_ROLE.get((p.get("teamPosition") or "").upper()) or TEAM_POSITION_TO_ROLE.get(
        (p.get("individualPosition") or "").upper()
    )


def _pos(obj: dict | None) -> tuple[int, int] | None:
    if not isinstance(obj, dict):
        return None
    p = obj.get("position")
    if not isinstance(p, dict):
        return None
    try:
        return int(float(p["x"])), int(float(p["y"]))
    except (KeyError, TypeError, ValueError, OverflowError):  # sin coordenadas o con null
        return None


def _mmss(ms: float) -> str:
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


def _secs(ms: float) -> str:
    s = round(ms / 1000)
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60:02d} s"


def _int(value: float) -> str:
    return f"{round(value):,}".replace(",", ".")


def _num(value, default=0):
    try:
        return type(default)(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    end = "" if text.endswith(".") else "."
    return text[:1].upper() + text[1:] + end


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " y " + parts[-1]


def find_me(match: dict, *, pid: int | None = None, puuid: str | None = None, game_name: str | None = None, tag_line: str | None = None) -> dict:
    parts = (match.get("info") or {}).get("participants") or []
    if pid:
        found = next((p for p in parts if _num(p.get("participantId")) == pid), None)
        if found:
            return found
    if puuid:
        found = next((p for p in parts if p.get("puuid") == puuid), None)
        if found:
            return found
    if game_name:
        found = _by_riot_id(parts, game_name, tag_line)
        if found:
            return found
    raise NotInMatch("El jugador no aparece en esta partida")


def _by_riot_id(parts: list[dict], game_name: str, tag_line: str | None) -> dict | None:
    """Participante con ese Riot ID (sin etiqueta, vale cualquiera); ``None`` si no está."""
    name, tag = game_name.casefold(), (tag_line or "").casefold()
    return next(
        (p for p in parts if (p.get("riotIdGameName") or "").casefold() == name and (not tag or (p.get("riotIdTagline") or "").casefold() == tag)),
        None,
    )


def _player_entry(pid: int, p: dict, role: str | None, team: int) -> dict:
    """Datos de un jugador para el análisis: equipo, campeón, rol, Riot ID y si lleva Castigo."""
    return {
        "pid": pid,
        "team": team,
        "champ": _num(p.get("championId")),
        "role": role,
        "name": p.get("riotIdGameName") or p.get("summonerName") or "",
        "tag": p.get("riotIdTagline") or "",
        "smite": SMITE in (_num(p.get("summoner1Id")), _num(p.get("summoner2Id"))),
    }


def _monster_kind(e: dict) -> str | None:
    """Objetivo de un ELITE_MONSTER_KILL («elder» aparte de los dragones); ``None`` si no es uno de los que se miran."""
    monster = MONSTERS.get((e.get("monsterType") or "").upper())
    if monster == "dragon" and (e.get("monsterSubType") or "").upper() == "ELDER_DRAGON":
        return "elder"
    return monster


def _building_kind(e: dict) -> str:
    """Placa, torre o inhibidor según el evento de estructura."""
    if e.get("type") == "TURRET_PLATE_DESTROYED":
        return "plate"
    return "tower" if e.get("buildingType") == "TOWER_BUILDING" else "inhibitor"


class _Game:
    """Datos de la partida indexados para consultas rápidas."""

    def __init__(
        self, match: dict, timeline: dict | None, me: dict, catalog: ItemCatalog | None = None, *, route_all: bool = True,
        shared: dict | None = None,
    ) -> None:
        # Rutas que rodean muros de los 10 o solo del jugador analizado (las partidas de otros jugadores que guarda el
        # recolector solo usan su recorrido: rodear los muros de los 10 era la mayor parte del tiempo de análisis).
        # ``shared``: lo que no depende de quién se analiza (los recorridos de los 10, rodeando muros todos, y dónde
        # pasa el tiempo cada uno), calculado una vez para los 10 análisis de una partida: así el recorrido de cada
        # jugador es el mismo desde los 10 puntos de vista (antes solo el del analizado rodeaba muros) y no se repite.
        self._shared = shared
        self.route_all = route_all or shared is not None
        info = match.get("info") or {}
        self.info = info
        self.duration = duration_seconds(info)
        self.duration_ms = self.duration * 1000
        self.minutes = max(self.duration, 60) / 60
        me_pid = self._load_players(info, me)
        self.me = self.players[me_pid]
        self.team = self.me["team"]
        self.role = self.me["role"]
        self.won = bool(me.get("win"))
        self.allies = [p for p in self.players if self.players[p]["team"] == self.team and p != self.me["pid"]]
        self.enemies = [p for p in self.players if self.players[p]["team"] != self.team]
        self.opponent = self._by_role(300 - self.team, self.role) if self.role else None
        self.enemy_jungler = self._jungler(300 - self.team)
        self.ally_jungler = self._jungler(self.team)

        self._load_frames(timeline)
        self.catalog = catalog if catalog else None
        self._index_events()
        self._detect_afk()
        # Relojes de los objetivos (cuándo apareció cada uno). Si no cuadran con la partida, nada de lo que
        # depende de ellos se juzga (``clocks_ok`` False).
        self.clocks = clk.clocks_for(info) if self.has_timeline else None
        self.clocks_ok = clk.assign_spawns(self.objectives, self.clocks)
        self.flagged: set[int] = set()  # objetivos que ``_objectives`` ya señaló (no repetir el mismo momento)
        self.used_gains: set[int] = set()  # kills, estructuras y objetivos que ya compensaron un objetivo rival
        self.inv = Inventory(self.catalog, self.item_events[me_pid]) if self.catalog and self.has_timeline else None
        # Oro y build solo se juzgan si el inventario se puede reconstruir con fiabilidad: catálogo de la
        # misma temporada que la partida y ningún objeto desconocido (si no, los huecos y precios no cuadran).
        self.items_reliable = self.inv is not None and not self.inv.unknown and _same_season(self.catalog, info)
        self.gold_rows: dict[int, dict] = {}
        self.hoard_frames: set[int] = set()
        self._invs: dict[int, Inventory | None] = {me_pid: self.inv}

    def _load_players(self, info: dict, me: dict) -> int:
        """Los 10 jugadores (``raw`` y ``players``, sin rol si se repite en su equipo); devuelve el ``pid`` de ``me``."""
        self.raw = {}
        self.players: dict[int, dict] = {}
        roles_seen: Counter = Counter()
        me_pid = None
        for index, p in enumerate(info.get("participants") or [], start=1):
            pid = _num(p.get("participantId"), index) or index
            role = player_role(p)
            team = _num(p.get("teamId"))
            if p is me:
                me_pid = pid
            self.raw[pid] = p
            self.players[pid] = _player_entry(pid, p, role, team)
            if role:
                roles_seen[(team, role)] += 1
        for pl in self.players.values():  # rol duplicado en un equipo: no es fiable
            if pl["role"] and roles_seen[(pl["team"], pl["role"])] > 1:
                pl["role"] = None
        if me_pid is None:
            raise NotInMatch("El jugador no aparece en esta partida")
        return me_pid

    def _load_frames(self, timeline: dict | None) -> None:
        """Frames del timeline: sus tiempos, el frame de cada jugador y todos los eventos ordenados por tiempo."""
        tl_info = (timeline or {}).get("info") or {}
        frames = [f for f in (tl_info.get("frames") or []) if isinstance(f, dict)]
        if self.team not in geo.FOUNTAIN:  # equipo desconocido: no se puede situar nada en el mapa
            frames = []
        self.has_timeline = bool(frames)
        self.ts: list[int] = [_num(f.get("timestamp")) for f in frames]
        if not self.duration and self.ts:  # sin duración en el resumen: la del timeline
            self.duration = max(self.ts) // 1000
            self.duration_ms = self.duration * 1000
            self.minutes = max(self.duration, 60) / 60
        self.pf: dict[int, list[dict | None]] = {pid: [] for pid in self.players}
        for f in frames:
            pfs = f.get("participantFrames") or {}
            for pid in self.players:
                self.pf[pid].append(pfs.get(str(pid)))
        events = [e for f in frames for e in (f.get("events") or []) if isinstance(e, dict)]
        events.sort(key=lambda e: _num(e.get("timestamp")))
        self.events = events

    def inventory(self, pid: int) -> Inventory | None:
        """Inventario reconstruido de cualquier jugador (``None`` sin catálogo o con objetos que no conoce)."""
        if pid not in self._invs:
            ok = self.catalog and self.has_timeline and _same_season(self.catalog, self.info)
            self._invs[pid] = Inventory(self.catalog, self.item_events.get(pid) or []) if ok else None
        inv = self._invs[pid]
        return inv if inv is not None and not inv.unknown else None

    def legendaries_at(self, pid: int, t: int) -> int | None:
        inv = self.inventory(pid)
        if inv is None:
            return None
        return sum(1 for i in inv.completed(inv.at(t)) if self.catalog.is_legendary(i))

    # ----------------------------------------------------------- jugadores
    def _by_role(self, team: int, role: str | None) -> int | None:
        return next((pid for pid, p in self.players.items() if p["team"] == team and p["role"] == role), None)

    def _jungler(self, team: int) -> int | None:
        pid = self._by_role(team, "JUNGLE")
        if pid:
            return pid
        return next((pid for pid, p in self.players.items() if p["team"] == team and p["smite"]), None)

    def natural_opponents(self) -> set[int] | None:
        """Rivales con los que es normal pelear en la fase de líneas (``None`` si no se sabe quiénes son)."""
        if not self.role:
            return None
        if self.role in ("ADC", "SUPPORT"):
            pair = (self._by_role(300 - self.team, "ADC"), self._by_role(300 - self.team, "SUPPORT"))
            return set(pair) if all(pair) else None
        return {self.opponent} if self.opponent else None

    # ------------------------------------------------------------- eventos
    def _index_events(self) -> None:
        self.levels: dict[int, list[tuple[int, int]]] = {pid: [] for pid in self.players}
        self.kills: list[dict] = []
        self.special_kills: list[tuple[int, int, tuple[int, int]]] = []
        self.objectives: list[dict] = []
        self.buildings: list[dict] = []
        self.wards: dict[int, list[int]] = {pid: [] for pid in self.players}
        self.ward_events: dict[int, list[tuple[int, str]]] = {pid: [] for pid in self.players}  # (t, tipo)
        self.control_wards_placed: Counter = Counter()
        self.wards_killed: Counter = Counter()
        self.ward_kills: dict[int, list[int]] = {pid: [] for pid in self.players}  # limpiar visión también es jugar
        self.ward_kill_events: list[tuple[int, int, str]] = []  # (t, quién, tipo): para saber qué ward cayó
        self.pinks_lost: dict[int, list[int]] = {100: [], 200: []}  # pinks de cada equipo destruidas por el rival
        self.purchases: dict[int, list[tuple[int, int]]] = {pid: [] for pid in self.players}
        self.item_events: dict[int, list[dict]] = {pid: [] for pid in self.players}
        self.skill_ups: dict[int, list[tuple[int, int, str]]] = {pid: [] for pid in self.players}
        handlers = {
            "LEVEL_UP": self._on_level_up,
            "SKILL_LEVEL_UP": self._on_skill_up,
            "CHAMPION_KILL": self._on_kill,
            "CHAMPION_SPECIAL_KILL": self._on_special_kill,
            "ELITE_MONSTER_KILL": self._on_monster,
            "BUILDING_KILL": self._on_building,
            "TURRET_PLATE_DESTROYED": self._on_building,
            "WARD_PLACED": self._on_ward_placed,
            "WARD_KILL": self._on_ward_kill,
            "ITEM_PURCHASED": self._on_item,
            "ITEM_SOLD": self._on_item,
            "ITEM_UNDO": self._on_item,
            "ITEM_DESTROYED": self._on_item,
        }
        for e in self.events:
            kind = e.get("type")
            handler = handlers.get(kind) if isinstance(kind, str) else None
            if handler is not None:
                handler(e, _num(e.get("timestamp")))
        self._deaths()
        self._tracks()

    def _on_level_up(self, e: dict, t: int) -> None:
        """LEVEL_UP: nivel de cada jugador con su momento."""
        pid = _num(e.get("participantId"))
        if pid in self.levels:
            self.levels[pid].append((t, _num(e.get("level"), 1)))

    def _on_skill_up(self, e: dict, t: int) -> None:
        """SKILL_LEVEL_UP: habilidad que sube cada jugador (y si es normal o una evolución)."""
        pid = _num(e.get("participantId"))
        if pid in self.skill_ups:
            self.skill_ups[pid].append((t, _num(e.get("skillSlot")), e.get("levelUpType") or "NORMAL"))

    def _on_kill(self, e: dict, t: int) -> None:
        """CHAMPION_KILL: la kill con su posición, quién mató, asistió o hizo daño, y lo que valía."""
        victim = _num(e.get("victimId"))
        if victim not in self.players:
            return
        bounty = _num(e.get("bounty")) + _num(e.get("shutdownBounty"))
        # Quien le hizo daño (o recibió el suyo) en sus últimos segundos estaba a distancia de combate, aunque no
        # figure como asistencia: un punto más para reconstruir dónde estaba cada uno.
        damagers = {
            _num(d.get("participantId")) for d in (e.get("victimDamageReceived") or []) + (e.get("victimDamageDealt") or [])
            if (d.get("type") or "OTHER") == "OTHER" and _num(d.get("participantId")) in self.players
        }
        self.kills.append(
            {
                "t": t,
                "killer": _num(e.get("killerId")),
                "victim": victim,
                "damagers": damagers - {victim},
                "assists": [_num(a) for a in e.get("assistingParticipantIds") or []],
                "pos": _pos(e),
                "value": max(300, min(1000, bounty)) if bounty else 300,
                "shutdown": _num(e.get("shutdownBounty")),  # recompensa extra por la racha de la víctima
            }
        )

    def _on_special_kill(self, e: dict, t: int) -> None:
        """CHAMPION_SPECIAL_KILL: otra posición exacta de quien mató."""
        killer = _num(e.get("killerId"))
        if killer in self.players and _pos(e):
            self.special_kills.append((t, killer, _pos(e)))

    def _on_monster(self, e: dict, t: int) -> None:
        """ELITE_MONSTER_KILL: objetivo con su equipo, su sitio y quién lo cogió (las larvas seguidas, juntas)."""
        monster = _monster_kind(e)
        if not monster:
            return
        killer = _num(e.get("killerId"))
        team = _num(e.get("killerTeamId")) or (self.players[killer]["team"] if killer in self.players else 0)
        if team not in (100, 200):
            return
        assists = [_num(a) for a in e.get("assistingParticipantIds") or []]
        if monster == "grubs" and self._merge_grubs(team, t, [killer, *assists]):
            return
        self.objectives.append(
            {
                "t": t,
                "t_end": t,
                "kind": monster,
                "team": team,
                "pos": _pos(e) or (geo.PITS["baron"] if monster in ("baron", "herald", "grubs") else geo.PITS["dragon"]),
                "takers": {killer, *assists},
                "sub": e.get("monsterSubType"),
                "assists_known": "assistingParticipantIds" in e,
                "count": 1,
            }
        )

    def _merge_grubs(self, team: int, t: int, takers: list[int]) -> bool:
        """Larvas del mismo equipo ≤ 2 min después de las anteriores: se suman a ese objetivo (``True``) en vez de otro."""
        last = self.objectives[-1] if self.objectives else None
        if last and last["kind"] == "grubs" and last["team"] == team and t - last["t_end"] <= 120_000:
            last["t_end"] = t
            last["count"] += 1
            last["takers"].update(takers)
            return True
        return False

    def _on_building(self, e: dict, t: int) -> None:
        """BUILDING_KILL y TURRET_PLATE_DESTROYED: torre, inhibidor o placa con su dueño, línea y quién la tiró."""
        owner = _num(e.get("teamId"))
        self.buildings.append(
            {
                "t": t,
                "owner": owner,
                "kind": _building_kind(e),
                "lane": (e.get("laneType") or "").replace("_LANE", "").lower(),
                "tower_type": e.get("towerType"),
                "takers": {_num(e.get("killerId")), *[_num(a) for a in e.get("assistingParticipantIds") or []]},
                "pos": _pos(e),
            }
        )

    def _on_ward_placed(self, e: dict, t: int) -> None:
        """WARD_PLACED: wards de cada jugador con su tipo (sin setas de Teemo ni tipos desconocidos)."""
        creator = _num(e.get("creatorId"))
        ward_type = (e.get("wardType") or "").upper()
        if creator in self.wards and ward_type not in ("UNDEFINED", "TEEMO_MUSHROOM", ""):
            self.wards[creator].append(t)
            self.ward_events[creator].append((t, ward_type))
            if ward_type == "CONTROL_WARD":
                self.control_wards_placed[creator] += 1

    def _on_ward_kill(self, e: dict, t: int) -> None:
        """WARD_KILL: wards que quita cada jugador (y las pinks que pierde cada equipo)."""
        killer = _num(e.get("killerId"))
        if killer in self.players:
            self.wards_killed[killer] += 1
            self.ward_kills[killer].append(t)
            self.ward_kill_events.append((t, killer, (e.get("wardType") or "").upper()))
            victim_team = 300 - self.players[killer]["team"]
            if (e.get("wardType") or "").upper() == "CONTROL_WARD" and victim_team in self.pinks_lost:
                self.pinks_lost[victim_team].append(t)

    def _on_item(self, e: dict, t: int) -> None:
        """ITEM_*: eventos de objetos de cada jugador (para su inventario) y sus compras."""
        pid = _num(e.get("participantId"))
        if pid in self.item_events:
            self.item_events[pid].append(e)
            if e.get("type") == "ITEM_PURCHASED":
                self.purchases[pid].append((t, _num(e.get("itemId"))))

    def level_at(self, pid: int, t: int) -> int:
        level = 1
        for ts, lvl in self.levels.get(pid) or []:
            if ts > t:
                break
            level = lvl
        if level == 1 and not self.levels.get(pid):  # sin eventos LEVEL_UP: el del frame anterior
            i = self.frame_before(t)
            if i is not None:
                level = _num((self.pf[pid][i] or {}).get("level"), 1)
        return level

    def _deaths(self) -> None:
        estimates: dict[int, list[list]] = {pid: [] for pid in self.players}
        for k in self.kills:
            seconds = respawn_seconds(self.level_at(k["victim"], k["t"]), k["t"] / 60_000)
            estimates[k["victim"]].append([k["t"], seconds, k["pos"]])
        self.dead: dict[int, list[tuple[int, int, tuple | None]]] = {}
        for pid, items in estimates.items():
            total = _num(self.raw[pid].get("totalTimeSpentDead"))
            est = sum(i[1] for i in items)
            factor = max(0.5, min(1.8, total / est)) if total and est else 1.0
            intervals = []
            for t, seconds, pos in items:
                end = min(t + int(seconds * factor * 1000), self.duration_ms or t + int(seconds * 1000))
                intervals.append((t, max(end, t + 1000), pos))
            self.dead[pid] = intervals

    def is_dead(self, pid: int, t: int) -> bool:
        return any(start <= t < end for start, end, _ in self.dead.get(pid) or [])

    def dead_overlap(self, pid: int, t0: int, t1: int) -> int:
        return sum(max(0, min(end, t1) - max(start, t0)) for start, end, _ in self.dead.get(pid) or [])

    def _detect_afk(self) -> None:
        """Minutos vivo sin moverse ni ganar experiencia ni hacer nada: desconexión o AFK."""
        self.afk: dict[int, list[tuple[int, int]]] = {pid: [] for pid in self.players}
        for pid in self.players:
            own = sorted([t for t, _ in self.purchases[pid]] + self.wards[pid] + [t for t, *_ in self.skill_ups[pid]])
            still = [i for i in range(1, len(self.ts)) if self._still(pid, i, own)]
            for run in _runs(still):
                if len(run) < AFK_MIN_FRAMES:
                    continue
                end = self.ts[run[-1]]
                if run[-1] >= len(self.ts) - 2:  # quieto hasta el final
                    end = max(end, self.duration_ms)
                self.afk[pid].append((self.ts[run[0] - 1], end))

    def _still(self, pid: int, i: int, own: list[int]) -> bool:
        """¿Pasó ``pid`` el minuto que acaba en el frame ``i`` vivo, sin moverse, sin experiencia y sin eventos propios?"""
        t0, t1 = self.ts[i - 1], self.ts[i]
        if t1 - t0 < 30_000:  # último frame (fin de partida)
            return False
        a, b = self.frame_pos(pid, i - 1), self.frame_pos(pid, i)
        xp0, xp1 = self.fval(pid, i - 1, "xp"), self.fval(pid, i, "xp")
        if not a or not b or xp0 is None or xp1 is None:
            return False
        if geo.dist(a, b) > 60 or xp1 - xp0 > 20 or self.dead_overlap(pid, t0, t1) > 0:
            return False
        j = bisect.bisect_right(own, t0)
        return not (j < len(own) and own[j] <= t1)

    def is_afk(self, pid: int, t: int) -> bool:
        return any(start <= t < end for start, end in self.afk.get(pid) or [])

    def afk_ms(self, pid: int) -> int:
        return sum(end - start for start, end in self.afk.get(pid) or [])

    def present(self, pid: int, t: int) -> bool:
        return not self.is_dead(pid, t) and not self.is_afk(pid, t)

    def respawn_buy(self, pid: int, t: int) -> bool:
        """Compra al reaparecer (con margen por la estimación del tiempo de muerte): no es volver a base."""
        return any(end - 5_000 <= t <= end + RESPAWN_BUY_MS for _, end, _ in self.dead.get(pid) or [])

    def buys_in_base(self, pid: int) -> bool:
        """¿Sus compras le sitúan en la base? (Ornn compra en cualquier parte del mapa)."""
        return self.players[pid]["champ"] not in BUYS_ANYWHERE

    def recalls(self, pid: int, t0: int, t1: int) -> list[int]:
        """Vueltas a base entre ``t0`` y ``t1``: compras estando vivo que no son las de al reaparecer."""
        if not self.buys_in_base(pid):
            return []
        return [t for t, _ in self.purchases[pid] if t0 <= t <= t1 and not self.is_dead(pid, t) and not self.respawn_buy(pid, t)]

    def health_ratio(self, pid: int, i: int | None) -> float | None:
        """Vida actual / máxima en el frame ``i`` (``None`` si el frame no la trae)."""
        if i is None or pid not in self.pf or i >= len(self.pf[pid]):
            return None
        stats = (self.pf[pid][i] or {}).get("championStats") or {}
        hp, top = stats.get("health"), stats.get("healthMax")
        try:
            return max(0.0, min(1.0, float(hp) / float(top))) if hp is not None and top else None
        except (TypeError, ValueError):
            return None

    def _tracks(self) -> None:
        """Puntos de paso de cada jugador: frames + eventos con posición + tienda y reapariciones."""
        shared = self._shared
        if shared is not None and "anchors" in shared:  # ya reconstruidos para otro de los 10 análisis de la partida
            self.raw_anchors, self.anchors, self._anchor_t = shared["raw_anchors"], shared["anchors"], shared["anchor_t"]
            self.camp_misses, self.camp_minutes = shared["camp_misses"], shared["camp_minutes"]
            return
        anchors: dict[int, list[tuple[int, int, int, int]]] = {pid: [] for pid in self.players}
        for pid in self.players:
            self._own_anchors(anchors, pid)
        self._kill_anchors(anchors)
        self._event_anchors(anchors)
        for pid, camps in infer_camps(self, anchors).items():
            anchors[pid].extend(camps)
        for pid, spots in infer_lane_farm(self, anchors).items():
            anchors[pid].extend(spots)
        for pid, spots in infer_fights(self, anchors).items():
            anchors[pid].extend(spots)
        for pid, spots in infer_ward_kills(self, anchors).items():
            anchors[pid].extend(spots)
        for pid, spots in infer_typical(self, anchors).items():
            anchors[pid].extend(spots)
        self.raw_anchors = {pid: sorted(a) for pid, a in anchors.items()}  # sin corregir (para validar la reconstrucción)
        # Corregidos (vueltas a base, teletransportes) y andando por donde se puede: rodeando los muros salvo los
        # campeones que los saltan (``navgrid``). Todo lo que usa posiciones (roams, contraganks, zonas) va por ahí.
        me_pid = self.me["pid"]
        self.anchors = {}
        for pid, items in self.raw_anchors.items():
            fixed = _reconstruct(items, geo.FOUNTAIN.get(self.players[pid]["team"]))
            self.anchors[pid] = _walk(fixed, self.players[pid].get("champ")) if self.route_all or pid == me_pid else fixed
        self._anchor_t = {pid: [a[0] for a in items] for pid, items in self.anchors.items()}
        if shared is not None:
            shared.update(
                raw_anchors=self.raw_anchors, anchors=self.anchors, anchor_t=self._anchor_t,
                camp_misses=getattr(self, "camp_misses", 0), camp_minutes=getattr(self, "camp_minutes", 0),
            )

    def _own_anchors(self, anchors: dict[int, list], pid: int) -> None:
        """Puntos de paso propios de ``pid``: sus frames, sus compras (en la fuente), sus muertes y sus reapariciones."""
        fountain = geo.FOUNTAIN.get(self.players[pid]["team"])  # None con un equipo raro: sin anclas de base
        for i, t in enumerate(self.ts):
            p = _pos(self.pf[pid][i])
            if p and not self.is_dead(pid, t):
                _add_anchor(anchors, pid, t, p, 0)
        for t, _ in self.purchases[pid] if self.buys_in_base(pid) else []:
            if not self.is_dead(pid, t):
                _add_anchor(anchors, pid, t, fountain, 2)
        for start, end, pos in self.dead.get(pid) or []:
            _add_anchor(anchors, pid, start, pos, 3)
            if end < self.duration_ms:
                _add_anchor(anchors, pid, end, fountain, 2)

    def _kill_anchors(self, anchors: dict[int, list]) -> None:
        """Puntos de paso de las kills: quien mató o asistió y quien hizo o recibió daño (a distancia de combate)."""
        for k in self.kills:
            self._taker_anchors(anchors, k["t"], (k["killer"], *k["assists"]), k["pos"])
            for pid in k.get("damagers") or ():
                if pid not in (k["killer"], *k["assists"]) and not self.is_dead(pid, k["t"]):
                    _add_anchor(anchors, pid, k["t"], k["pos"], 5)

    def _event_anchors(self, anchors: dict[int, list]) -> None:
        """Puntos de paso de las kills especiales, los objetivos (si se sabe quién participó) y las estructuras."""
        for t, pid, pos in self.special_kills:
            if not self.is_dead(pid, t):
                _add_anchor(anchors, pid, t, pos, 1)
        for o in self.objectives:
            if o["assists_known"]:
                self._taker_anchors(anchors, o["t"], o["takers"], o["pos"])
        for b in self.buildings:
            self._taker_anchors(anchors, b["t"], b["takers"], b["pos"])

    def _taker_anchors(self, anchors: dict[int, list], t: int, takers, pos) -> None:
        """Puntos de paso de quienes participaron en un evento (si son jugadores de la partida y estaban vivos)."""
        for pid in takers:
            if pid in self.players and not self.is_dead(pid, t):
                _add_anchor(anchors, pid, t, pos, 1)

    def anchor_gap(self, pid: int | None, t: int) -> float | None:
        """Segundos de ``t`` al punto real más cercano de ``pid`` (frame, evento propio, fuente o muerte): cuánto se fía
        uno de su posición en ese momento. Lo guarda cada detección (``ev``) para la auditoría de la calibración."""
        if pid is None:
            return None
        real = getattr(self, "_real_t", None)
        if real is None:
            real = self._real_t = {
                p: sorted(a[0] for a in items if a[3] in EXACT_ANCHOR_KINDS) for p, items in (self.raw_anchors or {}).items()
            }
        times = real.get(pid) or []
        if not times:
            return None
        i = bisect.bisect_left(times, t)
        return round(min(abs(t - times[j]) for j in (i - 1, i) if 0 <= j < len(times)) / 1000, 1)

    def real_around(self, pid: int, t: int) -> list[tuple[int, int]]:
        """Las posiciones reales (de Riot) justo antes y justo después de ``t``."""
        items = [a for a in (self.raw_anchors or {}).get(pid) or [] if a[3] in EXACT_ANCHOR_KINDS]
        before = [a for a in items if a[0] <= t]
        after = [a for a in items if a[0] > t]
        return [(a[1], a[2]) for a in ([before[-1]] if before else []) + ([after[0]] if after else [])]

    def pos_at(self, pid: int, t: int) -> tuple[float, float] | None:
        """Posición estimada (interpolada) o ``None`` si estaba muerto o no se sabe."""
        if self.is_dead(pid, t):
            return None
        return interpolate(self.anchors.get(pid) or [], t, self._anchor_t[pid])

    # -------------------------------------------------------------- frames
    def frame_before(self, t: int) -> int | None:
        i = bisect.bisect_right(self.ts, t) - 1
        return i if i >= 0 else None

    def frame_at_minute(self, minute: int) -> int | None:
        target = minute * 60_000
        for i, t in enumerate(self.ts):
            if abs(t - target) <= 30_000:
                return i
        return None

    def fval(self, pid: int | None, i: int | None, key: str):
        if pid is None or i is None or pid not in self.pf or i >= len(self.pf[pid]):
            return None
        frame = self.pf[pid][i] or {}
        if key == "cs":
            if "minionsKilled" not in frame and "jungleMinionsKilled" not in frame:
                return None
            return _num(frame.get("minionsKilled")) + _num(frame.get("jungleMinionsKilled"))
        if key in DAMAGE_KEYS:
            stats = frame.get("damageStats") or {}
            if key != "dmg" and DAMAGE_KEYS[key] not in stats:
                return None
            return _num(stats.get(DAMAGE_KEYS[key]))
        value = frame.get(key)
        return _num(value) if value is not None else None

    def frame_pos(self, pid: int, i: int) -> tuple[int, int] | None:
        return _pos(self.pf[pid][i]) if pid in self.pf and i < len(self.pf[pid]) else None

    def enemy_jungle_cs(self, pid: int, t0: int, t1: int) -> int:
        """CS de jungla que ``pid`` hizo en la jungla rival entre ``t0`` y ``t1`` (por frames)."""
        enemy = 300 - self.players[pid]["team"]
        total = 0
        for i in range(1, len(self.ts)):
            if self.ts[i] <= t0 or self.ts[i - 1] >= t1:
                continue
            a, b = self.fval(pid, i - 1, "jungleMinionsKilled"), self.fval(pid, i, "jungleMinionsKilled")
            if a is None or b is None or b <= a:
                continue
            spots = [p for p in (self.frame_pos(pid, i - 1), self.frame_pos(pid, i)) if p]
            if any(geo.is_jungle_of(geo.zone(p), enemy) for p in spots):
                total += b - a
        return total


# ====================================================================== análisis
# Puntos de paso: 0 frame (exacto, cada minuto) · 1 evento propio (kill, objetivo, torre) · 2 en la fuente (compra o
# reaparición) · 3 muerte · 4 «se queda aquí y salta» (vuelta a base o teletransporte) · 5 a distancia de combate de una
# muerte (hizo o recibió daño; aproximado) · 6 campamento de jungla · 7 ruta rodeando muros · 8 farmeando en una línea ·
# 9 pelea sin muerte (deducida del daño a campeones del minuto) · 10 rompiendo un ward rival · 11 recorrido típico (en
# mitad de un tramo, apartado como suele la gente: ``typical_paths``).
ANCHOR_BASE, ANCHOR_DEATH, ANCHOR_HOLD, ANCHOR_NEAR = 2, 3, 4, 5
EXACT_ANCHOR_KINDS = (0, 1, ANCHOR_BASE, ANCHOR_DEATH)  # posiciones que da Riot (el resto se deducen)
RECALL_MS = 8_000 + 1_000  # canalización de la vuelta a base y un segundo hasta comprar
MAX_SPEED = 0.65  # unidades por ms: ni con fantasmal y arrastres se va más rápido de media; más, teletransporte
NEAR_BASE = 1500
# Validado con las partidas del usuario (``track_check``: se quita cada posición real de un evento y se reconstruye con
# el resto; 8.831 casos). Casi nadie vuelve andando a base: si compra en la fuente y estaba lejos, fue una vuelta a
# base. Error medio 1.875 → 1.572 unidades y fallos de más de 4.000, 11,9 % → 7,0 %. Salir tarde de la fuente o «ir y
# esperar» en vez de la línea recta empeoraban; el umbral del teletransporte y los puntos de daño apenas cambian nada.


def _add_anchor(anchors: dict[int, list], pid: int, t: int, p, kind: int) -> None:
    """Añade un punto de paso a ``pid`` (si es de la partida y hay posición)."""
    if pid in anchors and p is not None:
        anchors[pid].append((t, int(p[0]), int(p[1]), kind))


def interpolate(items: list[tuple[int, int, int, int]], t: int, times: list[int] | None = None) -> tuple[float, float] | None:
    """Posición en ``t`` con los puntos de paso (ordenados): en línea recta entre dos, quieto antes de morir o de un salto."""
    if not items:
        return None
    times = times if times is not None else [a[0] for a in items]
    i = bisect.bisect_right(times, t)
    prev = items[i - 1] if i > 0 else None
    nxt = items[i] if i < len(items) else None
    if prev is not None and prev[3] in (ANCHOR_DEATH, ANCHOR_HOLD):  # justo antes de morir o de un salto
        return prev[1], prev[2]
    if prev is None:
        return (nxt[1], nxt[2]) if nxt else None
    if nxt is None or nxt[0] == prev[0]:
        return prev[1], prev[2]
    f = (t - prev[0]) / (nxt[0] - prev[0])
    return prev[1] + (nxt[1] - prev[1]) * f, prev[2] + (nxt[2] - prev[2]) * f


def _walk(anchors: list[tuple[int, int, int, int]], champ: int | None) -> list[tuple[int, int, int, int]]:
    """Los puntos de paso con las rutas que rodean muros entre cada dos (tipo 7; no en los tramos «quieto y salta»)."""
    return [(int(a[0]), int(a[1]), int(a[2]), int(a[3])) for a in navgrid.route_track(anchors, champ)]


def _reconstruct(anchors: list[tuple[int, int, int, int]], fountain: tuple[int, int] | None) -> list[tuple[int, int, int, int]]:
    """Corrige los tramos imposibles de los puntos de paso (ordenados) de un jugador.

    * Antes de una compra en la fuente estando lejos, hubo una **vuelta a base**: se queda donde se le vio por última vez
      y salta a la fuente tras los 8 s de canalización (antes cruzaba el mapa andando).
    * Entre dos puntos que no se pueden recorrer andando, hubo un **teletransporte**: se queda y salta al final.
    """
    out: list[list[int]] = []
    for t, x, y, kind in anchors:
        prev = out[-1] if out else None
        if prev is not None and prev[3] != ANCHOR_DEATH and t > prev[0]:
            start = _jump_start(prev, (t, x, y, kind), fountain)
            if start is not None and start > prev[0]:
                out.append([start, prev[1], prev[2], ANCHOR_HOLD])
            elif start is not None and prev[3] not in (ANCHOR_BASE,):
                prev[3] = ANCHOR_HOLD
        out.append([t, x, y, kind])
    return [tuple(a) for a in out]


def _jump_start(prev: list[int], anchor: tuple[int, int, int, int], fountain: tuple[int, int] | None) -> int | None:
    """Si de ``prev`` a ``anchor`` hubo vuelta a base o teletransporte, desde cuándo se queda quieto (si no, ``None``)."""
    t, x, y, kind = anchor
    gap = t - prev[0]
    far = geo.dist((prev[1], prev[2]), (x, y))
    recall = kind == ANCHOR_BASE and fountain is not None and prev[3] != ANCHOR_BASE and far > NEAR_BASE
    teleport = not recall and kind != ANCHOR_BASE and far / gap > MAX_SPEED and far > NEAR_BASE
    if recall or teleport:
        return t - (RECALL_MS if recall else 1_000)
    return None


# ---------------------------------------------------------------- campamentos de jungla deducidos
ANCHOR_CAMP = 6  # en un campamento de jungla (deducido del CS de jungla de ese minuto)
# Ajustado con las partidas del usuario: se esconde la posición del jungla en un minuto (su CS sí se sabe) y se
# reconstruye con la de antes, la de después y los campamentos que encajan con su CS. En los 244 casos en que farmeaba,
# error medio 3.602 → 3.069 unidades y fallos de más de 4.000, 40 % → 30 %. Probados: aparición 1:00/1:30/1:45,
# reaparición 2:15/2:30/3:00, CS por campamento 4-10 y limpieza 4-15 s.
CAMP_CS = 5  # CS de jungla de media por campamento (unos dan 4 y otros más)
CAMP_SPAWN_MS = 90_000
CAMP_RESPAWN_MS = 150_000
CAMP_RESPAWN_BUFF_MS = 300_000  # rojo y azul: 5:00 (con la de los pequeños, el rojo «revivía» a los 2:30)
BUFFS = ("red", "blue")
SKIPPED_NEAR = 1_300  # en un tramo saltado (kill, muerte), el campamento donde estaba al empezar o al acabar
CAMP_CLEAR_MS = 9_000
CAMP_SPEED = 0.40  # unidades por ms andando entre campamentos
# Si así no da tiempo (pasa en partida avanzada: 18-23 CS de jungla en un minuto), con la velocidad real que da Riot en
# esos frames (botas, objetos, misión de jungla) y limpiando como al final de partida. Sin esto, 9 de 821 tramos de tus
# partidas no cuadraban con ningún camino.
CAMP_FAST_CLEAR_MS = 3_500
NEAR_CAMP_REST = 1_500  # «sigue en el campamento de antes» (los pocos CS que faltaban de él caen en el minuto siguiente)
CAMP_MAX_SPEED = 0.60
CAMP_CANDIDATES = 6  # los campamentos vivos más cerca del tramo (para no probar todas las combinaciones)
CAMP_MAX = 4


def _camp_sites() -> list[tuple[str, tuple[float, float]]]:
    return [(f"{team}_{name}", pos) for (team, name), pos in geo.CAMPS.items()]


def _camp_paces(g, pid: int, a: int, b: int) -> list[tuple[int, float]]:
    """(limpieza por campamento, velocidad máxima) que se prueban en orden: la del principio de partida y, si no da
    tiempo, la velocidad real de esos frames con la limpieza rápida de partida avanzada."""
    paces = [(CAMP_CLEAR_MS, CAMP_SPEED)]
    speeds = []
    frames = (getattr(g, "pf", None) or {}).get(pid) or []
    for i in (a, b):
        frame = frames[i] if i < len(frames) else None
        v = _num(((frame or {}).get("championStats") or {}).get("movementSpeed"))
        if v:
            speeds.append(v / 1000)
    fast = min(CAMP_MAX_SPEED, max([CAMP_SPEED, *speeds]))
    paces.append((CAMP_FAST_CLEAR_MS, fast))
    return paces


def infer_camps(g, anchors: dict[int, list], hidden: frozenset = frozenset()) -> dict[int, list[tuple[int, int, int, int]]]:
    """Por qué campamentos pasó cada jungla en cada tramo sin otros datos.

    El CS de jungla de cada minuto dice cuántos campamentos hizo; su posición al empezar y al acabar, por dónde pudo ir;
    y los que ya se hicieron antes (los dos junglas) y su reaparición, cuáles estaban vivos. Se elige la combinación de
    campamentos vivos que forma el camino más corto y se le coloca en cada uno a su hora; el CS de los minutos
    intermedios (si un tramo dura más de uno) reparte los campamentos entre ellos. Si no da tiempo andando, no se inventa
    nada (y el tramo queda «sin cuadrar» en ``g.camp_misses``).

    ``hidden``: {(jugador, índice de frame)} cuya posición se da por desconocida (para validar la reconstrucción).
    """
    sites = _camp_sites()
    killed: dict[str, int] = {}  # campamento -> último momento en que se hizo (de cualquiera de los dos junglas)
    out: dict[int, list] = {}
    junglers = [pid for pid, p in g.players.items() if p.get("smite") or p.get("role") == "JUNGLE"]
    g.camp_misses = 0
    g.camp_minutes = 0
    if not g.has_timeline or len(g.ts) < 2:
        return out
    known = {pid: sorted(a[0] for a in anchors.get(pid) or [] if a[3] not in (0, ANCHOR_CAMP)) for pid in junglers}
    visible = {pid: [i for i in range(len(g.ts)) if (pid, i) not in hidden and g.frame_pos(pid, i) is not None] for pid in junglers}
    # Tramos de todos los junglas en orden de tiempo (los campamentos que hace uno no están vivos para el otro).
    spans = sorted(
        (g.ts[a], g.ts[b], pid, a, b)
        for pid in junglers for a, b in zip(visible[pid], visible[pid][1:])
    )
    for span in spans:
        _camp_span(g, sites, killed, out, known[span[2]], span)
    return out


def _camp_span(g, sites: list, killed: dict[str, int], out: dict[int, list], known: list[int], span: tuple) -> None:
    """Un tramo de un jungla entre dos frames: lo coloca en los campamentos que encajan con su CS (o lo da por «sin cuadrar»)."""
    t0, t1, pid, a, b = span
    c = [g.fval(pid, i, "jungleMinionsKilled") for i in range(a, b + 1)]
    if any(v is None for v in c):
        return
    k = round((c[-1] - c[0]) / CAMP_CS)
    if k <= 0:
        return
    p0, p1 = g.frame_pos(pid, a), g.frame_pos(pid, b)
    # Solo tramos «limpios»: vivo, sin pasar por base y sin otros puntos conocidos entre medias. En los que no, lo que
    # hizo no se coloca, pero sí cuenta como hecho: si no, ese campamento seguía «vivo» y otro tramo lo repetía (tu rojo
    # del principio, con una kill a los 1:33 en medio, volvía a salir hecho a los 2:24).
    if g.dead_overlap(pid, t0, t1) or any(t0 < t < t1 for t in known):
        _mark_skipped(sites, killed, (t0, t1), (p0, p1), k)
        return
    g.camp_minutes += 1
    last = (out.get(pid) or [None])[-1]
    if c[-1] - c[0] < CAMP_CS and last and t0 - last[0] <= 2 * CAMP_CLEAR_MS and geo.dist(p0, last[1:3]) <= NEAR_CAMP_REST:
        return  # lo que faltaba del campamento que acababa de hacer (los krugs se dividen): ya está colocado
    best = _camp_route(sites, killed, (p0, p1), t1, min(k, CAMP_MAX))
    fits = _camp_fit(_camp_paces(g, pid, a, b), t1 - t0, best) if best is not None else None
    if fits is None:
        g.camp_misses += 1  # no cuadra: no da tiempo andando (o el CS vino de otra forma)
        return
    # Con minutos intermedios conocidos (su CS), cada campamento cae en el minuto en que subió el CS.
    due = []
    for j in range(1, len(c)):
        due += [g.ts[a + j]] * max(0, round((c[j] - c[j - 1]) / CAMP_CS))
    _place_camps(out, killed, span, p0, best, fits, due)


def _respawn_ms(name: str) -> int:
    """Lo que tarda en reaparecer un campamento (``100_red``…): los bufos, 5:00; los demás, ``CAMP_RESPAWN_MS``."""
    return CAMP_RESPAWN_BUFF_MS if name.rsplit("_", 1)[-1] in BUFFS else CAMP_RESPAWN_MS


def _alive(name: str, killed: dict[str, int], t: int) -> bool:
    return t >= CAMP_SPAWN_MS and (name not in killed or killed[name] + _respawn_ms(name) <= t)


def _mark_skipped(sites: list, killed: dict[str, int], times: tuple[int, int], ends: tuple, k: int) -> None:
    """Un tramo que no se coloca (con una kill o una muerte en medio) pero en el que subió su CS de jungla: los ``k``
    campamentos vivos donde estaba al empezar o al acabar se dan por hechos (al acabar el tramo, o antes si es el del
    principio), para que no salgan repetidos después."""
    t0, t1 = times
    marked = 0
    for t, p in ((t0, ends[0]), (t1, ends[1])):
        if p is None or marked >= k:
            continue
        near = [name for name, pos in sites if _alive(name, killed, t1) and geo.dist(p, pos) <= SKIPPED_NEAR]
        for name in near[: k - marked]:
            killed[name] = max(t, CAMP_SPAWN_MS) + CAMP_CLEAR_MS
            marked += 1


def _camp_route(sites: list, killed: dict[str, int], ends: tuple, t1: int, k: int) -> tuple[float, tuple] | None:
    """De los campamentos vivos más cerca del tramo, los ``k`` que forman el camino más corto: (longitud, campamentos)."""
    p0, p1 = ends
    alive = [(name, pos) for name, pos in sites if _alive(name, killed, t1)]
    alive.sort(key=lambda s, p0=p0, p1=p1: geo._dist_segment(s[1], p0, p1))
    alive = alive[:CAMP_CANDIDATES]
    best = None
    for combo in permutations(alive, min(k, len(alive))):
        length = _route_length(p0, combo, p1)
        if best is None or length < best[0]:
            best = (length, combo)
    return best


def _route_length(start, combo: tuple, end) -> float:
    """Longitud del camino de ``start`` a ``end`` pasando en orden por los campamentos de ``combo``."""
    length, here = 0.0, start
    for _, pos in combo:
        length += geo.dist(here, pos)
        here = pos
    return length + geo.dist(here, end)


def _camp_fit(paces: list[tuple[int, float]], span_ms: int, route: tuple[float, tuple]) -> tuple[int, int] | None:
    """(limpieza por campamento, ms andando) con el primer ritmo al que da tiempo a hacer la ruta; ``None`` si no da."""
    length, combo = route
    for clear_ms, max_speed in paces:
        walk = span_ms - len(combo) * clear_ms
        if walk > 0 and length / walk <= max_speed:
            return clear_ms, walk
    return None


def _place_camps(
    out: dict[int, list], killed: dict[str, int], span: tuple, p0, route: tuple[float, tuple], fits: tuple[int, int], due: list[int]
) -> None:
    """Coloca al jungla en cada campamento de la ruta a su hora (entrada y salida) y apunta cuándo se hizo cada uno."""
    t0, t1, pid = span[0], span[1], span[2]
    length, combo = route
    clear_ms, walk = fits
    speed = length / walk
    t, here = t0, p0
    for n, (name, pos) in enumerate(combo):
        t += geo.dist(here, pos) / speed if speed > 0 else 0
        t = max(t, CAMP_SPAWN_MS)
        if n < len(due) and t + clear_ms > due[n]:
            t = max(t0, due[n] - clear_ms)  # el CS dice que ya lo había hecho a esa hora
        out.setdefault(pid, []).append((int(t), int(pos[0]), int(pos[1]), ANCHOR_CAMP))
        t += clear_ms
        out[pid].append((int(min(t, t1 - 1)), int(pos[0]), int(pos[1]), ANCHOR_CAMP))
        killed[name] = int(t)
        here = pos


# ---------------------------------------------------------------- paso por la línea deducido del CS de súbditos
ANCHOR_LANE = 8  # en una línea farmeando (deducido del CS de súbditos de ese minuto)
LANE_FARM_CS = 6  # CS de súbditos en un minuto que exige haber pasado por una línea
LANE_REACH = 1_600  # radio de experiencia de los súbditos
LANE_FARM_SHARE = 0.25  # si el recorrido pasa menos de esto del minuto cerca de una línea, no cuadra con su CS


def _closest_on(points: list, p) -> tuple[float, float]:
    best, best_d = points[0], math.inf
    for a, b in zip(points, points[1:]):
        ax, ay, bx, by = a[0], a[1], b[0], b[1]
        dx, dy = bx - ax, by - ay
        f = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
        q = (ax + dx * f, ay + dy * f)
        if (d := geo.dist(p, q)) < best_d:
            best, best_d = q, d
    return best


def infer_lane_farm(g, anchors: dict[int, list]) -> dict[int, list[tuple[int, int, int, int]]]:
    """Minutos en que alguien hizo CS de súbditos lejos de toda línea en su frame de antes y en el de después (y sin
    otros puntos entre medias): tuvo que ir a farmear. Se le coloca en el punto de la línea más cercano a su recorrido, a
    mitad de minuto. En tus partidas, 2 de 1.385 minutos de laners: un top que se ve en el río al empezar y al acabar
    el minuto con 6-9 CS de súbditos entre medias."""
    out: dict[int, list] = {}
    if not g.has_timeline:
        return out
    lanes = [pts for pts, _ in geo.LANES.values()]
    for pid in g.players:
        others = sorted(a[0] for a in anchors.get(pid) or [] if a[3] != 0)
        for i in range(1, len(g.ts)):
            spot = _lane_farm_spot(g, pid, i, others, lanes)
            if spot is not None:
                t0, t1 = g.ts[i - 1], g.ts[i]
                out.setdefault(pid, []).append(((t0 + t1) // 2, int(spot[0]), int(spot[1]), ANCHOR_LANE))
    return out


def _lane_farm_spot(g, pid: int, i: int, others: list[int], lanes: list) -> tuple[float, float] | None:
    """Punto de línea por el que tuvo que pasar ``pid`` para su CS de súbditos del minuto que acaba en ``i`` (o ``None``)."""
    t0, t1 = g.ts[i - 1], g.ts[i]
    cs0, cs1 = g.fval(pid, i - 1, "minionsKilled"), g.fval(pid, i, "minionsKilled")
    p0, p1 = g.frame_pos(pid, i - 1), g.frame_pos(pid, i)
    if None in (cs0, cs1) or not p0 or not p1 or cs1 - cs0 < LANE_FARM_CS:
        return None
    if g.dead_overlap(pid, t0, t1):
        return None
    j = bisect.bisect_right(others, t0)
    if j < len(others) and others[j] < t1:
        return None  # hay otros puntos (kills, compras, campamentos): ya se sabe por dónde fue
    path = navgrid.route(p0, p1, g.players[pid].get("champ"))  # por donde se anda (rodeando muros)
    samples = [navgrid.along(path, f) for f in (0.1, 0.3, 0.5, 0.7, 0.9)]
    near = sum(1 for q in samples if min(geo.dist_polyline(q, pts) for pts in lanes) <= LANE_REACH)
    if near / len(samples) >= LANE_FARM_SHARE:
        return None
    mid = navgrid.along(path, 0.5)
    return min((_closest_on(pts, mid) for pts in lanes), key=lambda q, mid=mid: geo.dist(q, mid))


# ---------------------------------------------------------------- peleas sin muerte deducidas
# Riot da cada minuto el daño a campeones acumulado de cada jugador, su vida y su velocidad. Quien hizo daño a campeones
# en un minuto tuvo que estar a distancia de pegar a algún rival; si ni mató, ni asistió, ni murió (eso ya le sitúa) y
# su recorrido no le acerca a ningún rival, fue una pelea sin muerte que la reconstrucción no veía: un gank que no salió,
# un encontronazo en la jungla, un intercambio en el río. 25-11-2026, con las partidas del usuario: en el 8,9 % de los
# minutos con daño a campeones (821 de 9.189) ningún rival pasaba a menos de 1.200 unidades; con esto, en el 5,3 %.
# Validado (``track_check``, 55 partidas): posición del minuto escondida a quien pegó sin kill, error mediano 2.625 →
# 2.192 y más de 4.000 unidades 35,2 % → 30,3 %; posición de un evento quitada, 1.130 → 1.113; los minutos de línea
# imposibles siguen en 0. El encuentro se busca por donde se anda (rodeando muros). Probado y descartado: elegir al rival
# por su vida de menos (todos la tienen por los súbditos), sacar de su línea a quien farmea (los minutos de línea
# imposibles pasaban de 0 a 5) y contar el tiempo de control a rivales como pelea (empeoraba: las ralentizaciones a
# distancia también cuentan).
ANCHOR_FIGHT = 9  # peleando con un rival (deducido del daño a campeones de ese minuto)
FIGHT_DMG = 200  # daño a campeones en un minuto que exige haber estado a distancia de pegar a un rival
FIGHT_RANGE = 1_200  # a esta distancia de un rival ya pudo pegarle: no hace falta mover a nadie
FIGHT_GAP = 500  # a qué distancia se coloca a los dos (alcance de un golpe o una habilidad corta)
FIGHT_STEP_MS = 2_500
FIGHT_EXPLAINED_MS = 10_000  # una kill, asistencia o muerte poco antes o en el minuto ya explica su daño
FIGHT_HP_SHARE = 0.25  # sin pegar, acabar el minuto con un cuarto de vida de menos también es señal de pelea
FIGHT_FARM_CS = 4  # súbditos en el minuto: estaba en su línea (no se le mueve; si pelea, el otro fue a por él)
FIGHT_SPEED_SLACK = 1.15  # sobre su velocidad del frame (fantasmal, arrastres cortos)
MIN_MOVE_SPEED = 325
# Pegan de lejos, con definitivas globales o con trampas y mascotas: su daño no dice dónde estaban.
LONG_REACH = frozenset({
    17, 22, 30, 35, 41, 51, 57, 74, 76, 81, 96, 99, 101, 110, 115, 119, 126, 142, 143, 161, 202, 222, 235, 901,
})


def _minute_delta(g, pid: int, i: int, key: str, group: str | None = "damageStats") -> int:
    """Lo que creció ``key`` (de ``damageStats``, o del frame si ``group`` es ``None``) del frame ``i - 1`` al ``i``; 0 si falta."""
    frames = [g.pf[pid][j] or {} for j in (i - 1, i)]
    a, b = [(f if group is None else f.get(group) or {}).get(key) for f in frames]
    return int(b - a) if a is not None and b is not None and b > a else 0


def _hp_missing(g, pid: int, i: int) -> int:
    """Vida que le faltaba al acabar el minuto (le pegaron hace poco: campeones, súbditos o monstruos)."""
    stats = (g.pf[pid][i] or {}).get("championStats") or {}
    hp, top = stats.get("health"), stats.get("healthMax")
    return max(0, int(top - hp)) if hp is not None and top else 0


def _move_speed(g, pid: int, i: int) -> float:
    """Unidades por ms a las que pudo ir en el minuto que acaba en ``i`` (su velocidad del frame, con margen)."""
    speeds = [((g.pf[pid][j] or {}).get("championStats") or {}).get("movementSpeed") for j in (i - 1, i)]
    return max([MIN_MOVE_SPEED, *[v for v in speeds if v]]) * FIGHT_SPEED_SLACK / 1000


def infer_fights(g, anchors: dict[int, list]) -> dict[int, list[tuple[int, int, int, int]]]:
    """Peleas sin muerte: a quien hizo daño a campeones en un minuto sin que nada le sitúe cerca de un rival se le junta
    con el rival que más encaja (el que también pegó o acabó el minuto con menos vida) en el momento y el sitio en que
    menos se desvían los dos de su recorrido, sin pasar de su velocidad. Si no da tiempo andando, no se inventa nada."""
    out: dict[int, list] = {}
    if not g.has_timeline or len(g.ts) < 2:
        return out
    tracks = {pid: sorted(anchors.get(pid) or []) for pid in g.players}
    for i in range(1, len(g.ts)):
        dealt = {pid: d for pid in g.players if (d := _minute_delta(g, pid, i, "totalDamageDoneToChampions")) >= FIGHT_DMG}
        for a in sorted(dealt, key=lambda pid: -dealt[pid]):
            for pid, anchor in _fight_points(g, tracks, a, i, dealt):
                out.setdefault(pid, []).append(anchor)
                bisect.insort(tracks[pid], anchor)
    return out


def _explained(track: list, t0: int, t1: int) -> bool:
    """¿Ya hay algo que le sitúe peleando en ese minuto (kill, asistencia, muerte, daño en una kill o pelea deducida)?"""
    return any(a[3] in (1, ANCHOR_DEATH, ANCHOR_NEAR, ANCHOR_FIGHT) and t0 - FIGHT_EXPLAINED_MS <= a[0] <= t1 for a in track)


def _fight_points(g, tracks: dict[int, list], a: int, i: int, dealt: dict[int, int]) -> list[tuple[int, tuple]]:
    """Los puntos de pelea de ``a`` (y de su rival) en el minuto que acaba en ``i``, o ``[]``."""
    t0, t1 = g.ts[i - 1], g.ts[i]
    if g.players[a].get("champ") in LONG_REACH or _explained(tracks[a], t0, t1):
        return []
    enemies = [q for q in g.players if g.players[q]["team"] != g.players[a]["team"]]
    times = list(range(t0 + FIGHT_STEP_MS, t1, FIGHT_STEP_MS))
    pos = {pid: [None if g.is_dead(pid, t) else interpolate(tracks[pid], t) for t in times] for pid in (a, *enemies)}
    for q in enemies:  # ya pasa cerca de algún rival: su daño está explicado
        if any(pa and pq and geo.dist(pa, pq) <= FIGHT_RANGE for pa, pq in zip(pos[a], pos[q])):
            return []
    best = None
    a_moves = not _farming(g, a, i)
    for q in enemies:
        if not _fought(g, q, i):
            continue
        q_moves = not _explained(tracks[q], t0, t1) and not _farming(g, q, i)
        if not (a_moves or q_moves):
            continue
        for k, t in enumerate(times):
            plan = _meet(g, tracks, (a, q), t, (pos[a][k], pos[q][k]), (a_moves, q_moves), i)
            if plan is not None and (best is None or plan[0] < best[0]):  # el rival y el momento que menos desvían
                best = plan
    return best[1] if best else []


def _fought(g, q: int, i: int) -> bool:
    """¿Hay señal de que ``q`` peleó en el minuto? Pegó a campeones o lo acabó con un cuarto de vida de menos (también si
    hizo campamentos o súbditos, que quitan vida: no distinguirlo acierta más, muchas peleas sin muerte son en los
    campamentos; probado el 25-11-2026: sin contar la vida de quien farmeó, 2.646 → 2.333 en vez de → 2.199)."""
    if _minute_delta(g, q, i, "totalDamageDoneToChampions") >= FIGHT_DMG / 2:
        return True
    stats = (g.pf[q][i] or {}).get("championStats") or {}
    top = stats.get("healthMax") or 0
    return bool(top) and _hp_missing(g, q, i) >= FIGHT_HP_SHARE * top


def _farming(g, pid: int, i: int) -> bool:
    """Farmeó súbditos en el minuto (estaba en su línea): no se le saca de ella, es el otro quien va."""
    return _minute_delta(g, pid, i, "minionsKilled", group=None) >= FIGHT_FARM_CS


def _meet(g, tracks: dict[int, list], pair: tuple[int, int], t: int, spots: tuple, moves: tuple[bool, bool], i: int):
    """(desvío, puntos) para que los dos estén a ``FIGHT_GAP`` en ``t`` sin pasar de su velocidad, o ``None``. El
    desvío es lo que se apartan de su recorrido; ``moves``: si cada uno se puede mover (quien farmea se queda)."""
    (a, q), (pa, pq) = pair, spots
    if pa is None or pq is None:
        return None
    path = navgrid.route(pa, pq, g.players[a].get("champ"))  # por donde se anda entre los dos (rodeando muros)
    d = navgrid.length(path)
    need = d - FIGHT_GAP
    if need <= 0 or geo.dist(pa, pq) <= FIGHT_GAP:
        return None
    a_moves, q_moves = moves
    shares = [w for w in (0.5, 0.25, 0.75, 1.0, 0.0) if (a_moves or w == 0) and (q_moves or w == 1)]
    best = None
    for w in shares:
        ma = navgrid.along(path, need * w / d)
        mq = navgrid.along(path, 1 - need * (1 - w) / d)
        effort = _speed_ratio(g, tracks[a], a, t, ma, i) if w > 0 else 0.0
        if w < 1:
            effort = max(effort, _speed_ratio(g, tracks[q], q, t, mq, i))
        if effort > 1:
            continue
        points = [(a, (t, int(ma[0]), int(ma[1]), ANCHOR_FIGHT))] if w > 0 else []
        if w < 1:
            points.append((q, (t, int(mq[0]), int(mq[1]), ANCHOR_FIGHT)))
        if best is None or (need, effort) < best[0]:
            best = ((need, effort), points)
    return best


def _speed_ratio(g, track: list, pid: int, t: int, p, i: int) -> float:
    """Velocidad que exige pasar por ``p`` en ``t`` (entre sus puntos de antes y de después) frente a la suya (≤ 1: puede)."""
    j = bisect.bisect_left(track, (t,))
    prev = track[j - 1] if j > 0 else None
    nxt = track[j] if j < len(track) else None
    if prev is not None and prev[3] == ANCHOR_DEATH:
        return math.inf
    speed = _move_speed(g, pid, i)
    ratio = 0.0
    if prev is not None:
        ratio = max(ratio, geo.dist((prev[1], prev[2]), p) / max(1, t - prev[0]) / speed)
    if nxt is not None:
        ratio = max(ratio, geo.dist(p, (nxt[1], nxt[2])) / max(1, nxt[0] - t) / speed)
    return ratio


# ---------------------------------------------------------------- wards rotos deducidos
# WARD_KILL dice quién rompió un ward rival y cuándo, no dónde; WARD_PLACED, quién lo puso y cuándo (estaba donde quien
# lo puso). Si el recorrido de quien lo rompió no pasa cerca de ningún ward rival vivo de ese tipo, fue a por uno: se le
# coloca junto al que le pilla más cerca de su camino y le da tiempo a alcanzar. Validado (``track_check``): posición del
# minuto escondida a quien rompió un ward, error mediano 2.277 → 1.947 y más de 4.000 unidades 28,5 % → 20,0 %.
ANCHOR_WARD = 10  # rompiendo un ward rival (deducido)
WARD_BREAK_REACH = 700  # a esta distancia de un ward rival ya pudo romperlo (o verlo con la lente)
WARD_BREAK_GAP = 400


def infer_ward_kills(g, anchors: dict[int, list]) -> dict[int, list[tuple[int, int, int, int]]]:
    """Puntos de paso de quien rompió un ward rival sin pasar cerca de ninguno (ver arriba)."""
    out: dict[int, list] = {}
    if not g.has_timeline:
        return out
    tracks = {pid: sorted(anchors.get(pid) or []) for pid in g.players}
    for t, killer, kind in sorted(g.ward_kill_events):
        if killer not in g.players or g.is_dead(killer, t):
            continue
        kp = interpolate(tracks[killer], t)
        spots = [p for p in _enemy_ward_spots(g, tracks, killer, t, kind) if p is not None]
        if kp is None or not spots or any(geo.dist(kp, p) <= WARD_BREAK_REACH for p in spots):
            continue
        i = max(1, min(len(g.ts) - 1, bisect.bisect_right(g.ts, t)))
        best = None
        for spot in spots:
            d = geo.dist(kp, spot)
            f = (d - WARD_BREAK_GAP) / d
            p = (kp[0] + (spot[0] - kp[0]) * f, kp[1] + (spot[1] - kp[1]) * f)
            if navgrid.walkable(p) and _speed_ratio(g, tracks[killer], killer, t, p, i) <= 1 and (best is None or d < best[0]):
                best = (d, p)
        if best is not None:
            anchor = (t, int(best[1][0]), int(best[1][1]), ANCHOR_WARD)
            out.setdefault(killer, []).append(anchor)
            bisect.insort(tracks[killer], anchor)
    return out


def _enemy_ward_spots(g, tracks: dict[int, list], killer: int, t: int, kind: str) -> list:
    """Sitios estimados de los wards rivales de ``kind`` que podían seguir vivos en ``t`` (donde estaba quien los puso)."""
    team = g.players[killer]["team"]
    spots = []
    for pid, events in g.ward_events.items():
        if pid not in g.players or g.players[pid]["team"] == team:
            continue
        for wt, wkind in events:
            if wt > t or (wkind != kind and kind in (*STEALTH_WARDS, "CONTROL_WARD")):
                continue
            life = WARD_LIFE_MS.get(wkind, WARD_WINDOW_MS) if wkind != "CONTROL_WARD" else None
            if life is None or t - wt <= life:
                spots.append(interpolate(tracks[pid], wt))
    return spots


# ---------------------------------------------------------------- recorridos típicos
# Entre dos puntos conocidos nadie va en línea recta: en mitad de cada tramo se le aparta como se aparta de media la
# gente con su rol, en esa fase y entre esas zonas (``typical_paths``, aprendido de ~48.000 partidas de todos los elos).
ANCHOR_TYPICAL = 11
TYPICAL_MIN_GAP_MS = 20_000  # tramos más cortos: casi no hay sitio para apartarse
TYPICAL_SKIP = (ANCHOR_BASE, ANCHOR_DEATH, ANCHOR_HOLD)  # vueltas a base, muertes y saltos van aparte (``_reconstruct``)


def infer_typical(g, anchors: dict[int, list]) -> dict[int, list[tuple[int, int, int, int]]]:
    """El punto medio de cada tramo, apartado como suele (si es andable y le da tiempo a su velocidad)."""
    out: dict[int, list] = {}
    if not g.has_timeline:
        return out
    for pid, items in anchors.items():
        info = g.players.get(pid) or {}
        track = sorted(items)
        for a, b in zip(track, track[1:]):
            spot = _typical_spot(g, pid, info, a, b)
            if spot is not None:
                out.setdefault(pid, []).append(spot)
    return out


def _typical_spot(g, pid: int, info: dict, a, b) -> tuple[int, int, int, int] | None:
    if b[0] - a[0] < TYPICAL_MIN_GAP_MS or a[3] in TYPICAL_SKIP or b[3] in TYPICAL_SKIP:
        return None
    t = (a[0] + b[0]) // 2
    if g.is_dead(pid, t):
        return None
    off = typical_paths.offset(info.get("role"), info.get("team"), t, (a[1], a[2]), (b[1], b[2]), 0.5)
    if off is None:
        return None
    p = ((a[1] + b[1]) / 2 + off[0], (a[2] + b[2]) / 2 + off[1])
    if not navgrid.walkable(p):
        return None
    i = max(1, min(len(g.ts) - 1, bisect.bisect_right(g.ts, t)))
    speed = _move_speed(g, pid, i)
    if geo.dist((a[1], a[2]), p) / (t - a[0]) > speed or geo.dist(p, (b[1], b[2])) / (b[0] - t) > speed:
        return None
    return (t, int(p[0]), int(p[1]), ANCHOR_TYPICAL)


MOVE_STEP_MS = 5_000
MOVE_GROUP_RADIUS = 2_000  # «agrupado»: al menos 2 aliados a esta distancia
MOVE_ZONES = ("lane", "other_lane", "river", "own_jungle", "enemy_jungle", "base", "dead")


def _move_zone(g: _Game, pid: int, p) -> str:
    z = geo.zone(p)
    team = g.players[pid]["team"]
    if z.startswith("base_"):
        return "base"
    if geo.is_lane(z):
        return "lane" if geo.ROLE_LANE.get(g.players[pid].get("role") or "") == z else "other_lane"
    if z == "river":
        return "river"
    return "own_jungle" if geo.is_jungle_of(z, team) else "enemy_jungle"


def _movement(g: _Game) -> dict:
    """Dónde pasa el tiempo cada uno de los 10 jugadores (cada 5 s con las posiciones reconstruidas), antes y después
    del 14, y pasado el 14 cuánto va agrupado con su equipo o solo en una línea lateral."""
    out = {}
    if not g.has_timeline:
        return out
    for pid in g.players:
        if g.afk_ms(pid) >= 60_000:
            continue  # sus posiciones no dicen nada de cómo juega
        out[str(pid)] = _player_movement(g, pid)
    return out


def _player_movement(g: _Game, pid: int) -> dict:
    """% del tiempo de un jugador en cada zona antes y después del 14 y, pasado el 14, agrupado o solo en una banda."""
    phases = {"early": Counter(), "late": Counter()}
    late = Counter()  # muestras pasado el 14 vivo y fuera de la base: en total, agrupado y solo en una línea lateral
    for t in range(0, g.duration_ms, MOVE_STEP_MS):
        _movement_sample(g, pid, t, phases, late)
    entry = {}
    for phase, c in phases.items():
        total = sum(c.values())
        if total:
            entry[phase] = {z: round(100 * c[z] / total, 1) for z in MOVE_ZONES if c[z]}
    if late["alive"]:
        entry["grouped_late"] = round(100 * late["grouped"] / late["alive"], 1)
        entry["side_alone_late"] = round(100 * late["alone_side"] / late["alive"], 1)
    return entry


def _movement_sample(g: _Game, pid: int, t: int, phases: dict[str, Counter], late: Counter) -> None:
    """Cuenta dónde estaba ``pid`` en ``t`` (su zona o muerto) y, pasado el 14, si iba agrupado o solo en una banda."""
    phase = "early" if t < EARLY_MS else "late"
    if g.is_dead(pid, t):
        phases[phase]["dead"] += 1
        return
    p = g.pos_at(pid, t)
    if p is None:
        return
    zone = _move_zone(g, pid, p)
    phases[phase][zone] += 1
    if phase == "late" and zone != "base":
        late["alive"] += 1
        allies = [a for a in g.players if a != pid and g.players[a]["team"] == g.players[pid]["team"]]
        near = sum(1 for a in allies if (q := g.pos_at(a, t)) is not None and geo.dist(p, q) <= MOVE_GROUP_RADIUS)
        if near >= 2:
            late["grouped"] += 1
        elif near == 0 and geo.zone(p) in ("top", "bot"):
            late["alone_side"] += 1


def analyze_game(
    match: dict,
    timeline: dict | None,
    *,
    pid: int | None = None,
    puuid: str | None = None,
    game_name: str | None = None,
    tag_line: str | None = None,
    items: ItemCatalog | None = None,
    route_all: bool = True,
    shared: dict | None = None,
) -> dict:
    """Análisis de la partida desde el punto de vista de un jugador. ``shared``: el mismo diccionario (vacío al empezar)
    para los 10 jugadores de una partida: sus recorridos y su movimiento se calculan una vez y son los mismos en los 10."""
    me_raw = find_me(match, pid=pid, puuid=puuid, game_name=game_name, tag_line=tag_line)
    g = _Game(match, timeline, me_raw, items, route_all=route_all, shared=shared)
    me = g.me["pid"]

    deaths = [_classify_death(g, k) for k in g.kills if k["victim"] == me]
    moments: list[dict] = [d["moment"] for d in deaths]
    enemy_pinks = _enemy_pinks(g, moments)
    metrics = _summary_metrics(g, me_raw)
    result = _summary_result(g, match, me_raw, metrics, deaths, moments)

    if not g.has_timeline:
        for key in ("deaths_pre14", "deaths_solo", "deaths_ganked", "deaths_to_jungler_pre14", "deaths_no_vision_pre14"):
            metrics[key] = None
        return result

    metrics.update(_death_metrics(g, deaths, enemy_pinks))

    _afk_moments(g, moments)
    _lane_phase(g, metrics, moments)
    _objectives(g, metrics, moments)
    _priority_stats(g, metrics)
    _macro(g, metrics, moments)
    _gold_use(g, metrics, moments)
    _time_use(g, metrics, moments)
    if g.role == "JUNGLE":
        result["jungle"] = _jungle(g, metrics, moments)
    else:
        _roaming(g, metrics)
    build = _build(g, metrics, moments)
    if build:
        result["build"] = build

    moments.sort(key=lambda m: m["t"])
    # Con qué certeza se decidió cada detección (segundos a una posición real tuya y, en las muertes, de los rivales
    # implicados): la auditoría (``calibration_audit``) separa así lo que no cambia entre elos de lo que el ruido iguala.
    for m in moments:
        m.setdefault("ev", {})["gap_tu_s"] = g.anchor_gap(me, int((m.get("t") or 0) * 1000))
    result["afk"] = [{"start": round(s / 1000, 1), "end": round(e / 1000, 1)} for s, e in g.afk[me]]
    result["series"] = _series(g)
    _gold_lead(result["series"], metrics)
    result["replay"] = _replay(g)
    # Coherencia de la reconstrucción: tramos de jungla sin otros datos y cuántos no cuadraban con el CS (sin inventar).
    # Estadísticas de Riot para las reglas medibles de las guías (``rule_metrics``): pinks, visión, roams, TP.
    result["riot_rules"] = rule_metrics.raw_fields(me_raw)
    # Tus wards (segundo y tipo) para la visión útil de ``route_macro`` (objetivos, jungla rival, pinks).
    result["my_wards"] = [[round(t / 1000, 1), kind] for t, kind in g.ward_events.get(me) or []]
    result["reconstruction"] = {"camp_spans": getattr(g, "camp_minutes", 0), "camp_misses": getattr(g, "camp_misses", 0)}
    _movement_metrics(g, result, metrics)
    return result


def _summary_metrics(g: _Game, me_raw: dict) -> dict:
    """Métricas que da el resumen de la partida (sin timeline): KDA, farm, oro, visión, muertes y objetivos."""
    info = g.info
    ch = me_raw.get("challenges") or {}
    kills = _num(me_raw.get("kills"))
    n_deaths = _num(me_raw.get("deaths"))
    assists = _num(me_raw.get("assists"))
    cs = _num(me_raw.get("totalMinionsKilled")) + _num(me_raw.get("neutralMinionsKilled"))
    team_kills = sum(_num(p.get("kills")) for p in info.get("participants") or [] if _num(p.get("teamId")) == g.team)
    kp = _kill_participation(ch, kills + assists, team_kills)
    dmg_share = ch.get("teamDamagePercentage")
    time_dead = _num(me_raw.get("totalTimeSpentDead"))
    # Por minuto jugado: los minutos de desconexión no cuentan (no son falta de farm ni de visión).
    active = max(g.minutes - g.afk_ms(g.me["pid"]) / 60_000, 1.0)
    metrics: dict[str, float | int | None] = {
        "win": int(bool(me_raw.get("win"))),
        "minutes": round(g.minutes, 2),
        "kills": kills,
        "deaths": n_deaths,
        "assists": assists,
        "kda": round((kills + assists) / max(1, n_deaths), 2),
        "kp": round(kp, 1) if kp is not None else None,
        "dmg_share": round(float(dmg_share) * 100, 1) if dmg_share is not None else None,
        "cs_min": round(cs / active, 2),
        "gold_min": round(_num(me_raw.get("goldEarned")) / active),
        "vision_min": round(_num(me_raw.get("visionScore")) / active, 2),
        "wards_min": round(_num(me_raw.get("wardsPlaced")) / active, 2),
        "control_wards": _num(me_raw.get("visionWardsBoughtInGame")),
        "wards_killed": _num(me_raw.get("wardsKilled")),
        "time_dead_pct": round(time_dead / max(1, g.duration) * 100, 1),
        "solo_kills": _num(ch.get("soloKills")) if "soloKills" in ch else None,
        # Jugadores que Riot marcó como AFK (``wasAfk``): aunque fuera poco rato, la partida no es comparable.
        "riot_afk": sum(1 for p in info.get("participants") or [] if p.get("wasAfk")),
        "plates": _num(ch.get("turretPlatesTaken")) if "turretPlatesTaken" in ch else None,
    }
    metrics["obj_participation"] = _summary_obj_participation(g, ch)
    return metrics


def _kill_participation(ch: dict, takedowns: int, team_kills: int) -> float | None:
    """Participación en kills (%): la de Riot si viene; si no, kills y asistencias entre las kills del equipo."""
    kp = ch.get("killParticipation")
    if kp is not None:
        return float(kp) * 100
    return takedowns / team_kills * 100 if team_kills else None


def _summary_obj_participation(g: _Game, ch: dict) -> float | None:
    """Objetivos (sin timeline, con lo que trae el resumen de la partida): % de dragones, barones y heraldos de tu equipo."""
    team_obj = next((t.get("objectives") or {} for t in g.info.get("teams") or [] if _num(t.get("teamId")) == g.team), {})
    team_total = sum(_num((team_obj.get(k) or {}).get("kills")) for k in ("dragon", "baron", "riftHerald"))
    mine_total = _num(ch.get("dragonTakedowns")) + _num(ch.get("baronTakedowns")) + _num(ch.get("riftHeraldTakedowns"))
    return round(min(100.0, mine_total / team_total * 100), 1) if team_total and ch else None


def _summary_result(g: _Game, match: dict, me_raw: dict, metrics: dict, deaths: list[dict], moments: list[dict]) -> dict:
    """El análisis con lo que da el resumen de la partida: datos de la partida, los 10 jugadores, métricas y muertes."""
    info = g.info
    return {
        "version": ANALYSIS_VERSION,
        "match_id": (match.get("metadata") or {}).get("matchId"),
        "queue": _num(info.get("queueId")),
        "queue_label": QUEUES.get(_num(info.get("queueId")), f"Cola {_num(info.get('queueId'))}"),
        "game_start": _num(info.get("gameStartTimestamp") or info.get("gameCreation")),
        "duration": g.duration,
        "patch": ".".join((info.get("gameVersion") or "").split(".")[:2]),
        "has_timeline": g.has_timeline,
        "clocks": g.clocks_ok,  # False: sin relojes fiables no se juzga la preparación de objetivos
        "items_known": g.inv is not None,
        "items_reliable": g.items_reliable,  # False: oro y build sin veredictos (catálogo de otra temporada u objetos desconocidos)
        "unknown_items": sorted(g.inv.unknown) if g.inv is not None else [],
        "me": g.me["pid"],
        "team": g.team,
        "role": g.role,
        "champ": g.me["champ"],
        "win": bool(me_raw.get("win")),
        "opponent": g.opponent,
        "enemy_jungler": g.enemy_jungler,
        "ally_jungler": g.ally_jungler,
        "players": [
            {k: p[k] for k in ("pid", "team", "champ", "role", "name", "tag")}
            | {
                "kills": _num(g.raw[pid].get("kills")),
                "deaths": _num(g.raw[pid].get("deaths")),
                "assists": _num(g.raw[pid].get("assists")),
                "cs": _num(g.raw[pid].get("totalMinionsKilled")) + _num(g.raw[pid].get("neutralMinionsKilled")),
                "gold": _num(g.raw[pid].get("goldEarned")),
                "level": _num(g.raw[pid].get("champLevel")),
                "items": [_num(g.raw[pid].get(f"item{i}")) for i in range(7)],
                "afk_min": round(g.afk_ms(pid) / 60_000, 1),
            }
            for pid, p in sorted(g.players.items())
        ],
        "metrics": metrics,
        "deaths": [{k: v for k, v in d.items() if k != "moment"} for d in deaths],
        "moments": moments,
    }


def _count_deaths(deaths: list[dict], tag: str, early_only: bool = False, unless: tuple[str, ...] = ()) -> int:
    """Muertes con la etiqueta ``tag`` (solo antes del 14 si ``early_only``) que no tienen ninguna de ``unless``."""
    return sum(1 for d in deaths if tag in d["tags"] and (not early_only or d["early"]) and not any(u in d["tags"] for u in unless))


def _death_metrics(g: _Game, deaths: list[dict], enemy_pinks: dict) -> dict:
    """Métricas con el timeline: muertes por tipo, pinks rivales, wards y minutos AFK (tuyos, de aliados y de rivales)."""
    me = g.me["pid"]
    return {
        # Las muertes en peleas ganadas, con algo a cambio, en el empujón final o antes de una desconexión
        # no cuentan como error en las métricas (deaths_pre14 sí es el recuento bruto).
        "deaths_pre14": sum(1 for d in deaths if d["early"]),
        "deaths_solo": _count_deaths(deaths, "solo", unless=CONTEXT_OK),
        "deaths_ganked": _count_deaths(deaths, "gank", True, unless=CONTEXT_OK),
        "deaths_to_jungler_pre14": _count_deaths(deaths, "jungler", True, unless=CONTEXT_OK),
        "deaths_no_vision_pre14": _count_deaths(deaths, "no_ward", True, unless=CONTEXT_OK),
        "deaths_overextended": _count_deaths(deaths, "overextended", True, unless=CONTEXT_OK),
        "deaths_enemy_jungle": _count_deaths(deaths, "enemy_jungle", unless=CONTEXT_OK),
        "deaths_unspent_gold": _count_deaths(deaths, "unspent_gold", unless=("game_end", "afk")) if g.items_reliable else None,
        "deaths_before_objective": _count_deaths(deaths, "before_objective", unless=("fight_won", "game_end", "afk")),
        "deaths_isolated_late": _count_deaths(deaths, "isolated", unless=CONTEXT_OK),
        "deaths_collapsed": _count_deaths(deaths, "collapsed", unless=CONTEXT_OK),
        "deaths_outnumbered_obj": _count_deaths(deaths, "fight_outnumbered", unless=CONTEXT_OK) if g.clocks_ok else None,
        "deaths_fight_won": _count_deaths(deaths, "fight_won"),
        "deaths_duel_behind": _count_deaths(deaths, "duel_behind"),
        "deaths_seen_ward": _count_deaths(deaths, "seen_ward", unless=CONTEXT_OK) if g.has_timeline else None,
        "deaths_lens_unused": _count_deaths(deaths, "lens_unused", unless=CONTEXT_OK) if g.has_timeline else None,
        **enemy_pinks,
        "wards_pre14": sum(1 for t in g.wards[me] if t < EARLY_MS),
        "control_wards_placed": g.control_wards_placed[me],
        "afk_min": round(g.afk_ms(me) / 60_000, 1),
        "ally_afk_min": round(max((g.afk_ms(a) for a in g.allies), default=0) / 60_000, 1),
        "enemy_afk_min": round(max((g.afk_ms(e) for e in g.enemies), default=0) / 60_000, 1),
    }


def _gold_lead(series: dict, metrics: dict) -> None:
    """Mayor ventaja y desventaja de oro de equipo (para «ventajas que se escapan» y remontadas)."""
    diffs = [d for d in series["team_gold_diff"] if d is not None]
    if diffs and len(diffs) == len(series["minute"]):
        best = max(range(len(diffs)), key=lambda j: diffs[j])
        metrics["max_lead"] = diffs[best]
        metrics["max_lead_min"] = series["minute"][best]
        metrics["max_deficit"] = min(diffs)


def _movement_metrics(g: _Game, result: dict, metrics: dict) -> None:
    """Movimiento de los 10 jugadores (``result["movement"]``) y, en las métricas, cómo repartiste tú el tiempo."""
    shared = g._shared
    if shared is not None and "movement" in shared:
        movement = shared["movement"]  # el mismo para los 10 análisis de la partida (era el 74 % del tiempo de cada uno)
    else:
        movement = _movement(g)
        if shared is not None:
            shared["movement"] = movement
    if not movement:
        return
    result["movement"] = movement
    mine = movement.get(str(g.me["pid"])) or {}
    early, late = mine.get("early") or {}, mine.get("late") or {}
    metrics["mv_lane_pre14"] = early.get("lane", 0.0) if g.role != "JUNGLE" else None
    metrics["mv_river_pre14"] = early.get("river", 0.0)
    metrics["mv_enemy_jungle_pre14"] = early.get("enemy_jungle", 0.0)
    metrics["mv_base_pct"] = round((early.get("base", 0.0) + late.get("base", 0.0)) / 2, 1) if early and late else early.get("base")
    metrics["mv_grouped_late"] = mine.get("grouped_late")
    metrics["mv_side_alone_late"] = mine.get("side_alone_late")


def _moment(t: float, kind: str, severity: str, title: str, detail: str = "", pos=None, tags=None) -> dict:
    return {
        "t": round(t / 1000, 1),
        "kind": kind,
        "severity": severity,
        "title": title,
        "detail": detail,
        "x": int(pos[0]) if pos else None,
        "y": int(pos[1]) if pos else None,
        "tags": tags or [kind],
    }


# ------------------------------------------------------------------ peleas
def _fight_cluster(g: _Game, k: dict) -> list[dict]:
    """Kills encadenadas (≤ 20 s y ≤ 3.200 unidades) alrededor de una muerte."""
    cluster = [k]
    changed = True
    while changed:
        changed = False
        for other in g.kills:
            if other in cluster or not other["pos"]:
                continue
            if any(f["pos"] and abs(other["t"] - f["t"]) <= FIGHT_GAP_MS and geo.dist(other["pos"], f["pos"]) <= FIGHT_RADIUS for f in cluster):
                cluster.append(other)
                changed = True
    return cluster


def _fight(g: _Game, k: dict, cluster: list[dict] | None = None) -> dict:
    cluster = cluster if cluster is not None else _fight_cluster(g, k)
    allies = sum(1 for f in cluster if g.players[f["victim"]]["team"] == g.team)
    return {"allies": allies, "enemies": len(cluster) - allies, "size": len(cluster)}


def _score(fight: dict) -> str:
    return f"{_plural(fight['enemies'], 'kill', 'kills')} y {_plural(fight['allies'], 'caído', 'caídos')}"


def _structures_value(buildings: list[dict]) -> int:
    """Valor de unas estructuras del mismo bando sin contar dos veces las placas de una torre que también cayó."""
    towers = [b for b in buildings if b["kind"] == "tower"]
    value = 0
    for b in buildings:
        if b["kind"] == "plate" and any(
            t["owner"] == b["owner"] and t["lane"] == b["lane"] and t["pos"] and b["pos"] and geo.dist(t["pos"], b["pos"]) <= SAME_TURRET
            for t in towers
        ):
            continue
        value += BUILDING_VALUE[b["kind"]]
    return value


def _team_gain(
    g: _Game, t: int, before: int = TRADE_GAIN_BEFORE_MS, after: int = 60_000, exclude: list[dict] | tuple = (), with_kills: bool = False
) -> dict | None:
    """Lo que cogió tu equipo justo después de ``t`` (lo que compensa una muerte).

    Objetivos, torres, inhibidores y placas. Las kills de otra pelea solo cuentan si atrajiste a varios
    rivales (``with_kills``): mientras te perseguían a ti, tu equipo jugaba con ventaja en otro sitio.
    Lo que se cogió *antes* de morir no lo compró tu muerte.
    """
    objectives = [o for o in g.objectives if o["team"] == g.team and t - before <= o["t"] <= t + after]
    buildings = [b for b in g.buildings if b["owner"] != g.team and t - before <= b["t"] <= t + after]
    kills = [
        k for k in g.kills
        if with_kills and k not in exclude and g.players[k["victim"]]["team"] != g.team and t - before <= k["t"] <= t + after
    ]
    if not objectives and not buildings and not kills:
        return None
    parts = [OBJ_LABEL[o["kind"]] for o in objectives]
    if kills:
        parts.append(_plural(len(kills), "kill", "kills"))
    n_towers = sum(1 for b in buildings if b["kind"] == "tower")
    n_inhib = sum(1 for b in buildings if b["kind"] == "inhibitor")
    n_plates = sum(1 for b in buildings if b["kind"] == "plate")
    parts += _structures_parts(n_towers, n_inhib, n_plates)
    value = sum(_obj_value(g, o) for o in objectives) + _structures_value(buildings) + sum(k["value"] for k in kills)
    # Desde 2026 se rompen placas toda la partida: unas placas en otra línea no pagan una muerte.
    solid = bool(objectives or kills or n_towers or n_inhib)
    return {"label": _join(parts), "value": value, "solid": solid}


def _structures_parts(n_towers: int, n_inhib: int, n_plates: int) -> list[str]:
    """«una torre», «2 inhibidores», «3 placas»… (solo las que hay, en ese orden)."""
    parts = []
    if n_towers:
        parts.append("una torre" if n_towers == 1 else f"{n_towers} torres")
    if n_inhib:
        parts.append("un inhibidor" if n_inhib == 1 else f"{n_inhib} inhibidores")
    if n_plates:
        parts.append("una placa" if n_plates == 1 else f"{n_plates} placas")
    return parts


# ------------------------------------------------------------------ muertes
class _Death:
    """Una muerte tuya mientras se clasifica: la pelea, los rivales que la causaron y sus etiquetas (en orden)."""

    def __init__(self, g: _Game, k: dict) -> None:
        self.g, self.k = g, k
        self.t, self.pos = k["t"], k["pos"]
        self.early = self.t < EARLY_MS
        self.enemies = {pid for pid in (k["killer"], *k["assists"]) if pid in g.players and g.players[pid]["team"] != g.team}
        self.tags: list[str] = []
        self.zone = geo.zone(self.pos) if self.pos else None
        self.lane = geo.ROLE_LANE.get(g.role or "")
        self.cluster = _fight_cluster(g, k) if g.has_timeline else [k]
        self.fight = _fight(g, k, self.cluster) if g.has_timeline else {"allies": 1, "enemies": 0, "size": 1}
        self.outsiders = _outsiders(g, self.enemies)
        self.seen: dict | None = None
        self.gold: dict | None = None
        self.gained: dict | None = None
        self.lost: dict | None = None
        self.numbers: dict | None = None
        self.duel: dict | None = None
        self.shutdown = _num(k.get("shutdown"))

    def tag_who(self) -> None:
        """executed, solo, jungler, gank y enemy_jungle: contra quién y dónde."""
        g, tags, enemies = self.g, self.tags, self.enemies
        if not enemies:
            tags.append("executed")
        # 1 contra 1 perdido: un único rival, sin más caídos en tu lado (si no, es una escaramuza), sin que cayera él
        # también (muerte mutua) y, en la fase de líneas, que sea de tu línea (si es el jungla u otro, es un gank).
        if len(enemies) == 1 and self.fight["allies"] == 1 and not self.fight["enemies"] and not (self.early and self.outsiders):
            tags.append("solo")
        if g.enemy_jungler and g.enemy_jungler in enemies and g.role != "JUNGLE":
            tags.append("jungler")
        # Gank: rivales de fuera de tu línea cuando estabas en ella (o en el río junto a ella). Morir en otra línea o en una
        # jungla tras rotar no es que te ganqueen.
        if self.early and g.role and g.role != "JUNGLE" and self.outsiders and _near_own_lane(self.lane, self.pos):
            tags.append("gank")
        if self.zone and geo.is_jungle_of(self.zone, 300 - g.team):
            tags.append("enemy_jungle")

    def tag_vision(self) -> None:
        """overextended, no_ward y seen_ward: adelantado, sin visión o visto por un ward rival."""
        g, tags, zone = self.g, self.tags, self.zone
        if self.early and self.lane and self.pos and zone == self.lane and _overextended(g, self.lane, self.pos, self.t):
            tags.append("overextended")
        # Sin visión solo cuenta si la visión podía cambiar algo (un gank o una rotación, el río o la jungla) y no
        # había ward tuyo ni de un aliado cerca: perder un 1 contra 1 a la vista o morir bajo torre no es cosa de wards.
        vision_matters = bool(self.outsiders) or bool(zone and not geo.is_lane(zone) and not zone.startswith("base_"))
        if self.early and g.has_timeline and self.enemies and vision_matters and not _had_vision(g, self.t, self.pos):
            tags.append("no_ward")
        self._tag_seen(vision_matters)

    def _tag_seen(self, vision_matters: bool) -> None:
        """seen_ward (y lens_unused o lens_cd si llevabas la lente): te vio un ward rival antes de la jugada."""
        # Te vio un ward rival antes de la jugada (en el río, en una jungla o en un gank) y llevabas la lente.
        # Solo en jugadas de pocos (antes del 14, o cogido con 1-2 aliados como mucho) y fuera de las bases: en una pelea de
        # equipos a mitad de partida que te vieran llegar no es lo que decide.
        g, t = self.g, self.t
        small_play = self.early or self.fight["allies"] <= 2
        out_of_base = not (self.zone or "").startswith("base")
        self.seen = _seen_by_ward(g, t) if g.has_timeline and self.enemies and vision_matters and small_play and out_of_base else None
        if not self.seen:
            return
        self.tags.append("seen_ward")
        if _trinket(g, g.me["pid"], t) == "lens":
            used = any(0 <= t - x <= LENS_COOLDOWN_MS for x in g.ward_kills.get(g.me["pid"]) or [])
            self.tags.append("lens_cd" if used else "lens_unused")

    def tag_outcome(self) -> None:
        """unspent_gold, fight_won o fight_even, traded y before_objective: cómo acabó y qué vino después."""
        fight = self.fight
        self.gold = _death_gold(self.g, self.t)
        if self.gold:
            self.tags.append("unspent_gold")
        if fight["enemies"] > fight["allies"]:
            self.tags.append("fight_won")
        elif fight["enemies"] and fight["enemies"] == fight["allies"]:
            self.tags.append("fight_even")
        self._tag_trade()
        self._tag_objective()

    def _tag_trade(self) -> None:
        """traded: tu equipo cogió a cambio algo que vale la muerte."""
        # Solo «compra» algo la muerte que atrajo a varios rivales: un 1 contra 1 perdido o una ejecución no se
        # cambian por lo que haga tu equipo en otra parte del mapa, y un error claro (adelantado o gank sin ward) tampoco.
        g, tags = self.g, self.tags
        avoidable = "overextended" in tags or ("gank" in tags and "no_ward" in tags)
        can_trade = len(self.enemies) >= 2 and "solo" not in tags and not avoidable
        gained = _team_gain(g, self.t, exclude=self.cluster, with_kills=True) if g.has_timeline and can_trade else None
        if gained and gained["value"] >= 600 and gained["solid"]:
            tags.append("traded")
            self.gained = gained

    def _tag_objective(self) -> None:
        """before_objective: el rival cogió justo después un objetivo que tu muerte pudo costar (y que no compensasteis)."""
        g, t, gained = self.g, self.t, self.gained
        self.lost = next(
            (
                o for o in g.objectives
                if o["team"] != g.team and t < o["t"] <= t + OBJECTIVE_WINDOW_MS
                and (not gained or gained["value"] < 0.6 * _obj_value(g, o)) and _death_affects(g, self.pos, o)
            ),
            None,
        )
        if self.lost:
            self.tags.append("before_objective")

    def tag_context(self) -> None:
        """isolated, game_end y afk: sin aliados cerca, en el empujón final o antes de una desconexión."""
        g, t, pos, fight = self.g, self.t, self.pos, self.fight
        if not self.early and pos and g.has_timeline and fight["allies"] == 1 and not fight["enemies"]:
            near = [a for a in g.allies if (ap := g.pos_at(a, t)) is not None and geo.dist(ap, pos) <= ISOLATED_RADIUS]
            if not near:
                self.tags.append("isolated")
        if g.won and t >= g.duration_ms - FINAL_PUSH_MS:
            self.tags.append("game_end")
        if g.has_timeline and _afk_after_death(g, self.k):
            self.tags.append("afk")

    def tag_macro(self) -> None:
        """fight_outnumbered, collapsed, baron_buff, duel_behind y bounty: macro con los relojes y duelos."""
        g, tags, t = self.g, self.tags, self.t
        excused = any(x in tags for x in CONTEXT_OK)
        # Macro con los relojes: pelear junto a un objetivo con uno menos, que te cacen solo en su mitad, morir solo con
        # la mejora del barón y regalar una recompensa grande.
        self.numbers = _numbers_down(g, self.k, self.cluster, self.pos) if g.has_timeline and not excused else None
        if self.numbers:
            tags.append("fight_outnumbered")
        if self._collapsed():
            tags.append("collapsed")
        if "isolated" in tags and _baron_buff(g, t):
            tags.append("baron_buff")
        # Duelo en desventaja: si te ganqueaban, lo que cuenta es el gank, no el nivel.
        self.duel = _duel_gap(g, self.k, self.enemies, self.fight) if g.has_timeline and not excused and "gank" not in tags else None
        if self.duel:
            tags.append("duel_behind")
        if not excused and self.shutdown >= BOUNTY_GIFT and any(x in tags for x in ("isolated", "collapsed", "enemy_jungle")):
            tags.append("bounty")

    def _collapsed(self) -> bool:
        """¿Te cazaron 3 o más rivales aislado en una banda de su mitad del mapa, sin ningún aliado cerca?"""
        g, t, pos = self.g, self.t, self.pos
        return bool(
            "isolated" in self.tags and pos and self.zone in ("top", "bot") and geo.side_of(pos) == 300 - g.team and len(self.enemies) >= 3
            and not any((ap := g.pos_at(a, t)) is not None and geo.dist(ap, pos) <= COLLAPSED_RADIUS for a in g.allies)
        )


def _classify_death(g: _Game, k: dict) -> dict:
    d = _Death(g, k)
    d.tag_who()
    d.tag_vision()
    d.tag_outcome()
    d.tag_context()
    d.tag_macro()
    title = _death_title(d)
    details = _death_details(d) + _death_details_macro(d)
    return _death_entry(d, title, details, _death_severity(d))


def _death_title(d: _Death) -> str:
    """Título de la muerte: lo que más la explica (AFK, final, pelea ganada, gank o uno menos; si no, el resto)."""
    g, tags = d.g, d.tags
    if "afk" in tags:
        return "Muerte antes de quedarte AFK o desconectado"
    if "game_end" in tags:
        return "Muerte en el empujón final"
    if "fight_won" in tags:
        return f"Caíste, pero ganasteis la pelea ({_score(d.fight)})"
    if "gank" in tags:
        names = [_champ_ref(g, pid) for pid in sorted(d.enemies)]
        return f"Gank de {_champ_ref(g, g.enemy_jungler)} (jungla rival)" if "jungler" in tags else f"Gank ({', '.join(names)})"
    if "fight_outnumbered" in tags:
        numbers = d.numbers
        return f"Muerte junto {_al(numbers['label'])} con uno menos ({numbers['allies']} contra {numbers['enemies']})"
    return _death_title_rest(d)


def _death_title_rest(d: _Death) -> str:
    """Título de una muerte sin gank ni pelea con uno menos: jungla rival, duelo, cazado, aislado, ejecutado o pelea."""
    g, tags = d.g, d.tags
    if "enemy_jungle" in tags and not (g.role == "JUNGLE" and d.fight["allies"] >= 2):
        return "Muerte en la jungla rival"
    if "solo" in tags:
        killer_name = _champ_ref(g, d.k["killer"]) if d.k["killer"] in g.players else "torre/súbditos"
        return f"Duelo perdido en desventaja contra {killer_name}" if d.duel else f"Muerte 1 contra 1 contra {killer_name}"
    if "collapsed" in tags:
        return f"Te cazaron solo en su mitad del mapa ({_plural(len(d.enemies), 'rival', 'rivales')})"
    if "isolated" in tags:
        return "Muerte aislado de tu equipo"
    if "executed" in tags:
        return "Ejecutado por torre o súbditos"
    return f"Muerte en pelea ({_plural(len(d.enemies), 'rival', 'rivales')})"


def _death_details(d: _Death) -> list[str]:
    """Detalle de la muerte: dónde, la pelea, adelantado, visión, oro, el objetivo de después y lo que sacasteis."""
    g, tags, fight = d.g, d.tags, d.fight
    details = []
    if d.zone:
        details.append(f"en {geo.zone_label(d.zone, g.team)}")
    if fight["size"] > 1 and "fight_won" not in tags:
        details.append(f"pelea {'igualada' if 'fight_even' in tags else 'perdida'} ({_score(fight)})")
    if "overextended" in tags:
        details.append("estabas adelantado, más cerca de su torre que de la tuya")
    if "no_ward" in tags:
        details.append("sin ward tuyo ni de un aliado cerca en los 2 minutos previos")
    if d.seen:
        details.append(_seen_text(g, d.seen, tags))
    if d.gold:
        details.append(_unspent_text(g, d.gold))
    if "before_objective" in tags:
        details += _lost_objective_texts(d)
    if d.gained:
        details.append(f"tu equipo cogió {d.gained['label']} a cambio")
    return details


def _death_details_macro(d: _Death) -> list[str]:
    """Detalle de macro de la muerte: aislado, cazado en su mitad, barón, duelo, recompensa, uno menos y AFK."""
    tags = d.tags
    details = []
    if "isolated" in tags and "fight_won" not in tags:
        details.append("sin ningún aliado cerca")
    if "collapsed" in tags:
        details.append(f"te cazaron {len(d.enemies)} rivales al otro lado del río: en su mitad del mapa, entra solo con visión y sabiendo dónde están")
    if "baron_buff" in tags:
        details.append("con la mejora del barón, que se aprovecha asediando en grupo o por dos líneas, no farmeando solo")
    if d.duel:
        details.append(_duel_text(d.g, d.duel))
    if "bounty" in tags:
        details.append(f"y regalaste {_int(d.shutdown)} de oro de recompensa")
    if d.numbers:
        details.append(_numbers_text(d.g, d.numbers))
    if "afk" in tags:
        details.append("al reaparecer ya no te moviste (desconexión o AFK): no se valora como error")
    return details


def _seen_text(g: _Game, seen: dict, tags: list[str]) -> str:
    """«una pink de X (puesta en el 4:10) te veía antes de la jugada» (y el consejo de la lente si no la usaste)."""
    kind, put = ("una pink", "puesta") if seen["kind"] == "CONTROL_WARD" else ("un ward", "puesto")
    lens = (", y llevabas la lente: barre antes de entrar en zonas donde suelen poner wards" if "lens_unused" in tags else "")
    return f"{kind} de {_champ_ref(g, seen['pid'])} ({put} en el {_mmss(seen['t'])}) te veía antes de la jugada{lens}"


def _unspent_text(g: _Game, gold: dict) -> str:
    """«con 1.450 de oro sin gastar: te llegaba para …»."""
    buy = f": {_buy_text(g, gold['item'], gold['cost'])}" if gold.get("cost") else ""
    return f"con {_int(gold['gold'])} de oro sin gastar{buy}"


def _lost_objective_texts(d: _Death) -> list[str]:
    """El objetivo que cogió el rival después y, si moriste junto al foso sin prioridad, por qué pelear ahí era a su favor."""
    g, lost, pos = d.g, d.lost, d.pos
    texts = [f"y el rival cogió {OBJ_LABEL[lost['kind']]} a continuación"]
    prio = _priority(g, lost["kind"], d.t) if pos and geo.dist(pos, lost["pos"]) <= OBJ_FIGHT_RADIUS else None
    if prio and prio["state"] == "theirs":
        texts.append(f"sin prioridad ({_prio_text(prio)}): llegaban antes y pelear ahí era a su favor")
    return texts


def _numbers_text(g: _Game, numbers: dict) -> str:
    """«peleasteis junto al dragón 4 contra 5, con X muerto desde antes de la pelea…»."""
    dead = _join([_champ_ref(g, a) for a in numbers["dead"]])
    return (
        f"peleasteis junto {_al(numbers['label'])} {numbers['allies']} contra {numbers['enemies']}, con {dead} "
        f"{'muerto' if len(numbers['dead']) == 1 else 'muertos'} desde antes de la pelea: con uno menos, cede el objetivo o cámbialo al otro lado"
    )


def _death_score(d: _Death) -> float:
    """Puntuación de la muerte: 1 de base, más lo que la agrava y menos lo que la justifica (siempre en este orden)."""
    g, tags = d.g, d.tags
    steps = (
        ("gank" in tags and "no_ward" in tags, 1.0),
        ("overextended" in tags, 0.8),
        # Antes de un objetivo pesa más morir suelto (dejas al equipo con uno menos) que caer en una pelea que perdisteis
        # varios: ahí el error es la pelea, no tu muerte.
        ("before_objective" in tags, 0.3 if d.fight["allies"] >= 2 else 1.0),
        ("unspent_gold" in tags, 0.5),
        ("enemy_jungle" in tags and "no_ward" in tags, 0.6),
        # Que te cojan solo (o en su jungla a mitad de partida) regala ventaja numérica para el siguiente objetivo.
        ("isolated" in tags and "fight_won" not in tags, 0.7),
        # Para el jungla, la jungla rival es su trabajo (invadir, contrajunglear, visión): no suma por sí sola.
        ("enemy_jungle" in tags and not d.early and "no_ward" not in tags and g.role != "JUNGLE", 0.5),
        ("fight_outnumbered" in tags, 0.8),
        ("collapsed" in tags, 0.5),
        ("baron_buff" in tags, 0.5),
        ("bounty" in tags, 0.3),
        ("duel_behind" in tags, 0.5),
    )
    score = 1.0
    for applies, value in steps:
        if applies:
            score += value
    if "fight_won" in tags:
        score -= 1.0
    elif "fight_even" in tags:
        score -= 0.5
    if "traded" in tags:
        score -= 0.7
    return score


def _death_severity(d: _Death) -> str:
    """Gravedad de la muerte según su puntuación (info si no es un error: empujón final o desconexión)."""
    score = _death_score(d)
    if "game_end" in d.tags or "afk" in d.tags:
        return "info"  # no es un error: se enseña para entender la partida
    if score >= 2.5:
        return "high"
    return "medium" if score >= 1.5 else "low"


def _death_entry(d: _Death, title: str, details: list[str], severity: str) -> dict:
    """La muerte para el análisis, con su momento y la certeza de las posiciones de los rivales implicados."""
    g, t, pos = d.g, d.t, d.pos
    entry = {
        "t": round(t / 1000, 1),
        "x": pos[0] if pos else None,
        "y": pos[1] if pos else None,
        "zone": d.zone,
        "early": d.early,
        "tags": d.tags,
        "killer": d.k["killer"],
        "enemies": sorted(d.enemies),
        "severity": severity,
        "fight": d.fight,
    }
    entry["moment"] = {
        "t": entry["t"],
        "kind": "death",
        "severity": severity,
        "title": title,
        "detail": _sentence(", ".join(details)) if details else "",
        "x": entry["x"],
        "y": entry["y"],
        "tags": d.tags,
    }
    # Certeza de las posiciones de los rivales implicados (gank, aislado, sin visión… dependen de dónde estaban).
    gaps = [x for e in d.enemies if (x := g.anchor_gap(e, t)) is not None]
    if gaps:
        entry["moment"]["ev"] = {"gap_rivales_s": max(gaps)}
    return entry


def _near_own_lane(lane: str | None, pos) -> bool:
    """¿Moriste en tu línea o en el río junto a ella? (sin posición no se puede afirmar)."""
    if not lane or pos is None:
        return False
    z = geo.zone(pos)
    if z == lane:
        return True
    return z == "river" and geo.dist_polyline(pos, geo.LANES[lane][0]) <= GANK_LANE_RADIUS


def _outsiders(g: _Game, enemies: set[int]) -> set[int]:
    """Rivales de la muerte que no son de tu línea.

    Si no se sabe quién es tu rival de línea (``teamPosition`` vacío o repetido), solo los seguros: el
    jungla rival o, si había más rivales que los de una línea, el grupo entero (seguro que vino alguien).
    """
    opps = g.natural_opponents()
    if opps is not None:
        return enemies - opps
    sure = {e for e in enemies if e == g.enemy_jungler and g.role != "JUNGLE"}
    lane_size = 1 if g.role in ("TOP", "MID", "JUNGLE") else 2
    return sure | (enemies if len(enemies) > lane_size else set())


def _had_vision(g: _Game, t: int, pos) -> bool:
    """¿Había un ward tuyo, o de un aliado que estaba cerca, todavía activo cuando moriste?

    Los WARD_PLACED no traen posición: un ward de un aliado cuenta si el aliado estaba a menos de
    ``VISION_NEAR`` de donde moriste al ponerlo (o no se sabe dónde estaba). Las pinks duran hasta que las
    destruyen: cuentan mientras el rival no haya destruido tantas como pusisteis.
    """
    me = g.me["pid"]
    return any(_ward_covers(g, pid, t, None if pid == me else pos) for pid in (me, *g.allies))


def _ward_covers(g: _Game, pid: int, t: int, pos) -> bool:
    """¿Seguía activo en ``t`` algún ward de ``pid`` puesto cerca de ``pos``? (con ``pos`` None vale cualquiera)."""
    for wt, kind in g.ward_events.get(pid) or []:
        if wt > t:
            break
        if not _ward_alive(g, pid, (wt, kind), t):
            continue
        if pos is None:
            return True
        where = g.pos_at(pid, wt)
        if where is None or geo.dist(where, pos) <= VISION_NEAR:
            return True
    return False


def _ward_alive(g: _Game, pid: int, ward: tuple[int, str], t: int) -> bool:
    """¿Seguía vivo en ``t`` el ward (momento, tipo) de ``pid``? Por su duración y, las pinks, si no las destruyeron."""
    wt, kind = ward
    if t - wt > WARD_LIFE_MS.get(kind, WARD_WINDOW_MS):
        return False
    return kind != "CONTROL_WARD" or _pinks_alive(g, pid, wt, t)


def _ward_map(g: _Game) -> list[dict]:
    """Wards de los dos equipos: quién, tipo, sitio estimado, desde y hasta cuándo, y quién lo quitó (``None``: caducó)."""
    cached = getattr(g, "_wards_cache", None)
    if cached is not None:
        return cached
    wards = _placed_wards(g)
    wards.sort(key=lambda w: w["t"])
    # Límite por jugador: la pink nueva quita la anterior; el cuarto ward sigiloso, el más antiguo.
    caps = ((("CONTROL_WARD",), WARD_MAX_ALIVE["CONTROL_WARD"]), (STEALTH_WARDS, WARD_MAX_ALIVE["STEALTH"]))
    for pid in g.players:
        for group, cap in caps:
            _cap_wards([w for w in wards if w["pid"] == pid and w["kind"] in group], cap)
    for t, killer, kind in sorted(g.ward_kill_events):
        if killer not in g.players:
            continue
        w = _killed_ward(g, wards, (t, killer, kind))
        if w is not None:
            w["end"], w["killer"] = t, killer
    g._wards_cache = wards
    return wards


def _placed_wards(g: _Game) -> list[dict]:
    """Wards y pinks puestos por los jugadores de la partida: quién, tipo, sitio estimado y hasta cuándo duran."""
    wards: list[dict] = []
    for pid, events in g.ward_events.items():
        if pid not in g.players:
            continue
        for t, kind in events:
            if kind not in STEALTH_WARDS and kind != "CONTROL_WARD":
                continue
            life = _ward_life(g, pid, t, kind)
            wards.append({
                "pid": pid, "team": g.players[pid]["team"], "kind": kind, "t": t, "pos": g.pos_at(pid, t),
                "end": min(t + life, g.duration_ms) if life else g.duration_ms, "killer": None,
            })
    return wards


def _ward_life(g: _Game, pid: int, t: int, kind: str) -> int | None:
    """Duración de un ward: el amarillo según el nivel, el de support la suya y la pink ``None`` (hasta que la quitan)."""
    if kind == "YELLOW_TRINKET":
        return 90_000 + 30_000 * (min(g.level_at(pid, t), 18) - 1) // 17
    return WARD_LIFE_MS.get(kind, WARD_WINDOW_MS) if kind != "CONTROL_WARD" else None


def _cap_wards(mine: list[dict], cap: int) -> None:
    """Con ``cap`` wards vivos del mismo jugador (ordenados), el siguiente que pone quita el más antiguo."""
    for i, w in enumerate(mine):
        alive = [x for x in mine[:i] if x["end"] > w["t"]]
        if len(alive) >= cap:
            alive[0]["end"] = w["t"]


def _killed_ward(g: _Game, wards: list[dict], kill: tuple[int, int, str]) -> dict | None:
    """El ward rival que cayó en un WARD_KILL (momento, quién, tipo): el del tipo más cerca de quien lo quitó."""
    t, killer, kind = kill
    kpos = g.pos_at(killer, t)
    team = g.players[killer]["team"]
    cands = [
        w for w in wards
        if w["team"] != team and w["t"] <= t < w["end"] and w["killer"] is None
        and (w["kind"] == kind or kind not in STEALTH_WARDS + ("CONTROL_WARD",))
    ]
    if not cands:
        return None
    if kpos is None:
        return cands[0]
    near = [(geo.dist(kpos, w["pos"]), w) for w in cands if w["pos"] is not None]
    near = [x for x in near if x[0] <= WARD_KILL_MATCH]
    if not near:
        return None
    return min(near, key=lambda x: x[0])[1]


def _trinket(g: _Game, pid: int, t: int) -> str | None:
    """Trinket que llevaba en ``t`` (el último comprado): ``ward``, ``farsight`` o ``lens``."""
    out = None
    for ts, item in g.purchases.get(pid) or []:
        if ts > t:
            break
        out = TRINKETS.get(item, out)
    return out


def _seen_by_ward(g: _Game, t: int) -> dict | None:
    """Ward rival vivo que te veía en los 15 s antes de morir (por tu recorrido) y que no quitaste."""
    me = g.me["pid"]
    for ts in range(t - SEEN_BEFORE_MS, t + 1, 2_500):
        p = g.pos_at(me, ts) if ts < t else None
        if p is None:
            continue
        for w in _ward_map(g):
            if w["team"] != g.team and w["pos"] is not None and w["t"] <= ts < w["end"] and geo.dist(p, w["pos"]) <= SEEN_BY_WARD:
                return w
    return None


def _enemy_pinks(g: _Game, moments: list) -> dict:
    """Pinks rivales que revelan tu jungla 3 min o más. Si pasaste a su lado (una vez basta: tu posición cada 5 s a
    menos de ``ENEMY_PINK_PASS``, con la pink puesta desde hace 20 s o más) es error en cualquier rol (el mid que roamea
    y pasa por al lado); si no, solo en jungla y support (limpiar vuestra jungla es parte de vuestro trabajo): a quien no
    pasa por donde afecta no se le dice nada."""
    out = {"enemy_pinks_own_jungle": 0, "enemy_pinks_long": 0, "enemy_pinks_ignored": 0}
    if not g.has_timeline:
        return out
    for w in _ward_map(g):
        if w["team"] == g.team or w["kind"] != "CONTROL_WARD" or w["pos"] is None:
            continue
        if not geo.is_jungle_of(geo.zone(w["pos"]), g.team):
            continue
        out["enemy_pinks_own_jungle"] += 1
        if w["end"] - w["t"] < ENEMY_PINK_LONG_MS:
            continue
        out["enemy_pinks_long"] += 1
        _long_enemy_pink(g, w, out, moments)
    return out


def _long_enemy_pink(g: _Game, w: dict, out: dict, moments: list) -> None:
    """Pink rival que vivió 3 min o más en tu jungla: error si pasaste a su lado; si no, aviso para jungla y support."""
    me = g.me["pid"]
    passed = next(
        (ts for ts in range(w["t"] + ENEMY_PINK_SETTLE_MS, w["end"] - ENEMY_PINK_AFTER_MS, 5_000)
         if (p := g.pos_at(me, ts)) is not None and geo.dist(p, w["pos"]) <= ENEMY_PINK_PASS),
        None,
    )
    alive = (w["end"] - w["t"]) // 60_000
    end = _pink_end_text(g, w)
    where = geo.zone_label(geo.zone(w["pos"]), g.team)
    why = "les daba la visión de tu jungla (tus campamentos, tus rutas, quién entra al río)"
    if passed is not None:
        out["enemy_pinks_ignored"] += 1
        moments.append(_moment(
            passed, "vision", "medium" if w["end"] - passed >= 2 * ENEMY_PINK_LONG_MS else "low",
            "Pink rival en tu jungla y pasas a su lado sin quitarla",
            f"{_champ_ref(g, w['pid'])} puso una pink en {where} en el {_mmss(w['t'])}. Pasaste a su lado en el "
            f"{_mmss(passed)} y siguió viva {alive} min en total, {end}: {why}. Las pinks se ven sin lente: quítala al pasar (unos 3 golpes).",
            w["pos"], ["vision", "enemy_pink", "passed"],
        ))
    elif g.role in ("JUNGLE", "SUPPORT"):
        moments.append(_moment(
            w["t"], "vision", "low",
            f"Pink rival {alive} min en tu jungla",
            f"{_champ_ref(g, w['pid'])} puso una pink en {where} en el {_mmss(w['t'])} y siguió viva {alive} min, {end}: {why}. "
            "Al volver de base, jungla y support: revisad las entradas a vuestra jungla y los arbustos del río de vuestro lado.",
            w["pos"], ["vision", "enemy_pink"],
        ))


def _pink_end_text(g: _Game, w: dict) -> str:
    """Hasta cuándo siguió viva una pink rival: quién la quitó y cuándo, el final de la partida o que nadie la quitó."""
    if w["killer"] == g.me["pid"]:
        who = "tú"
    else:
        who = _champ_ref(g, w["killer"]) if w["killer"] else None
    if who:
        return f"hasta que la quitó {who} en el {_mmss(w['end'])}"
    return "hasta el final de la partida" if w["end"] >= g.duration_ms else "sin que nadie la quitara"


def _pinks_alive(g: _Game, pid: int, since: int, t: int) -> bool:
    """¿Seguía viva en ``t`` la pink que ``pid`` puso en ``since``?

    El timeline no dice qué pink destruyó el rival. Lo prudente (nunca dar por muerta una pink que pudo seguir viva)
    es suponer que cada pink destruida era otra de tu equipo que siguiera en pie: esta solo cae si era la única.
    Si el mismo jugador puso otra después, la primera ya no está (solo puede haber una pink suya en el mapa).
    """
    if any(since < wt <= t for wt, kind in g.ward_events.get(pid) or [] if kind == "CONTROL_WARD"):
        return False
    events = [
        (wt, 1, p) for p in (g.me["pid"], *g.allies) for wt, kind in g.ward_events.get(p) or [] if kind == "CONTROL_WARD" and wt <= t
    ]
    events += [(x, 0, None) for x in g.pinks_lost.get(g.team) or [] if x <= t]
    others = 0
    alive = False
    for when, placed, owner in sorted(events, key=lambda e: (e[0], e[1])):  # a la vez, la destrucción va antes
        if placed:
            if not alive and owner == pid and when == since:
                alive = True
            else:
                others += 1
        elif others:
            others -= 1
        elif alive:
            return False
    return alive


def _overextended(g: _Game, lane: str, pos, t: int) -> bool:
    """Adelantado frente a las torres exteriores de tu línea.

    Si su torre exterior ya cayó, la línea se juega más adelante (hacia su torre interior) y no se puede
    juzgar con las exteriores.
    """
    enemy = 300 - g.team
    if any(b["kind"] == "tower" and b["owner"] == enemy and b["lane"] == lane and b["t"] < t for b in g.buildings):
        return False
    return geo.overextended(g.team, lane, pos)


def _death_affects(g: _Game, pos, o: dict) -> bool:
    """¿Pudo tu muerte costar ese objetivo? Si te tocaba disputarlo o si moriste cerca de él."""
    return _contest_expected(g, o) or (pos is not None and geo.dist(pos, o["pos"]) <= OBJ_RELEVANT_RADIUS)


def _afk_after_death(g: _Game, k: dict) -> bool:
    """¿Te quedaste AFK nada más reaparecer de esta muerte? (una desconexión: la muerte no se juzga)."""
    me = g.me["pid"]
    end = next((e for s, e, _ in g.dead.get(me) or [] if s == k["t"]), None)
    if end is None:
        return False
    return any(end - 5_000 <= s <= end + AFK_AFTER_RESPAWN_MS for s, _ in g.afk.get(me) or [])


def _numbers_down(g: _Game, k: dict, cluster: list[dict], pos) -> dict | None:
    """¿Moriste peleando junto a un objetivo con uno menos?

    El objetivo estaba vivo (según los relojes) o el rival lo cogió en el minuto siguiente; 10 s antes de la
    primera kill de la pelea el rival tenía más jugadores presentes y un aliado tuyo llevaba ya ≥ 10 s muerto
    (la inferioridad se sabía antes de pelear: no la creó la propia pelea). No cuenta en tu base ni con el
    ancestral o el alma del rival en juego (esos hay que disputarlos aunque sea con uno menos).
    """
    if not g.clocks_ok or pos is None or geo.zone(pos).startswith("base_"):
        return None
    found = _pit_objective(g, k["t"], pos)
    if not found:
        return None
    kind, taken = found
    if _must_contest(g, kind, k["t"]):
        return None
    before = min(f["t"] for f in cluster) - 10_000
    allies_up, enemies_up = _present_counts(g, before)
    if enemies_up <= allies_up:
        return None
    dead = [a for a in g.allies if any(s <= before - OUTNUMBERED_DEAD_MS and before < e for s, e, _ in g.dead.get(a) or [])]
    if not dead:
        return None
    label = _obj_label(g, taken) if taken else OBJ_LABEL[kind]
    return {"kind": kind, "label": label, "allies": allies_up, "enemies": enemies_up, "dead": dead}


def _pit_objective(g: _Game, t: int, pos) -> tuple[str, dict | None] | None:
    """Objetivo del foso junto al que moriste: vivo según los relojes o cogido por el rival en el minuto siguiente."""
    for pit_kind in ("dragon", "baron"):
        pit = geo.PITS[pit_kind]
        if geo.dist(pos, pit) > OBJ_FIGHT_RADIUS:
            continue
        taken = next(
            (o for o in g.objectives if o["team"] != g.team and t - 5_000 <= o["t"] <= t + 60_000 and clk.pit_of(o["kind"]) == pit),
            None,
        )
        alive = [x for x in clk.alive_at(g.objectives, g.clocks, t) if clk.pit_of(x) == pit]
        if taken or alive:
            return (taken["kind"] if taken else alive[0], taken)
    return None


def _must_contest(g: _Game, kind: str, t: int) -> bool:
    """¿Ancestral o alma del rival en juego? Hay que disputarlos aunque sea con uno menos."""
    enemy_dragons = sum(1 for x in g.objectives if x["kind"] == "dragon" and x["team"] != g.team and x["t"] < t)
    return kind == "elder" or (kind == "dragon" and enemy_dragons >= 3)


def _present_counts(g: _Game, t: int) -> tuple[int, int]:
    """Cuántos de tu equipo (contigo) y cuántos rivales estaban vivos y conectados en ``t``."""
    allies_up = sum(1 for a in (g.me["pid"], *g.allies) if g.present(a, t))
    enemies_up = sum(1 for e in g.enemies if g.present(e, t))
    return allies_up, enemies_up


def _duel_gap(g: _Game, k: dict, enemies: set[int], fight: dict) -> dict | None:
    """¿Perdiste un duelo (1 contra 1, sin muerte mutua) peleando con desventaja de nivel, objetos o definitiva?

    Niveles exactos (eventos LEVEL_UP). Salta si el rival te sacaba ≥ 2 niveles, si tenía un legendario más y ≥ 1.000
    de oro total más, o si tú no tenías la R (nivel < 6) y él sí. Nunca si en lo otro ibas tú por delante (más
    legendarios o más nivel): ahí la pelea tenía sentido. Los campeones cuya R no sigue la regla 6/11/16 no cuentan
    para lo de la definitiva.
    """
    if len(enemies) != 1 or fight["allies"] != 1 or fight["enemies"]:
        return None
    me, e = g.me["pid"], next(iter(enemies))
    t = k["t"] - DUEL_BEFORE_MS  # antes del golpe final: la experiencia de tu muerte ya le sube de nivel
    lv, elv = g.level_at(me, t), g.level_at(e, t)
    leg, eleg = g.legendaries_at(me, t), g.legendaries_at(e, t)
    items_known = leg is not None and eleg is not None
    i = g.frame_before(t)
    gold, egold = g.fval(me, i, "totalGold"), g.fval(e, i, "totalGold")
    gold_gap = egold - gold if gold is not None and egold is not None else None
    if items_known and leg > eleg:
        return None
    reasons = []
    if elv - lv >= DUEL_LEVEL_GAP:
        reasons.append("levels")
    if items_known and eleg > leg and gold_gap is not None and gold_gap >= DUEL_GOLD_GAP and lv <= elv:
        reasons.append("items")
    no_r = {g.me["champ"], g.players[e]["champ"]} & NO_R_CHECK
    if lv < ULT_LEVEL <= elv and not no_r and t < EARLY_MS:
        reasons.append("ult")
    if not reasons:
        return None
    return {"reasons": reasons, "level": lv, "enemy_level": elv, "items": leg, "enemy_items": eleg, "gold_gap": gold_gap, "enemy": e}


def _duel_text(g: _Game, gap: dict) -> str:
    who = _champ_ref(g, gap["enemy"])
    parts = []
    if "levels" in gap["reasons"]:
        diff = gap["enemy_level"] - gap["level"]
        parts.append(f"con {diff} niveles menos ({gap['level']} contra {gap['enemy_level']})")
    elif "ult" in gap["reasons"]:
        parts.append(f"sin la definitiva (nivel {gap['level']} contra su nivel {gap['enemy_level']})")
    if "items" in gap["reasons"]:
        n = gap["enemy_items"] - gap["items"]
        parts.append(f"con {'un legendario' if n == 1 else f'{n} legendarios'} menos y {_int(gap['gold_gap'])} de oro menos")
    return f"peleaste contra {who} {' y '.join(parts)}: compara nivel, objetos y definitivas antes de aceptar un 1 contra 1"


def _baron_buff(g: _Game, t: int) -> dict | None:
    """El barón que cogió tu equipo en los 3 minutos anteriores a ``t`` estando tú vivo (llevabas la mejora)."""
    me = g.me["pid"]
    return next(
        (o for o in g.objectives if o["kind"] == "baron" and o["team"] == g.team and 0 < t - o["t"] <= BARON_WINDOW_MS and not g.is_dead(me, o["t"])),
        None,
    )


def _same_season(catalog: ItemCatalog | None, info: dict) -> bool:
    """¿El catálogo de objetos es de la misma temporada que la partida? (si no se sabe, se da por bueno)."""
    mine, game = season(getattr(catalog, "version", None)), season(info.get("gameVersion"))
    return mine is None or game is None or mine == game


def _champ_ref(g: _Game, pid: int | None) -> str:
    """Referencia legible a un campeón (el nombre lo pone el frontend: «{champ:ID}»)."""
    if not pid or pid not in g.players:
        return "?"
    return f"{{champ:{g.players[pid]['champ']}}}"


def _buy_text(g: _Game, item: int | None, cost: int | None) -> str:
    """«te llegaba para completar X (1.450)» o «te llegaba para X (1.100)»."""
    if not item or not g.catalog:
        return "te llegaba para un componente"
    verb = "completar " if g.catalog.is_completed(item) and cost is not None and cost < g.catalog.cost(item) else ""
    return f"te llegaba para {verb}{g.catalog.name(item)} ({_int(cost)})"


# ------------------------------------------------------------------- oro
def _targets(g: _Game, t: int) -> list[int]:
    """Lo que compraste después (tu build): el siguiente objeto terminado y lo de antes."""
    cat = g.catalog
    later = [
        p for p in g.inv.bought(since=t)
        if p["cost"] >= 300 and not cat.is_consumable(p["item"]) and not cat.is_starter(p["item"]) and not cat.is_trinket(p["item"])
    ]
    nxt = next((p for p in later if cat.is_completed(p["item"])), None)
    out = [nxt["item"]] if nxt else []
    for p in later:
        if nxt and p["t"] > nxt["t"]:
            break
        if p["item"] not in out:
            out.append(p["item"])
    return out


def _spend(g: _Game, t: int, gold: int) -> dict:
    """Qué podías comprar en ``t`` con ``gold`` para avanzar en tu build (y con hueco)."""
    if g.inv is None:
        return {"known": False, "useful": gold if gold >= HOARD_GOLD else 0, "item": None, "cost": None, "free": None, "full": False, "completed": 0}
    cat = g.catalog
    inv = g.inv.at(t)
    free = g.inv.free_slots(inv)
    targets = _targets(g, t)
    if targets:
        opts = cat.options(targets, inv, free)
    else:  # no volviste a comprar: lo que combine con lo que llevas o, con hueco, un componente
        opts = cat.merges(inv, free)
        if free > 0:
            opts = [*opts, (0, GENERIC_COMPONENT)]
    affordable = [o for o in opts if o[1] <= gold]
    best = max(affordable, key=lambda o: o[1]) if affordable else None
    cheapest = min(opts, key=lambda o: o[1]) if opts else None
    return {
        "known": True,
        "useful": best[1] if best else 0,
        "item": best[0] if best else None,
        "cost": best[1] if best else None,
        "next": cheapest,
        "need": min((o[1] for o in affordable if o[1] >= USEFUL_SPEND), default=None),  # lo mínimo que ya era útil
        "free": free,
        "full": not opts and free == 0 and not targets,  # build terminada: ya no ibas a comprar nada
        "completed": len(g.inv.completed(inv)),
        "targets": targets,
    }


def _death_gold(g: _Game, t: int) -> dict | None:
    """Oro que ya llevabas 30 s antes de morir y que podías gastar en tu build.

    Solo con el inventario reconstruido de forma fiable: sin catálogo (o con objetos que no conoce) no se
    sabe si tenías hueco ni si ese oro era el ahorro para un objeto completo.
    """
    me = g.me["pid"]
    if not g.has_timeline or not g.items_reliable or (g.won and t >= g.duration_ms - FINAL_PUSH_MS):
        return None
    i = g.frame_before(t - 30_000)
    if i is None or g.ts[i] < 3 * 60_000 or g.is_dead(me, g.ts[i]):
        return None
    tf = g.ts[i]
    gold = g.fval(me, i, "currentGold")
    if gold is None or any(tf < pt <= t for pt, _ in g.purchases[me]):  # compraste entre medias
        return None
    s = _spend(g, tf, gold)
    if s["useful"] < USEFUL_SPEND:
        return None
    return {"gold": gold, "item": s["item"], "cost": s["cost"]}


def _busy_windows(g: _Game) -> list[tuple[int, int]]:
    """Momentos en los que no tocaba volver a base: peleas, objetivos, torres, final de partida."""
    me = g.me["pid"]
    out = []
    for k in g.kills:
        if me in (k["killer"], k["victim"], *k["assists"]):
            out.append((k["t"] - 25_000, k["t"] + 10_000))
    for o in g.objectives:
        p = g.pos_at(me, o["t"])
        if me in o["takers"] or (p is not None and geo.dist(p, o["pos"]) <= NEAR_OBJECTIVE):
            out.append((o["t"] - 60_000, o["t_end"] + 10_000))
    for b in g.buildings:
        if me in b["takers"]:
            out.append((b["t"] - 30_000, b["t"] + 10_000))
    out.append((g.duration_ms - (FINAL_PUSH_MS if g.won else 60_000), g.duration_ms + 1))
    out.sort()
    return _merge_windows(out)


def _merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Une las ventanas (ordenadas) que se solapan o se tocan."""
    merged: list[list[int]] = []
    for s, e in windows:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _overlap(windows: list[tuple[int, int]], t0: int, t1: int) -> int:
    return sum(max(0, min(e, t1) - max(s, t0)) for s, e in windows)


def _gold_since(g: _Game, i: int, row: dict) -> int:
    """Desde cuándo tenías el oro útil del frame ``i``: interpolando el oro entre el frame anterior y este.

    Como pronto 30 s antes del frame (no se penaliza más tiempo del que se puede asegurar).
    """
    t = g.ts[i]
    start = t - 30_000
    need = row.get("need")
    prev = g.fval(g.me["pid"], i - 1, "currentGold") if i > 0 else None
    if need is None or prev is None or prev >= need or row["gold"] <= prev:
        return start
    t_prev = g.ts[i - 1]
    if any(t_prev < pt <= t for pt, _ in g.purchases[g.me["pid"]]):  # compraste entre medias: no se puede interpolar
        return start
    return int(max(start, t_prev + (need - prev) / (row["gold"] - prev) * (t - t_prev)))


def _gold_use(g: _Game, metrics: dict, moments: list) -> None:
    """Oro que te daba para algo de tu build, con hueco, y que tardaste en gastar."""
    if not g.items_reliable:  # sin inventario fiable no se juzga el oro (ni para bien ni para mal)
        metrics["gold_hoard_min"] = None
        metrics["gold_saving_min"] = None
        return
    rows, flagged = _gold_rows(g)
    g.gold_rows = rows
    total = _gold_hoards(g, rows, flagged, moments)
    metrics["gold_hoard_min"] = round(total / 60_000, 1)
    # Ahorro legítimo: inventario lleno o build terminada (no cuenta como error).
    saving = [i for i, r in rows.items() if r["known"] and r["gold"] >= HOARD_GOLD and r["useful"] < USEFUL_SPEND]
    metrics["gold_saving_min"] = len(saving)
    full = [i for i, r in sorted(rows.items()) if r["known"] and r["full"] and r["completed"] >= 5 and r["gold"] >= HOARD_GOLD]
    full = [i for i in full if g.ts[i] < g.duration_ms - FINAL_PUSH_MS]
    run = next((r for r in _runs(full) if len(r) >= 2), None)
    if run:
        moments.append(
            _moment(
                g.ts[run[0]],
                "gold",
                "info",
                f"Build completa con {_int(rows[run[-1]]['gold'])} de oro sin uso",
                "Con el inventario lleno de objetos terminados ese oro no es un error, pero puedes convertirlo en elixires (500) y pinks antes de cada pelea u objetivo.",
                tags=["gold", "full_build"],
            )
        )


def _gold_rows(g: _Game) -> tuple[dict[int, dict], list[int]]:
    """Qué podías comprar en cada frame (vivo, desde el 3 y fuera de la fuente) y los frames con oro útil sin gastar."""
    me = g.me["pid"]
    rows: dict[int, dict] = {}
    flagged: list[int] = []
    for i, t in enumerate(g.ts):
        if t < 3 * 60_000 or not g.present(me, t):
            continue
        gold = g.fval(me, i, "currentGold")
        p = g.frame_pos(me, i)
        if gold is None or (p and geo.in_fountain(g.team, p)):
            continue
        s = _spend(g, t, gold)
        s["gold"] = gold
        rows[i] = s
        if s["useful"] >= USEFUL_SPEND:
            flagged.append(i)
    return rows, flagged


def _gold_hoards(g: _Game, rows: dict[int, dict], flagged: list[int], moments: list) -> int:
    """Ms de más con oro útil sin gastar (descontando peleas y objetivos); avisa de cada tramo de 40 s o más."""
    me = g.me["pid"]
    busy = _busy_windows(g)
    buys = sorted(pt for pt, _ in g.purchases[me])
    respawns = sorted(end for _, end, _ in g.dead.get(me) or [])
    deaths = sorted(start for start, _, _ in g.dead.get(me) or [])
    total = 0
    for run in _runs(flagged):
        t_first, t_last = g.ts[run[0]], g.ts[run[-1]]
        since = _gold_since(g, run[0], rows[run[0]])
        start = max([since, *[b for b in buys if b <= t_first][-1:], *[r for r in respawns if r <= t_first][-1:]])
        ends = [g.duration_ms, t_last + 60_000]
        ends += [b for b in buys if b > t_last][:1] + [d for d in deaths if d > t_last][:1]
        end = min(ends)
        excess = (end - start) - _overlap(busy, start, end) - HOARD_GRACE_MS
        if excess <= 0:
            continue
        total += excess
        g.hoard_frames.update(run)
        if excess >= 40_000:
            moments.append(_hoard_moment(g, [rows[i] for i in run], (start, end), excess))
    return total


def _hoard_moment(g: _Game, run_rows: list[dict], span: tuple[int, int], excess: int) -> dict:
    """Aviso de un tramo con oro sin gastar: qué te daba para comprar y cuánto tardaste de más en volver."""
    start, end = span
    best = max(run_rows, key=lambda r: r["useful"])
    peak = max(r["gold"] for r in run_rows)
    if best["known"]:
        what = _buy_text(g, best["item"], best["cost"])
        what += " y tenías hueco" if best["free"] else " con lo que llevabas"
    else:
        what = "te llegaba para un componente"
    return _moment(
        start,
        "gold",
        "medium" if excess >= 120_000 or best["useful"] >= 2500 else "low",
        f"{_secs(end - start)} con oro sin gastar (hasta {_int(peak)})",
        f"Del {_mmss(start)} al {_mmss(end)} {what}. Descontando peleas y objetivos, tardaste {_secs(excess)} de más en volver: "
        "vuelve a base al acabar la oleada o el campamento, ese oro no pelea.",
        tags=["gold"],
    )


# --------------------------------------------------------------- AFK
def _afk_moments(g: _Game, moments: list) -> None:
    me = g.me["pid"]
    for start, end in g.afk[me]:
        if end >= g.duration_ms - 1000 and end - start < 150_000:
            continue  # quieto en base esperando el final: no es una desconexión
        moments.append(
            _moment(
                start,
                "afk",
                "info",
                f"Desconexión o AFK: {_secs(end - start)} sin moverte",
                "Esos minutos no cuentan como minutos vacíos ni como oro sin gastar.",
                tags=["afk"],
            )
        )
    allies = [(a, g.afk_ms(a)) for a in g.allies if g.afk_ms(a) >= 120_000]
    for a, ms in allies:
        start = g.afk[a][0][0]
        moments.append(
            _moment(
                start,
                "afk",
                "info",
                f"{_champ_ref(g, a)} (tu equipo) estuvo AFK {_secs(ms)}",
                "Jugasteis en inferioridad: se tiene en cuenta al valorar los objetivos que perdisteis.",
                tags=["afk", "ally"],
            )
        )


# --------------------------------------------------------- fase de líneas
def _lane_phase(g: _Game, metrics: dict, moments: list) -> None:
    opp = g.opponent
    for minute in (10, 15):
        _lane_minute(g, metrics, minute)
    gd = metrics.get("gold_diff15")
    if g.role and g.role not in ("JUNGLE", "SUPPORT") and gd is not None and gd <= -1000:
        moments.append(
            {
                "t": 900.0,
                "kind": "lane",
                "severity": "medium" if gd > -2000 else "high",
                "title": f"Línea perdida: {gd:+,} de oro a los 15".replace(",", "."),
                "detail": f"Frente a {_champ_ref(g, opp)}: {metrics.get('cs_diff15') or 0:+} CS y {metrics.get('xp_diff15') or 0:+} de experiencia.",
                "x": None,
                "y": None,
                "tags": ["lane"],
            }
        )


def _lane_minute(g: _Game, metrics: dict, minute: int) -> None:
    """Tu CS a ese minuto y las diferencias de CS, oro y experiencia con tu rival de línea."""
    me, opp = g.me["pid"], g.opponent
    # Con una desconexión (tuya o de tu rival de línea) antes de ese minuto la comparación no dice nada.
    limit = minute * 60_000
    mine_afk = any(s < limit for s, _ in g.afk.get(me) or [])
    opp_afk = bool(opp) and any(s < limit for s, _ in g.afk.get(opp) or [])
    i = g.frame_at_minute(minute)
    cs = None if mine_afk else g.fval(me, i, "cs")
    metrics[f"cs{minute}"] = cs
    if opp and not mine_afk and not opp_afk:
        ocs, gold, ogold = g.fval(opp, i, "cs"), g.fval(me, i, "totalGold"), g.fval(opp, i, "totalGold")
        xp, oxp = g.fval(me, i, "xp"), g.fval(opp, i, "xp")
        metrics[f"cs_diff{minute}"] = _difference(cs, ocs)
        metrics[f"gold_diff{minute}"] = _difference(gold, ogold)
        metrics[f"xp_diff{minute}"] = _difference(xp, oxp)
    else:
        metrics[f"cs_diff{minute}"] = metrics[f"gold_diff{minute}"] = metrics[f"xp_diff{minute}"] = None


def _difference(a: int | None, b: int | None) -> int | None:
    """``a - b`` o ``None`` si falta alguno de los dos."""
    return a - b if a is not None and b is not None else None


# --------------------------------------------------------------- objetivos
def _obj_value(g: _Game, o: dict) -> int:
    kind = o["kind"]
    if kind == "grubs":
        return OBJ_VALUE["grubs"] * o.get("count", 1)
    if kind == "dragon":
        prior = sum(1 for x in g.objectives if x["kind"] == "dragon" and x["team"] == o["team"] and x["t"] < o["t"])
        if prior >= 3:
            return 2500
        return 1400 if prior == 2 else OBJ_VALUE["dragon"]
    return OBJ_VALUE.get(kind, 800)


def _obj_label(g: _Game, o: dict) -> str:
    if o["kind"] == "dragon":
        prior = sum(1 for x in g.objectives if x["kind"] == "dragon" and x["team"] == o["team"] and x["t"] < o["t"])
        if prior >= 3:
            return "el alma del dragón"
        if prior == 2:
            return "el tercer dragón (punto de alma)"
    return OBJ_LABEL[o["kind"]]


def _exchange(g: _Game, o: dict, used: set[int] | None = None) -> dict:
    """Qué ganó y qué perdió tu equipo alrededor de un objetivo del rival.

    ``used``: lo que ya compensó otro objetivo (una misma torre no paga dos objetivos). ``ids`` devuelve lo que
    entra en este cambio, para marcarlo si se da por bueno.
    """
    used = used if used is not None else set()
    window = (o["t"] - TRADE_BEFORE_MS, o["t"] + TRADE_AFTER_MS)
    ex = {
        "gain": {"kills": 0, "towers": 0, "inhibitors": 0, "plates": 0, "objectives": [], "jungle_cs": 0},
        "mine": {"takedowns": 0, "towers": 0, "plates": 0, "jungle_cs": 0, "objectives": []},
        # Lo que además sacó el rival en esa ventana (también cuenta en la balanza y se explica en el texto).
        "loss": {"kills": 0, "towers": 0, "inhibitors": 0, "plates": 0},
        "value_gain": 0,
        "value_loss": _obj_value(g, o),
        "ids": [],
    }
    _exchange_kills(g, window, used, ex)
    _exchange_buildings(g, window, used, ex)
    _exchange_objectives(g, o, window, used, ex)
    _exchange_jungle(g, window, ex)
    value_gain, value_loss = ex["value_gain"], ex["value_loss"]
    return {
        "gain": ex["gain"], "mine": ex["mine"], "loss": ex["loss"], "value_gain": value_gain, "value_loss": value_loss,
        "ratio": value_gain / max(1, value_loss), "ids": ex["ids"],
    }


def _exchange_kills(g: _Game, window: tuple[int, int], used: set[int], ex: dict) -> None:
    """Kills de la ventana: las de rivales suman a tu equipo (si no pagaron ya otro objetivo); las vuestras, al rival."""
    me, team = g.me["pid"], g.team
    t0, t1 = window
    for k in g.kills:
        if not t0 <= k["t"] <= t1:
            continue
        if g.players[k["victim"]]["team"] == team:
            ex["value_loss"] += k["value"]
            ex["loss"]["kills"] += 1
            continue
        if id(k) in used:
            continue
        ex["ids"].append(id(k))
        ex["gain"]["kills"] += 1
        ex["value_gain"] += k["value"]
        ex["mine"]["takedowns"] += int(me == k["killer"] or me in k["assists"])


def _exchange_buildings(g: _Game, window: tuple[int, int], used: set[int], ex: dict) -> None:
    """Estructuras de la ventana: las vuestras suman al rival y las suyas a tu equipo (sin contar dos veces las placas)."""
    me, team = g.me["pid"], g.team
    t0, t1 = window
    ours, theirs = [], []
    for b in g.buildings:
        if not t0 <= b["t"] <= t1:
            continue
        key = {"tower": "towers", "inhibitor": "inhibitors", "plate": "plates"}[b["kind"]]
        if b["owner"] == team:
            ours.append(b)
            ex["loss"][key] += 1
            continue
        if id(b) in used:
            continue
        ex["ids"].append(id(b))
        theirs.append(b)
        ex["gain"][key] += 1
        if me in b["takers"]:
            ex["mine"]["plates" if b["kind"] == "plate" else "towers"] += 1
    ex["value_loss"] += _structures_value(ours)
    ex["value_gain"] += _structures_value(theirs)


def _exchange_objectives(g: _Game, o: dict, window: tuple[int, int], used: set[int], ex: dict) -> None:
    """Otros objetivos de tu equipo en la ventana (los que no pagaron ya otro objetivo)."""
    me = g.me["pid"]
    t0, t1 = window
    for x in g.objectives:
        if x is o or x["team"] != g.team or not t0 <= x["t"] <= t1 or id(x) in used:
            continue
        ex["ids"].append(id(x))
        ex["gain"]["objectives"].append(x["kind"])
        ex["value_gain"] += _obj_value(g, x)
        if me in x["takers"]:
            ex["mine"]["objectives"].append(x["kind"])


def _exchange_jungle(g: _Game, window: tuple[int, int], ex: dict) -> None:
    """CS que hizo tu equipo en la jungla rival durante la ventana (y 30 s más)."""
    me = g.me["pid"]
    t0, t1 = window
    for pid in (me, *g.allies):
        cs = g.enemy_jungle_cs(pid, t0, t1 + 30_000)
        ex["gain"]["jungle_cs"] += cs
        if pid == me:
            ex["mine"]["jungle_cs"] = cs
    ex["value_gain"] += ex["gain"]["jungle_cs"] * ENEMY_JUNGLE_CS_VALUE


def _de(label: str) -> str:
    """«de» + etiqueta con la contracción: «del heraldo», «de las larvas»."""
    return f"del {label[3:]}" if label.startswith("el ") else f"de {label}"


def _al(label: str) -> str:
    """«a» + etiqueta con la contracción: «al dragón», «a las larvas»."""
    return f"al {label[3:]}" if label.startswith("el ") else f"a {label}"


def _gain_text(gain: dict) -> str:
    parts = [OBJ_LABEL[k] for k in gain.get("objectives") or []]
    if gain.get("kills") or gain.get("takedowns"):
        n = gain.get("kills", gain.get("takedowns"))
        parts.append(_plural(n, "kill", "kills"))
    if gain.get("towers"):
        parts.append(_plural(gain["towers"], "torre", "torres"))
    if gain.get("inhibitors"):
        parts.append(_plural(gain["inhibitors"], "inhibidor", "inhibidores"))
    if gain.get("plates"):
        parts.append(_plural(gain["plates"], "placa", "placas"))
    if gain.get("jungle_cs", 0) >= 4:
        parts.append(f"{gain['jungle_cs']} CS de su jungla")
    return _join(parts)


def _is_big(g: _Game, o: dict) -> bool:
    return o["kind"] in ("baron", "elder") or _obj_label(g, o).startswith(SOUL_LABEL)


def _contest_expected(g: _Game, o: dict) -> bool:
    """¿Te tocaba disputar ese objetivo?

    Los grandes (barón, ancestral, alma) siempre, y al jungla y al mid todos. Un objetivo al otro lado del
    mapa no es cosa de tu línea (top con los dragones; ADC y support con las larvas o el heraldo) mientras
    dura la fase de líneas: hasta el 14 nunca, y hasta el 20 solo si ya cayó una torre exterior de tu línea
    (entonces se rota; antes, el heraldo de las 15:00 llega con el ADC y el support aún en su línea). Si no
    se sabe tu rol, antes del 14 solo los grandes.
    """
    if _is_big(g, o) or g.role in ("JUNGLE", "MID"):
        return True
    if not g.role:
        return o["t"] >= EARLY_MS
    side = "top" if o["kind"] in ("grubs", "herald") else "bot"
    if ROLE_SIDE.get(g.role) == side or o["t"] >= LANE_PHASE_END_MS:
        return True
    if o["t"] < EARLY_MS:
        return False
    lane = geo.ROLE_LANE.get(g.role)
    return any(
        b["kind"] == "tower" and b["lane"] == lane and b.get("tower_type") in ("OUTER_TURRET", None) and b["t"] < o["t"] for b in g.buildings
    )


def _lane_cs_diff(g: _Game, lane: str, t: int) -> int | None:
    """CS de tu jugador de esa línea menos el de su rival en el último frame antes de ``t`` (bot: los ADC)."""
    ally, enemy = g._by_role(g.team, LANE_ROLE[lane]), g._by_role(300 - g.team, LANE_ROLE[lane])
    if not ally or not enemy or any(s < t for p in (ally, enemy) for s, _ in g.afk.get(p) or []):
        return None  # rol desconocido o alguien desconectado: no es una comparación justa
    i = g.frame_before(t)
    a, b = g.fval(ally, i, "cs"), g.fval(enemy, i, "cs")
    return a - b if a is not None and b is not None else None


def _priority(g: _Game, kind: str, t: int) -> dict | None:
    """Prioridad de las dos líneas junto a un objetivo: ``ours`` si las dos van ≥ +5 CS, ``theirs`` si ≤ −5."""
    lanes = PRIO_LANES.get(kind)
    if not lanes:
        return None
    diffs = {lane: _lane_cs_diff(g, lane, t) for lane in lanes}
    if any(v is None for v in diffs.values()):
        return None
    return {"lanes": diffs, "state": _prio_state(list(diffs.values()))}


def _prio_state(vals: list[int]) -> str:
    """``ours`` si todas las líneas van ≥ +5 CS, ``theirs`` si todas van ≤ −5 y ``mixed`` si no."""
    if all(v >= PRIO_CS for v in vals):
        return "ours"
    return "theirs" if all(v <= -PRIO_CS for v in vals) else "mixed"


def _prio_text(prio: dict) -> str:
    """«tu mid iba −12 CS y tu ADC −8 CS frente a los suyos»."""
    parts = [f"tu {LANE_NAME[lane]} iba {v:+d} CS" for lane, v in prio["lanes"].items()]
    return f"{_join(parts)} frente a los suyos"


def _priority_stats(g: _Game, metrics: dict) -> None:
    """Dragones normales según la prioridad de mid y bot, y la primera torre (para «Prioridad y objetivos»)."""
    counts = {"ours": [0, 0], "mixed": [0, 0], "theirs": [0, 0]}
    seen = {100: 0, 200: 0}
    for o in g.objectives:
        if o["kind"] != "dragon":
            continue
        soulish = max(seen.values()) >= 2  # punto de alma o alma: se pelean distinto
        seen[o["team"]] = seen.get(o["team"], 0) + 1
        if soulish or g.is_afk(g.me["pid"], o["t"]):
            continue
        prio = _priority(g, "dragon", o["t"])
        if prio:
            counts[prio["state"]][0] += 1
            counts[prio["state"]][1] += int(o["team"] == g.team)
    for state, key in (("ours", "prio"), ("mixed", "mixed"), ("theirs", "noprio")):
        metrics[f"drag_{key}_n"], metrics[f"drag_{key}_won"] = counts[state]
    towers = sorted((b for b in g.buildings if b["kind"] == "tower"), key=lambda b: b["t"])
    metrics["first_tower_ours"] = int(towers[0]["owner"] != g.team) if towers else None
    ours = next((b["t"] for b in towers if b["owner"] != g.team), None)
    metrics["our_first_tower_min"] = round(ours / 60_000, 1) if ours is not None else None
    _grubs_priority(g, metrics)
    metrics["obj_noprio_deaths"] = _noprio_deaths(g)


PRIO_KEY = {"ours": "prio", "mixed": "mixed", "theirs": "noprio"}
NOPRIO_DEATH_MS = 45_000  # tu muerte y el objetivo para el rival en los 45 s siguientes: una jugada
NOPRIO_NEAR = 3_000  # cerca del foso


def _grubs_priority(g: _Game, metrics: dict) -> None:
    """C-2 (``macro/macro_internet_por_elo.md``): las larvas (la primera, que es cuando se pelean) según la prioridad de
    top y mid, como los dragones con mid y bot. Desde el 25-11-2026, sin cambiar la versión del análisis: solo las
    partidas nuevas lo traen (se compara por elo cuando haya bastantes)."""
    for key in PRIO_KEY.values():
        metrics[f"grubs_{key}_n"] = metrics[f"grubs_{key}_won"] = 0
    first = next((o for o in g.objectives if o["kind"] == "grubs"), None)
    if first is None or g.is_afk(g.me["pid"], first["t"]):
        return
    prio = _priority(g, "grubs", first["t"])
    if prio:
        key = PRIO_KEY[prio["state"]]
        metrics[f"grubs_{key}_n"] = 1
        metrics[f"grubs_{key}_won"] = int(first["team"] == g.team)


def _noprio_deaths(g: _Game) -> int:
    """C-2: tus muertes junto al foso en los 45 s antes de que el rival coja un dragón o unas larvas para los que no
    teníais prioridad (las dos líneas de ese lado por detrás en CS): pelear sin prioridad y perderlo."""
    n = 0
    for k in g.kills:
        if k["victim"] != g.me["pid"] or not k.get("pos"):
            continue
        lost = next((o for o in g.objectives if o["kind"] in ("dragon", "grubs") and o["team"] != g.team
                     and k["t"] <= o["t"] <= k["t"] + NOPRIO_DEATH_MS and geo.dist(k["pos"], clk.pit_of(o["kind"])) <= NOPRIO_NEAR), None)
        if lost is not None and (_priority(g, lost["kind"], lost["t"]) or {}).get("state") == "theirs":
            n += 1
    return n


def _contributed(mine: dict) -> bool:
    """¿Participaste tú en lo que sacó tu equipo a cambio?"""
    return bool(mine["takedowns"] or mine["towers"] or mine["plates"] or mine["objectives"] or mine["jungle_cs"] >= 4)


def _objectives(g: _Game, metrics: dict, moments: list) -> None:
    me = g.me["pid"]
    counts: Counter = Counter()  # taken, participated, lost_dead, conceded, trades, fair y no_prio
    my_deaths = [k["t"] for k in g.kills if k["victim"] == me]
    for o in g.objectives:
        _objective(g, o, my_deaths, counts, moments)
    taken = counts["taken"]
    metrics["objectives_team"] = taken
    if taken:
        metrics["obj_participation"] = round(counts["participated"] / taken * 100, 1)
    metrics["obj_lost_dead"] = counts["lost_dead"]
    metrics["obj_lost_far"] = counts["conceded"]
    metrics["obj_lost_noprio"] = counts["no_prio"]  # dragones cedidos sin prioridad en mid y bot: no cuentan como regalados
    metrics["obj_trades"] = counts["trades"]  # solo buenos cambios en los que participaste
    metrics["obj_fair_trades"] = counts["fair"]


def _objective(g: _Game, o: dict, my_deaths: list[int], counts: Counter, moments: list) -> None:
    """Un objetivo: cuenta si fue de tu equipo; si lo cogió el rival y te tocaba disputarlo, lo juzga."""
    me = g.me["pid"]
    p = g.pos_at(me, o["t"])
    here = _was_at(g, o, p)
    if g.is_afk(me, o["t"]):
        return
    if o["team"] == g.team:
        counts["taken"] += 1
        counts["participated"] += int(here)
        return
    if here or not _contest_expected(g, o):
        return  # estabas allí, o no te tocaba disputarlo: ni error ni mérito tuyo
    ex = _exchange(g, o, g.used_gains)
    if ex["ratio"] >= FAIR_TRADE and ex["value_gain"]:
        g.used_gains.update(ex["ids"])  # esto ya compensa este objetivo: no puede pagar otro
    if g.is_dead(me, o["t"]):
        _objective_while_dead(g, o, ex, my_deaths, counts, moments)
    else:
        _objective_far(g, o, ex, p, my_deaths, counts, moments)


def _was_at(g: _Game, o: dict, p) -> bool:
    """¿Estabas en el objetivo? (entre quienes lo cogieron o a menos de ``NEAR_OBJECTIVE`` cuando cayó)."""
    here = g.me["pid"] in o["takers"] if o["assists_known"] else False
    if not here and p is not None and geo.dist(p, o["pos"]) <= NEAR_OBJECTIVE:
        here = True
    return here


def _died_before(o: dict, my_deaths: list[int]) -> bool:
    """¿Moriste en los 90 s antes del objetivo? (entonces ya lo cuenta esa muerte)."""
    return any(o["t"] - OBJECTIVE_WINDOW_MS <= d <= o["t"] for d in my_deaths)


def _objective_while_dead(g: _Game, o: dict, ex: dict, my_deaths: list[int], counts: Counter, moments: list) -> None:
    """El rival cogió el objetivo mientras estabas muerto: compensado por tu equipo (info) o perdido por tu muerte."""
    label = _obj_label(g, o)
    if ex["ratio"] >= GOOD_TRADE:  # lo compensó tu equipo (no cuenta como cambio tuyo)
        g.flagged.add(id(o))
        moments.append(
            _moment(o["t"], "trade", "info", f"El rival cogió {label} mientras estabas muerto, pero tu equipo lo compensó",
                    f"Mientras tanto sacasteis {_gain_text(ex['gain'])}.", o["pos"], ["objective", "dead", "trade"])
        )
        return
    counts["lost_dead"] += 1
    if _died_before(o, my_deaths):
        return  # la muerte de antes ya lo cuenta
    g.flagged.add(id(o))
    moments.append(
        _moment(o["t"], "objective", "high" if o["kind"] in ("baron", "elder", "dragon") else "medium",
                f"El rival cogió {label} mientras estabas muerto", "Una muerte antes de un objetivo se paga doble: no puedes disputarlo.",
                o["pos"], ["objective", "dead"])
    )


def _objective_far(g: _Game, o: dict, ex: dict, p, my_deaths: list[int], counts: Counter, moments: list) -> None:
    """El rival cogió un objetivo que te tocaba estando tú vivo y lejos: cambio, inferioridad, sin prioridad o cedido."""
    if not _far_from(g, o, p):
        return
    if _died_before(o, my_deaths):
        return  # acababas de morir y volvías de la base: la muerte de antes ya lo cuenta
    if _objective_trade(g, o, ex, p, counts, moments):
        return
    label = _obj_label(g, o)
    allies_up, enemies_up = _present_counts(g, o["t"])
    if enemies_up - allies_up >= 2:
        g.flagged.add(id(o))
        moments.append(
            _moment(o["t"], "objective", "info", f"El rival cogió {label} con ventaja numérica ({allies_up} contra {enemies_up})",
                    "No se podía disputar: lo que costó el objetivo fueron las muertes de antes.", p, ["objective", "outnumbered"])
        )
        return
    prio = _priority(g, o["kind"], o["t"])
    if o["kind"] == "dragon" and not label.startswith(SOUL_LABEL) and "punto de alma" not in label and prio and prio["state"] == "theirs":
        # Sin prioridad en las dos líneas del dragón, cederlo es lo normal (en Challenger lo pierde el 53 % de los
        # equipos así; en Esmeralda, el 59 %): no es error. Lo que se puede hacer es cambiarlo al otro lado.
        counts["no_prio"] += 1
        g.flagged.add(id(o))
        if g.role in ("JUNGLE", "SUPPORT", "MID"):  # como los cedidos: al top y al ADC no se les enseña
            moments.append(
                _moment(o["t"], "objective", "info", f"El rival cogió {label}: tenía la prioridad en mid y bot",
                        f"Cuando cayó, {_prio_text(prio)}: llegaban antes al río y cederlo era razonable. Aprovecha ese tiempo al otro lado "
                        "(larvas, heraldo, su jungla de arriba o una torre).", p, ["objective", "no_prio"])
            )
        return
    counts["conceded"] += 1
    if g.role in ("JUNGLE", "SUPPORT", "MID") or _is_big(g, o):
        g.flagged.add(id(o))
        moments.append(_conceded_moment(g, o, ex, p, prio))


def _far_from(g: _Game, o: dict, p) -> bool:
    """¿Estabas lejos del objetivo cuando cayó? Con tu posición poco segura, lejos con margen."""
    # Con tu posición lejos de un punto real (más de 20 s), que estuvieras «lejos» se exige con margen: la auditoría
    # de la calibración vio que casi la mitad de estos avisos se decidían así (24-11-2026).
    # Si en los puntos reales de antes y de después también estabas lejos, vale sin margen.
    gap = g.anchor_gap(g.me["pid"], o["t"])
    around = g.real_around(g.me["pid"], o["t"])
    sure = (gap is not None and gap <= OBJ_FAR_SURE_GAP_S) or (
        len(around) == 2 and all(geo.dist(q, o["pos"]) >= FAR_FROM_OBJECTIVE for q in around)
    )
    far = FAR_FROM_OBJECTIVE + (0 if sure else OBJ_FAR_UNSURE_MARGIN)
    return not (p is None or geo.dist(p, o["pos"]) < far)


def _objective_trade(g: _Game, o: dict, ex: dict, p, counts: Counter, moments: list) -> bool:
    """¿Lo compensó tu equipo al otro lado (buen cambio, o justo si sacasteis algo)? Mérito tuyo solo si participaste."""
    got = _gain_text(ex["gain"])
    good = ex["ratio"] >= GOOD_TRADE
    if not good and not (ex["ratio"] >= FAIR_TRADE and got):
        return False
    if not _contributed(ex["mine"]):  # solo es mérito tuyo si participaste en lo que sacasteis
        return True  # lo compensó tu equipo sin ti: ni error ni mérito tuyo
    label = _obj_label(g, o)
    mine = _gain_text(ex["mine"])
    where = geo.zone_label(geo.zone(p), g.team)
    you = f"; tú: {mine}" if mine else ""
    g.flagged.add(id(o))
    if good:
        counts["trades"] += 1
        moments.append(
            _moment(o["t"], "trade", "good", f"Buen cambio: {label} por {got}",
                    f"Estabas en {where}{you}. Si no se puede disputar un objetivo, cambiarlo por algo al otro lado es la decisión correcta.",
                    p, ["objective", "trade"])
        )
    else:
        counts["fair"] += 1
        moments.append(
            _moment(o["t"], "trade", "low", f"Cambio justo: {label} por {got}",
                    f"Estabas en {where}{you}. Valía algo menos que el objetivo: si llegabais, era mejor disputarlo.", p,
                    ["objective", "trade", "fair_trade"])
        )
    return True


def _conceded_moment(g: _Game, o: dict, ex: dict, p, prio: dict | None) -> dict:
    """Aviso de un objetivo cedido estando lejos: qué hacías, qué sacasteis y qué sacó el rival, y la prioridad."""
    me = g.me["pid"]
    label = _obj_label(g, o)
    recalled = any(
        o["t"] - 60_000 <= pt <= o["t"] and not g.is_dead(me, pt) and not g.respawn_buy(me, pt) for pt, _ in g.purchases[me]
    )
    if recalled:
        detail = "Volviste a base justo antes del objetivo"
    else:
        detail = f"Estabas en {geo.zone_label(geo.zone(p), g.team)}, a {_int(geo.dist(p, o['pos']))} unidades"
    detail += _conceded_gain_text(label, _gain_text(ex["gain"]), _gain_text(ex["loss"]))
    if prio:
        detail += f"; prioridad: {_prio_text(prio)}"
    detail += ". Si no vas a disputarlo, cámbialo: invade su jungla al otro lado, presiona una torre o haz un dive."
    free = ex["value_gain"] < 0.25 * _obj_value(g, o)
    title = f"El rival cogió {label} gratis y estabas lejos" if free else f"El rival cogió {label} y no lo compensasteis"
    return _moment(o["t"], "objective", _conceded_severity(g, o), title, detail, p, ["objective", "far"])


def _conceded_gain_text(label: str, got: str, extra: str) -> str:
    """Lo que sacasteis a cambio frente a lo que además sacó el rival."""
    if got and extra:
        # Sin esto parecía un falso positivo («sacasteis 1 torre y 3 placas» por un heraldo): la balanza
        # también suma lo que sacó el rival a la vez.
        return f"; sacasteis {got}, pero el rival, además {_de(label)}, sacó {extra}"
    if got:
        return f"; solo sacasteis {got}"
    if extra:
        return f"; tu equipo no sacó nada a cambio y el rival además sacó {extra}"
    return "; tu equipo no sacó nada a cambio"


def _conceded_severity(g: _Game, o: dict) -> str:
    """Gravedad de un objetivo cedido: alta si es grande y leve si es un dragón normal (flujo de partida); si no, media."""
    if _is_big(g, o):
        return "high"
    # Un dragón normal cedido es flujo de partida (pasa igual en Esmeralda que en Aspirante): leve.
    return "low" if o["kind"] == "dragon" else "medium"


# ------------------------------------------------------------------- macro
MACRO_METRICS = ("obj_late_recall", "obj_late_arrival", "pink_unused", "baron_wasted", "fights_unconverted")


def _macro(g: _Game, metrics: dict, moments: list) -> None:
    """Preparación de los objetivos y conversión de ventajas, con los relojes de la temporada.

    Sin relojes fiables para la partida (otra temporada, Swiftplay, un objetivo que «muere» antes de su hora)
    no se juzga nada de esto: las métricas quedan en ``None``.
    """
    if not g.clocks_ok:
        for key in MACRO_METRICS:
            metrics[key] = None
        return
    metrics["obj_late_recall"] = _late_recalls(g, moments)
    metrics["obj_late_arrival"] = _late_arrivals(g, moments)
    metrics["pink_unused"] = _pinks_unused(g, moments)
    metrics["baron_wasted"] = _baron_wasted(g, moments)
    metrics["fights_unconverted"] = _unconverted_fights(g, moments)


def _contested(g: _Game, o: dict) -> bool:
    """¿Se disputó? Murió ≤ 2 min después de aparecer con al menos 2 aliados tuyos cerca o uno caído junto al foso."""
    if o.get("spawn") is None or o["t"] - o["spawn"] > CONTEST_WINDOW_MS:
        return False
    t = o["t"]
    near = sum(1 for a in g.allies if (p := g.pos_at(a, t)) is not None and geo.dist(p, o["pos"]) <= NEAR_OBJECTIVE)
    return near >= 2 or _ally_losses(g, o) > 0


def _ally_losses(g: _Game, o: dict) -> int:
    """Aliados tuyos (sin contarte) caídos junto al foso mientras se peleaba el objetivo."""
    t = o["t"]
    return sum(
        1 for k in g.kills
        if k["victim"] in g.allies and t - 30_000 <= k["t"] <= t + 10_000 and k["pos"] and geo.dist(k["pos"], o["pos"]) <= CONTEST_DEATH_RADIUS
    )


def _forced_recall(g: _Game, pt: int) -> bool:
    """¿Volver a base era obligado? Poca vida en el frame anterior, una pelea justo antes o mucho daño recibido.

    Sin datos de vida en el frame no se puede descartar: se da por obligada (nunca se acusa sin pruebas).
    """
    me = g.me["pid"]
    i = g.frame_before(pt)
    hp = g.health_ratio(me, i)
    if hp is None or hp < FORCED_RECALL_HP:
        return True
    if any(pt - FORCED_RECALL_FIGHT_MS <= k["t"] <= pt and me in (k["killer"], k["victim"], *k["assists"]) for k in g.kills):
        return True
    if i is not None and i + 1 < len(g.ts):
        a, b = g.fval(me, i, "taken"), g.fval(me, i + 1, "taken")
        stats = (g.pf[me][i] or {}).get("championStats") or {}
        top = _num(stats.get("healthMax"), 0.0)
        # Mucho daño recibido ese minuto (≥ 35 % de tu vida máxima; los campamentos de jungla también cuentan, así que es prudente).
        if a is not None and b is not None and b - a >= max(FORCED_RECALL_TAKEN, 0.35 * top):
            return True
    return False


def _late_recalls(g: _Game, moments: list) -> int:
    """Volver a base cuando ya no daba tiempo a llegar a un objetivo disputado que te tocaba (desde S − 30 s)."""
    me = g.me["pid"]
    my_deaths = [k["t"] for k in g.kills if k["victim"] == me]
    n = 0
    for o in g.objectives:
        pt = _late_recall(g, o, my_deaths)
        if pt is None:
            continue
        spawn = o.get("spawn")
        label = _obj_label(g, o)
        when = f"{_secs(spawn - pt)} antes de que apareciera {label}" if pt < spawn else f"con {label} ya en el mapa"
        losses = _ally_losses(g, o)
        result = f"lo cogisteis, pero con {_plural(losses, 'caído', 'caídos')}" if o["team"] == g.team else "el rival se lo llevó"
        g.flagged.add(id(o))
        n += 1
        moments.append(
            _moment(
                pt, "objective", "high" if _is_big(g, o) else "medium", f"Volviste a base a destiempo antes {_de(label)}",
                f"Volviste a base {when}, con más de la mitad de la vida, y tu equipo lo peleó sin ti: {result}. "
                "Vuelve a base 60-90 s antes de que aparezca para llegar con el oro gastado y la vida llena.",
                o["pos"], ["objective", "late_recall"],
            )
        )
    return n


def _missed_contest(g: _Game, o: dict, my_deaths: list[int]) -> bool:
    """¿Se disputó sin ti un objetivo que te tocaba, sin señalar aún, estando tú vivo y sin morir desde S − 90 s?"""
    me = g.me["pid"]
    spawn, t = o.get("spawn"), o["t"]
    if spawn is None or id(o) in g.flagged or me in o["takers"]:
        return False
    if not _contested(g, o) or not _contest_expected(g, o):
        return False
    return g.present(me, t) and not any(spawn - 90_000 <= d <= t for d in my_deaths)


def _late_recall(g: _Game, o: dict, my_deaths: list[int]) -> int | None:
    """Vuelta a base a destiempo (no obligada) antes de un objetivo que se disputó sin ti: su momento o ``None``."""
    me = g.me["pid"]
    if not _missed_contest(g, o, my_deaths):
        return None
    spawn, t = o["spawn"], o["t"]
    p = g.pos_at(me, t)
    if p is None or geo.dist(p, o["pos"]) <= NEAR_OBJECTIVE:
        return None
    ours = o["team"] == g.team
    if ours and not _ally_losses(g, o):
        return None  # lo cogió tu equipo sin bajas: tu vuelta a base no costó nada
    if not ours and _exchange(g, o, g.used_gains)["ratio"] >= GOOD_TRADE:
        return None  # tu equipo lo compensó al otro lado
    recalls = g.recalls(me, max(spawn - RECALL_LATE_FROM_MS, t - RECALL_LATE_LAST_MS), t)
    if not recalls or _forced_recall(g, recalls[0]):
        return None
    return recalls[0]


def _late_arrivals(g: _Game, moments: list) -> int:
    """Tu equipo disputó un objetivo que te tocaba y el rival lo cogió mientras tú aún llegabas (a 2.600-5.500)."""
    me = g.me["pid"]
    my_deaths = [k["t"] for k in g.kills if k["victim"] == me]
    n = 0
    for o in g.objectives:
        q = _late_arrival_from(g, o, my_deaths)
        if q is None:
            continue
        t = o["t"]
        p = g.pos_at(me, t)
        label = _obj_label(g, o)
        where = geo.zone_label(geo.zone(q), g.team)
        g.flagged.add(id(o))
        n += 1
        moments.append(
            _moment(
                t, "objective", _late_severity(g, o), f"Llegaste tarde {_al(label)}",
                f"Tu equipo peleó {label} sin ti: un minuto antes de que apareciera estabas en {where}, a {_int(geo.dist(q, o['pos']))} unidades, "
                f"y cuando cayó seguías a {_int(geo.dist(p, o['pos']))}. Empuja tu oleada o acaba el campamento y ve hacia el foso 60-90 s antes.",
                p, ["objective", "late"],
            )
        )
    return n


def _late_arrival_from(g: _Game, o: dict, my_deaths: list[int]) -> tuple[int, int] | None:
    """Dónde estabas un minuto antes de aparecer un objetivo rival al que llegaste tarde (``None`` si no fue así)."""
    me = g.me["pid"]
    if o["team"] == g.team or not _missed_contest(g, o, my_deaths):
        return None
    spawn, t = o["spawn"], o["t"]
    p = g.pos_at(me, t)
    if p is None or not NEAR_OBJECTIVE < geo.dist(p, o["pos"]) <= FAR_FROM_OBJECTIVE:
        return None
    i = g.frame_before(spawn - 30_000)
    q = g.frame_pos(me, i) if i is not None else None
    if q is None or geo.dist(q, o["pos"]) <= LATE_FAR_BEFORE:
        return None
    if g.recalls(me, spawn - 120_000, t) or _cross_map_play(g, t, geo.half_of(o["pos"])):
        return None  # volviste a base (se juzga aparte) o hiciste algo al otro lado
    return q if _late_arrival_cost(g, o) else None


def _late_arrival_cost(g: _Game, o: dict) -> bool:
    """¿Costó algo llegar tarde? No si el rival iba 2 o más por encima ni si tu equipo lo compensó contigo."""
    allies_up, enemies_up = _present_counts(g, o["t"])
    if enemies_up - allies_up >= 2:
        return False
    ex = _exchange(g, o, g.used_gains)
    return not (ex["ratio"] >= FAIR_TRADE and _contributed(ex["mine"]))


def _late_severity(g: _Game, o: dict) -> str:
    """Gravedad de llegar tarde: alta en los grandes, leve en las larvas y media en el resto."""
    if _is_big(g, o):
        return "high"
    return "low" if o["kind"] == "grubs" else "medium"


def _pinks_unused(g: _Game, moments: list) -> int | None:
    """Llevabas una pink de S − 90 s a S + 30 s, estuviste cerca del foso y no la pusiste."""
    if not g.items_reliable or g.inv is None:
        return None
    me = g.me["pid"]
    placed = [t for t, kind in g.ward_events[me] if kind == "CONTROL_WARD"]
    n = 0
    for o in g.objectives:
        spawn = o.get("spawn")
        if spawn is None or o["t"] > spawn + 180_000:
            continue
        if not _pink_forgotten(g, o, placed):
            continue
        t0 = spawn - PINK_BEFORE_MS
        label = OBJ_LABEL["dragon"] if o["kind"] == "dragon" else OBJ_LABEL[o["kind"]]
        n += 1
        moments.append(
            _moment(
                spawn, "vision", "low", f"Pink sin usar antes {_de(label)}",
                f"Llevabas una pink desde antes del {_mmss(t0)}, estuviste cerca del foso y no la pusiste en el minuto previo {_al(label)}. "
                "Ponla en el foso o en el arbusto de entrada unos 60 s antes: te da la visión del objetivo y quita la suya.",
                o["pos"], ["vision", "pink"],
            )
        )
    return n


def _pink_forgotten(g: _Game, o: dict, placed: list[int]) -> bool:
    """¿Llevabas una pink de S − 90 s a S + 30 s, vivo y conectado, estuviste cerca del foso y no la pusiste?"""
    me = g.me["pid"]
    spawn = o["spawn"]
    t0, t1 = spawn - PINK_BEFORE_MS, spawn + PINK_AFTER_MS
    if g.dead_overlap(me, t0, t1) > 0 or any(s < t1 and e > t0 for s, e in g.afk[me]):
        return False
    if any(t0 - PINK_ALIVE_MS <= x <= t1 for x in placed):
        return False  # la pusiste (o tenías una puesta hace poco: solo puede haber una tuya)
    if any(g.inv.at(x)[CONTROL_WARD] < 1 for x in (t0, spawn, t1)):
        return False
    return any(
        t0 <= ts <= t1 and (fp := g.frame_pos(me, i)) is not None and geo.dist(fp, o["pos"]) <= PINK_NEAR for i, ts in enumerate(g.ts)
    )


def _baron_wasted(g: _Game, moments: list) -> int:
    """Barón de tu equipo sin convertir: en 3 minutos no cae torre, inhibidor, 3 placas, ancestral ni alma."""
    n = 0
    for o in g.objectives:
        if o["kind"] != "baron" or o["team"] != g.team:
            continue
        if not _baron_unconverted(g, o):
            continue
        n += 1
        moments.append(
            _moment(
                o["t"], "objective", "low", "Barón sin aprovechar",
                "En los 3 minutos con la mejora del barón no cayó ninguna torre ni inhibidor. La mejora potencia a vuestros súbditos: "
                "asediad en grupo o por dos líneas a la vez (1-3-1) en vez de separaros a farmear o pelear lejos de sus torres.",
                o["pos"], ["objective", "baron_wasted", "team"],
            )
        )
    return n


def _baron_unconverted(g: _Game, o: dict) -> bool:
    """¿Pasaron los 3 minutos con la mejora del barón (estando tú vivo) sin torre, inhibidor, 3 placas, ancestral ni alma?"""
    me = g.me["pid"]
    enemy = 300 - g.team
    t, until = o["t"], o["t"] + BARON_WINDOW_MS
    if until > g.duration_ms or (g.won and g.duration_ms <= t + 240_000) or g.is_dead(me, t):
        return False
    gained = [b for b in g.buildings if b["owner"] == enemy and t <= b["t"] <= until]
    if any(b["kind"] != "plate" for b in gained) or sum(1 for b in gained if b["kind"] == "plate") >= 3:
        return False
    if any(
        x["team"] == g.team and t < x["t"] <= until and (x["kind"] == "elder" or _obj_label(g, x).startswith(SOUL_LABEL))
        for x in g.objectives
    ):
        return False
    # Con 2 o más caídos justo después, perdisteis la pelea de después del barón: lo que falló fue esa pelea.
    return sum(1 for k in g.kills if k["victim"] in (me, *g.allies) and t <= k["t"] <= t + 30_000) < 2


def _fights(g: _Game) -> list[list[dict]]:
    """Peleas de la partida (kills encadenadas), cada una ordenada por tiempo."""
    seen: set[int] = set()
    out = []
    for k in g.kills:
        if id(k) in seen or not k["pos"]:
            continue
        cluster = sorted(_fight_cluster(g, k), key=lambda f: f["t"])
        seen.update(id(f) for f in cluster)
        out.append(cluster)
    return out


def _unconverted_fights(g: _Game, moments: list) -> int:
    """Peleas ganadas con claridad (≥ 2 kills de ventaja) tras las que no cae nada hasta que reaparece el 2.º rival."""
    n = 0
    for cluster in _fights(g):
        until = _won_fight_until(g, cluster)
        if until is None:
            continue
        t0, t1 = cluster[0]["t"], cluster[-1]["t"]
        options = _fight_targets(g, cluster[-1]["pos"], t1)
        if not options or _fight_converted(g, (t0, t1), until):
            continue
        n += 1
        moments.append(_unconverted_moment(g, cluster, until, min(options)[1]))
    return n


def _won_fight_until(g: _Game, cluster: list[dict]) -> int | None:
    """Pelea ganada con claridad (≥ 2 kills de ventaja, como mucho 1 caído) y en la que estabas: hasta cuándo dura la ventaja."""
    me = g.me["pid"]
    t0, t1 = cluster[0]["t"], cluster[-1]["t"]
    if t0 < 3 * 60_000:
        return None
    enemy_dead = [f for f in cluster if g.players[f["victim"]]["team"] != g.team]
    ally_dead = [f for f in cluster if g.players[f["victim"]]["team"] == g.team]
    if len(enemy_dead) < 2 or len(enemy_dead) - len(ally_dead) < 2 or len(ally_dead) > 1:
        return None
    if g.won and g.duration_ms <= t1 + 120_000:
        return None
    center = cluster[-1]["pos"]
    p = g.pos_at(me, t1)
    if not g.present(me, t1) or p is None or geo.dist(p, center) > CONTEST_DEATH_RADIUS:
        return None
    ends = sorted(e for f in enemy_dead for s, e, _ in g.dead.get(f["victim"]) or [] if s == f["t"])
    if len(ends) < 2:
        return None
    if ends[1] - t1 < WON_FIGHT_MIN_WINDOW:
        return None  # reaparecen enseguida (fase de líneas, peleas largas): no da tiempo a convertir nada
    return min(ends[1] + WON_FIGHT_RESPAWN_MARGIN, g.duration_ms)


def _fight_targets(g: _Game, center, t1: int) -> list[tuple[float, str]]:
    """Objetivos vivos y torres rivales al alcance tras la pelea (con su distancia), según cuántos quedabais."""
    allies_up = sum(1 for a in (g.me["pid"], *g.allies) if g.present(a, t1))
    options = [
        (geo.dist(center, clk.pit_of(kind)), OBJ_LABEL[kind])
        for kind in clk.alive_at(g.objectives, g.clocks, t1)
        if geo.dist(center, clk.pit_of(kind)) <= WON_FIGHT_OBJECTIVE and allies_up >= (3 if kind in ("baron", "elder") else 2)
    ]
    if allies_up >= 2:
        options += [
            (geo.dist(center, pos), f"la torre {clk.TURRET_LABEL[kind]} de {lane}")
            for lane, kind, pos in clk.standing_turrets(g.buildings, 300 - g.team, t1)
            if geo.dist(center, pos) <= WON_FIGHT_TOWER
        ]
    return options


def _fight_converted(g: _Game, fight: tuple[int, int], until: int) -> bool:
    """¿Sacasteis algo de la pelea (de su inicio a ``until``)? Un objetivo, una estructura, 2 placas o 8 CS de su jungla."""
    me = g.me["pid"]
    t0, t1 = fight
    lo = t0 - TRADE_GAIN_BEFORE_MS
    if any(o["team"] == g.team and lo <= o["t"] <= until for o in g.objectives):
        return True
    gained = [b for b in g.buildings if b["owner"] != g.team and lo <= b["t"] <= until]
    if any(b["kind"] != "plate" for b in gained) or sum(1 for b in gained if b["kind"] == "plate") >= 2:
        return True
    return sum(g.enemy_jungle_cs(a, t1, until) for a in (me, *g.allies)) >= 8


def _unconverted_moment(g: _Game, cluster: list[dict], until: int, target: str) -> dict:
    """Aviso de una pelea ganada sin convertir: volviste a base con vida o no cayó nada hasta que reaparecieron."""
    me = g.me["pid"]
    t1, center = cluster[-1]["t"], cluster[-1]["pos"]
    enemy_dead = sum(1 for f in cluster if g.players[f["victim"]]["team"] != g.team)
    ally_dead = sum(1 for f in cluster if g.players[f["victim"]]["team"] == g.team)
    score = f"{enemy_dead} kills y {_plural(ally_dead, 'caído', 'caídos')}"
    recall = next(iter(g.recalls(me, t1 + 1, until)), None)
    hp = g.health_ratio(me, g.frame_before(recall)) if recall is not None else None
    if recall is not None and hp is not None and hp >= 0.6 and not _forced_recall(g, recall):
        return _moment(
            recall, "objective", "medium", f"Volviste a base tras ganar la pelea en vez de ir a por {target}",
            f"Ganasteis la pelea del {_mmss(t1)} ({score}) y volviste a base antes de que reaparecieran. Con ventaja numérica, "
            f"primero el objetivo más cercano ({target}) y después la base.",
            center, ["objective", "unconverted"],
        )
    return _moment(
        t1, "objective", "low", f"Pelea ganada sin aprovechar ({score})",
        f"Ganasteis la pelea del {_mmss(t1)} y, hasta que reaparecieron, no cayó nada: tocaba {target}. "
        "La ventaja de una pelea ganada dura poco: conviértela en un objetivo o una torre antes de volver a base.",
        center, ["objective", "unconverted", "team"],
    )


# ---------------------------------------------------------- uso del tiempo
def _setup_windows(g: _Game) -> list[tuple[int, int]]:
    """Antes de cada objetivo (de cualquier equipo) en el que estabas cerca o participaste: colocarse,
    hacer y quitar visión y esperar a que aparezca es jugar, no tiempo vacío."""
    me = g.me["pid"]
    out = []
    for o in g.objectives:
        near = me in o["takers"] or any(
            (p := g.pos_at(me, o["t"] - dt)) is not None and geo.dist(p, o["pos"]) <= OBJ_SETUP_RADIUS for dt in (0, 30_000, 60_000)
        )
        if near:
            out.append((o["t"] - OBJ_SETUP_MS, o["t_end"] + 10_000))
    return out


def _frame_activity(g: _Game, i: int) -> bool:
    """Actividad que el CS y el daño a campeones no ven: daño a estructuras y monstruos, recibir daño, control de masas."""
    me = g.me["pid"]

    def delta(key: str) -> int:
        a, b = g.fval(me, i - 1, key), g.fval(me, i, key)
        return b - a if a is not None and b is not None else 0

    return delta("dmg_all") >= 1500 or delta("taken") >= 400 or delta("timeEnemySpentControlled") > 0


def _near_ally(g: _Game, i: int) -> bool:
    """El support junto a un aliado vivo (al principio o al final del minuto): está jugando su rol."""
    me = g.me["pid"]
    for j in (i - 1, i):
        p = g.frame_pos(me, j)
        if not p:
            continue
        for a in g.allies:
            q = g.frame_pos(a, j)
            if q and not g.is_dead(a, g.ts[j]) and geo.dist(p, q) <= SUPPORT_NEAR_ALLY:
                return True
    return False


def _time_use(g: _Game, metrics: dict, moments: list) -> None:
    me = g.me["pid"]
    # Minutos vacíos desde el 15: sin farmear, pelear, wardear, limpiar wards, comprar ni hacer objetivos.
    my_events = sorted(
        [k["t"] for k in g.kills if me == k["killer"] or me in k["assists"]]
        + [o["t"] for o in g.objectives if me in o["takers"]]
        + [b["t"] for b in g.buildings if me in b["takers"]]
        + list(g.wards[me])
        + list(g.ward_kills.get(me) or [])
        + [t for t, _ in g.purchases[me]]
    )
    setup = _setup_windows(g)
    eligible = 0
    idle: list[int] = []
    for i in range(1, len(g.ts)):
        if not _idle_eligible(g, i):
            continue
        eligible += 1
        if not _minute_busy(g, i, my_events, setup):
            idle.append(i)
    metrics["idle_min"] = len(idle)
    metrics["idle_pct"] = round(len(idle) / eligible * 100, 1) if eligible else None
    for run in _runs(idle):
        if len(run) >= 2:
            moments.append(_idle_moment(g, run))
    _recall_metrics(g, metrics)


def _idle_eligible(g: _Game, i: int) -> bool:
    """¿Se juzga el minuto que acaba en el frame ``i``? Desde el 15, completo y sin más de 15 s muerto ni AFK."""
    me = g.me["pid"]
    t0, t1 = g.ts[i - 1], g.ts[i]
    if t0 < IDLE_FROM_MS or t1 - t0 < 40_000:
        return False
    return not (g.dead_overlap(me, t0, t1) > 15_000 or any(min(e, t1) - max(s, t0) > 15_000 for s, e in g.afk[me]))


def _minute_busy(g: _Game, i: int, my_events: list[int], setup: list[tuple[int, int]]) -> bool:
    """¿Hiciste algo en ese minuto? CS, daño, otra actividad, un evento tuyo, preparar un objetivo o (support) acompañar."""
    me = g.me["pid"]
    t0, t1 = g.ts[i - 1], g.ts[i]
    cs0, cs1 = g.fval(me, i - 1, "cs"), g.fval(me, i, "cs")
    d0, d1 = g.fval(me, i - 1, "dmg"), g.fval(me, i, "dmg")
    if (cs0 is not None and cs1 is not None and cs1 - cs0 >= 3) or (d0 is not None and d1 is not None and d1 - d0 >= 400):
        return True
    if _frame_activity(g, i):
        return True
    j = bisect.bisect_left(my_events, t0)
    if j < len(my_events) and my_events[j] <= t1:
        return True
    if _overlap(setup, t0, t1) >= 20_000:
        return True
    return g.role == "SUPPORT" and _near_ally(g, i)


def _idle_moment(g: _Game, run: list[int]) -> dict:
    """Aviso de 2 o más minutos seguidos sin farmear, pelear, wardear ni hacer objetivos."""
    p = g.frame_pos(g.me["pid"], run[0])
    return {
        "t": round(g.ts[run[0] - 1] / 1000, 1),
        "kind": "idle",
        "severity": "medium" if len(run) >= 3 else "low",
        "title": f"{len(run)} minutos sin farmear ni pelear",
        "detail": f"Del minuto {round(g.ts[run[0] - 1] / 60_000)} al {round(g.ts[run[-1]] / 60_000)} no hiciste CS, daño, wards ni objetivos. Si no hay jugada, farmea una línea o la jungla.",
        "x": p[0] if p else None,
        "y": p[1] if p else None,
        "tags": ["idle"],
    }


def _recall_metrics(g: _Game, metrics: dict) -> None:
    """Viajes a base (compras estando vivo, sin contar la compra inicial ni la de al reaparecer) y pinks compradas."""
    me = g.me["pid"]
    trips: list[int] = []
    for t in g.recalls(me, 90_000, g.duration_ms + 1):
        if not trips or t - trips[-1] > 40_000:
            trips.append(t)
    known = g.buys_in_base(me)  # Ornn compra en cualquier sitio: sus compras no dicen cuándo volvió a base
    metrics["recalls"] = len(trips) if known else None
    metrics["first_recall_min"] = round(trips[0] / 60_000, 1) if trips else None
    metrics["control_wards_bought"] = sum(1 for _, item in g.purchases[me] if item == CONTROL_WARD)


def _runs(indices: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for i in indices:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


# ------------------------------------------------------------------ jungla
def _jungle(g: _Game, metrics: dict, moments: list) -> dict:
    me = g.me["pid"]
    path = _jungle_path(g)
    start = next((s for s in path if s["camp"] and s["camp_team"] == g.team), None)
    frames = _early_frames(g)
    presence, invade = _jungle_presence(g, frames)
    gank_events, first_gank = _jungle_ganks(g)
    impact, invade_cs, crabs = _early_impact(g)
    enemy_ganks, far = _enemy_jungle_ganks(g)
    ej = g.enemy_jungler
    i10 = g.frame_at_minute(10)
    jcs10 = g.fval(me, i10, "jungleMinionsKilled")
    ejcs10 = g.fval(ej, i10, "jungleMinionsKilled") if ej else None
    ch = g.raw[me].get("challenges") or {}
    metrics.update(
        {
            "ganks_pre10": len(gank_events),
            "impact_pre10": impact,
            "invade_cs_pre10": invade_cs,
            "first_gank_min": round(first_gank["t"] / 60_000, 1) if first_gank else None,
            "invade_min": invade,
            "jungle_cs10": jcs10,
            "jungle_cs_diff10": _difference(jcs10, ejcs10),
            "enemy_ganks_far": far,
            "mirror_pct": _mirror_pct(g, frames),
            "scuttles": _num(ch.get("scuttleCrabKills")) if "scuttleCrabKills" in ch else None,
            "crabs_pre10": crabs,
            "enemy_jungle_cs": _num(g.raw[me].get("totalEnemyJungleMinionsKilled")),
        }
    )
    _jungle_moments(g, impact, far, moments)
    return {
        "start_camp": start["camp"] if start else None,
        "start_side": geo.half_of((start["x"], start["y"])) if start else None,
        "path": path,
        "presence": dict(presence),
        "ganks": [{"t": round(k["t"] / 1000, 1), "x": k["pos"][0], "y": k["pos"][1], "zone": geo.zone(k["pos"])} for k in gank_events],
        "enemy_ganks": enemy_ganks,
    }


def _jungle_path(g: _Game) -> list[dict]:
    """Tus primeros minutos (frames 1 a 8): posición, zona y el campamento más cercano."""
    me = g.me["pid"]
    path = []
    for i in range(1, min(len(g.ts), 9)):
        p = g.frame_pos(me, i)
        if not p:
            continue
        camp = geo.nearest_camp(p, 1500)
        z = geo.zone(p)
        path.append(
            {
                "minute": round(g.ts[i] / 60_000),
                "x": p[0],
                "y": p[1],
                "zone": z,
                "camp": camp[1] if camp else None,
                "camp_team": camp[0] if camp else None,
            }
        )
    return path


def _early_frames(g: _Game) -> list[tuple[int, int, tuple[int, int]]]:
    """Tus frames del 2 al 14 vivo y con posición: (índice, momento, posición)."""
    me = g.me["pid"]
    out = []
    for i, t in enumerate(g.ts):
        if t < 2 * 60_000 or t > EARLY_MS:
            continue
        p = g.frame_pos(me, i)
        if p and not g.is_dead(me, t):
            out.append((i, t, p))
    return out


def _jungle_presence(g: _Game, frames: list[tuple[int, int, tuple[int, int]]]) -> tuple[Counter, int]:
    """En esos frames: minutos en cada línea y en el río, y minutos en la jungla rival (invasión)."""
    presence: Counter = Counter()
    invade = 0
    for _, _, p in frames:
        z = geo.zone(p)
        if geo.is_lane(z):
            presence[z] += 1
        elif geo.is_jungle_of(z, 300 - g.team):
            invade += 1
        elif z == "river":
            presence["river"] += 1
    return presence, invade


def _mirror_pct(g: _Game, frames: list[tuple[int, int, tuple[int, int]]]) -> int | None:
    """% de esos frames en la misma mitad del mapa que el jungla rival (cuando se le ve vivo)."""
    ej = g.enemy_jungler
    mirror_same = mirror_n = 0
    for i, t, p in frames:
        q = g.frame_pos(ej, i) if ej else None
        if q and not g.is_dead(ej, t):
            mirror_n += 1
            mirror_same += int(geo.half_of(p) == geo.half_of(q))
    return round(mirror_same / mirror_n * 100) if mirror_n else None


def _jungle_ganks(g: _Game) -> tuple[list[dict], dict | None]:
    """Tus ganks antes del 10 (kills en una línea, uno por cada 30 s) y el primero antes del 14."""
    me = g.me["pid"]
    takedowns = [k for k in g.kills if (me == k["killer"] or me in k["assists"]) and k["pos"]]
    ganks = [k for k in takedowns if k["t"] < 10 * 60_000 and geo.is_lane(geo.zone(k["pos"]))]
    gank_events = []
    for k in ganks:
        if not gank_events or k["t"] - gank_events[-1]["t"] > 30_000:
            gank_events.append(k)
    first_gank = next((k for k in takedowns if k["t"] < EARLY_MS and geo.is_lane(geo.zone(k["pos"]))), None)
    return gank_events, first_gank


def _early_impact(g: _Game) -> tuple[int, int, int]:
    """Impacto antes del 10 (kills en cualquier zona, objetivos, placas o torres, cangrejos e invasiones), CS invadido y cangrejos."""
    me, team = g.me["pid"], g.team
    early_takedowns = [k for k in g.kills if k["t"] < 10 * 60_000 and (me == k["killer"] or me in k["assists"])]
    early_objectives = [o for o in g.objectives if o["team"] == team and o["t"] < 10 * 60_000 and me in o["takers"]]
    early_buildings = [b["t"] for b in g.buildings if b["t"] < 10 * 60_000 and b["owner"] != team and me in b["takers"]]
    invade_cs = g.enemy_jungle_cs(me, 0, 10 * 60_000)
    crabs = _early_crabs(g)
    impact = (
        len(early_takedowns) + len(early_objectives) + len(_runs_in_time(early_buildings, 30_000)) + crabs + (1 if invade_cs >= 8 else 0)
    )
    return impact, invade_cs, crabs


def _enemy_jungle_ganks(g: _Game) -> tuple[int, int]:
    """Ganks del jungla rival antes del 14 y cuántos fueron en la otra mitad del mapa mientras no hacías nada allí."""
    team, ej = g.team, g.enemy_jungler
    enemy_ganks = far = 0
    for k in g.kills:
        if not ej or k["t"] >= EARLY_MS or not k["pos"] or g.players[k["victim"]]["team"] != team:
            continue
        if ej != k["killer"] and ej not in k["assists"]:
            continue
        gank_zone = geo.zone(k["pos"])
        if not geo.is_lane(gank_zone):
            continue
        enemy_ganks += 1
        if gank_zone == "mid":
            continue  # mid se alcanza desde las dos mitades: no es «la otra mitad del mapa»
        far += int(_far_gank(g, k))
    return enemy_ganks, far


def _far_gank(g: _Game, k: dict) -> bool:
    """¿Estabas lejos, en la otra mitad, sin hacer nada allí mientras el jungla rival ganqueaba?"""
    p = g.pos_at(g.me["pid"], k["t"])
    if p is None or geo.zone(p).startswith("base_") or geo.dist(p, k["pos"]) <= FAR_GANK:
        return False  # en base (volviendo o reapareciendo) o lo bastante cerca para responder
    # En la otra mitad haciendo algo (objetivo, torre, kill, su jungla) es justo el cambio que se recomienda.
    return geo.half_of(p) != geo.half_of(k["pos"]) and not _cross_map_play(g, k["t"], geo.half_of(k["pos"]))


def _jungle_moments(g: _Game, impact: int, far: int, moments: list) -> None:
    """Avisos de jungla: early sin impacto y ganks del jungla rival en la otra mitad del mapa."""
    if not impact and g.duration_ms >= 12 * 60_000:
        moments.append(
            _moment(
                600_000,
                "jungle",
                "medium",
                "Early sin impacto: ni kills, ni objetivos, ni invasiones antes del 10",
                "Tras la primera limpieza busca una jugada: gank a la línea con empuje y control de masas, larvas o dragón con prioridad, o invadir donde no está su jungla.",
                tags=["jungle"],
            )
        )
    if far >= 2:
        moments.append(
            {
                "t": 840.0,
                "kind": "jungle",
                "severity": "low",
                "title": f"El jungla rival ganqueó {far} veces en la otra mitad del mapa",
                "detail": "Si sabes dónde está el jungla rival, juega en espejo: contragank en su lado o invade/objetivo en el contrario.",
                "x": None,
                "y": None,
                "tags": ["jungle", "tracking"],
            }
        )


def _runs_in_time(times: list[int], gap: int) -> list[list[int]]:
    """Agrupa instantes separados como mucho ``gap`` ms (p. ej. las placas de una misma jugada)."""
    out: list[list[int]] = []
    for t in sorted(times):
        if out and t - out[-1][-1] <= gap:
            out[-1].append(t)
        else:
            out.append([t])
    return out


def _early_crabs(g: _Game) -> int:
    """Cangrejos del río antes del 10 (0-2).

    Con el reto ``initialCrabCount`` de Riot si viene; si no, estar junto a un cangrejo en un frame entre
    el 3 y el 10 mientras subía tu CS de jungla.
    """
    me = g.me["pid"]
    ch = g.raw[me].get("challenges") or {}
    sides: set[str] = set()
    for i in range(1, len(g.ts)):
        if g.ts[i] > 10 * 60_000 or g.ts[i - 1] < 3 * 60_000:
            continue
        a, b = g.fval(me, i - 1, "jungleMinionsKilled"), g.fval(me, i, "jungleMinionsKilled")
        if a is None or b is None or b <= a:
            continue
        for j in (i - 1, i):
            sides.update(_scuttles_near(g.frame_pos(me, j)))
    return max(len(sides), min(2, _num(ch.get("initialCrabCount"))))


def _scuttles_near(p) -> list[str]:
    """Lados del río con un cangrejo a menos de 1.600 de ``p`` (ninguno sin posición)."""
    return [side for side, spot in geo.SCUTTLE.items() if p and geo.dist(p, spot) <= 1600]


def _cross_map_play(g: _Game, t: int, gank_half: str) -> bool:
    """¿Hiciste algo en la otra mitad del mapa mientras el jungla rival ganqueaba? (objetivo, estructura, kill o su jungla)."""
    me = g.me["pid"]
    lo, hi = t - CROSS_MAP_MS, t + CROSS_MAP_MS
    if any(lo <= o["t"] <= hi and o["team"] == g.team and me in o["takers"] and _other_half(o["pos"], gank_half) for o in g.objectives):
        return True
    if any(lo <= b["t"] <= hi and b["owner"] != g.team and me in b["takers"] and _other_half(b["pos"], gank_half) for b in g.buildings):
        return True
    if any(
        lo <= k["t"] <= hi and (me == k["killer"] or me in k["assists"]) and g.players[k["victim"]]["team"] != g.team
        and _other_half(k["pos"], gank_half)
        for k in g.kills
    ):
        return True
    return _farmed_other_half(g, (lo, hi), gank_half)


def _other_half(pos, gank_half: str) -> bool:
    """¿Está ``pos`` en la otra mitad del mapa que ``gank_half``? (sin posición, no)."""
    return pos is not None and geo.half_of(pos) != gank_half


def _farmed_other_half(g: _Game, window: tuple[int, int], gank_half: str) -> bool:
    """¿Hiciste 4 CS o más en un minuto de esa ventana en su jungla, en la otra mitad del mapa?"""
    me = g.me["pid"]
    lo, hi = window
    enemy = 300 - g.team
    for i in range(1, len(g.ts)):
        if g.ts[i] <= lo or g.ts[i - 1] >= hi:
            continue
        a, b = g.fval(me, i - 1, "jungleMinionsKilled"), g.fval(me, i, "jungleMinionsKilled")
        if a is None or b is None or b - a < 4:
            continue
        spots = (g.frame_pos(me, i - 1), g.frame_pos(me, i))
        if any(p and geo.is_jungle_of(geo.zone(p), enemy) and _other_half(p, gank_half) for p in spots):
            return True
    return False


def _roaming(g: _Game, metrics: dict) -> None:
    me = g.me["pid"]
    lane = geo.ROLE_LANE.get(g.role or "")
    if not lane:
        return
    roam = 0
    for i, t in enumerate(g.ts):
        if t < 3 * 60_000 or t > EARLY_MS or g.is_dead(me, t):
            continue
        p = g.frame_pos(me, i)
        if not p:
            continue
        z = geo.zone(p)
        if z != lane and not z.startswith("base_"):
            roam += 1
    metrics["roam_min"] = roam
    metrics["roam_takedowns"] = sum(
        1
        for k in g.kills
        if (me == k["killer"] or me in k["assists"]) and k["t"] < EARLY_MS and k["pos"] and geo.zone(k["pos"]) not in (lane,)
    )


# ------------------------------------------------------------------- build
def _build(g: _Game, metrics: dict, moments: list) -> dict | None:
    """Orden de compra, minutos de cada objeto y errores de build."""
    if g.inv is None:
        return None
    cat, inv = g.catalog, g.inv
    me = g.me["pid"]
    raw = g.raw[me]
    bought = inv.bought()
    completed = [p for p in bought if cat.is_completed(p["item"])]
    legendaries = [p for p in completed if cat.is_legendary(p["item"])]
    boots = next((p for p in completed if cat.is_boots(p["item"])), None)
    order = [{"t": round(p["t"] / 1000, 1), "item": p["item"], "kind": "boots" if cat.is_boots(p["item"]) else "legendary"} for p in completed]
    backs = _backs(g, bought)

    # Con objetos que el catálogo no conoce (u otra temporada) los tiempos de objeto no son fiables y
    # las conclusiones sobre la build (botas, Heridas graves, resistencias, ventas) tampoco.
    reliable = g.items_reliable
    metrics["first_item_min"] = _item_minute(legendaries, 0) if reliable else None
    metrics["second_item_min"] = _item_minute(legendaries, 1) if reliable else None
    metrics["boots_min"] = round(boots["t"] / 60_000, 1) if boots and reliable else None
    metrics["legendaries"] = len(legendaries)

    final_inv = inv.at(g.duration_ms + 1)
    final_resists = set().union(*[cat.resist(i) for i in final_inv]) if final_inv else set()
    notes: list[dict] = []

    _clogged_check(g, metrics, moments)
    sold = _sold_check(g, metrics, moments)
    skills = [(t, slot) for t, slot, kind in g.skill_ups[me] if kind == "NORMAL" and slot in SKILL_KEYS]
    r_delays = _r_check(g, skills, metrics, moments)
    letters = "".join(SKILL_KEYS[slot] for _, slot in skills)
    anti, style = _anti_heal_check(g, bought, len(legendaries), moments)
    notes.append({"kind": "anti_heal", **anti})
    shares = _resist_check(g, len(legendaries), final_resists, moments)
    _boots_check(g, boots, moments)

    return {
        "order": order,
        "backs": backs,
        "final": [_num(raw.get(f"item{i}")) for i in range(6)],
        "first_item_min": metrics["first_item_min"],
        "second_item_min": metrics["second_item_min"],
        "boots_min": metrics["boots_min"],
        "skills": letters,
        "skill_t": [round(t / 1000, 1) for t, _ in skills],  # segundo de cada subida (mismo orden que ``skills``)
        "skill_max": _skill_max(letters)[:3],
        "r_delays": r_delays,
        "sold": sold,
        "anti_heal": anti,
        "damage_taken": shares,
        "resists": sorted(final_resists),
        "damage_style": style,
    }


def _backs(g: _Game, bought: list[dict]) -> list[dict]:
    """Compras agrupadas por vuelta a base (≤ 40 s entre una y otra): segundo, objetos, oro gastado y si estabas muerto."""
    cat = g.catalog
    me = g.me["pid"]
    backs: list[dict] = []
    for p in bought:
        spent = p["cost"] - sum(cat.cost(c) for c in p["consumed"])
        if backs and p["t"] - backs[-1]["_last"] <= 40_000:
            backs[-1]["items"].append(p["item"])
            backs[-1]["gold"] += spent
            backs[-1]["_last"] = p["t"]
        else:
            backs.append({"t": round(p["t"] / 1000, 1), "items": [p["item"]], "gold": spent, "_last": p["t"], "dead": g.is_dead(me, p["t"])})
    for b in backs:
        b.pop("_last")
    return backs


def _item_minute(legendaries: list[dict], i: int) -> float | None:
    """Minuto en que completaste tu legendario número ``i`` (desde 0)."""
    return round(legendaries[i]["t"] / 60_000, 1) if len(legendaries) > i else None


def _clogged_check(g: _Game, metrics: dict, moments: list) -> None:
    """Inventario lleno con componentes de otros objetos: por el orden de compra no podías gastar."""
    # (Llevar los componentes de tu siguiente objeto y ahorrar para combinarlos no es un error.)
    cat = g.catalog
    clogged: list[tuple[int, list[int]]] = []
    for i, r in sorted(g.gold_rows.items()):
        if not r.get("known") or r["free"] or r["gold"] < USEFUL_SPEND or r["useful"] >= USEFUL_SPEND or g.ts[i] < 8 * 60_000:
            continue
        strays = _stray_components(g, i, r)
        if strays and r.get("targets"):
            clogged.append((i, strays))
    metrics["inv_clogged_min"] = len(clogged)
    # Solo si se repite: llevar un componente de otro objeto una vez es normal (en Aspirante pasa más que en Esmeralda).
    if len(clogged) >= 2:
        i, strays = clogged[0]
        names = _join([cat.name(c) for c in strays[:3]])
        nxt = cat.name(g.gold_rows[i]["targets"][0])
        moments.append(
            _moment(
                g.ts[i], "build", "medium" if len(clogged) >= 3 else "low",
                "Inventario lleno con componentes de otros objetos",
                f"Con {_int(g.gold_rows[i]['gold'])} de oro no podías avanzar hacia {nxt}: {names} ocupaban hueco sin ser parte de ese objeto. Termina un objeto antes de empezar el siguiente.",
                tags=["build", "clogged"],
            )
        )


def _stray_components(g: _Game, i: int, r: dict) -> list[int]:
    """Componentes que llevabas en el frame ``i`` sin ser parte de lo que ibas a comprar (las botas nunca sobran)."""
    cat = g.catalog
    snap = g.inv.at(g.ts[i])
    planned = Counter()
    for target in r.get("targets") or []:
        planned.update(cat.tree(target))
        planned[target] += 1
    strays = []
    for c in snap.elements():
        # Las botas (también las básicas) ocupan su hueco toda la partida: nunca «sobran» para avanzar otro objeto.
        if not cat.uses_slot(c) or cat.is_completed(c) or cat.is_starter(c) or cat.is_consumable(c) or cat.is_boots(c):
            continue
        if planned[c] > 0:
            planned[c] -= 1
        else:
            strays.append(c)
    return strays


def _sold_check(g: _Game, metrics: dict, moments: list) -> list[dict]:
    """Objetos terminados vendidos (se pierde ~30 % de su valor): la lista y un aviso por cada uno."""
    cat = g.catalog
    sold = []
    for s in g.inv.sold:
        if cat.is_legendary(s["item"]):
            loss = cat.cost(s["item"]) - cat.get(s["item"]).get("sell", 0)
            sold.append({"t": round(s["t"] / 1000, 1), "item": s["item"], "loss": loss})
    metrics["sold_legendaries"] = len(sold)
    for s in sold if g.items_reliable else []:
        moments.append(
            _moment(s["t"] * 1000, "build", "low", f"Vendiste {cat.name(s['item'])}",
                    f"Al venderlo pierdes {_int(s['loss'])} de oro. Si cambias de objeto, que sea por una amenaza concreta.", tags=["build", "sold"])
        )
    return sold


def _r_check(g: _Game, skills: list[tuple[int, int]], metrics: dict, moments: list) -> list[dict]:
    """La R a tiempo (solo al llegar a nivel 6): retrasos de más de 30 s, con un aviso por cada uno."""
    me = g.me["pid"]
    r_delays = []
    if skills and g.me["champ"] not in NO_R_CHECK:
        reached = {lvl: t for t, lvl in g.levels[me]}
        r_ups = [t for t, slot in skills if slot == 4]
        # Solo nivel 6 (desbloquearla): a 11 y 16, los Aspirante la retrasan a propósito en el 11 % de partidas
        # (subir antes otra habilidad clave), así que no es un error claro.
        for lvl in (6,):
            delay = _r_delay(g, reached, r_ups, lvl)
            if delay is not None:
                r_delays.append(delay)
    metrics["r_late"] = len(r_delays)
    for d in r_delays:
        moments.append(
            _moment(d["t"] * 1000, "build", "medium" if d["level"] == 6 else "low", f"Subiste la R {d['delay']} s tarde (nivel {d['level']})",
                    "Sube la R en cuanto llegues a nivel 6: es tu mayor pico de poder y el rival lo sabe.", tags=["build", "skills"])
        )
    return r_delays


def _r_delay(g: _Game, reached: dict[int, int], r_ups: list[int], lvl: int) -> dict | None:
    """Retraso al subir la R al llegar a ``lvl`` (``None`` si no llegó, si estabas desconectado o si fue a tiempo)."""
    if lvl not in reached:
        return None
    tl = reached[lvl]
    tr = next((t for t in r_ups if t >= tl - 1000), None)
    upto = tr if tr is not None else g.duration_ms
    delay = upto - tl
    if any(s < upto and e > tl for s, e in g.afk.get(g.me["pid"]) or []):
        return None  # desconectado o AFK en ese momento: no se juzga
    if delay > R_LATE_S * 1000 and (tr is not None or g.duration_ms - tl > 60_000):
        return {"level": lvl, "t": round(tl / 1000, 1), "delay": round(delay / 1000)}
    return None


def _skill_max(letters: str) -> list[str]:
    """Orden en que maximizaste Q, W y E (las que no llegaron a 5, detrás, de más a menos puntos)."""
    rank: Counter = Counter()
    maxed: list[str] = []
    for ch in letters:
        rank[ch] += 1
        if ch in "QWE" and rank[ch] == 5:
            maxed.append(ch)
    maxed += [k for k, _ in sorted(((k, -rank[k]) for k in "QWE" if k not in maxed), key=lambda x: x[1])]
    return maxed


def _anti_heal_check(g: _Game, bought: list[dict], n_legendaries: int, moments: list) -> tuple[dict, str]:
    """Heridas graves contra curanderos: los rivales que más se curaban, si las llevabas y cuál te tocaba; y tu tipo de daño."""
    cat = g.catalog
    raw = g.raw[g.me["pid"]]
    healers = _healers(g)
    has_gw = any(cat.anti_heal(p["item"]) for p in bought)
    phys = _num(raw.get("physicalDamageDealtToChampions"))
    magic = _num(raw.get("magicDamageDealtToChampions"))
    style = "ad" if phys >= magic else "ap"
    suggest = _anti_heal_item(cat, style)
    anti = {"healers": healers, "has": has_gw, "suggest": suggest}
    if (
        g.items_reliable and healers and not has_gw and g.role in ("TOP", "JUNGLE", "MID", "ADC") and n_legendaries >= 2
        and (healers[0]["per_min"] >= 900 or len(healers) >= 2)
    ):
        who = _join([_champ_ref(g, h["pid"]) for h in healers[:2]])
        tool = f"{cat.name(suggest)} ({_int(cat.cost(suggest))})" if suggest else "Un objeto con Heridas graves"
        moments.append(
            _moment(min(g.duration_ms, 15 * 60_000), "build", "low", f"Sin Heridas graves contra {who}",
                    f"Se curaba{'n' if len(healers) > 1 else ''} hasta {_int(healers[0]['per_min'])} por minuto. {tool} corta esa curación un 40 %.",
                    tags=["build", "anti_heal"])
        )
    return anti, style


def _healers(g: _Game) -> list[dict]:
    """Rivales que curaban (a sí mismos y a sus aliados) al menos ``HEAVY_HEAL_MIN`` por minuto, de más a menos."""
    minutes = max(g.minutes, 1)
    healers = []
    for e in g.enemies:
        heal = (_num(g.raw[e].get("totalHeal")) + _num(g.raw[e].get("totalHealsOnTeammates"))) / minutes
        if heal >= HEAVY_HEAL_MIN:
            healers.append({"pid": e, "champ": g.players[e]["champ"], "per_min": round(heal)})
    healers.sort(key=lambda h: h["per_min"], reverse=True)
    return healers


def _resist_check(g: _Game, n_legendaries: int, final_resists: set, moments: list) -> dict[str, int]:
    """Resistencias frente al daño que recibiste: el reparto (%) y un aviso si moriste mucho sin ninguna contra el daño que dominaba."""
    cat = g.catalog
    raw = g.raw[g.me["pid"]]
    taken = {k: _num(raw.get(f"{k}DamageTaken")) for k in ("physical", "magic", "true")}
    total_taken = sum(taken.values()) or 1
    shares = {k: round(v / total_taken * 100) for k, v in taken.items()}
    need = _resist_needed(shares)
    # En top no: los Aspirante de top lo hacen más que los Diamante (data/otps, 23-11-2026).
    if g.items_reliable and need and need not in final_resists and _num(raw.get("deaths")) >= 6 and n_legendaries >= 2 and g.role != "TOP":
        armor = need == "armor"
        boots_id = 3047 if armor else 3111
        kind = "físico" if armor else "mágico"
        res = "armadura" if armor else "resistencia mágica"
        moments.append(
            _moment(min(g.duration_ms, 20 * 60_000), "build", "low", f"El {shares['physical' if armor else 'magic']} % del daño que recibiste fue {kind}",
                    f"Moriste {_num(raw.get('deaths'))} veces sin ningún objeto con {res}. Unas {cat.name(boots_id)} o un objeto defensivo de tu build te habrían dado más margen.",
                    tags=["build", "resist"])
        )
    return shares


def _resist_needed(shares: dict[str, int]) -> str | None:
    """``armor`` o ``mr`` si el 65 % o más del daño que recibiste fue físico o mágico (si no, ``None``)."""
    if shares["physical"] >= 65:
        return "armor"
    return "mr" if shares["magic"] >= 65 else None


def _boots_check(g: _Game, boots: dict | None, moments: list) -> None:
    """Sin botas mejoradas al minuto 20 (salvo jungla, top y quien no puede comprarlas)."""
    # Al jungla no: 1 de cada 3 junglas Aspirante no tiene botas mejoradas al 20 (van antes a por objetos). Al top
    # tampoco: los Aspirante de top lo hacen el doble que los Diamante (0,079 frente a 0,035 por partida).
    if (
        g.items_reliable and not boots and g.duration_ms >= 22 * 60_000 and g.me["champ"] not in NO_BOOTS
        and g.role not in ("JUNGLE", "TOP")
    ):
        moments.append(
            _moment(20 * 60_000, "build", "low", "Sin botas mejoradas al minuto 20",
                    "Las botas de nivel 2 cuestan poco (900-1.250) y dan mucha movilidad y estadísticas: suelen ir entre el primer y el segundo objeto.", tags=["build", "boots"])
        )


def _anti_heal_item(cat: ItemCatalog, style: str) -> int | None:
    """El objeto con Heridas graves más barato para tu tipo de daño."""
    want = "SpellDamage" if style == "ap" else "Damage"
    options = [
        (cat.cost(i), i)
        for i, it in cat.items.items()
        if cat.anti_heal(i) and it.get("buyable") and want in it["tags"] and not it.get("champion") and cat.cost(i) <= 1500
    ]
    return min(options)[1] if options else None


# -------------------------------------------------------- series y replay
def _series(g: _Game) -> dict:
    me, opp = g.me["pid"], g.opponent
    keys = ("minute", "gold", "opp_gold", "cs", "opp_cs", "xp", "opp_xp", "current_gold", "level", "team_gold_diff", "useful_gold", "hoard")
    out: dict[str, list] = {k: [] for k in keys}
    for i, t in enumerate(g.ts):
        out["minute"].append(round(t / 60_000, 2))
        out["gold"].append(g.fval(me, i, "totalGold"))
        out["cs"].append(g.fval(me, i, "cs"))
        out["xp"].append(g.fval(me, i, "xp"))
        out["current_gold"].append(g.fval(me, i, "currentGold"))
        out["level"].append(g.fval(me, i, "level"))
        out["opp_gold"].append(g.fval(opp, i, "totalGold") if opp else None)
        out["opp_cs"].append(g.fval(opp, i, "cs") if opp else None)
        out["opp_xp"].append(g.fval(opp, i, "xp") if opp else None)
        row = g.gold_rows.get(i)
        out["useful_gold"].append(row["useful"] if row else None)
        out["hoard"].append(1 if i in g.hoard_frames else 0)
        out["team_gold_diff"].append(_team_gold_diff(g, i))
    return out


def _team_gold_diff(g: _Game, i: int) -> int:
    """Oro total de tu equipo menos el del rival en el frame ``i``."""
    diff = 0
    for pid, p in g.players.items():
        value = g.fval(pid, i, "totalGold") or 0
        diff += value if p["team"] == g.team else -value
    return diff


def _replay(g: _Game) -> dict:
    """Datos compactos para la «repetición» 2D del frontend (tiempos en segundos)."""
    tracks = {}
    for pid in g.players:
        tracks[str(pid)] = {
            "a": [[round(t / 1000, 1), x, y, kind] for t, x, y, kind in g.anchors[pid]],
            "dead": [
                [round(s / 1000, 1), round(e / 1000, 1), p[0] if p else None, p[1] if p else None]
                for s, e, p in g.dead.get(pid) or []
            ],
        }
    # Nivel y oro total de cada jugador al final de cada minuto (contexto de cada muerte en el visor).
    frames = {
        "t": [round(t / 1000) for t in g.ts],
        "level": {str(pid): [g.fval(pid, i, "level") for i in range(len(g.ts))] for pid in g.players},
        "gold": {str(pid): [g.fval(pid, i, "totalGold") for i in range(len(g.ts))] for pid in g.players},
    }
    return {
        "tracks": tracks,
        "routed": g.route_all,  # ya rodean los muros (``_walk``): la vista 2D no tiene que calcularlas
        "frames": frames,
        "kills": [[round(k["t"] / 1000, 1), k["killer"], k["victim"], *(k["pos"] or (None, None)), k["assists"]] for k in g.kills],
        "objectives": [[round(o["t"] / 1000, 1), o["kind"], o["team"], int(o["pos"][0]), int(o["pos"][1])] for o in g.objectives],
        "buildings": [
            [round(b["t"] / 1000, 1), b["owner"], b["kind"], b["lane"], *(b["pos"] or (None, None))] for b in g.buildings if b["kind"] != "plate"
        ],
    }
