"""Recolector de partidas optimizado para la API key gratuita.

Diferencias con el sistema antiguo (OP.GG + Playwright + preload.json):

* **Semillas oficiales**: los jugadores salen de league-v4 (páginas de 205
  jugadores por división, o las ligas Master/GM/Challenger completas) que
  ya incluyen PUUID, tier y plataforma. Antes cada nombre costaba de 1 a 16
  llamadas solo para averiguar en qué región jugaba.
* **Un hilo por región de routing** (europe, americas, asia, sea): Riot
  limita por host, así que trabajar en paralelo multiplica el ritmo.
* **Solo ranked** (``queue=420`` o ``type=ranked``) y deduplicación antes de
  pedir el detalle: cada partida nueva cuesta ~1 llamada.
* **Incremental**: de cada jugador solo se piden partidas posteriores a la
  última vista (``startTime``).
* **Prioridad baja**: deja cupo libre para las búsquedas de la web.
* **Por campeones (OTPs)**: con campeones elegidos, de cada jugador del ranking
  del servidor y elo elegidos se mira su maestría (1 llamada, se recuerda 7
  días) y solo se recogen las partidas de los OTPs de esos campeones (o de quien
  tenga muchísima maestría con ellos). La API de Riot no tiene un ranking de
  maestría por campeón, así que se busca así, a ritmo de la key.

  De cada OTP se baja **su historial entero** (sin tope de partidas, de 100 en
  100 hacia atrás) hasta la profundidad elegida (temporada actual por defecto).
  Primero lo reciente de todos los OTPs y después lo más antiguo, y sus
  partidas nuevas van antes que el historial viejo. La búsqueda de OTPs va en
  un hilo aparte: la maestría y las ligas usan el host de la plataforma (kr,
  euw1…) y las partidas el de la región (asia, europe…), que Riot limita por
  separado, así que buscar no le quita ritmo a la descarga.
"""

from __future__ import annotations

import calendar
import contextlib
import json
import queue
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, deque
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from app.data.db import now_s
from app.data.repository import known_match_ids, mark_skipped, store_match, upsert_seed_players
from app.ingest import otp_details
from app.ingest import patterns as pattern_search
from app.ingest.extractor import parse_match
from app.logbus import get_logger
from app.riot.client import Cancelled, RiotAPIError, RiotKeyError
from app.riot.ratelimit import Priority
from app.riot.routing import APEX_TIERS, DIVISIONS, PLATFORMS, QUEUE_SOLO, REGIONS, ROLES, TIER_INDEX, TIERS, region_of

log = get_logger("crawler")

APEX_REFRESH_S = 12 * 3600
LADDER_RESET_S = 24 * 3600
EXHAUSTED_COOLDOWN_S = 20 * 60
MASTERY_TTL_S = 7 * 86400
MASTERY_BATCH = 25
SPECIALIST_RATIO_CAP = 10.0  # principal con 10 veces o más los puntos del segundo: igual de especialista
BOARD_RATIO = SPECIALIST_RATIO_CAP + 1  # en la tabla de los mejores de OP.GG: el que más pinta tiene
FOCUSES = ("otp", "mastery", "games")
GAMES_MIN_MASTERY = 10_000  # mínimo de puntos con el campeón (entre sus 3 primeros) para cualquier búsqueda
# Qué es un OTP: «ratio» (el buscador de la web): su campeón con más maestría lo juega muy por encima del segundo
# (``OTP_RATIO``), sin revisar sus partidas para decidirlo; «share» (configuraciones antiguas): la mitad o más de sus
# partidas revisadas con él.
OTP_RULES = ("share", "ratio")
OTP_RATIO = otp_details.OTP_RATIO
# Colas que se pueden buscar y sus colas de Riot. Normales: recluta, a ciegas, Swiftplay y Quickplay (en la Grieta;
# ARAM, URF y demás modos no cuentan).
QUEUE_GROUPS = {"solo": (QUEUE_SOLO,), "flex": (440,), "normal": (400, 430, 480, 490)}
# Variedad antes que cantidad: de cada jugador, primero sus 20 partidas con esos campeones (el resto de su historial,
# cuando ya no quedan jugadores por revisar) y nunca se vuelve a por sus partidas nuevas.
VARIETY_GAMES = 20
# Especialistas: qué parte de sus partidas tiene que ser con ese campeón. La maestría es de toda la vida de la cuenta
# (300 K con Lee Sin y 130 K con Yuumi no hacen a nadie OTP si hoy juega diez campeones): lo que cuenta es lo que juega,
# medido en las partidas suyas que el recolector revisa (las baja igualmente para saber con quién jugó cada una).
SHARE_OPTIONS = (0.0, 0.25, 0.5, 0.75)
DEFAULT_SHARE = otp_details.OTP_MIN_SHARE
SHARE_MIN_SEEN = otp_details.OTP_SHARE_MIN_SEEN  # partidas revisadas para juzgarlo
FOCUS_NOUN = {"otp": "OTPs", "mastery": "jugadores con mucha maestría", "games": "jugadores"}
ELO_MIN_OTPS = 3  # variedad: cada campeón con al menos 3 OTPs en cada elo antes de profundizar en nadie
ROLE_MIN_RATE = 0.08  # «toda una línea» = campeones con ≥ 8 % de sus partidas en ella (tier list de OP.GG/Lolalytics)

# Historial de los OTPs (sin tope de partidas por jugador).
OTP_PAGE = 100  # ids por petición de match-v5 (el máximo de Riot)
OTP_VISIT_MATCHES = 100  # partidas descargadas por visita antes de pasar a otro OTP (reparto justo)
# Reparto por elo, igual en todos los modos y con cualquier selección de elos: cada elo elegido recibe una parte de las
# partidas nuevas inversa (al cuadrado) a las partidas analizadas que ya tiene (las de la gráfica «Partidas por elo
# (total)»), con un mínimo para que ninguno se quede sin partidas nuevas. Cada visita (y cada búsqueda en el ranking) va
# al elo más por debajo de su parte en lo recogido en esta sesión, contando las visitas en curso de las 4 regiones; al
# arrancar, el de más parte. Antes (25-11-2026): con todo el ranking se contaban las partidas de la tabla ``matches`` (las
# del ranking entero, donde Hierro y Bronce eran los que menos tenían) y no las analizadas (donde lo son Bronce y Oro); y
# siempre iba el elo con menos partidas, así que los demás no recibían ninguna hasta alcanzarlo.
RECENT_TIER_VISITS = 40  # (compatibilidad) visitas recientes
RECENT_TIER_GAMES = 3000  # últimas partidas analizadas por elo (diagnóstico: «recientes_pct» del embudo)
MIN_TIER_SHARE = 0.03  # cada elo elegido se lleva al menos el 3 % de las partidas nuevas
SHARE_POWER = 2  # con 2.400 frente a 17.400 partidas, unas 50 veces más parte (antes del mínimo)
SHARE_SMOOTH = 100  # partidas que se suman a cada elo al invertir (uno con 0 no se lo lleva todo)
NEXT_VISIT_GAMES = 50  # lo que puede dar la siguiente visita (para medir cuánto le falta a cada elo)
ACTIVE_VISIT_GAMES = 25  # lo que se cuenta por una visita en curso (menos lo que ya lleva analizado)
# Las últimas partidas analizadas se guardan en la base de datos (cada 5 min y al detener): antes se perdían al
# reiniciar y cada sesión de 5-15 min (unas 10 visitas) empezaba el reparto de cero.
RECENT_META_KEY = "crawler_recent_games"
PATTERN_SCAN_WAIT_S = 120  # espera máxima de las regiones a la búsqueda de las ventanas en lo ya guardado
VISIT_LOG_SIZE = 40  # últimas visitas en el embudo (``_busqueda.json``): elo, por qué se eligió, cuánto dio y por qué acabó
PLAN_KIND = {0: "nuevo", 1: "partidas nuevas", 2: "historial", 3: "sin análisis"}
OTP_BACK_PAGES = 5  # páginas del historial por visita (las ya conocidas no gastan descargas)
OTP_FORWARD_PAGES = 10  # partidas nuevas listadas por visita (hasta 1.000)
OTP_LOW_SHARE = 0.10  # páginas del historial con ≤ 10 % de partidas con el campeón…
OTP_LOW_SHARE_MIN = 30  # …(de al menos 30 revisadas)…
OTP_LOW_PAGES = 2  # …dos seguidas, pasadas sus 200 más recientes: más atrás ya no lo jugaba
OTP_LOW_SKIP = 200  # sus partidas más recientes nunca paran el historial (puede estar de descanso del campeón)
OTP_ANCHOR_LAG_S = 2 * 3600  # el ancla va 2 h atrás: la partida en curso o recién acabada entra por «nuevas»
OTP_IDLE_WAIT_S = 30
OTP_FAIL_COOLDOWN_S = 3600  # un OTP que falla 3 veces seguidas descansa una hora
# Periodo de la búsqueda de OTPs: cuenta quien jugó el campeón en él y se bajan sus partidas de ese mismo periodo.
OTP_DEPTHS = ("30", "90", "180", "season", "365", "all")
STOP_REPORT_AFTER_S = 15  # si al detener sigue algún hilo pasado esto, el registro dice cuál y dónde…
STOP_REPORT_EVERY_S = 30  # …y lo repite cada tanto
STOP_GIVE_UP_S = 900
FUNNEL_EVERY_S = 300  # cada cuánto se escribe data/otps/_busqueda.json
LEADERBOARD_TTL_S = 24 * 3600  # la tabla de los mejores de cada campeón (OP.GG) se vuelve a pedir una vez al día
LEADERBOARD_TIERS = {"DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"}  # elos que salen en esa tabla
LEADERBOARD_MAX = 20  # jugadores por tabla: cada uno nuevo cuesta una llamada de account-v1 (cupo regional)
OPGG_REGION = {p: label for p, (_, label, _) in PLATFORMS.items()} | {"sg2": "SG"}
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}
OTP_DETAIL_BACKFILL = 25  # partidas ya guardadas a las que se les baja el micro/macro por visita (2 llamadas cada una)


def season_start(version: str | None, now: int | None = None) -> tuple[int, int | None]:
    """(inicio de la temporada en s, número mayor del parche de la temporada o ``None``).

    Riot numera los parches por año (16.x = 2026). La temporada empieza con el parche N.1 a primeros de enero: se toma
    el 1 de enero (UTC) y, al bajar el historial, se para en la primera partida de un parche de la temporada anterior.
    Sin versión conocida se usa el año en curso.
    """
    now = now or now_s()
    this_year = time.gmtime(now).tm_year
    head = str(version or "").split(".")[0]
    major = int(head) if head.isdigit() else None
    year = 2010 + major if major else None
    if year is None or not 2020 <= year <= this_year:
        year, major = this_year, None
    return min(calendar.timegm((year, 1, 1, 0, 0, 0, 0, 0, 0)), now), major


def _patch_major(patch: str | None) -> int | None:
    head = str(patch or "").split(".")[0]
    return int(head) if head.isdigit() else None


def _roles_key(cfg: CrawlerConfig) -> str:
    """Roles pedidos por campeón («|r:266:TOP;517:JUNGLE+MID») y líneas completas («|l:TOP»); vacío sin ninguno (así
    las claves de antes no cambian). Las líneas van por nombre, no por su lista de campeones: si la tier list cambia
    un poco de un parche a otro, los historiales no se recorren de nuevo."""
    roles = cfg.champion_roles or {}
    out = ("|r:" + ";".join(f"{c}:{'+'.join(roles[c])}" for c in sorted(roles))) if roles else ""
    return out + (f"|l:{'+'.join(cfg.roles)}" if cfg.roles else "")


def otp_list_key(cfg: CrawlerConfig) -> str:
    """Qué se pide y qué se descarta: colas (Flex sí/no), campeones (y sus roles) y «solo sus partidas con esos
    campeones». Si cambia, el historial se recorre de nuevo (lo ya guardado no se descarga; lo descartado que ahora
    interesa, sí)."""
    return f"{_queues_key(cfg)}|{','.join(str(c) for c in sorted(cfg.champions))}|{int(cfg.only_target_games)}{_roles_key(cfg)}"


def _queues_key(cfg: CrawlerConfig) -> str:
    """Las colas en la clave de la lista: «420» (SoloQ) y «ranked» (SoloQ y Flex) como antes; el resto, por nombre."""
    chosen = tuple(cfg.queues or ["solo"])
    return {("solo",): str(QUEUE_SOLO), ("solo", "flex"): "ranked"}.get(chosen) or "q:" + "+".join(chosen)


def otp_depth_start(cfg: CrawlerConfig, version: str | None, now: int | None = None) -> tuple[int, int | None]:
    """(``startTime`` del historial de los OTPs, 0 = sin límite; número mayor del parche de la temporada o None)."""
    depth = cfg.otp_depth
    if depth == "all":
        return 0, None
    if depth == "season":
        return season_start(version, now)
    return (now or now_s()) - int(depth) * 86400, None


def otp_config_key(cfg: CrawlerConfig) -> str:
    """Campeones, roles y filtro: si cambian, un historial parado por «ya no lo jugaba» se reabre."""
    share = f"|s:{round(100 * cfg.min_share)}" if cfg.focus == "otp" and cfg.min_share != DEFAULT_SHARE else ""
    return f"{','.join(str(c) for c in sorted(cfg.champions))}|{int(cfg.only_target_games)}{_roles_key(cfg)}{share}"


def target_sql(cfg: CrawlerConfig, alias: str = "") -> tuple[str, list]:
    """Condición SQL «partida con uno de los campeones elegidos, en su rol» sobre ``participants`` y sus parámetros."""
    col = f"{alias}." if alias else ""
    by_roles: dict[tuple[str, ...], list[int]] = {}
    free = []
    for champ in cfg.targets:
        roles = cfg.roles_for(champ)
        if roles is None:
            free.append(champ)
        else:
            by_roles.setdefault(tuple(roles), []).append(champ)
    parts, params = [], []
    if free:
        parts.append(f"{col}champ IN ({','.join('?' * len(free))})")
        params.extend(free)
    for roles, champs in by_roles.items():
        parts.append(f"({col}champ IN ({','.join('?' * len(champs))}) AND {col}role IN ({','.join('?' * len(roles))}))")
        params.extend([*champs, *roles])
    return ("(" + " OR ".join(parts) + ")") if parts else "0", params


def otp_history_state(row, list_key: str, depth_start: int, config_key: str | None = None) -> str:
    """Estado del historial de un OTP: ``queued`` (sin empezar), ``loading``, ``done`` o ``share`` (más atrás ya no
    jugaba esos campeones). Si ahora se pide más historial que cuando se completó, o cambian los campeones o el filtro
    de una parada por «ya no lo jugaba», vuelve a ``loading``."""
    if row["anchor"] is None or row["list_key"] != list_key:
        return "queued"
    if row["back_done_at"] is None:
        return "loading"
    if row["back_done_reason"] == "share":
        # sqlite3.Row no admite «in»
        stored = row["config_key"] if "config_key" in row.keys() else None  # noqa: SIM118
        return "loading" if config_key is not None and stored != config_key else "share"
    if depth_start < (row["back_done_start"] or 0):
        return "loading"
    return "done"


@dataclass
class CrawlerConfig:
    platforms: list[str] = field(default_factory=lambda: ["euw1", "kr", "na1"])
    tiers: list[str] = field(default_factory=lambda: ["EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"])
    include_flex: bool = False
    matches_per_player: int = 20
    recrawl_hours: float = 12.0
    # Búsqueda por campeones: OTPs («otp»), jugadores con mucha maestría («mastery») o **partidas de un campeón**
    # («games»: toda partida en la que alguien lo juegue en ese rol, sea OTP o no; los jugadores salen de quienes lo
    # tienen entre sus 3 más jugados y se guarda cualquier partida suya en la que aparezca, aunque lo juegue otro; sus
    # partidas con él también se analizan minuto a minuto).
    champions: list[int] = field(default_factory=list)
    focus: str = "otp"
    min_mastery: int = 300_000
    # Especialistas («otp»): parte mínima de sus partidas (revisadas) con ese campeón. 0 = da igual («mastery»).
    min_share: float = DEFAULT_SHARE
    only_target_games: bool = True
    # Hasta dónde se baja el historial de cada OTP: temporada actual, N días o todo lo que tenga Riot.
    otp_depth: str = "season"
    # Rol buscado de cada campeón ({campeón: [roles]}): sin entrada = cualquier rol. Ej.: Sylas solo jungla o mid.
    champion_roles: dict[int, list[str]] = field(default_factory=dict)
    # Líneas completas: OTPs de todos los campeones que se juegan en ellas (p. ej. solo top), en esa línea.
    roles: list[str] = field(default_factory=list)
    # Campeones de esas líneas (los pone ``Crawler.start`` con la tier list; de más a menos jugados).
    role_champions: list[int] = field(default_factory=list)
    # Los mismos, por línea: cada campeón cuenta solo en la línea de cuya lista sale (Sylas de mid no se busca en top
    # aunque elijas top y mid). Vacío (configuración antigua): en todas las líneas elegidas.
    role_pools: dict[str, list[int]] = field(default_factory=dict)
    # Versión del juego al arrancar (para saber cuándo empezó la temporada).
    season_version: str | None = None
    # Análisis minuto a minuto (micro y macro) de cada partida suya con esos campeones (timeline: 1 llamada más por
    # partida). Siempre activo para el usuario (la web y la API no lo ofrecen); solo los tests lo apagan para aislar.
    save_details: bool = True
    # Variedad antes que cantidad: mientras la búsqueda sigue encontrando OTPs, de cada uno solo se bajan sus N
    # partidas más recientes con esos campeones; el resto de su historial, cuando ya no quedan jugadores por revisar.
    # 0 = sin tope.
    games_per_otp: int = 30
    # Búsqueda por partidas (``patterns``): ventanas tipo minidraft con matchups («Lee Sin vs Hecarim en jungla y Ahri vs
    # Viktor en mid, en la misma partida») o composiciones. Con ventanas, los campeones y sus roles salen de ellas, el
    # elo no filtra (todos) y solo se guardan como resultado las partidas que encajan (enteras, con los 10 analizados).
    patterns: list[dict] = field(default_factory=list)
    # Colas («solo», «flex», «normal»; ver ``QUEUE_GROUPS``). Vacío (configuración antigua): SoloQ, y Flex con
    # ``include_flex``.
    queues: list[str] = field(default_factory=list)
    # Qué es un OTP (``OTP_RULES``).
    otp_rule: str = "share"

    def validated(self) -> CrawlerConfig:
        from app.ingest import patterns as pat

        windows = pat.clean(self.patterns)
        platforms, tiers = self._platforms_tiers(bool(windows))
        rule = self.otp_rule if self.otp_rule in OTP_RULES else "share"
        focus, share = self._focus_share(rule)
        roles = [] if windows else [r for r in ROLES if r in {str(x).upper() for x in (self.roles or [])}]
        per_otp = int(self.games_per_otp or 0)
        queues = self._queues()
        return CrawlerConfig(
            platforms=platforms,
            tiers=tiers,
            include_flex="flex" in queues,
            queues=queues,
            otp_rule=rule,
            matches_per_player=max(5, min(int(self.matches_per_player), 100)),
            recrawl_hours=_recrawl_hours(self.recrawl_hours),
            champions=pat.champions(windows) if windows else _ids(self.champions),
            focus=focus,
            min_mastery=self._min_mastery(rule, focus),
            min_share=share,
            only_target_games=bool(self.only_target_games) or self.focus == "games" or bool(windows),
            otp_depth=str(self.otp_depth) if str(self.otp_depth) in OTP_DEPTHS else "season",
            champion_roles=pat.champion_roles(windows) if windows else self._clean_roles(),
            save_details=bool(self.save_details),
            games_per_otp=max(5, min(per_otp, 1000)) if per_otp else 0,
            roles=roles,
            role_champions=_ids(self.role_champions) if roles else [],
            role_pools=self._pools(roles),
            season_version=str(self.season_version) if self.season_version else None,
            patterns=windows,
        )

    def _platforms_tiers(self, by_game: bool) -> tuple[list[str], list[str]]:
        """Servidores y elos válidos; buscando por partidas, todos los elos (el elo no filtra)."""
        platforms = [p for p in dict.fromkeys(p.lower() for p in self.platforms) if p in PLATFORMS]
        if by_game:
            tiers = sorted(TIER_INDEX, key=TIER_INDEX.get)
        else:
            tiers = sorted({t.upper() for t in self.tiers if t.upper() in TIER_INDEX}, key=TIER_INDEX.get)
        if not platforms:
            raise ValueError("Selecciona al menos una plataforma válida")
        if not tiers:
            raise ValueError("Selecciona al menos un tier")
        return platforms, tiers

    def _min_mastery(self, rule: str, focus: str) -> int:
        """Mínimo de puntos con el campeón: buscando con el buscador sencillo (``rule`` «ratio»), solo el de cuentas
        nuevas (``GAMES_MIN_MASTERY``); antes, el elegido."""
        if rule == "ratio":
            return GAMES_MIN_MASTERY
        default = GAMES_MIN_MASTERY if focus == "games" else 300_000
        return max(GAMES_MIN_MASTERY, min(int(self.min_mastery or default), 10_000_000))

    def _queues(self) -> list[str]:
        """Colas pedidas, en orden (``QUEUE_GROUPS``); sin ninguna (configuración antigua), SoloQ y, con
        ``include_flex``, también Flex."""
        wanted = {str(q).lower() for q in (self.queues or [])}
        return [q for q in QUEUE_GROUPS if q in wanted] or (["solo", "flex"] if self.include_flex else ["solo"])

    def _focus_share(self, rule: str = "share") -> tuple[str, float]:
        """A quién se busca y, si son especialistas, qué parte mínima de sus partidas con el campeón. Con OTP por
        maestría (``rule`` «ratio»): OTPs o cualquiera, sin mínimo de partidas con él."""
        focus = self.focus if self.focus in FOCUSES else "otp"
        if rule == "ratio":
            return ("games" if focus == "games" else "otp"), 0.0
        try:
            share = float(self.min_share)
        except (TypeError, ValueError):
            share = DEFAULT_SHARE
        share = min(SHARE_OPTIONS, key=lambda o: abs(o - share))
        if focus == "otp" and share <= 0:
            focus = "mastery"  # especialistas sin mínimo de partidas = mucha maestría
        return focus, (share if focus == "otp" else 0.0)

    def _pools(self, roles: list[str]) -> dict[str, list[int]]:
        """Campeones de cada línea elegida (de la tier list)."""
        return {str(lane).upper(): _ids(champs) for lane, champs in (self.role_pools or {}).items() if str(lane).upper() in roles}

    @classmethod
    def from_saved(cls, data: dict) -> CrawlerConfig:
        """Configuración guardada (``meta``), aunque traiga campos de versiones anteriores de la app."""
        known = {f.name for f in fields(cls)}
        data = dict(data or {})
        data["save_details"] = True  # ya no es opcional: una configuración antigua con él apagado se reanuda con él
        if "otp_rule" not in data:
            data.update(_simple_search(data))
        return cls(**{k: v for k, v in data.items() if k in known}).validated()

    @property
    def is_ratio_otp(self) -> bool:
        """¿Busca OTPs por maestría (el buscador de la web)?"""
        return self.focus == "otp" and self.otp_rule == "ratio"

    def main_otp(self, top: list, now: int | None = None) -> int | None:
        """OTP por maestría: su campeón con más puntos si es de los buscados, con ``OTP_RATIO`` veces los del segundo (o
        sin segundo), el mínimo de puntos y jugado en el periodo; si no, ``None``."""
        entries = [e for e in (top or []) if len(e) >= 2]
        if not entries:
            return None
        champ, points = int(entries[0][0]), int(entries[0][1])
        second = int(entries[1][1]) if len(entries) > 1 else 0
        if champ not in set(self.targets) or points < self.min_mastery or points < OTP_RATIO * second:
            return None
        last = int(entries[0][2]) if len(entries[0]) > 2 and entries[0][2] else None
        return champ if last is None or last >= self.active_since(now) else None

    def queue_ids(self) -> tuple[int, ...]:
        """Colas de Riot de las partidas que cuentan (``queues``)."""
        return tuple(q for name in (self.queues or ["solo"]) for q in QUEUE_GROUPS.get(name, ()))

    def list_filter(self) -> tuple[int | None, str | None]:
        """Cómo pedir a Riot las listas de partidas: (cola, tipo). Una cola sola, o el tipo entero (ranked o normal) si
        es justo lo elegido; si no, sin filtro, y lo que no cuenta (ARAM, URF o la cola que no se pidió) se descarta al
        bajarla."""
        chosen = tuple(self.queues or ["solo"])
        if chosen in (("solo",), ("flex",)):
            return QUEUE_GROUPS[chosen[0]][0], None
        return None, {("solo", "flex"): "ranked", ("normal",): "normal"}.get(chosen)

    @property
    def targets(self) -> list[int]:
        """Campeones buscados: los elegidos uno a uno y los de las líneas completas."""
        return list(dict.fromkeys([*self.champions, *self.role_champions]))

    def roles_for(self, champ: int) -> list[str] | None:
        """Roles en los que cuenta ese campeón (``None`` = cualquiera)."""
        own = self.champion_roles.get(champ) if champ in self.champions else None
        if champ in self.champions and own is None:
            return None
        if champ not in self.role_champions:
            lanes = []
        elif self.role_pools:
            lanes = [r for r in self.roles if champ in (self.role_pools.get(r) or [])]
        else:
            lanes = list(self.roles)
        wanted = set(own or []) | set(lanes)
        return [r for r in ROLES if r in wanted] or None

    def active_since(self, now: int | None = None) -> int:
        """Cuenta como OTP quien jugó el campeón desde aquí (el inicio del periodo; 0 = sin límite)."""
        return otp_depth_start(self, self.season_version, now)[0]

    def _clean_roles(self) -> dict[int, list[str]]:
        champions = {int(c) for c in (self.champions or []) if str(c).isdigit()}
        out: dict[int, list[str]] = {}
        for key, roles in (self.champion_roles or {}).items():
            champ = int(key) if str(key).isdigit() else 0
            wanted = {str(r).upper() for r in (roles or [])}
            clean = [r for r in ROLES if r in wanted]
            # Los 5 roles = cualquier rol: no se guarda (no cambia qué se recoge).
            if champ in champions and clean and len(clean) < len(ROLES):
                out[champ] = clean
        return out

    def is_target(self, champ: int | None, role: str | None) -> bool:
        """¿Partida con uno de los campeones elegidos y en el rol que buscas (si le pusiste rol)?

        Sin rol fiable en esa partida (``teamPosition`` repetido o vacío) no cuenta para un campeón con rol elegido.
        """
        if champ is None or champ not in self.targets:
            return False
        roles = self.roles_for(champ)
        return not roles or role in roles

    def qualifies(self, top: list, now: int | None = None) -> bool:
        """¿Este jugador es candidato: alguno de los campeones elegidos entre sus 3 con más puntos, con el mínimo de
        maestría y jugado en el periodo? (Si de verdad es especialista se comprueba después, con sus partidas:
        ``share_ok``.)

        ``top``: [[campeón, puntos, última partida (s, opcional)], …] de más a menos puntos.
        """
        targets = set(self.targets)
        if not targets or not top:
            return False
        if self.is_ratio_otp:
            return self.main_otp(top, now) is not None
        cutoff = self.active_since(now)

        def active(entry) -> bool:
            last = int(entry[2]) if len(entry) > 2 and entry[2] else None
            return last is None or last >= cutoff

        return any(int(e[0]) in targets and int(e[1]) >= self.min_mastery and active(e) for e in top[:3])

    def own_champs(self, top: list, sourced=(), now: int | None = None) -> set[int]:
        """Buscando OTPs: los campeones por los que este jugador es candidato (de los elegidos, entre sus 3 con más
        puntos con el mínimo de maestría y jugados en el periodo, o por los que sale en la tabla de los mejores). Solo
        cuentan y se bajan con timeline sus partidas con ellos: con «todos los campeones» se bajaban todas las suyas
        (acababan en «resto») y su parte con los elegidos salía siempre al 100 %, así que nunca se dejaba a nadie."""
        targets = set(self.targets)
        cutoff = self.active_since(now)
        own = {int(c) for c in sourced if int(c) in targets}
        if self.is_ratio_otp:
            main = self.main_otp(top, now)
            return own | ({main} if main is not None else set())
        for e in (top or [])[:3]:
            last = int(e[2]) if len(e) > 2 and e[2] else None
            if int(e[0]) in targets and int(e[1]) >= self.min_mastery and (last is None or last >= cutoff):
                own.add(int(e[0]))
        return own

    def share_ok(self, seen: int | None, hits: int | None) -> bool:
        """Con sus partidas revisadas: ¿juega esos campeones lo bastante? Solo filtra buscando especialistas con un mínimo
        de partidas. Sin la muestra aún (``SHARE_MIN_SEEN``) se le da el beneficio de la duda mientras pueda llegar: si
        ni con todas las que faltan hasta la muestra con esos campeones llegaría al mínimo, ya no. Con 11 de 20 sin
        ellos ya no llega al 50 %: antes se bajaban las 20 igual para el mismo veredicto (de 149 descartados, 38 no
        tenían ninguna con su campeón; 25-11-2026)."""
        seen, hits = int(seen or 0), int(hits or 0)
        if self.focus != "otp" or self.min_share <= 0:
            return True
        if seen < SHARE_MIN_SEEN:
            return hits + (SHARE_MIN_SEEN - seen) >= self.min_share * SHARE_MIN_SEEN
        return hits >= self.min_share * seen


def _recrawl_hours(value) -> float:
    """Cada cuánto se vuelve a por las partidas nuevas de un jugador ya visitado: 0 = nunca (se busca variedad)."""
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if hours <= 0 else max(1.0, min(hours, 168.0))


def _simple_search(data: dict) -> dict:
    """Una búsqueda guardada con el buscador antiguo, al nuevo (26-11-2026): OTP por maestría (o cualquiera), sin
    mínimos de puntos ni de partidas con él, variedad (``VARIETY_GAMES`` de cada jugador primero, sin volver a por sus
    partidas nuevas) y las colas en lista."""
    return {
        "otp_rule": "ratio",
        "focus": "games" if data.get("focus") == "games" else "otp",
        "min_share": 0.0,
        "recrawl_hours": 0,
        "games_per_otp": VARIETY_GAMES,
        "only_target_games": True,
        "queues": ["solo", "flex"] if data.get("include_flex") else ["solo"],
    }


DIVISION_STEP = {"IV": 0, "III": 1, "II": 2, "I": 3}
RANK_TTL_S = 7 * 86400  # el elo de un jugador sin clasificar no se vuelve a pedir en una semana


def rank_value(tier: str | None, division: str | None) -> float | None:
    """Elo en número para hacer medias (Hierro IV = 0, cada división 1, cada elo 4; Maestro y más, sin divisiones)."""
    t = str(tier or "").upper()
    if t not in TIER_INDEX:
        return None
    return float(TIER_INDEX[t] * 4 + (0 if t in APEX_TIERS else DIVISION_STEP.get(str(division or "").upper(), 0)))


def rank_of_value(value: float) -> dict:
    """El elo (``tier`` y ``division``) más cercano a un número de ``rank_value``."""
    idx = min(len(TIERS) - 1, max(0, int(value // 4)))
    tier = TIERS[idx]
    if tier in APEX_TIERS:
        return {"tier": tier, "division": "I", "lp": None}
    step = min(3, max(0, round(value - idx * 4)))
    return {"tier": tier, "division": ("IV", "III", "II", "I")[step], "lp": None}


def _ids(values) -> list[int]:
    """Ids de campeón válidos (positivos), sin repetir y en orden."""
    return list(dict.fromkeys(int(c) for c in (values or []) if str(c).isdigit() and int(c) > 0))


def _config_with(config: CrawlerConfig, **changes) -> CrawlerConfig:
    """Copia de la configuración con esos campos cambiados (lo mismo que ``dataclasses.replace``, con su tipo)."""
    return CrawlerConfig(**({f.name: getattr(config, f.name) for f in fields(config)} | changes))


def role_pool(svc, roles: list[str]) -> list[int]:
    """Campeones que se juegan en alguna de esas líneas, de más a menos jugados (todas las listas de ``role_pools``)."""
    return role_pool_from(role_pools(svc, roles))


def role_pool_from(pools: dict[str, list[int]]) -> list[int]:
    """Une las listas por línea (cada campeón en su mejor puesto)."""
    best: dict[int, int] = {}
    for champs in pools.values():
        for i, c in enumerate(champs):
            best[c] = min(best.get(c, i), i)
    return [c for c, _ in sorted(best.items(), key=lambda kv: kv[1])]


def role_pools(svc, roles: list[str]) -> dict[str, list[int]]:
    """Por línea, los campeones que se juegan en ella (≥ 8 % de sus partidas ahí), de más a menos jugados.

    Sale de la tier list de OP.GG o, si no, de la de Lolalytics (las que usa el resto de la app); sin ninguna, de las
    partidas de tu base de datos.
    """
    pools: dict[str, list[int]] = {}
    scope = _tier_list_scope(svc)
    for role in roles:
        data = _web_tier_list(svc, scope, role)
        rows = [(int(c), float(r.get("pick") or 0)) for c, r in data.items() if (r.get("role_rate") or 0) >= ROLE_MIN_RATE]
        if not rows:  # sin webs: lo que dicen tus partidas guardadas
            rows = _stored_role_rows(svc, role)
        if rows:
            pools[role] = [c for c, _ in sorted(rows, key=lambda kv: -kv[1])]
    return pools


def _tier_list_scope(svc):
    """Elo, región y parches de las tier lists de las webs (``None`` sin parches conocidos o con las webs desactivadas)."""
    from app.analytics.evidence import ext_scope  # aquí: evita un import circular al arrancar
    from app.analytics.filters import Filters

    try:
        return ext_scope(svc, Filters())
    # sin parches conocidos o webs desactivadas
    except Exception:  # noqa: BLE001
        return None


def _web_tier_list(svc, scope, role: str) -> dict:
    """Tier list de esa línea en OP.GG o, si no, en Lolalytics (``{}`` si ninguna la da)."""
    data: dict = {}
    for name in ("opgg", "lolalytics"):
        src = getattr(svc, name, None)
        if scope is None or src is None or not getattr(src, "enabled", False):
            continue
        try:
            data = src.tier_list(role, scope.tier, scope.region, scope.patches[0]) or {}
        # web caída: se prueba con la siguiente
        except Exception as exc:  # noqa: BLE001
            log.warning("Tier list de %s (%s): %s", role, name, exc)
            data = {}
        if data:
            break
    return data


def _stored_role_rows(svc, role: str) -> list[tuple[int, float]]:
    """Campeones que se juegan en esa línea según tus partidas guardadas: [(campeón, partidas en ella)]."""
    return [
        (int(r["champ"]), float(r["n"]))
        for r in svc.db.query(
            "SELECT champ, SUM(role = ?) AS n, COUNT(*) AS total FROM participants GROUP BY champ "
            "HAVING n >= 20 AND n >= ? * total",
            (role, ROLE_MIN_RATE),
        )
    ]


def search_funnel(db, cfg: CrawlerConfig, now: int | None = None) -> dict:
    """Dónde se quedan los jugadores revisados (por plataforma y en total): para ver por qué salen pocos OTPs.

    Fases (las de tu configuración): revisados → con el campeón entre sus 3 primeros → con el mínimo de puntos →
    jugado en el periodo → (especialistas) comprobado con sus partidas: juega el campeón lo bastante.
    """
    now = now or now_s()
    targets = set(cfg.targets)
    platforms, tiers = list(cfg.platforms), list(cfg.tiers)
    if not targets or not platforms or not tiers:
        return {"stages": [], "platforms": {}}
    pm, tm = ",".join("?" * len(platforms)), ",".join("?" * len(tiers))
    cutoff = cfg.active_since(now)
    stages = {
        "otp": ["checked", "champ", "ratio", "active"] if cfg.is_ratio_otp else ["checked", "champ", "points", "active", "share"],
        "mastery": ["checked", "champ", "points", "active"],
        "games": ["checked", "champ", "points", "active"],
    }[cfg.focus]
    per: dict[str, dict] = {}
    for r in db.query(
        f"SELECT platform, COUNT(*) AS n FROM players WHERE is_seed = 1 AND platform IN ({pm}) AND tier IN ({tm}) GROUP BY platform",
        (*platforms, *tiers),
    ):
        _funnel_bucket(per, r["platform"])["seeds"] = int(r["n"])
    for r in db.query(
        f"SELECT p.platform, m.top, m.checked_at, o.seen, o.hits FROM mastery_checks m JOIN players p ON p.puuid = m.puuid "
        f"LEFT JOIN otp_progress o ON o.puuid = p.puuid "
        f"WHERE p.is_seed = 1 AND p.platform IN ({pm}) AND p.tier IN ({tm})",
        (*platforms, *tiers),
    ):
        b = _funnel_bucket(per, r["platform"])
        b["checked"] += 1
        b["last_hour"] += int((r["checked_at"] or 0) >= now - 3600)
        _funnel_stages(b, r, cfg, targets, cutoff)
    total = {k: sum(b[k] for b in per.values()) for k in (*stages, "seeds", "last_hour")}
    shown = {p: {k: b[k] for k in (*stages, "seeds", "last_hour")} for p, b in per.items()}  # solo las fases de este modo
    return {"stages": stages, "total": total, "platforms": dict(sorted(shown.items(), key=lambda kv: -kv[1]["checked"])), "at": now}


def _funnel_bucket(per: dict[str, dict], platform: str) -> dict:
    """Contadores del embudo de esa plataforma (a 0 la primera vez)."""
    return per.setdefault(platform, dict.fromkeys(("checked", "champ", "points", "ratio", "active", "share", "seeds", "last_hour"), 0))


def _funnel_stages(b: dict, r, cfg: CrawlerConfig, targets: set[int], cutoff: int) -> None:
    """Suma a su plataforma las fases del embudo que pasa un jugador revisado (las de ``search_funnel``)."""
    try:
        top = [e for e in json.loads(r["top"]) if len(e) >= 2]
    except (TypeError, ValueError):
        return
    if cfg.is_ratio_otp:
        _funnel_ratio(b, top, cfg, targets, cutoff)
        return
    entry = next((e for e in top[:3] if int(e[0]) in targets), None)
    if entry is None:
        return
    b["champ"] += 1
    if int(entry[1]) < cfg.min_mastery:
        return
    b["points"] += 1
    last = int(entry[2]) if len(entry) > 2 and entry[2] else None
    if last is not None and last < cutoff:
        return
    b["active"] += 1
    # Especialista comprobado: con sus partidas revisadas, juega el campeón lo bastante (sin muestra aún, no cuenta).
    if cfg.focus == "otp" and int(r["seen"] or 0) >= SHARE_MIN_SEEN and cfg.share_ok(r["seen"], r["hits"]):
        b["share"] += 1


def _funnel_ratio(b: dict, top: list, cfg: CrawlerConfig, targets: set[int], cutoff: int) -> None:
    """Fases de OTP por maestría: su principal es uno de los buscados → lo juega muy por encima del segundo (OTP) →
    jugado en el periodo."""
    if not top or int(top[0][0]) not in targets:
        return
    b["champ"] += 1
    points, second = int(top[0][1]), int(top[1][1]) if len(top) > 1 else 0
    if points < cfg.min_mastery or points < OTP_RATIO * second:
        return
    b["ratio"] += 1
    last = int(top[0][2]) if len(top[0]) > 2 and top[0][2] else None
    if last is None or last >= cutoff:
        b["active"] += 1


def otp_rows(db, cfg: CrawlerConfig, platforms: list[str] | None = None, tiers: list[str] | None = None, now: int | None = None) -> list:
    """Jugadores del ranking que cumplen la búsqueda por campeones, con su maestría y su progreso: [(fila, top)].

    El filtro por campeón va en SQL (``json_extract`` sobre los 3 primeros de la maestría), así que no depende de
    cuántos jugadores se hayan revisado: antes solo se miraban 300 filas y, con muchos revisados que no eran OTP, los
    OTPs ya encontrados dejaban de volver a visitarse.
    """
    targets = cfg.targets
    if not targets:
        return []
    marks = ",".join("?" * len(targets))
    slots = ("$[0][0]", "$[1][0]", "$[2][0]")
    where = ["p.is_seed = 1"]
    params: list = []
    if platforms:
        where.append(f"p.platform IN ({','.join('?' * len(platforms))})")
        params.extend(platforms)
    if tiers:
        where.append(f"p.tier IN ({','.join('?' * len(tiers))})")
        params.extend(tiers)
    champ_sql = " OR ".join(f"json_extract(m.top, '{slot}') IN ({marks})" for slot in slots)
    # OTP por su maestría o porque sale en la tabla de los mejores de ese campeón (OP.GG).
    where.append(
        f"((CASE WHEN json_valid(m.top) THEN ({champ_sql}) ELSE 0 END) "
        f"OR p.puuid IN (SELECT puuid FROM otp_sources WHERE champ IN ({marks})))"
    )
    params.extend(targets * len(slots) + targets)
    rows = db.query(
        "SELECT p.id, p.puuid, p.platform, p.tier, p.division, p.lp, p.game_name, p.tag_line, p.newest_match_at, "
        "p.last_crawled_at, m.top, m.checked_at, o.list_key, o.anchor, o.back_offset, o.back_done_at, "
        "o.back_done_start, o.back_done_reason, o.fwd_more, o.fwd_newest, o.low_pages, o.config_key, o.seen, o.hits, "
        "(SELECT group_concat(s.champ) FROM otp_sources s WHERE s.puuid = p.puuid) AS sourced "
        "FROM mastery_checks m JOIN players p ON p.puuid = m.puuid LEFT JOIN otp_progress o ON o.puuid = p.puuid "
        f"WHERE {' AND '.join(where)}",
        params,
    )
    now = now or now_s()
    wanted = set(targets)
    out = []
    for row in rows:
        try:
            top = json.loads(row["top"])
        except (TypeError, ValueError):
            continue
        if not cfg.share_ok(row["seen"], row["hits"]):
            continue  # ya revisado: no juega esos campeones lo bastante para lo que buscas
        if cfg.qualifies(top, now) or sourced_champs(row) & wanted:
            out.append((row, top))
    return out


PLAN_UNVISITED_PER_TIER = 100  # sin visitar que entran en la cola por plataforma y elo (ver ``_unvisited_order``)


def _unvisited_order(cfg: CrawlerConfig, now: int) -> tuple[str, tuple]:
    """Qué nunca visitados entran en la cola (``ORDER BY`` y sus parámetros). Buscando OTPs, los de más pinta de
    especialista (``specialist_ratio``: su principal, de los buscados, con el mínimo y jugado en el periodo, cuantas
    más veces los puntos del segundo); a igualdad, y buscando otra cosa, los de más LP. Antes entraban solo por LP:
    con líneas enteras, 2 de cada 3 visitas acababan en «no es especialista» (Platino, 25-11-2026)."""
    if cfg.focus != "otp" or not cfg.targets:
        return "p.lp DESC", ()
    cutoff = cfg.active_since(now)
    # OTP por maestría: solo cuenta quien lo es (su principal, muy por encima del segundo).
    ratio = "AND json_extract(m.top, '$[0][1]') >= ? * COALESCE(json_extract(m.top, '$[1][1]'), 0) " if cfg.is_ratio_otp else ""
    order = (
        f"CASE WHEN json_extract(m.top, '$[0][0]') IN ({','.join('?' * len(cfg.targets))}) "
        f"AND json_extract(m.top, '$[0][1]') >= ? {ratio}AND COALESCE(json_extract(m.top, '$[0][2]'), ?) >= ? THEN 0 ELSE 1 END, "
        "MIN(?, CAST(json_extract(m.top, '$[0][1]') AS REAL) / MAX(1, COALESCE(json_extract(m.top, '$[1][1]'), 0))) DESC, "
        "p.lp DESC"
    )
    ratio_args = (OTP_RATIO,) if cfg.is_ratio_otp else ()
    return order, (*cfg.targets, cfg.min_mastery, *ratio_args, cutoff, cutoff, SPECIALIST_RATIO_CAP)


def otp_rows_for_plan(db, cfg: CrawlerConfig, now: int | None = None) -> list:
    """Como ``otp_rows`` pero solo lo que necesita la cola de visitas: todos los ya visitados (o de las tablas de OP.GG)
    y, de los que nunca se han visitado, ``PLAN_UNVISITED_PER_TIER`` de cada plataforma y elo (``_unvisited_order``).
    Con ~350.000 jugadores encontrados, ordenarlos todos costaba 15-19 s por región en Windows cada vez que se rehacía
    la cola; al visitar unos, entran los siguientes."""
    targets = cfg.targets
    platforms, tiers = list(cfg.platforms), list(cfg.tiers)
    if not targets or not platforms or not tiers:
        return []
    now = now or now_s()
    pm, tm = ",".join("?" * len(platforms)), ",".join("?" * len(tiers))
    cols = (
        "p.id, p.puuid, p.platform, p.tier, p.division, p.lp, p.game_name, p.tag_line, p.newest_match_at, "
        "p.last_crawled_at, m.top, m.checked_at, o.list_key, o.anchor, o.back_offset, o.back_done_at, "
        "o.back_done_start, o.back_done_reason, o.fwd_more, o.fwd_newest, o.low_pages, o.config_key, o.seen, o.hits, "
        "(SELECT group_concat(s.champ) FROM otp_sources s WHERE s.puuid = p.puuid) AS sourced"
    )
    base = (
        "FROM mastery_checks m JOIN players p ON p.puuid = m.puuid LEFT JOIN otp_progress o ON o.puuid = p.puuid "
        f"WHERE p.is_seed = 1 AND p.platform IN ({pm}) AND p.tier IN ({tm})"
    )
    known = db.query(
        f"SELECT {cols} {base} AND (o.puuid IS NOT NULL OR p.puuid IN (SELECT puuid FROM otp_sources))", (*platforms, *tiers)
    )
    order, order_args = _unvisited_order(cfg, now)
    fresh = db.query(
        f"SELECT * FROM (SELECT {cols}, ROW_NUMBER() OVER (PARTITION BY p.platform, p.tier ORDER BY {order}) AS rn "
        f"{base} AND o.puuid IS NULL AND p.puuid NOT IN (SELECT puuid FROM otp_sources)) WHERE rn <= ?",
        (*order_args, *platforms, *tiers, PLAN_UNVISITED_PER_TIER),
    )
    wanted = set(targets)
    out = []
    for row in [*known, *fresh]:
        try:
            top = json.loads(row["top"])
        except (TypeError, ValueError):
            continue
        if not cfg.share_ok(row["seen"], row["hits"]):
            continue
        if cfg.qualifies(top, now) or sourced_champs(row) & wanted:
            out.append((row, top))
    return out


def otp_champs(cfg: CrawlerConfig, top: list, sourced: set[int] | None = None) -> list[int]:
    """Campeones buscados por los que cuenta este jugador: los de su top 3 con el mínimo de maestría o los de la tabla
    de los mejores de cada campeón (OP.GG)."""
    targets = set(cfg.targets)
    if cfg.is_ratio_otp:
        main = cfg.main_otp(top)
        found = [main] if main is not None else []
    else:
        entries = [(int(e[0]), int(e[1])) for e in top[:3] if len(e) >= 2]
        found = [c for c, pts in entries if c in targets and pts >= cfg.min_mastery]
    return list(dict.fromkeys([*found, *sorted((sourced or set()) & targets)]))


def specialist_ratio(cfg: CrawlerConfig, top: list, row) -> float | None:
    """Cuánta pinta de especialista tiene: las veces que los puntos de su principal (si es de los buscados) superan a
    los del segundo, hasta ``SPECIALIST_RATIO_CAP`` (sin segundo, el tope). ``BOARD_RATIO`` si sale en la tabla de los
    mejores de OP.GG; ``None`` si el campeón buscado no es su principal."""
    if sourced_champs(row) & set(cfg.targets):
        return BOARD_RATIO
    entries = [(int(e[0]), int(e[1])) for e in top if len(e) >= 2]
    if not entries or entries[0][0] not in set(cfg.targets):
        return None
    second = entries[1][1] if len(entries) > 1 else 0
    return min(SPECIALIST_RATIO_CAP, entries[0][1] / second) if second > 0 else SPECIALIST_RATIO_CAP


RATIO_BUCKETS = ("tabla", "≥5×", "3-5×", "2-3×", "1,5-2×", "<1,5×")
# Visitas que llegaron a un veredicto (las paradas a medias o por error no dicen nada de si acertó la pinta).
JUDGED_ENDS = frozenset({"pocas con esos campeones", "historial completo", "tope de la visita", "tope por jugador", "páginas de la visita"})


def ratio_bucket(pinta) -> str | None:
    """Grupo de la pinta de especialista de una visita (``_visit_ratio``) para el diagnóstico ``por_pinta``."""
    if pinta == "tabla":
        return "tabla"
    if not isinstance(pinta, (int, float)):
        return None
    for floor, name in ((5, "≥5×"), (3, "3-5×"), (2, "2-3×"), (1.5, "1,5-2×")):
        if pinta >= floor:
            return name
    return "<1,5×"


def specialist_rank(cfg: CrawlerConfig, top: list, row) -> float:
    """Orden de los nuevos por su pinta de especialista (menos = antes; comprobar a uno cuesta ~20 descargas): primero
    los de la tabla de OP.GG; luego, cuanto más concentrada su maestría en su principal, antes; al final, aquellos
    cuyo campeón buscado no es su principal."""
    ratio = specialist_ratio(cfg, top, row)
    return 0.0 if ratio is None else -ratio


def cap_count(cfg: CrawlerConfig, hits: int, seen: int) -> int:
    """Lo que cuenta para el tope «Primero, de cada jugador, sus N partidas»: buscando partidas de un campeón, todas
    las suyas que se bajan (casi todas se guardan: con una línea entera, en casi cualquier partida hay alguien con un
    campeón de ella); buscando OTPs, solo las suyas con ese campeón (las demás ni se guardan)."""
    return int(seen or 0) if cfg.focus == "games" else int(hits or 0)


def sourced_champs(row) -> set[int]:
    """Campeones por los que ese jugador sale en la tabla de los mejores (columna ``sourced`` de ``otp_rows``)."""
    # sqlite3.Row no admite «in»
    raw = row["sourced"] if "sourced" in row.keys() else None  # noqa: SIM118
    return {int(x) for x in str(raw or "").split(",") if x.strip().isdigit()}


@dataclass
class Ingested:
    """Resultado de descargar una partida para un jugador."""

    result: str  # stored | duplicate | remake | ignored | other | missing | unknown
    game_start: int = 0  # ms
    patch_major: int | None = None
    valid: bool = False  # ranked de la Grieta, sin remake y con el jugador dentro
    mine_target: bool = False  # el jugador jugó con uno de los campeones elegidos
    detailed: bool = False  # se bajó también su timeline (micro y macro)


@dataclass
class _PlanContext:
    """Lo que usa ``RegionWorker._plan_otps`` para ordenar la cola: cobertura de lo ya recogido y reparto por elo."""

    now: int
    recrawl: int  # s sin visitarlo a partir de los que tocan sus partidas nuevas (``recrawl_hours``)
    cap: int  # partidas por OTP mientras se buscan más (0 = sin tope)
    lacking: set  # jugadores con partidas guardadas sin el análisis minuto a minuto
    # Con líneas enteras o «cualquiera que lo juegue», lo primero es tener muchas partidas de cada elo en cada línea
    # (para comparar elos), no un jugador de cada uno de 150 campeones.
    by_elo: bool
    info: list = field(default_factory=list)  # [(fila, campeón, estado del historial)]
    top_of: dict = field(default_factory=dict)  # puuid -> su maestría (top 3)
    # Cobertura: OTPs ya visitados por campeón y por (campeón, elo), y partidas por campeón.
    champ_otps: dict = field(default_factory=dict)
    champ_tier: dict = field(default_factory=dict)
    champ_games: dict = field(default_factory=dict)
    found_by_tier: dict = field(default_factory=dict)  # jugadores encontrados por (plataforma, elo)
    waiting_by_tier: dict = field(default_factory=dict)  # encontrados aún sin visitar
    lane_tier_games: dict = field(default_factory=dict)  # partidas ya recogidas por (línea, elo)
    need: dict = field(default_factory=dict)  # partidas que le faltan a cada elo para su parte (``tier_need``)

    def behind(self, tier: str) -> float:
        """Menos = más por debajo de su parte (va antes): lo que le falta, en negativo."""
        return -self.need.get(tier, float("-inf"))


@dataclass
class _OtpVisit:
    """Una visita en curso a un OTP (``RegionWorker._visit_otp``): a quién, su progreso y lo que le queda de presupuesto."""

    row: object  # su fila de la cola (``otp_rows``)
    platform: str
    puuid: str
    player: dict  # {id, puuid, tier}: lo que necesita ``_ingest``
    list_queue: int | None  # cola de las listas de partidas (``CrawlerConfig.list_filter``)
    list_type: str | None  # o su tipo («ranked», «normal»); sin ninguno, todas (se descartan al bajarlas)
    kw: dict  # prioridad y parada de las llamadas a Riot
    list_key: str
    config_key: str
    depth_start: int  # ``startTime`` del historial (0 = sin límite)
    season_major: int | None  # número mayor del parche de la temporada (para parar en la anterior)
    now: int  # s al empezar la visita
    fresh: bool  # sin progreso guardado o con otra búsqueda: el historial empieza de cero
    prog: dict  # progreso del historial (lo que se guarda en ``otp_progress``)
    newest: int  # ms de su partida más reciente vista
    budget: int  # descargas que le quedan a la visita (un timeline cuenta como otra)
    ingested: int = 0  # partidas bajadas en la visita (cada 25 se guarda el progreso)


@dataclass
class _HistoryPage:
    """Una página del historial de un OTP (``OTP_PAGE`` ids hacia atrás): desde dónde y qué dio."""

    base: int  # ids del historial ya recorridos al pedirla (``offset``)
    consumed: int = 0  # ids de la página recorridos
    seen: int = 0  # partidas suyas revisadas (válidas)
    hits: int = 0  # … con esos campeones
    old_season: bool = False  # llegó a la primera partida de la temporada anterior


ROWS_REFRESH_IN_BACKGROUND = True  # la lista caducada se rehace en otro hilo y mientras se sigue con la anterior
OTP_PLAN_TTL_S = 60  # la cola se recalcula cada minuto (antes 5: el reparto por elo se ponía al día tarde; la lista de jugadores, OTP_ROWS_FRESH_S)
OTP_PLAN_EMPTY_S = 15
OTP_ROWS_FRESH_S = 120  # las regiones que recalculan su cola en esos 2 min comparten la lectura de jugadores
# Jugadores sin visitar de un elo (por plataforma) a partir de los que no se buscan más. Tiene que ser menor que
# PLAN_UNVISITED_PER_TIER (la cola solo carga esos de cada elo): con 200 el contador nunca llegaba y las 15 búsquedas no
# paraban (1.864 escrituras en 5 min acaparando la base de datos).
SCOUT_BACKLOG = 60
SCOUT_BACKLOG_WAIT_S = 60
SAVE_QUEUE_MAX = 4  # partidas descargadas esperando su análisis (por región)
# Analizar en otro hilo mientras se descarga: desactivado (con él, el recolector se quedó parado en Windows el
# 24-11-2026 a las ~22 partidas; hasta saber por qué, se analiza en el mismo hilo, como cuando iba a 36/min).
SAVE_IN_BACKGROUND = False
PREFETCH = True  # descargar la siguiente partida mientras se analiza la actual (solo llamadas a Riot, ver _Prefetch)
# Partidas por delante: con 1, el hilo de descarga se quedaba parado mientras la región guardaba y analizaba (~2,2 s) y
# cada región usaba ~29 llamadas por minuto de las ~47 que permite el ritmo. Al cortar una visita se tiran como mucho estas.
# Con 4, mientras la región esperaba 1-3 s el candado para guardar, las 4 ya estaban bajadas y los hilos de descarga se
# quedaban parados el 15-25 % del tiempo (84-93 % del ritmo de la key, 24-11-2026 11:39). Con 8 cubren esas esperas.
PREFETCH_AHEAD = 8
# Hilos de descarga por región: con uno, cada llamada esperaba a que llegara la anterior (un timeline pesa 1-2 MB) y la
# región usaba ~28 llamadas por minuto aunque el ritmo permite ~47; con dos, 45-69 de 100 cada 2 min. Se solapan (el turno
# de Riot se respeta igual).
PREFETCH_THREADS = 3
RECENT_RATE_S = 300  # «velocidad actual»: partidas guardadas en los últimos 5 minutos
SEARCH_FUNNEL_EVERY_S = 1800  # embudo de la búsqueda (maestrías revisadas): cada 30 min (lo demás del informe, cada 5)


def tier_shares(totals: dict[str, int], tiers: list[str]) -> dict[str, float]:
    """Parte de las partidas nuevas de cada elo elegido: ``MIN_TIER_SHARE`` para cada uno y el resto inversa a la
    potencia ``SHARE_POWER`` de las partidas que ya tiene (más parte al que menos tiene). Suma 1."""
    tiers = list(dict.fromkeys(tiers))
    if not tiers:
        return {}
    inv = {t: 1.0 / (max(0, totals.get(t, 0)) + SHARE_SMOOTH) ** SHARE_POWER for t in tiers}
    total = sum(inv.values())
    floor = min(MIN_TIER_SHARE, 1 / len(tiers))
    return {t: floor + (1 - floor * len(tiers)) * inv[t] / total for t in tiers}


def tier_need(session: dict[str, float], shares: dict[str, float]) -> dict[str, float]:
    """Partidas que le faltan a cada elo para su parte de lo recogido en la sesión contando la siguiente visita (más =
    va antes). ``session``: partidas de la sesión por elo, con lo que darán las visitas en curso."""
    total = sum(session.get(t, 0) for t in shares)
    return {t: sh * (total + NEXT_VISIT_GAMES) - session.get(t, 0) for t, sh in shares.items()}


def _field(row, key: str):
    """Un campo de un jugador, sea un ``dict`` o una fila de la base de datos (``sqlite3.Row`` no tiene ``get``)."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


class _Prefetch:
    """Descarga en otro hilo la siguiente partida (y su timeline si es de las que interesan) mientras la región analiza
    la actual. Solo habla con Riot: la base de datos y los ficheros siguen en el hilo de la región (el intento de
    analizar en otro hilo se quedó parado en Windows). Con la key gratuita, cada llamada espera su turno ~1,4 s: antes la
    región descargaba y analizaba en fila (2,8 s + 1,5 s por partida); así se solapan."""

    def __init__(self, worker: RegionWorker) -> None:
        self.w = worker
        self.pool: ThreadPoolExecutor | None = None
        self.futures: dict[str, object] = {}
        self.timelines: dict[str, dict] = {}

    def hint(self, match_id: str | None, puuid: str) -> None:
        if not PREFETCH or not match_id or match_id in self.futures:
            return
        if self.pool is None:
            self.pool = ThreadPoolExecutor(max_workers=PREFETCH_THREADS, thread_name_prefix=f"crawler-dl-{self.w.region}")
        self.futures[match_id] = self.pool.submit(self._fetch, match_id, puuid)

    def _fetch(self, match_id: str, puuid: str):
        w = self.w
        kw = {"priority": Priority.LOW, "stop": w.stop_event}
        payload = w.svc.riot.match(match_id, **kw)
        timeline = None
        cfg = w.config
        if payload is not None and cfg.targets and cfg.save_details:
            record = parse_match(payload)
            if cfg.patterns:
                # Buscando por partidas: el timeline, solo de las que encajan con una ventana (~1 de cada 20-30).
                if pattern_search.match_record(cfg.patterns, record):
                    timeline = w.svc.riot.timeline(match_id, **kw)
                return payload, timeline
            mine = next((pr for pr in record.participants if pr.puuid == puuid), None) if record else None
            if mine is not None and record.is_rift and not record.is_remake and cfg.is_target(mine.champ, mine.role):
                timeline = w.svc.riot.timeline(match_id, **kw)  # aún no guardada (si no, no se estaría descargando)
        return payload, timeline

    def take(self, match_id: str) -> dict | None:
        """La partida ya descargada (o ``None``: se descarga como siempre)."""
        fut = self.futures.pop(match_id, None)
        if fut is None:
            return None
        try:
            with self.w.timed("esperando_descarga"):
                payload, timeline = fut.result()
        # error de Riot o parada: se repite en el hilo de la región, que sabe tratarlo
        except Exception:  # noqa: BLE001
            return None
        if timeline is not None:
            self.timelines[match_id] = timeline
        return payload

    def take_timeline(self, match_id: str) -> dict | None:
        return self.timelines.pop(match_id, None)

    def drop(self) -> None:
        """Fin de una visita: lo que quedó por consumir se descarta."""
        for fut in self.futures.values():
            fut.cancel()
        self.futures.clear()
        self.timelines.clear()

    def close(self) -> None:
        self.drop()
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool = None


class RegionWorker(threading.Thread):
    def __init__(self, crawler: Crawler, region: str, platforms: list[str], config: CrawlerConfig) -> None:
        super().__init__(name=f"crawler-{region}", daemon=True)
        self.crawler = crawler
        self.svc = crawler.svc
        self.region = region
        self.platforms = platforms
        self.config = config
        self.stop_event = crawler.stop_event
        self._cooldown: dict[tuple[str, str], float] = {}
        self._ladder_turns: dict[str, int] = {}  # todo el ranking: turno de servidor dentro de cada elo
        self._wake = threading.Event()
        self._otp_fails: dict[str, tuple[int, float]] = {}  # puuid -> (fallos seguidos, descansa hasta)
        self._plan: list = []  # cola de OTPs (``_next_otp``)
        self._plan_at = 0.0
        self._plan_rows_at = 0.0
        # Reparto por elo (``_plan_need``): elos de la búsqueda y lo recogido por elo al calcular la cola.
        self._plan_tiers: list[str] = []
        self._plan_totals: dict[str, int] = {}
        self._visited: dict[str, float] = {}  # puuid -> cuándo empezó su última visita
        self._pick: dict | None = None  # por qué se eligió al jugador de la visita en curso (diagnóstico del reparto)
        self._visit_analyzed = 0  # partidas analizadas en la visita en curso
        self._visit_result: dict = {}
        self.pattern_backlog: deque = deque()  # partidas ya guardadas que encajan con una ventana (``scan_stored``)
        # Segundos de esta sesión en cada fase (para ver dónde se va el tiempo si el cupo de Riot no se llena).
        self.timing: dict[str, float] = {}
        self.waiting_by_tier: dict[tuple[str, str], int] = {}
        # Análisis y guardado en otro hilo: mientras se analiza una partida se descarga la siguiente (antes todo iba en
        # fila: descargar, esperar el turno de Riot, analizar 1,7 s en Windows y volver a empezar).
        self._save_q: queue.Queue = queue.Queue(maxsize=SAVE_QUEUE_MAX)
        self._saver: threading.Thread | None = None
        self._saving: set[tuple[str, str]] = set()  # (partida, jugador) descargadas y aún sin guardar
        self._prefetch = _Prefetch(self)
        self.scouts: list[OtpScout] = []  # una búsqueda de OTPs por plataforma (cada una tiene su propio cupo)
        self.found_by_tier: dict[tuple[str, str], int] = {}  # OTPs encontrados por (plataforma, elo)
        self.stored_at: deque[float] = deque(maxlen=5000)  # cuándo se guardó cada partida (velocidad reciente)
        self.state = {
            "region": region,
            "platforms": platforms,
            "status": "starting",
            "platform": None,
            "tier": None,
            "players": 0,
            "ids_seen": 0,
            "fetched": 0,
            "stored": 0,
            "duplicates": 0,
            "remakes": 0,
            "ignored": 0,
            "errors": 0,
            "seeds_added": 0,
            "mastery_checked": 0,
            "targets_found": 0,
            "target_games": 0,
            "other_champ_skipped": 0,
            "search": None,  # estado de la búsqueda de OTPs (hilo aparte)
            "otps_known": 0,
            "otps_complete": 0,
            "otp_pages": 0,
            "details": 0,  # partidas de OTPs con micro y macro guardados
            "last_error": None,
            "started_at": time.time(),
            "last_activity": time.time(),
        }

    @contextlib.contextmanager
    def timed(self, phase: str):
        t0 = time.monotonic()
        try:
            yield
        finally:
            self.timing[phase] = self.timing.get(phase, 0.0) + time.monotonic() - t0

    def wake(self) -> None:
        self._wake.set()

    # ------------------------------------------------------------------ bucle
    def run(self) -> None:
        if self.config.targets:
            self._run_otp()
        else:
            self._run_ladder()
        self.finish_saving()
        self._prefetch.close()
        self.state["status"] = "stopped"
        if self.config.targets and self.config.save_details:
            otp_details.flush(self.svc)  # los resúmenes de campeón pendientes, ya
        log.info("Recolector %s detenido. Guardadas %d partidas.", self.region, self.state["stored"])

    def _run_ladder(self) -> None:
        combos = [(p, t) for t in self.config.tiers for p in self.platforms]
        log.info("Recolector %s iniciado (%s).", self.region, ", ".join(PLATFORMS[p][1] for p in self.platforms))
        base = self._tier_base()
        shares = tier_shares(base, list(self.config.tiers))
        log.info("Reparto por elo (todo el ranking, región %s): parte de las partidas nuevas %s.", self.region, ", ".join(
            f"{t} {100 * sh:.1f} % ({base.get(t, 0)} analizadas)" for t, sh in shares.items()))
        while not self.stop_event.is_set():
            try:
                combo = self._ladder_combo(combos)
                if combo is None:
                    continue
                self._ladder_visit(*combo)
            except Cancelled:
                break
            except RiotKeyError as exc:
                self.state["last_error"] = exc.message
                log.warning("Recolector %s en pausa: %s", self.region, exc.message)
                self._wait_for_key()
            except RiotAPIError as exc:
                self.state["errors"] += 1
                self.state["last_error"] = exc.message
                log.warning("Recolector %s: %s", self.region, exc.message)
                self.stop_event.wait(3)
            except Exception as exc:  # no tumbar el hilo por un fallo inesperado
                self.state["errors"] += 1
                self.state["last_error"] = str(exc)
                log.exception("Error inesperado en el recolector %s", self.region)
                self.stop_event.wait(5)

    def _ladder_combo(self, combos: list[tuple[str, str]]) -> tuple[str, str] | None:
        """Modo normal: la (plataforma, elo) a la que le toca el turno o ``None`` tras esperar (key, partida, descanso)."""
        if not self.svc.keys.get() or self.svc.keys.status == "invalid":
            self._wait_for_key()
            return None
        if self.crawler.paused_reason():
            # Estás en partida: no te quito ancho de banda; sigue al terminar.
            self.state["status"] = "paused_game"
            self.stop_event.wait(5)
            return None
        now = time.time()
        available = [c for c in combos if self._cooldown.get(c, 0) <= now]
        if not available:
            self.state["status"] = "idle"
            self.stop_event.wait(30)
            return None
        return self._ladder_pick(available)

    def _ladder_pick(self, available: list[tuple[str, str]]) -> tuple[str, str]:
        """El elo más por debajo de su parte (``tier_need``, entre los elegidos; a igualdad, el más bajo) de los que
        tienen jugadores por visitar y, dentro de él, sus servidores por turnos."""
        need = self._tier_need(list(self.config.tiers))
        tier = max({t for _, t in available}, key=lambda t: (need.get(t, 0), -TIER_INDEX.get(t, 0)))
        platforms = [p for p, t in available if t == tier]
        turn = self._ladder_turns.get(tier, 0)
        self._ladder_turns[tier] = turn + 1
        return platforms[turn % len(platforms)], tier

    def _ladder_visit(self, platform: str, tier: str) -> None:
        """Modo normal: el siguiente jugador de esa plataforma y elo o, si no queda ninguno, más del ranking."""
        self.state.update(status="running", platform=platform, tier=tier)
        player = self._next_player(platform, tier)
        if player is None:
            added = self._refill_seeds(platform, tier)
            if added == 0:
                self._cooldown[(platform, tier)] = time.time() + EXHAUSTED_COOLDOWN_S
            return
        self.crawler.visit_started(self.region, tier, expected=self.config.matches_per_player)
        try:
            self._crawl_player(player, platform)
        finally:
            self.crawler.visit_ended(self.region)

    def _run_otp(self) -> None:
        """Modo por campeones: este hilo baja partidas de los OTPs y ``OtpScout`` los busca a la vez."""
        log.info(
            "Recolector %s por campeones (%s) iniciado (%s): periodo %s.",
            self.region,
            FOCUS_NOUN[self.config.focus],
            ", ".join(PLATFORMS[p][1] for p in self.platforms),
            self.config.otp_depth,
        )
        self.scouts = [OtpScout(self, platform) for platform in self.platforms]
        for scout in self.scouts:
            scout.start()
        try:
            while not self.stop_event.is_set():
                self.crawler.write_funnel()
                try:
                    self._otp_step()
                except Cancelled:
                    break
                except RiotKeyError as exc:
                    self.state["last_error"] = exc.message
                    log.warning("Recolector %s en pausa: %s", self.region, exc.message)
                    self._wait_for_key()
                except RiotAPIError as exc:
                    self.state["errors"] += 1
                    self.state["last_error"] = exc.message
                    log.warning("Recolector %s: %s", self.region, exc.message)
                    self.stop_event.wait(3)
                except Exception as exc:
                    self.state["errors"] += 1
                    self.state["last_error"] = str(exc)
                    log.exception("Error inesperado en el recolector %s", self.region)
                    self.stop_event.wait(5)
        finally:
            for scout in self.scouts:
                scout.join(timeout=5)

    def _otp_step(self) -> None:
        """Una vuelta del bucle por campeones: esperar a la key o a la partida en curso, completar partidas ya guardadas
        que encajan con una ventana o visitar al siguiente jugador."""
        if not self.svc.keys.get() or self.svc.keys.status == "invalid":
            self._wait_for_key()
            return
        if self.crawler.paused_reason():
            self.state["status"] = "paused_game"
            self.stop_event.wait(5)
            return
        # Se lee el estado de la búsqueda ANTES de buscar trabajo: si ya había terminado, todo lo que encontró está
        # guardado y «no hay nada pendiente» es cierto.
        search_done = all(sc.status in ("idle", "stopped") for sc in self.scouts)
        self._wake.clear()
        if self._pattern_step():
            return
        target = self._next_otp()
        if target is None:
            self.state["status"] = "idle" if search_done else "searching"
            # Mientras se buscan jugadores, los candidatos pueden aparecer en cualquier momento: se vuelve a mirar cada
            # 15 s (con la búsqueda terminada, cada 30).
            with self.timed("esperando"):
                self._wake.wait(OTP_IDLE_WAIT_S if search_done else OTP_PLAN_EMPTY_S)
            return
        self._visit(target)

    def _pattern_step(self) -> bool:
        """Buscando por partidas: esperar a la búsqueda en lo ya guardado (como mucho ``PATTERN_SCAN_WAIT_S``) y
        completar lo que encontró (2 llamadas por partida, frente a ~30-40 por cada una que encaja buscando entre las de
        los jugadores). ``True`` si ha hecho algo (la vuelta acaba aquí)."""
        if not self.crawler.pattern_scan_done.is_set() and time.time() - (self.crawler.started_at or 0) < PATTERN_SCAN_WAIT_S:
            self.state["status"] = "searching"  # mirando las ventanas en lo ya guardado
            self.crawler.pattern_scan_done.wait(2)
            return True
        if not self.pattern_backlog:
            return False
        self.state["status"] = "running"
        self._pattern_from_store(self.pattern_backlog.popleft())
        return True

    def _visit(self, target) -> None:
        """Una visita, con su diagnóstico (elo, por qué se eligió, cuánto dio y por qué acabó)."""
        self.state.update(status="running", platform=target["platform"], tier=target["tier"])
        self.crawler.visit_started(self.region, target["tier"] or "")
        t0 = time.time()
        self._visit_analyzed = 0
        self._visit_result = {"fin": "error o detenido"}
        try:
            with self.timed("visitas"):
                self._visit_otp(target)
        finally:
            self.crawler.visit_ended(self.region, {
                "plataforma": PLATFORMS.get(target["platform"], (None, target["platform"]))[1],
                "elo": target["tier"] or "", "analizadas": self._visit_analyzed,
                "s": round(time.time() - t0, 1), **(self._pick or {}), **self._visit_result, **self._visit_ratio(target),
            })
        self.timing["n_visitas"] = self.timing.get("n_visitas", 0) + 1

    def _visit_ratio(self, target) -> dict:
        """Su pinta de especialista al elegirlo (``specialist_ratio``), para medir si acierta: ``{"pinta": veces}``
        (``"tabla"`` si sale en la de OP.GG; nada si el campeón buscado no es su principal o no se busca OTPs)."""
        if self.config.focus != "otp":
            return {}
        try:
            top = json.loads(target["top"] or "[]")
        except (TypeError, ValueError, IndexError, KeyError):
            return {}
        ratio = specialist_ratio(self.config, top, target)
        if ratio is None:
            return {}
        return {"pinta": "tabla" if ratio >= BOARD_RATIO else round(ratio, 1)}

    def _wait_for_key(self) -> None:
        self.state["status"] = "waiting_key"
        self.svc.keys.wait_for_change(self.svc.keys.version, self.stop_event)

    # ---------------------------------------------------------------- semillas
    def _next_player(self, platform: str, tier: str):
        hours = self.config.recrawl_hours
        cutoff = now_s() - int(hours * 3600) if hours > 0 else 0  # 0: nunca se vuelve a un jugador ya visto
        return self.svc.db.one(
            "SELECT id, puuid, tier, newest_match_at FROM players "
            "WHERE is_seed = 1 AND platform = ? AND tier = ? AND (last_crawled_at IS NULL OR last_crawled_at < ?) "
            "ORDER BY last_crawled_at LIMIT 1",
            (platform, tier, cutoff),
        )

    # ------------------------------------------------------ por campeones
    def _discover(self, platform: str, tier: str) -> int:
        """Mira la maestría de una tanda de jugadores del ranking (primero los que más LP tienen). Devuelve cuántos."""
        # Primero quien ya se ha visto jugando esos campeones en partidas guardadas (lo más probable es que sea OTP),
        # luego los nunca revisados y, a igualdad, los de más LP.
        champs = self.config.targets or [0]
        marks = ",".join("?" * len(champs))
        if self.config.focus == "games" or self.config.roles:
            # Líneas enteras o «cualquiera que lo juegue»: casi todos valen, así que se ahorra la cuenta de partidas
            # guardadas de cada jugador del elo (con 140.000 en Diamante, cada búsqueda tardaba mucho y las 15 a la vez
            # ocupaban la base de datos justo al arrancar).
            rows = self.svc.db.query(
                "SELECT p.puuid FROM players p LEFT JOIN mastery_checks m ON m.puuid = p.puuid "
                "WHERE p.is_seed = 1 AND p.platform = ? AND p.tier = ? AND (m.puuid IS NULL OR m.checked_at < ?) "
                "ORDER BY (m.puuid IS NOT NULL), p.lp DESC LIMIT ?",
                (platform, tier, now_s() - MASTERY_TTL_S, MASTERY_BATCH),
            )
        else:
            rows = self.svc.db.query(
                "SELECT p.puuid FROM players p LEFT JOIN mastery_checks m ON m.puuid = p.puuid "
                "WHERE p.is_seed = 1 AND p.platform = ? AND p.tier = ? AND (m.puuid IS NULL OR m.checked_at < ?) "
                f"ORDER BY (SELECT COUNT(*) FROM participants pa WHERE pa.player_id = p.id AND pa.champ IN ({marks})) DESC, "
                "(m.puuid IS NOT NULL), p.lp DESC LIMIT ?",
                (platform, tier, now_s() - MASTERY_TTL_S, *champs, MASTERY_BATCH),
            )
        checked = 0
        for row in rows:
            if self.stop_event.is_set() or self.crawler.paused_reason():
                break
            self._check_mastery(platform, row["puuid"])
            checked += 1
        return checked

    def leaderboard_pending(self, platform: str) -> list[int]:
        """Campeones cuya tabla de los mejores (OP.GG) de esa plataforma toca pedir (no se ha pedido en 24 h).

        Solo buscando especialistas («otp» o «mastery»): con líneas enteras o «cualquiera que lo juegue» las tablas solo
        añaden jugadores de Diamante+ (de los que sobran) y cada uno nuevo cuesta una llamada de account-v1 del cupo
        regional de las partidas: con 173 campeones y 15 plataformas, miles al día."""
        if self.config.focus == "games" or self.config.roles:
            return []
        opgg = getattr(self.svc, "opgg", None)
        if opgg is None or not getattr(opgg, "enabled", False) or not OPGG_REGION.get(platform):
            return []
        if not set(self.config.tiers) & LEADERBOARD_TIERS:
            return []
        fresh = {
            int(r[0])
            for r in self.svc.db.query(
                "SELECT champ FROM otp_sources WHERE platform = ? AND source = 'opgg' GROUP BY champ HAVING MAX(fetched_at) >= ?",
                (platform, now_s() - LEADERBOARD_TTL_S),
            )
        }
        return [c for c in self.config.targets if c not in fresh]

    def _seed_from_leaderboards(self, platform: str, champs: list[int] | None = None) -> int:
        """OTPs al instante: los mejores de cada campeón elegido en esa plataforma según OP.GG (una vez al día).

        Cada uno cuesta 1 llamada de account-v1 (su PUUID para tu key) y 1 de maestría, frente a revisar la maestría
        de cientos de jugadores del ranking hasta dar con ellos. Devuelve cuántos se añadieron.
        """
        opgg = getattr(self.svc, "opgg", None)
        region = OPGG_REGION.get(platform)
        if opgg is None or not getattr(opgg, "enabled", False) or not region:
            return 0
        if not set(self.config.tiers) & LEADERBOARD_TIERS:
            return 0  # la tabla solo trae Diamante alto, Maestro o más: con elos más bajos no aporta nadie
        added = 0
        for champ in self.config.targets if champs is None else champs:
            if self.stop_event.is_set():
                break
            added += self._seed_champion_board(opgg, (platform, region), champ)
        return added

    def _seed_champion_board(self, opgg, where: tuple[str, str], champ: int) -> int:
        """La tabla de los mejores de un campeón (si no se ha pedido en 24 h): cuántos jugadores de tus elos añade."""
        platform, region = where
        fresh = self.svc.db.scalar(
            "SELECT MAX(fetched_at) FROM otp_sources WHERE champ = ? AND platform = ? AND source = 'opgg'", (champ, platform)
        )
        if fresh and now_s() - int(fresh) < LEADERBOARD_TTL_S:
            return 0
        try:
            data = opgg.mcp_call(
                "lol_list_champion_leaderboard",
                {
                    "region": region,
                    "champion": opgg.mcp_name(champ),
                    "desired_output_fields": [
                        "leaderboard[].rank",
                        "leaderboard[].summoner.{game_name,tagline}",
                        "leaderboard[].summoner.league_stats[].{game_type}",
                        "leaderboard[].summoner.league_stats[].tier_info.{tier,division,lp}",
                        "leaderboard[].most_champion_stat.{play,win}",
                    ],
                },
            )
        # OP.GG caído o sin ese campeón: se sigue con el ranking de Riot
        except Exception as exc:  # noqa: BLE001
            log.warning("Tabla de los mejores de %s en %s (OP.GG): %s", champ, platform, exc)
            return 0
        entries = (data.get("leaderboard") or []) if isinstance(data, dict) else []
        champ_added = 0
        for entry in entries[:LEADERBOARD_MAX]:
            if self.stop_event.is_set():
                break
            champ_added += self._seed_board_entry(platform, champ, entry)
        dd = getattr(self.svc, "dd", None)
        champ_name = (dd.name(champ) if dd is not None and hasattr(dd, "name") else None) or f"campeón {champ}"
        log.info(
            "Tabla de los mejores de %s en %s (OP.GG): %d jugadores de tus elos añadidos.", champ_name, PLATFORMS[platform][1], champ_added
        )
        return champ_added

    def _seed_board_entry(self, platform: str, champ: int, entry: dict) -> int:
        """Un jugador de la tabla de los mejores: 1 si es de tus elos y se sabe su PUUID (queda guardado), si no 0."""
        summ = entry.get("summoner") or {}
        name, tag = summ.get("game_name"), summ.get("tagline")
        if not name or not tag:
            return 0
        solo = next((ls.get("tier_info") or {} for ls in summ.get("league_stats") or [] if ls.get("game_type") == "SOLORANKED"), {})
        if str(solo.get("tier") or "").upper() not in self.config.tiers:
            return 0  # no es de los elos que buscas: ni se gasta la llamada
        puuid = self._board_puuid(platform, name, tag)
        if not puuid:
            return 0
        self._save_board_player(platform, champ, puuid, (name, tag), entry, solo)
        self.state["targets_found"] += 1
        self.wake()
        return 1

    def _board_puuid(self, platform: str, name: str, tag: str) -> str | None:
        """PUUID de un jugador de la tabla: el que ya está en la base de datos o el de account-v1 (``None`` si no lo da)."""
        puuid = self.svc.db.scalar(
            "SELECT puuid FROM players WHERE platform = ? AND lower(game_name) = lower(?) AND lower(tag_line) = lower(?)",
            (platform, name, tag),
        )
        if puuid:
            return puuid
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        try:
            account = self.svc.riot.account_by_riot_id(name, tag, platform, **kw) or {}
        except RiotKeyError:
            raise
        except RiotAPIError:
            return None  # cambió de nombre: ya aparecerá por el ranking
        return account.get("puuid")

    def _save_board_player(self, platform: str, champ: int, puuid: str, riot_id: tuple[str, str], entry: dict, solo: dict) -> None:
        """Guarda a un jugador de la tabla: su elo, su Riot ID, de qué tabla sale y (si hace falta) su maestría."""
        db = self.svc.db
        name, tag = riot_id
        tier = str(solo.get("tier") or "").upper()
        if tier in TIER_INDEX:
            upsert_seed_players(
                db, platform, [{"puuid": puuid, "tier": tier, "rank": ROMAN.get(int(solo.get("division") or 1), "I"), "leaguePoints": solo.get("lp") or 0}], tier
            )
        stat = entry.get("most_champion_stat") or {}
        with db.write() as conn:
            conn.execute(
                "UPDATE players SET game_name = COALESCE(game_name, ?), tag_line = COALESCE(tag_line, ?) WHERE puuid = ?",
                (name, tag, puuid),
            )
            conn.execute(
                "INSERT OR REPLACE INTO otp_sources (puuid, champ, platform, source, rank, games, wins, fetched_at) "
                "VALUES (?, ?, ?, 'opgg', ?, ?, ?, ?)",
                (puuid, champ, platform, entry.get("rank"), stat.get("play"), stat.get("win"), now_s()),
            )
        if not db.scalar("SELECT 1 FROM mastery_checks WHERE puuid = ? AND checked_at >= ?", (puuid, now_s() - MASTERY_TTL_S)):
            self._check_mastery(platform, puuid)  # necesaria para su ficha (y si es OTP estricto)

    def _check_mastery(self, platform: str, puuid: str) -> list:
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        try:
            data = self.svc.riot.top_masteries(platform, puuid, 3, **kw)
        except RiotKeyError:
            raise
        except RiotAPIError as exc:
            if exc.status not in (400, 404):
                raise
            data = []  # jugador ya no existe en esa plataforma: se recuerda vacío
        top = [
            [int(m.get("championId") or 0), int(m.get("championPoints") or 0), int(m.get("lastPlayTime") or 0) // 1000]
            for m in data
            if m.get("championId")
        ]
        before = self.svc.db.scalar("SELECT top FROM mastery_checks WHERE puuid = ?", (puuid,))
        with self.svc.db.write() as conn:
            conn.execute(
                "INSERT INTO mastery_checks (puuid, platform, checked_at, top) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(puuid) DO UPDATE SET platform = excluded.platform, checked_at = excluded.checked_at, top = excluded.top",
                (puuid, platform, now_s(), json.dumps(top)),
            )
        self.state["mastery_checked"] += 1
        if self.config.qualifies(top):
            try:
                was = bool(before) and self.config.qualifies(json.loads(before))
            except (TypeError, ValueError):
                was = False
            if not was:
                self.state["targets_found"] += 1
            # Sin pedir su Riot ID aparte (account-v1, del mismo cupo regional que las partidas): con ~7.500 revisados por
            # hora y casi todos válidos eran ~100 llamadas por minuto. Llega con su primera partida descargada.
            self.wake()
        return top

    def _resolve_name(self, platform: str, puuid: str) -> None:
        """Riot ID de un OTP encontrado (para enlazar su perfil aunque aún no tenga partidas guardadas)."""
        db = self.svc.db
        if db.scalar("SELECT game_name FROM players WHERE puuid = ?", (puuid,)):
            return
        try:
            account = self.svc.riot.account_by_puuid(puuid, platform, priority=Priority.LOW, stop=self.stop_event) or {}
        except RiotKeyError:
            raise
        except RiotAPIError:
            return  # no es imprescindible
        if account.get("gameName"):
            with db.write() as conn:
                conn.execute(
                    "UPDATE players SET game_name = ?, tag_line = ?, updated_at = ? WHERE puuid = ?",
                    (account["gameName"], account.get("tagLine"), now_s(), puuid),
                )

    def _list_key(self) -> str:
        return otp_list_key(self.config)

    def _depth(self) -> tuple[int, int | None]:
        return otp_depth_start(self.config, getattr(getattr(self.svc, "dd", None), "version", None))

    def _next_otp(self):
        """El OTP con más prioridad que tenga algo pendiente (o ``None``), de la cola calculada con ``_plan_otps``.

        Calcular la cola cuesta segundos con cientos de miles de jugadores revisados (5 s con 280.000 en un Mac): antes se
        hacía antes de cada visita, en cada región, y las 4 regiones se esperaban entre ellas (Python usa un núcleo) en
        vez de descargar; el cupo de Riot se quedaba a la mitad. Ahora la cola se reutiliza ``OTP_PLAN_TTL_S`` (los
        jugadores de la base de datos se leen una vez para todas las regiones) y quien ya se visitó desde que se leyó no
        se vuelve a elegir hasta la siguiente.
        """
        self._refresh_plan()
        # El reparto por elo, con las partidas y visitas de ahora (de las 4 regiones), no con las de cuando se calculó la
        # cola: en ese rato las regiones seguían su orden sin enterarse (salían Bronce, Maestro y Aspirante una y otra
        # vez, 24-11-2026). Reordenar sobre la cola ya calculada cuesta milisegundos.
        need = self._tier_need(self._plan_tiers) if self._plan_tiers else {}
        self._pick = None
        if need:
            self._note_queue(need)
        return self._pop_planned(need)

    def _refresh_plan(self) -> None:
        """Rehace la cola si ha caducado (``OTP_PLAN_TTL_S``; vacía, cada ``OTP_PLAN_EMPTY_S``)."""
        now_t = time.time()
        if now_t - self._plan_at >= OTP_PLAN_TTL_S or (not self._plan and now_t - self._plan_at >= OTP_PLAN_EMPTY_S):
            with self.timed("cola"):
                self._plan = self._plan_otps()  # fija ``_plan_rows_at``: cuándo se leyeron los jugadores
            self._plan_at = now_t

    @staticmethod
    def _live_key(entry, need: dict) -> tuple:
        """Orden de una entrada de la cola con el reparto por elo de ahora (``need`` vacío: el de la cola)."""
        # Primero el elo con menos datos y, dentro de él, el tipo de jugador (nuevos, partidas nuevas, historial a
        # medias). Antes el tipo iba primero: sin jugadores nuevos de Oro o Platino esperando, cualquier jugador nuevo
        # de Maestro iba antes que seguir con uno de Oro, y Oro y Platino se quedaban a 0.
        key, row = entry
        if not need:
            return key
        if key[0] == 3:
            return (float("inf"), *key)
        tier = row["tier"] or ""
        return (-need.get(tier, float("-inf")), *key)  # el elo al que más partidas le faltan

    def _pop_planned(self, need: dict):
        """Saca de la cola al siguiente jugador (o ``None``): ni visitado ya con esta cola ni descansando por fallos."""
        while self._plan:
            best = min(range(len(self._plan)), key=lambda i: self._live_key(self._plan[i], need))
            key, row = self._plan.pop(best)
            if self._visited.get(row["puuid"], 0.0) >= self._plan_rows_at:
                continue  # ya visitado con esta cola: su progreso ha cambiado, vuelve en la siguiente
            if self._otp_fails.get(row["puuid"], (0, 0.0))[1] > time.time():
                continue  # falló varias veces seguidas: descansa un rato
            self._pick = {**(self._pick or {}), "tipo": PLAN_KIND.get(key[0], str(key[0]))}
            return row
        return None

    def _note_queue(self, need: dict) -> None:
        """Diagnóstico del reparto por elo: qué hay en la cola de la región por elo y tipo, cuál es el elo más atrasado
        y si hay alguien de ese elo esperando (si no, la región elige otro elo y ese se queda atrás)."""
        counts: dict[str, list[int]] = {}
        for key, row in self._plan:
            c = counts.setdefault(row["tier"] or "", [0, 0, 0, 0])
            c[min(3, max(0, int(key[0])))] += 1
        wanted = max(need, key=need.get) if need else None
        has = bool(wanted) and sum(counts.get(wanted, [0, 0, 0, 0])[:3]) > 0
        self._pick = {"mas_atrasado": wanted, "hay_de_ese": has}
        self.crawler.note_queue(self.region, counts)
        self.crawler.note_pick(self.region, wanted, has)

    def _plan_otps(self) -> list:
        """La cola de OTPs por prioridad: ``[(clave, fila)]``, la primera la más prioritaria. **Variedad antes que cantidad**:

        1. Recién encontrados (aún sin visitar). Con líneas enteras o «cualquiera que lo juegue»: primero la línea y el elo
           con menos partidas recogidas (para comparar elos hacen falta muchas de cada uno, sea el campeón que sea).
           Buscando OTPs de campeones sueltos: primero los de campeones que aún no tienen ninguno; después los de un elo
           en el que su campeón tiene menos de ``ELO_MIN_OTPS``; luego el resto (más jugadores distintos).
        2. Partidas nuevas: no se visitan desde hace ``recrawl_hours`` o quedaron partidas nuevas por bajar.
        3. Historial (profundidad): primero el campeón con menos partidas y, dentro de él, el OTP que menos lleva.
           Mientras se siguen buscando OTPs, nadie pasa de ``games_per_otp``.
        4. Partidas guardadas sin el análisis minuto a minuto.
        """
        cfg = self.config
        now = now_s()
        depth_start, _ = self._depth()
        list_key, config_key = self._list_key(), otp_config_key(cfg)
        recrawl = int(cfg.recrawl_hours * 3600)
        rows, self._plan_rows_at = self.crawler.otp_rows_cached(cfg, now)
        rows = [(row, top) for row, top in rows if row["platform"] in self.platforms]
        # Buscando por partidas no: su análisis va con la partida entera, solo si encaja con una ventana.
        lacking = self._lacking_details([row["id"] for row, _ in rows]) if cfg.save_details and not cfg.patterns else set()
        ctx = _PlanContext(now=now, recrawl=recrawl, cap=self._games_cap(), lacking=lacking, by_elo=bool(cfg.roles) or cfg.focus == "games")
        self._plan_coverage(ctx, rows, (list_key, depth_start, config_key))
        self.found_by_tier = ctx.found_by_tier  # la búsqueda en el ranking reparte por elos con esto
        self.waiting_by_tier = ctx.waiting_by_tier  # y no busca más en un elo con jugadores de sobra esperando visita
        plan = []
        complete = 0
        ctx.need = self._plan_need(ctx.lane_tier_games)
        for row, champ, state in ctx.info:
            if state in ("done", "share"):
                complete += 1
            key = self._plan_key(ctx, row, champ, state)
            if key is not None:
                plan.append((key, row))
        self.state.update(otps_known=len(ctx.info), otps_complete=complete)
        plan.sort(key=lambda kr: kr[0])
        return plan

    def _lane_of(self, champ: int) -> str:
        """Línea de un campeón para el reparto por (línea, elo): la suya si solo cuenta en una; si no, «ANY»."""
        lanes = self.config.roles_for(champ) or []
        return lanes[0] if len(lanes) == 1 else "ANY"

    def _plan_coverage(self, ctx: _PlanContext, rows: list, history: tuple[str, int, str]) -> None:
        """Estado del historial de cada jugador de la cola y lo ya recogido: por campeón, (campeón, elo) y (línea, elo)."""
        cfg = self.config
        list_key, depth_start, config_key = history
        ctx.top_of = {row["puuid"]: top for row, top in rows}
        for row, top in rows:
            champ = (otp_champs(cfg, top, sourced_champs(row)) or [0])[0]
            state = otp_history_state(row, list_key, depth_start, config_key)
            tier = row["tier"] or ""
            ctx.info.append((row, champ, state))
            ctx.found_by_tier[(row["platform"], tier)] = ctx.found_by_tier.get((row["platform"], tier), 0) + 1
            if state == "queued" and row["anchor"] is None:
                ctx.waiting_by_tier[(row["platform"], tier)] = ctx.waiting_by_tier.get((row["platform"], tier), 0) + 1
            # Lo ya recogido cuenta siempre, también si cambió la búsqueda (otros campeones o líneas: el jugador vuelve a
            # «queued» para recorrer su historial otra vez). Antes solo contaban los no «queued»: al cambiar la búsqueda
            # todas las líneas y elos quedaban a 0 partidas, el desempate era «el elo más alto primero» y miles de
            # jugadores de Diamante+ ya visitados pasaban por delante de los de Hierro a Esmeralda recién encontrados.
            if state != "queued" or row["anchor"] is not None:
                ctx.champ_otps[champ] = ctx.champ_otps.get(champ, 0) + 1
                ctx.champ_tier[(champ, tier)] = ctx.champ_tier.get((champ, tier), 0) + 1
                ctx.champ_games[champ] = ctx.champ_games.get(champ, 0) + int(row["hits"] or 0)
                key_lt = (self._lane_of(champ), tier)
                ctx.lane_tier_games[key_lt] = ctx.lane_tier_games.get(key_lt, 0) + int(row["hits"] or 0)

    def _plan_need(self, lane_tier_games: dict) -> dict[str, float]:
        """Reparto por elo de la cola (en todos los modos, ``tier_need``); la parte de cada elo se apunta para el embudo."""
        totals: dict[str, int] = {}
        for (_, t), n in lane_tier_games.items():
            totals[t] = totals.get(t, 0) + n
        self._plan_totals = totals
        self._plan_tiers = list(self.config.tiers or totals)
        base = self._tier_base()
        shares = tier_shares(base, self._plan_tiers)
        self.crawler.note_tier_shares(self.region, shares, base)
        return tier_need(self.crawler.session_load(), shares)

    def _tier_base(self) -> dict[str, int]:
        """Partidas analizadas por elo ahora (las de la gráfica «Partidas por elo (total)», al día solas). Sin ninguna
        aún, lo recogido de los jugadores de la cola más lo analizado en esta sesión."""
        live = self.crawler.analyzed_by_tier()
        if live:
            return live
        base = dict(self._plan_totals)
        for t, n in self.crawler.analyzed_noted().items():
            base[t] = base.get(t, 0) + n
        return base

    def _tier_need(self, tiers: list[str]) -> dict[str, float]:
        """Lo que le falta a cada elo de ``tiers`` para su parte de la sesión (más = va antes)."""
        return tier_need(self.crawler.session_load(), tier_shares(self._tier_base(), tiers))

    def _plan_key(self, ctx: _PlanContext, row, champ: int, state: str) -> tuple | None:
        """Prioridad de un jugador en la cola (menos = antes) o ``None`` si no tiene nada pendiente."""
        tier = row["tier"] or ""
        rank = (-TIER_INDEX.get(tier, 0), -(row["lp"] or 0))
        if state == "queued":
            return self._queued_key(ctx, row, champ, tier, rank)
        if ctx.recrawl and (row["fwd_more"] or (row["last_crawled_at"] or 0) < ctx.now - ctx.recrawl):
            return (1, row["last_crawled_at"] or 0, 0, 0, *rank)
        if state == "loading" and not (ctx.cap and cap_count(self.config, row["hits"], row["seen"]) >= ctx.cap):
            return self._history_key(ctx, row, champ, tier, rank)
        if row["id"] in ctx.lacking:
            return (3, ctx.champ_games.get(champ, 0), 0, 0, *rank)  # sin análisis minuto a minuto (de antes o que fallaron)
        return None

    def _queued_key(self, ctx: _PlanContext, row, champ: int, tier: str, rank: tuple) -> tuple:
        """Clave de un jugador por visitar: recién encontrado o de vuelta en la cola por un cambio de búsqueda."""
        cfg = self.config
        in_tier, in_champ = ctx.champ_tier.get((champ, tier), 0), ctx.champ_otps.get(champ, 0)
        level = self._variety_level(in_tier, in_champ)
        likely = 0.0 if cfg.focus != "otp" else specialist_rank(cfg, ctx.top_of[row["puuid"]], row)
        if ctx.by_elo:
            # Primero la línea y el elo con menos partidas; la variedad de campeones, después. Los nunca visitados
            # antes que los que vuelven a la cola por un cambio de búsqueda (de esos ya hay partidas).
            # A igualdad (muchas líneas y elos a 0 partidas), el elo más bajo: de los altos hay mucho más guardado
            # y las tablas de OP.GG ya traen jugadores de Diamante+ nada más arrancar. Buscando OTPs, dentro de la
            # línea y el elo, primero quien más pinta tiene: iba detrás del elo y los LP (casi únicos), así que no
            # contaba y 2 de cada 3 visitas acababan en «no es especialista» (Platino, 25-11-2026).
            again = int(row["anchor"] is not None)
            low_first = (TIER_INDEX.get(tier, 0), -(row["lp"] or 0))
            return (0, ctx.behind(tier), ctx.lane_tier_games.get((self._lane_of(champ), tier), 0), again, likely, low_first, level, in_tier)
        # Buscando especialistas, primero los que más pinta tienen (comprobar a uno cuesta ~20 descargas).
        return (0, level, likely, in_tier, in_champ, *rank)

    @staticmethod
    def _variety_level(in_tier: int, in_champ: int) -> int:
        """Variedad de OTPs: 0 si su campeón aún no tiene ninguno, 1 si tiene menos de ``ELO_MIN_OTPS`` en su elo y 2 si no."""
        if not in_champ:
            return 0
        return 1 if in_tier < ELO_MIN_OTPS else 2

    def _history_key(self, ctx: _PlanContext, row, champ: int, tier: str, rank: tuple) -> tuple:
        """Clave de un historial a medias: primero el campeón (o la línea y el elo) con menos partidas y el OTP que menos lleva."""
        first = ctx.lane_tier_games.get((self._lane_of(champ), tier), 0) if ctx.by_elo else ctx.champ_games.get(champ, 0)
        return (2, ctx.behind(tier) if ctx.by_elo else 0, first, int(row["hits"] or 0), row["back_offset"] or 0, *rank)

    def tier_order(self, platform: str, tiers: list[str]) -> list[str]:
        """Elos por revisar en el ranking: como las visitas, primero el más por debajo de su parte; a igualdad, el que
        tiene menos jugadores encontrados en esa plataforma y luego el más bajo. Antes iba primero el de menos jugadores
        encontrados (a igualdad, el más alto): tras 250 de Platino buscaba en Oro aunque Bronce tuviera menos partidas."""
        found = self.found_by_tier or {}
        need = self._tier_need(tiers)
        return sorted(tiers, key=lambda t: (-need.get(t, 0), found.get((platform, t), 0), TIER_INDEX.get(t, 0)))

    def searching(self) -> bool:
        """¿Alguna búsqueda de OTPs de esta región sigue revisando jugadores?"""
        return any(sc.status not in ("idle", "stopped") for sc in self.scouts)

    def _games_cap(self) -> int:
        """Partidas por OTP mientras se buscan más (0 = sin tope; también sin tope cuando ya no queda nadie por revisar)."""
        return int(self.config.games_per_otp or 0) if self.searching() else 0

    def _lacking_details(self, player_ids: list[int]) -> set[int]:
        """OTPs con partidas suyas con los campeones elegidos (en su rol) guardadas pero sin micro/macro."""
        if not player_ids:
            return set()
        cond, params = target_sql(self.config, "p")
        out: set[int] = set()
        for i in range(0, len(player_ids), 400):
            chunk = player_ids[i : i + 400]
            out |= {
                r[0]
                for r in self.svc.db.query(
                    f"SELECT DISTINCT p.player_id FROM participants p JOIN players pl ON pl.id = p.player_id "
                    f"LEFT JOIN otp_games o ON o.match_id = p.match_id AND o.puuid = pl.puuid "
                    f"WHERE p.player_id IN ({','.join('?' * len(chunk))}) AND {cond} AND o.match_id IS NULL",
                    (*chunk, *params),
                )
            }
        return out

    def _pattern_detail(self, match_id: str, payload: dict, tier: str | None, found: list) -> bool:
        """Partida que encaja con una o varias ventanas: su timeline y la partida entera (los 10 analizados) a sus
        resultados. ``True`` si se guardó en alguna."""
        db = self.svc.db
        todo = [(w, res) for w, res in found if not pattern_search.done(db, match_id, w["id"])]
        if not todo:
            return False
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        try:
            with self.timed("riot"):
                timeline = self._prefetch.take_timeline(match_id) or self.svc.riot.timeline(match_id, **kw)
        except RiotKeyError:
            raise
        except RiotAPIError as exc:
            if exc.status not in (400, 403, 404, 422):
                raise
            timeline = None  # Riot no tiene el timeline: se guarda lo que da la partida
        try:
            with self.timed("analizar_guardar"):
                saved = pattern_search.save_game(self.svc, payload, timeline, todo, tier)
        except Exception:
            log.exception("No se pudo guardar la partida %s de la búsqueda", match_id)
            return False
        if saved:
            self.state["details"] += 1
            self.crawler.note_pattern_saved(todo)
        return bool(saved)

    def _pattern_from_store(self, item: dict) -> None:
        """Una partida ya guardada que encaja con una ventana (``scan_stored``): se completa con su timeline (y la
        partida, que la base de datos no guarda en bruto) y va a los resultados."""
        svc, db = self.svc, self.svc.db
        mid = item["match_id"]
        todo = [(w, res) for w, res in item["found"] if not pattern_search.done(db, mid, w["id"])]
        if not todo:
            return
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        match, timeline = otp_details.load_raw(db, svc, mid, "")
        try:
            with self.timed("riot"):
                if match is None:
                    match = svc.riot.match(mid, **kw)
                if match is not None and timeline is None:
                    timeline = svc.riot.timeline(mid, **kw)
        except RiotKeyError:
            raise
        except RiotAPIError as exc:
            if exc.status not in (400, 403, 404, 422):
                raise
            if match is None:
                return
        if match is None:
            return
        try:
            with self.timed("analizar_guardar"):
                saved = pattern_search.save_game(svc, match, timeline, todo, item.get("tier"))
        except Exception:
            log.exception("No se pudo guardar la partida %s de la búsqueda", mid)
            return
        if saved:
            self.state["details"] += 1
            self.crawler.note_pattern_saved(todo, from_store=True)
            self.crawler.note_analyzed(item.get("tier") or "", region=self.region)
            from app import autopush

            autopush.note_game(svc, item.get("tier"), analyzed=True)

    def _detail(self, match_id: str, payload: dict | None, player) -> bool:
        """Baja el timeline (y la partida si no se tiene) y guarda el micro/macro de ese OTP. ``True`` si se guardó."""
        svc = self.svc
        if (match_id, player["puuid"]) in self._saving:
            return False  # ya descargada, esperando su análisis
        if svc.db.scalar("SELECT 1 FROM otp_games WHERE match_id = ? AND puuid = ?", (match_id, player["puuid"])):
            return False
        fetched = self._detail_downloads(match_id, payload)
        if fetched is None:
            return False
        payload, timeline = fetched
        info = otp_details.player_info(svc.db, player["puuid"])  # elo al bajarla (el de la liga ahora mismo)
        elo = {"tier": info.get("tier") or player.get("tier"), "division": info.get("division"), "lp": info.get("lp")}
        if not elo["tier"]:
            elo = self._game_elo(payload, player["puuid"])  # sin elo propio: el de la partida
        if not SAVE_IN_BACKGROUND:
            return self._save_detail(match_id, payload, timeline, player["puuid"], elo)
        return self._queue_detail(match_id, payload, timeline, player["puuid"], elo)

    def _game_elo(self, payload: dict, puuid: str) -> dict:
        """Elo de la partida para quien no tiene elo propio (sin clasificar en SoloQ): la media del de los demás con
        elo. El de cada uno, el que ya se sabe o el de su liga (1 llamada a su servidor, que va sobrado; se recuerda)."""
        platform = str((payload.get("metadata") or {}).get("matchId") or "").split("_", 1)[0].lower()
        if platform not in PLATFORMS:
            return {"tier": None, "division": None, "lp": None}
        values = []
        for part in (payload.get("info") or {}).get("participants") or []:
            other = part.get("puuid")
            value = self._rank_value(platform, other) if other and other != puuid else None
            if value is not None:
                values.append(value)
        return rank_of_value(sum(values) / len(values)) if values else {"tier": None, "division": None, "lp": None}

    def _rank_value(self, platform: str, puuid: str) -> float | None:
        """Elo de SoloQ de un jugador en número (``rank_value``): el guardado o, si no se ha mirado en ``RANK_TTL_S``,
        el de su liga (se guarda). ``None`` sin clasificar."""
        db = self.svc.db
        row = db.one("SELECT tier, division, rank_checked_at FROM players WHERE puuid = ?", (puuid,))
        if row is not None and row["tier"]:
            return rank_value(row["tier"], row["division"])
        if row is not None and row["rank_checked_at"] and now_s() - int(row["rank_checked_at"]) < RANK_TTL_S:
            return None  # mirado hace poco: sin clasificar
        try:
            entries = self.svc.riot.league_entries_by_puuid(platform, puuid, priority=Priority.LOW, stop=self.stop_event)
        except RiotKeyError:
            raise
        except RiotAPIError:
            return None
        solo = next((e for e in entries or [] if e.get("queueType") == "RANKED_SOLO_5x5"), None) or {}
        with db.write() as conn:
            conn.execute(
                "UPDATE players SET tier = COALESCE(?, tier), division = COALESCE(?, division), lp = COALESCE(?, lp), "
                "rank_checked_at = ? WHERE puuid = ?",
                (solo.get("tier"), solo.get("rank"), solo.get("leaguePoints"), now_s(), puuid),
            )
        return rank_value(solo.get("tier"), solo.get("rank"))

    def _detail_downloads(self, match_id: str, payload: dict | None) -> tuple[dict, dict | None] | None:
        """(partida, timeline o ``None``), de la precarga o de Riot; ``None`` si Riot no tiene la partida."""
        svc = self.svc
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        try:
            with self.timed("riot"):
                if payload is None:
                    payload = svc.riot.match(match_id, **kw)
                    if payload is None:
                        return None
                timeline = self._prefetch.take_timeline(match_id) or svc.riot.timeline(match_id, **kw)
        except RiotKeyError:
            raise
        except RiotAPIError as exc:
            if exc.status not in (400, 403, 404, 422):
                raise  # red o Riot caído: se reintenta en otra visita
            timeline = None  # Riot no tiene el timeline: se guarda lo que da la partida
            if payload is None:
                return None
        return payload, timeline

    def _save_detail(self, match_id: str, payload: dict, timeline: dict | None, puuid: str, elo: dict) -> bool:
        """Analiza y guarda el micro/macro de esa partida en el hilo de la región. ``True`` si se guardó."""
        svc = self.svc
        try:
            # OTP o no lo decide ``classify`` (lo que juega, con sus partidas revisadas): ser candidato no basta.
            with self.timed("analizar_guardar"):
                saved = otp_details.save(svc.db, svc, payload, timeline, puuid, elo)
        except Exception:
            log.exception("No se pudo analizar la partida %s del jugador", match_id)
            return False
        if saved:
            self.state["details"] += 1
        return saved

    def _queue_detail(self, match_id: str, payload: dict, timeline: dict | None, puuid: str, elo: dict) -> bool:
        """Deja la partida al hilo que analiza (``SAVE_IN_BACKGROUND``), esperando si va por detrás; ``False`` si se para."""
        if self._saver is None or not self._saver.is_alive():
            self._saver = threading.Thread(target=self._save_loop, name=f"crawler-save-{self.region}", daemon=True)
            self._saver.start()
        item = (match_id, payload, timeline, puuid, elo)
        self._saving.add((match_id, puuid))
        with self.timed("esperando_analisis"):  # si el análisis va por detrás, se espera (no se acumulan en memoria)
            while True:
                try:
                    self._save_q.put(item, timeout=0.5)
                    break
                except queue.Full:
                    if self.stop_event.is_set():
                        self._saving.discard((match_id, puuid))
                        return False
        return True

    def _save_loop(self) -> None:
        """Analiza y guarda lo que descarga la región (en su propio hilo)."""
        svc = self.svc
        while True:
            item = self._save_q.get()
            try:
                if item is None:
                    return
                match_id, payload, timeline, puuid, elo = item
                try:
                    # OTP o no lo decide ``classify`` (lo que juega, con sus partidas revisadas): ser candidato no basta.
                    with self.timed("analizar_guardar"):
                        saved = otp_details.save(svc.db, svc, payload, timeline, puuid, elo)
                except Exception:
                    log.exception("No se pudo analizar la partida %s del jugador", match_id)
                    continue
                if saved:
                    self.state["details"] += 1
            finally:
                if item is not None:
                    self._saving.discard((item[0], item[3]))
                self._save_q.task_done()

    def finish_saving(self, timeout: float = 120) -> None:
        """Espera a que se analice y guarde lo descargado (al parar el recolector)."""
        saver = self._saver
        if saver is None or not saver.is_alive():
            return
        self._save_q.put(None)
        saver.join(timeout)

    def _list_ids(self, *args, **kwargs) -> list[str]:
        with self.timed("riot_listas"):
            return self.svc.riot.match_ids(*args, **kwargs)

    def _visit_otp(self, row) -> None:
        """Una visita a un OTP: sus partidas nuevas y, con lo que quede de presupuesto, su historial hacia atrás."""
        v = self._start_visit(row)
        stored_before = self.state["stored"]
        if v.fresh:
            self.state["players"] += 1
            self._save_progress(v)
            # Su nombre llega con su primera partida (``store_match`` guarda el Riot ID de los 10): pedirlo aparte gastaba
            # una llamada de account-v1 del mismo cupo regional que las partidas por cada jugador nuevo.
        try:
            self._visit_new_games(v)
            self._visit_history(v)
            self._check_specialist(v)
            self._backfill_details(v)
            self._otp_fails.pop(v.puuid, None)
        except RiotAPIError as exc:
            if not isinstance(exc, RiotKeyError):
                fails = self._otp_fails.get(v.puuid, (0, 0.0))[0] + 1
                self._otp_fails[v.puuid] = (fails, time.time() + OTP_FAIL_COOLDOWN_S if fails >= 3 else 0.0)
            raise
        finally:
            self._prefetch.drop()
            self._save_progress(v, final=True)
            self._reclassify(v)
        self._end_visit(v, self.state["stored"] - stored_before)
        from app import autopush  # aquí: evita un import circular

        autopush.note_player(self.svc)  # jugador revisado: AUTO_PUSH_PLAYERS de ellos también suben los datos

    def _reclassify(self, v: _OtpVisit) -> None:
        """Con su historial revisado, sus partidas ya guardadas pasan a OTP o a jugador por lo que juega (las primeras
        se guardan antes de saberlo); si cambian de carpeta, se mueven en segundo plano."""
        try:
            moved = otp_details.reclassify_player(self.svc.db, v.puuid)
        except Exception:  # reclasificar no puede tumbar la visita: al abrir la app se vuelve a hacer
            log.debug("No se pudo reclasificar a %s", v.puuid[:8], exc_info=True)
            return
        if moved:
            threading.Thread(target=otp_details.relocate, args=(self.svc, moved), name="otp-relocate", daemon=True).start()

    def _own_champs(self, row) -> set[int] | None:
        """Sus campeones de candidato (``CrawlerConfig.own_champs``) buscando OTPs; ``None`` en los demás modos."""
        cfg = self.config
        if cfg.focus != "otp":
            return None
        try:
            top = [e for e in json.loads(_field(row, "top") or "[]") if len(e) >= 2]
        except (TypeError, ValueError):
            top = []
        own = cfg.own_champs(top, sourced_champs(row) if "sourced" in row.keys() else ())  # noqa: SIM118
        return own or None

    def _start_visit(self, row) -> _OtpVisit:
        """Empieza una visita: la apunta como visitada y carga su progreso (de cero si es nuevo o cambió la búsqueda)."""
        cfg = self.config
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        platform, puuid = row["platform"], row["puuid"]
        self._visited[puuid] = time.time()
        player = {"id": row["id"], "puuid": puuid, "tier": row["tier"], "champs": self._own_champs(row)}
        list_queue, list_type = cfg.list_filter()
        list_key, config_key = self._list_key(), otp_config_key(cfg)
        depth_start, season_major = self._depth()
        now = now_s()
        fresh = row["anchor"] is None or row["list_key"] != list_key
        prog = self._visit_progress(row, fresh, now)
        newest = int(row["newest_match_at"] or 0)
        if prog["done_at"] and otp_history_state(row, list_key, depth_start, config_key) == "loading":
            # Se pide más historial que la última vez, o cambiaron los campeones o el filtro: se sigue bajando.
            prog.update(done_at=None, done_start=None, reason=None, low_pages=0)
        return _OtpVisit(
            row=row, platform=platform, puuid=puuid, player=player, list_queue=list_queue, list_type=list_type, kw=kw,
            list_key=list_key,
            config_key=config_key, depth_start=depth_start, season_major=season_major, now=now, fresh=fresh, prog=prog,
            newest=newest, budget=OTP_VISIT_MATCHES,
        )

    @staticmethod
    def _visit_progress(row, fresh: bool, now: int) -> dict:
        """Progreso del historial al empezar la visita: el guardado o, si es nuevo o cambió la búsqueda, uno de cero."""
        if fresh:
            return {
                # El ancla va unas horas atrás: lo posterior (la partida en curso o aún sin publicar) entra por «nuevas».
                "anchor": now - OTP_ANCHOR_LAG_S, "offset": 0, "done_at": None, "done_start": None, "reason": None,
                "fwd_more": 0, "fwd_newest": 0, "low_pages": 0, "config_key": None,
                "seen": int(row["seen"] or 0), "hits": int(row["hits"] or 0),
            }
        return {
            "anchor": int(row["anchor"]), "offset": int(row["back_offset"] or 0), "done_at": row["back_done_at"],
            "done_start": row["back_done_start"], "reason": row["back_done_reason"], "fwd_more": int(row["fwd_more"] or 0),
            "fwd_newest": int(row["fwd_newest"] or 0), "low_pages": int(row["low_pages"] or 0), "config_key": row["config_key"],
            "seen": int(row["seen"] or 0), "hits": int(row["hits"] or 0),
        }

    def _save_progress(self, v: _OtpVisit, final: bool = False) -> None:
        """Guarda el progreso de la visita y su partida más reciente (al terminar, también cuándo se visitó)."""
        # Al empezar, cada 25 partidas y al terminar (aunque falle o se pare a mitad): la siguiente visita sigue
        # justo donde se quedó y la web ve el avance.
        prog = v.prog
        with self.svc.db.write() as conn:
            conn.execute(
                "INSERT INTO otp_progress (puuid, list_key, anchor, back_offset, back_done_at, back_done_start, "
                "back_done_reason, fwd_more, fwd_newest, low_pages, config_key, seen, hits, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(puuid) DO UPDATE SET "
                "list_key = excluded.list_key, anchor = excluded.anchor, back_offset = excluded.back_offset, "
                "back_done_at = excluded.back_done_at, back_done_start = excluded.back_done_start, "
                "back_done_reason = excluded.back_done_reason, fwd_more = excluded.fwd_more, "
                "fwd_newest = excluded.fwd_newest, low_pages = excluded.low_pages, config_key = excluded.config_key, "
                "seen = excluded.seen, hits = excluded.hits, updated_at = excluded.updated_at",
                (
                    v.puuid, v.list_key, prog["anchor"], prog["offset"], prog["done_at"], prog["done_start"],
                    prog["reason"], prog["fwd_more"], prog["fwd_newest"] or None, prog["low_pages"],
                    prog["config_key"], prog["seen"], prog["hits"], now_s(),
                ),
            )
            conn.execute(
                "UPDATE players SET newest_match_at = MAX(COALESCE(newest_match_at, 0), ?)"
                + (", last_crawled_at = ?" if final else "")
                + " WHERE id = ?",
                (v.newest, now_s(), v.row["id"]) if final else (v.newest, v.row["id"]),
            )

    def _visit_ingest(self, v: _OtpVisit, match_id: str) -> Ingested:
        """Baja y guarda una partida de la visita y apunta lo que gasta del presupuesto y lo que dice de lo que juega."""
        with self.timed("partidas"):  # cada partida entera (lo no desglosado: base de datos, progreso…)
            got = self._ingest(match_id, v.player)
        v.budget -= 1 + got.detailed
        v.ingested += 1
        v.newest = max(v.newest, got.game_start)
        v.prog["seen"] += got.valid
        v.prog["hits"] += got.valid and got.mine_target
        if v.ingested % 25 == 0:
            self._save_progress(v)
        return got

    def _hint_ahead(self, v: _OtpVisit, match_id: str, ahead_of: dict) -> None:
        """Precarga esa partida y las siguientes que caben en el presupuesto de la visita."""
        self._prefetch.hint(match_id, v.puuid)
        for nxt in ahead_of.get(match_id, [])[: max(0, v.budget - 1)]:
            self._prefetch.hint(nxt, v.puuid)  # las siguientes, mientras se guarda y analiza esta

    def _visit_new_games(self, v: _OtpVisit) -> None:
        """1) Partidas nuevas (posteriores al ancla), de la más antigua a la más reciente, con su propio cursor."""
        # Si se corta a mitad, la siguiente visita sigue desde la última recorrida.
        prog = v.prog
        after = max(prog["anchor"], prog["fwd_newest"] // 1000)
        prog["fwd_more"] = 1  # hasta terminar esta pasada
        ids = self._new_game_ids(v, after)
        self.state["ids_seen"] += len(ids)
        known = self._known_for(v.puuid, ids)
        starts = self._stored_starts([m for m in ids if m in known])
        finished = True
        todo = [m for m in reversed(ids) if m not in known]
        ahead_of = {m: todo[i + 1 : i + 1 + PREFETCH_AHEAD] for i, m in enumerate(todo)}
        for match_id in reversed(ids):
            if self.stop_event.is_set():
                finished = False
                break
            if match_id in known:
                prog["fwd_newest"] = max(prog["fwd_newest"], starts.get(match_id, 0))
                continue
            if v.budget <= 0 or not self.config.share_ok(prog["seen"], prog["hits"]):
                finished = False
                break
            self._hint_ahead(v, match_id, ahead_of)
            got = self._visit_ingest(v, match_id)
            prog["fwd_newest"] = max(prog["fwd_newest"], got.game_start)
        if finished:
            prog["fwd_more"] = 0

    def _new_game_ids(self, v: _OtpVisit, after: int) -> list[str]:
        """Ids de sus partidas nuevas (posteriores a ``after``), sin repetir; con más de 1.000, ninguno (se reancla)."""
        ids: list[str] = []
        for page in range(OTP_FORWARD_PAGES):
            chunk = self._list_ids(
                v.platform, v.puuid, queue=v.list_queue, match_type=v.list_type, start_time=after,
                start=page * OTP_PAGE, count=OTP_PAGE, **v.kw,
            )
            ids.extend(chunk)
            if len(chunk) < OTP_PAGE:
                break
        else:
            # Más de 1.000 partidas nuevas (mucho tiempo sin abrir la app): se reancla el historial para no
            # dejar un hueco; lo ya guardado no se vuelve a descargar.
            v.prog.update(anchor=v.now - OTP_ANCHOR_LAG_S, offset=0, done_at=None, done_start=None, reason=None, low_pages=0, fwd_newest=0)
            ids = []
        return list(dict.fromkeys(ids))

    def _visit_history(self, v: _OtpVisit) -> None:
        """2) Historial hacia atrás con ``endTime`` fijo (el ancla), página a página."""
        # La lista no se mueve aunque juegue más partidas, así que ``start`` (cuántas se llevan recorridas) siempre
        # apunta al mismo sitio.
        cfg, prog = self.config, v.prog
        pages = 0
        cap = self._games_cap()
        while (
            prog["done_at"] is None and v.budget > 0 and pages < OTP_BACK_PAGES and not self.stop_event.is_set()
            and not self._at_cap(cap, prog)  # variedad: el resto, al terminar la búsqueda
            and cfg.share_ok(prog["seen"], prog["hits"])  # no es especialista: no se gastan más llamadas en él
        ):
            pages += 1
            if self._history_page(v, cap):
                break  # se acabó el presupuesto de la visita a mitad de página

    def _at_cap(self, cap: int, prog: dict) -> bool:
        """¿Llegó al tope de partidas por jugador mientras se buscan más OTPs (``cap``; 0 = sin tope)?"""
        return bool(cap and cap_count(self.config, prog["hits"], prog["seen"]) >= cap)

    def _history_page(self, v: _OtpVisit, cap: int) -> bool:
        """Una página del historial: la pide, la recorre y la cierra. ``True`` si se cortó a mitad (no se piden más)."""
        prog = v.prog
        page = _HistoryPage(base=prog["offset"])
        ids = self._list_ids(
            v.platform, v.puuid, queue=v.list_queue, match_type=v.list_type, start_time=v.depth_start or None,
            end_time=prog["anchor"], start=page.base, count=OTP_PAGE, **v.kw,
        )
        self.state["otp_pages"] += 1
        self.state["ids_seen"] += len(ids)
        self._walk_history_page(v, ids, page, cap)
        self._history_page_end(v, ids, page)
        return page.consumed < len(ids)

    def _walk_history_page(self, v: _OtpVisit, ids: list[str], page: _HistoryPage, cap: int) -> None:
        """Recorre la página: baja las partidas que no se conocen y mueve el cursor (``offset``) hasta acabarla o cortarla."""
        cfg, prog = self.config, v.prog
        known = self._known_for(v.puuid, ids)
        todo = [m for m in ids if m not in known]
        ahead_of = {m: todo[i + 1 : i + 1 + PREFETCH_AHEAD] for i, m in enumerate(todo)}
        for match_id in ids:
            if self.stop_event.is_set():
                break
            if match_id not in known:
                if v.budget <= 0 or not cfg.share_ok(prog["seen"], prog["hits"]):
                    break
                self._history_ingest(v, match_id, ahead_of, page)
            page.consumed += 1
            prog["offset"] = page.base + page.consumed
            if page.old_season or self._at_cap(cap, prog):
                break

    def _history_ingest(self, v: _OtpVisit, match_id: str, ahead_of: dict, page: _HistoryPage) -> None:
        """Baja una partida del historial y la suma a su página (revisadas, con esos campeones, temporada anterior)."""
        self._hint_ahead(v, match_id, ahead_of)
        got = self._visit_ingest(v, match_id)
        page.seen += got.valid
        page.hits += got.valid and got.mine_target
        if v.season_major and got.patch_major and got.patch_major < v.season_major:
            page.old_season = True  # primera partida de la temporada anterior: hasta aquí

    def _history_page_end(self, v: _OtpVisit, ids: list[str], page: _HistoryPage) -> None:
        """Tras una página: historial completo o, si más atrás casi no juega esos campeones, parado por «ya no lo jugaba»."""
        cfg, prog = self.config, v.prog
        if page.old_season or (page.consumed == len(ids) and len(ids) < OTP_PAGE):
            prog.update(done_at=now_s(), done_start=v.depth_start, reason="end")
        elif cfg.only_target_games and page.seen >= OTP_LOW_SHARE_MIN:
            low = page.hits <= OTP_LOW_SHARE * page.seen
            prog["low_pages"] = prog["low_pages"] + 1 if low else 0
            # Sus partidas más recientes nunca paran el historial (puede estar de descanso del campeón);
            # más atrás, dos páginas seguidas casi sin esos campeones sí: bajar más sería gastar llamadas en
            # partidas que se descartan.
            if prog["low_pages"] >= OTP_LOW_PAGES and page.base >= OTP_LOW_SKIP:
                prog.update(done_at=now_s(), done_start=v.depth_start, reason="share", config_key=v.config_key)

    def _check_specialist(self, v: _OtpVisit) -> None:
        """Si con lo revisado no juega esos campeones lo bastante, su historial se para ahí (``share``)."""
        cfg, prog = self.config, v.prog
        if not cfg.share_ok(prog["seen"], prog["hits"]):
            prog.update(done_at=now_s(), done_start=v.depth_start, reason="share", config_key=v.config_key)
            log.info(
                "%s no es especialista: %d de %d partidas revisadas con esos campeones (pides %d %%).",
                v.row["game_name"] or v.puuid[:8], prog["hits"], prog["seen"], round(100 * cfg.min_share),
            )

    def _backfill_details(self, v: _OtpVisit) -> None:
        """3) Micro/macro de sus partidas ya guardadas que aún no lo tienen (de antes de activarlo o que fallaron)."""
        cfg = self.config
        if cfg.save_details and not cfg.patterns and v.budget > 0 and not self.stop_event.is_set():
            cond, params = target_sql(cfg, "p")
            for match_id in otp_details.missing(self.svc.db, v.puuid, v.row["id"], cond, params, min(v.budget, OTP_DETAIL_BACKFILL)):
                if self.stop_event.is_set():
                    break
                self._detail(match_id, None, v.player)
                v.budget -= 1

    def _end_visit(self, v: _OtpVisit, stored_now: int) -> None:
        """Fin de la visita: las medias al día, por qué acabó (para el diagnóstico) y su línea en el registro."""
        prog = v.prog
        if stored_now:
            self.svc.agg.bump()
        # Por qué acabó la visita: con el tope de la visita (``OTP_VISIT_MATCHES``) da ~50 partidas analizadas; si acaba
        # antes (historial completo: pocas partidas en la temporada, típico de elos bajos) da menos.
        fin = self._visit_end_reason(v)
        self._visit_result = {"nuevas": stored_now, "revisadas": prog["offset"], "fin": fin}
        why = self._pick_reason(v.row)
        log.info(
            "%s · %s · jugador: %d partidas nuevas, %d analizadas en %.1f min (historial: %d revisadas%s) · fin: %s%s.",
            PLATFORMS[v.platform][1],
            v.row["tier"],
            stored_now,
            getattr(self, "_visit_analyzed", 0),
            (time.time() - self._visited.get(v.puuid, time.time())) / 60,
            prog["offset"],
            ", completo" if prog["done_at"] else "",
            fin,
            why,
        )

    def _visit_end_reason(self, v: _OtpVisit) -> str:
        """Por qué acabó la visita (para el diagnóstico del reparto por elo)."""
        prog = v.prog
        if self.stop_event.is_set():
            return "detenido"
        if v.budget <= 0:
            return "tope de la visita"
        if prog["done_at"]:
            return "historial completo" if prog["reason"] == "end" else "pocas con esos campeones"
        if self._games_cap() and cap_count(self.config, prog["hits"], prog["seen"]) >= self._games_cap():
            return "tope por jugador"
        return "páginas de la visita"

    def _pick_reason(self, row) -> str:
        """Por qué se eligió al jugador (el elo más atrasado y si había alguien de él en la cola), para el registro."""
        pick = getattr(self, "_pick", None) or {}
        why = ""
        if pick.get("mas_atrasado"):
            why = f" · elegido: {pick.get('tipo')}, elo más atrasado {pick['mas_atrasado']}"
            if pick["mas_atrasado"] != (row["tier"] or "") and not pick.get("hay_de_ese"):
                why += f" (sin nadie de ese elo en la cola de {self.region})"
        return why

    def _known_for(self, puuid: str, ids: list[str]) -> set[str]:
        """Ids que este OTP no necesita pedir: guardados, descartados para todos o descartados solo para él."""
        known = known_match_ids(self.svc.db, ids)
        rest = [m for m in ids if m not in known]
        cfg = self.config
        if rest and cfg.only_target_games and cfg.targets:
            marks = ",".join("?" * len(rest))
            for r in self.svc.db.query(
                f"SELECT match_id, champ, role FROM otp_skips WHERE puuid = ? AND match_id IN ({marks})", (puuid, *rest)
            ):
                champ, role = int(r["champ"] or 0), r["role"]
                if role is None and cfg.roles_for(champ) is not None:
                    continue  # descarte de antes de los roles con un campeón que ahora interesa: se vuelve a mirar
                if not cfg.is_target(champ, role or None):
                    known.add(r["match_id"])
        return known

    def _stored_starts(self, match_ids: list[str]) -> dict[str, int]:
        if not match_ids:
            return {}
        marks = ",".join("?" * len(match_ids))
        rows = self.svc.db.query(f"SELECT match_id, game_start FROM matches WHERE match_id IN ({marks})", match_ids)
        return {r[0]: int(r[1] or 0) for r in rows}

    def _other_target(self, record, puuid: str) -> bool:
        """¿Otro OTP buscado jugó esta partida con uno de los campeones elegidos? Entonces también interesa (buscando
        partidas de un campeón, basta con que alguien lo juegue en ese rol)."""
        cfg = self.config
        others = [p.puuid for p in record.participants if p.puuid and p.puuid != puuid and cfg.is_target(p.champ, p.role)]
        if not others:
            return False
        if cfg.focus == "games":
            return True  # partidas de un campeón: vale cualquiera que lo juegue en ese rol
        marks = ",".join("?" * len(others))
        champs = cfg.targets
        if self.svc.db.scalar(
            f"SELECT 1 FROM otp_sources WHERE puuid IN ({marks}) AND champ IN ({','.join('?' * len(champs))}) LIMIT 1", (*others, *champs)
        ):
            return True  # uno de los mejores de ese campeón (OP.GG)
        for r in self.svc.db.query(f"SELECT top FROM mastery_checks WHERE puuid IN ({marks})", others):
            try:
                if self.config.qualifies(json.loads(r["top"])):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _download_match(self, match_id: str) -> tuple[dict | None, Ingested | None]:
        """La partida (de la precarga o de Riot). Si Riot da un error propio de esa partida, se recuerda para no atascar
        el historial en ella y se devuelve su resultado."""
        svc = self.svc
        try:
            payload = self._prefetch.take(match_id)
            if payload is None:
                with self.timed("riot"):
                    payload = svc.riot.match(match_id, priority=Priority.LOW, stop=self.stop_event)
        except RiotKeyError:
            raise
        except RiotAPIError as exc:
            if exc.status not in (400, 403, 422):
                raise  # red o Riot caído: se reintenta en la siguiente visita
            mark_skipped(svc.db, match_id, f"error {exc.status}")
            self.state["errors"] += 1
            return None, Ingested("unknown")
        self.state["fetched"] += 1
        self.state["last_activity"] = time.time()
        if payload is None:
            mark_skipped(svc.db, match_id, "404")
            return None, Ingested("missing")
        return payload, None

    def _keep(self, record, mine, player, got: Ingested) -> tuple[bool, list]:
        """¿Se guarda la partida? Y, buscando por partidas, las ventanas con las que encaja."""
        cfg = self.config
        own = _field(player, "champs")  # buscando OTPs: solo sus campeones de candidato
        got.mine_target = mine is not None and cfg.is_target(mine.champ, mine.role) and (not own or mine.champ in own)
        if cfg.patterns:
            # Buscando por partidas: toda partida bajada se guarda en la base de datos (sirve para todo lo demás, y una
            # ventana nueva la encuentra sin volver a bajarla); como resultado, solo las que encajan.
            found = pattern_search.match_record(cfg.patterns, record)
            self.crawler.note_pattern_checked(found)
            if found:
                self.state["target_games"] += 1
            return True, found
        keep = got.mine_target or self._other_target(record, player["puuid"])
        if keep:
            self.state["target_games"] += 1
        return keep, []

    def _skip_for_player(self, match_id: str, mine, player) -> None:
        """Solo interesan sus partidas con los campeones elegidos (en su rol): se recuerda para su recorrido (no para
        todo el programa: otro OTP o su perfil sí pueden necesitarla)."""
        champ, role = (mine.champ, mine.role or "") if mine is not None else (0, "")
        with self.svc.db.write() as conn:
            conn.execute("INSERT OR REPLACE INTO otp_skips (puuid, match_id, champ, role) VALUES (?, ?, ?, ?)", (player["puuid"], match_id, champ, role))
        self.state["other_champ_skipped"] += 1

    def _details_for(self, match_id: str, payload: dict, player, got: Ingested, found: list) -> bool:
        """Timeline y análisis: de la partida entera si encaja con una ventana; con todo el ranking, de los 10 jugadores de
        cada partida nueva; si no, del jugador con su campeón."""
        cfg = self.config
        if not cfg.save_details or got.result not in ("stored", "duplicate"):
            return False
        if cfg.patterns:
            return bool(found) and self._pattern_detail(match_id, payload, _field(player, "tier"), found)
        if not cfg.targets:
            return got.result == "stored" and self._ranking_detail(match_id, payload, player)
        return got.mine_target and self._detail(match_id, payload, player)

    def _ranking_detail(self, match_id: str, payload: dict, player) -> bool:
        """Todo el ranking: el timeline y el análisis de los 10 jugadores de la partida, solo en la base de datos
        (``otp_details.save_ranking_game``: cuentan en la gráfica del total; no van a data/ ni a GitHub). ``True`` si se
        guardó alguno. Cuesta una llamada más por partida (el timeline)."""
        fetched = self._detail_downloads(match_id, payload)
        if fetched is None:
            return False
        payload, timeline = fetched
        try:
            with self.timed("analizar_guardar"):
                saved = otp_details.save_ranking_game(self.svc.db, self.svc, payload, timeline, _field(player, "tier"))
        except Exception:
            log.exception("No se pudo analizar la partida %s del ranking", match_id)
            return False
        self.state["details"] += saved
        return saved > 0

    def _count_stored(self, payload: dict, player, got: Ingested) -> None:
        """Contadores, subida a GitHub y medias del ranking de una partida ya guardada (o repetida)."""
        svc, cfg = self.svc, self.config
        if got.detailed:
            self.crawler.note_analyzed(_field(player, "tier") or "", region=self.region)
            self._visit_analyzed += 1
        # Buscando por partidas solo cuentan para la subida las que encajan (las demás van a la base de datos, que no se sube).
        if got.detailed or (got.result == "stored" and not cfg.patterns):
            from app import autopush  # cada AUTO_PUSH_GAMES partidas nuevas, commit «datos» y push

            autopush.note_game(svc, _field(player, "tier"), analyzed=bool(got.detailed))
        if got.result != "stored":
            key = {"duplicate": "duplicates", "remake": "remakes"}.get(got.result, "ignored")
            self.state[key] += 1
            return
        if not cfg.targets and not got.detailed:  # todo el ranking: las que no se pudieron analizar también cuentan
            self.crawler.note_session(self.region, _field(player, "tier") or "")
        # Las estadísticas exactas de Riot de sus 10 jugadores, a las medias por elo (también sin análisis).
        try:
            from app.coach import ranking_stats

            stats = ranking_stats.of(svc)
            if stats is not None:
                stats.add(payload, _field(player, "tier"))
        # son medias: nunca paran la recogida
        except Exception:  # noqa: BLE001
            log.debug("No se pudieron sumar las medias del ranking", exc_info=True)
        self.state["stored"] += 1
        self.crawler.total_stored += 1
        self.stored_at.append(time.time())

    def _ingest(self, match_id: str, player) -> Ingested:
        """Descarga una partida y la guarda (o la recuerda como descartada para no volver a pedirla)."""
        svc, cfg = self.svc, self.config
        payload, failed = self._download_match(match_id)
        if failed is not None:
            return failed
        record = parse_match(payload)
        if record is None:
            mark_skipped(svc.db, match_id, "formato desconocido")
            return Ingested("unknown")
        mine = next((pr for pr in record.participants if pr.puuid == player["puuid"]), None)
        valid = mine is not None and record.is_rift and not record.is_remake and record.queue in cfg.queue_ids()
        got = Ingested("", record.game_start, _patch_major(record.patch), valid)
        found: list = []
        if cfg.targets:
            # Otra cola (ARAM, URF o la que no se pidió: con colas mezcladas la lista llega sin filtrar), otro mapa o un
            # remake: no se guarda como partida de esos campeones.
            keep, found = self._keep(record, mine, player, got) if valid else (False, [])
            if not keep and cfg.only_target_games:
                self._skip_for_player(match_id, mine, player)
                got.result = "other"
                return got
        with self.timed("guardar_partida"):
            got.result = store_match(svc.db, record, seed_tier=player["tier"], stats_queues=svc.settings.stats_queues)
        got.detailed = self._details_for(match_id, payload, player, got, found)
        self._count_stored(payload, player, got)
        return got

    def _refill_seeds(self, platform: str, tier: str) -> int:
        now = now_s()
        added = self._refill_apex(platform, tier, now) if tier in APEX_TIERS else self._refill_division(platform, tier, now)
        if added is None:
            return 0
        self.state["seeds_added"] += added
        if added:
            if self.config.targets:
                log.info(
                    "Ranking %s %s: +%d jugadores por revisar (candidatos: solo cuentan los que cumplan al mirar su maestría).",
                    PLATFORMS[platform][1], tier, added,
                )
            else:
                log.info("Ranking %s %s: +%d jugadores para recoger sus partidas.", PLATFORMS[platform][1], tier, added)
        return added

    def _refill_apex(self, platform: str, tier: str, now: int) -> int | None:
        """Maestro, Gran Maestro o Aspirante: la liga entera, cada ``APEX_REFRESH_S`` (``None`` si aún no toca)."""
        riot, db = self.svc.riot, self.svc.db
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        row = db.one(
            "SELECT refreshed_at FROM seed_pages WHERE platform = ? AND queue = ? AND tier = ? AND division = 'I'",
            (platform, QUEUE_SOLO, tier),
        )
        if row and row["refreshed_at"] and now - row["refreshed_at"] < APEX_REFRESH_S:
            return None
        league = riot.apex_league(platform, tier, **kw) or {}
        entries = [dict(e, tier=tier) for e in league.get("entries") or []]
        added = upsert_seed_players(db, platform, entries, tier)
        self._save_page(platform, tier, "I", 1, None, now)
        return added

    def _refill_division(self, platform: str, tier: str, now: int) -> int | None:
        """Elos con divisiones: la siguiente página de la división que va más atrás (``None`` si no hay o llega vacía)."""
        riot, db = self.svc.riot, self.svc.db
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        pages = {
            r["division"]: r
            for r in db.query(
                "SELECT division, next_page, exhausted_at FROM seed_pages WHERE platform = ? AND queue = ? AND tier = ?",
                (platform, QUEUE_SOLO, tier),
            )
        }
        candidates = self._division_candidates(pages, now)
        if not candidates:
            return None
        page, division = min(candidates)
        entries = riot.league_entries(platform, tier, division, page, **kw)
        if not entries:
            self._save_page(platform, tier, division, page, now, now)
            return None
        added = upsert_seed_players(db, platform, entries, tier, division)
        self._save_page(platform, tier, division, page + 1, None, now)
        return added

    @staticmethod
    def _division_candidates(pages: dict, now: int) -> list[tuple[int, str]]:
        """(página siguiente, división) de las divisiones por pedir; una agotada vuelve a la 1 pasado ``LADDER_RESET_S``."""
        candidates = []
        for division in DIVISIONS:
            info = pages.get(division)
            if info and info["exhausted_at"]:
                if now - info["exhausted_at"] < LADDER_RESET_S:
                    continue
                candidates.append((1, division))  # refrescar la escalera desde el principio
            else:
                candidates.append((info["next_page"] if info else 1, division))
        return candidates

    def _save_page(self, platform, tier, division, next_page, exhausted_at, refreshed_at) -> None:
        with self.svc.db.write() as conn:
            conn.execute(
                "INSERT INTO seed_pages (platform, queue, tier, division, next_page, exhausted_at, refreshed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, queue, tier, division) DO UPDATE SET "
                "next_page = excluded.next_page, exhausted_at = excluded.exhausted_at, refreshed_at = excluded.refreshed_at",
                (platform, QUEUE_SOLO, tier, division, next_page, exhausted_at, refreshed_at),
            )

    # ---------------------------------------------------------------- partidas
    def _crawl_player(self, player, platform: str) -> None:
        """Modo normal: las partidas recientes de un jugador del ranking (hasta ``matches_per_player``, del periodo)."""
        svc, cfg = self.svc, self.config
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        newest_known = player["newest_match_at"] or 0
        newest = newest_known
        # La primera vez, sus partidas del periodo elegido (las más recientes, hasta matches_per_player).
        start_time = newest_known // 1000 + 1 if newest_known else (cfg.active_since() or None)
        stored_before = self.state["stored"]
        try:
            ids = svc.riot.match_ids(
                platform,
                player["puuid"],
                queue=cfg.list_filter()[0],
                match_type=cfg.list_filter()[1],
                start_time=start_time,
                count=cfg.matches_per_player,
                **kw,
            )
            known = known_match_ids(svc.db, ids)
            new_ids = [m for m in ids if m not in known]
            self.state["ids_seen"] += len(ids)
            # De la más antigua a la más reciente: si se cancela a mitad, la
            # siguiente pasada recoge las que faltan (startTime > última vista).
            for match_id in reversed(new_ids):
                got = self._ingest(match_id, player)
                if got.game_start and got.result != "unknown":
                    newest = max(newest, got.game_start)
            stored_now = self.state["stored"] - stored_before
            if stored_now:
                svc.agg.bump()
            self.state["players"] += 1
            if ids:
                log.info(
                    "%s · %s: %d partidas nuevas de %d (total región: %d).",
                    PLATFORMS[platform][1],
                    player["tier"],
                    stored_now,
                    len(ids),
                    self.state["stored"],
                )
        finally:
            # Se marca aunque falle para no reintentar en bucle al mismo jugador.
            with svc.db.write() as conn:
                conn.execute(
                    "UPDATE players SET last_crawled_at = ?, newest_match_at = ? WHERE id = ?",
                    (now_s(), newest or None, player["id"]),
                )


class OtpScout(threading.Thread):
    """Busca OTPs en el ranking de una plataforma mientras ``RegionWorker`` descarga sus partidas.

    La maestría y las ligas van al host de cada plataforma (kr, euw1…) y las partidas al de la región (asia,
    europe…). Riot cuenta cada host por separado, así que hay una búsqueda por plataforma (EUW y EUNE buscan a la
    vez) y ninguna le quita ritmo a la descarga.

    Orden: primero la tabla de los mejores de cada campeón de OP.GG (OTPs al instante, sin revisar maestrías) y
    después el ranking, **repartido por elos**: cada tanda va al elo con menos OTPs encontrados en esa plataforma (a
    igualdad, el más alto), así hay OTPs de todos los elos antes que muchos de uno solo.
    """

    def __init__(self, worker: RegionWorker, platform: str) -> None:
        super().__init__(name=f"crawler-{platform}-otps", daemon=True)
        self.worker = worker
        self.platform = platform
        self.status = "starting"
        self._cooldown: dict[str, float] = {}  # elo -> hasta cuándo descansa (ranking ya revisado)
        # Tablas de los mejores de OP.GG (Diamante o más) que faltan (``None``: aún sin mirar) y si les toca el turno.
        self._pending_boards: list[int] | None = None
        self._board_turn = False

    def _set(self, status: str) -> None:
        if status != self.status:
            self.status = status
            statuses = {sc.status for sc in self.worker.scouts} or {status}
            # Estado conjunto para la web: buscando si alguna busca; en pausa/esperando key si todas lo están.
            for state in ("running", "starting", "waiting_key", "paused_game", "idle", "stopped"):
                if state in statuses:
                    self.worker.state["search"] = state
                    break
            self.worker.wake()  # el hilo de descarga decide si queda algo pendiente

    def run(self) -> None:
        w = self.worker
        tiers = list(reversed(w.config.tiers))
        while not w.stop_event.is_set():
            try:
                self._search_step(tiers)
            except Cancelled:
                break
            except RiotKeyError as exc:
                w.state["last_error"] = exc.message
                self._set("waiting_key")
                w.svc.keys.wait_for_change(w.svc.keys.version, w.stop_event)
            except RiotAPIError as exc:
                w.state["errors"] += 1
                w.state["last_error"] = exc.message
                log.warning("Búsqueda de jugadores %s: %s", self.platform, exc.message)
                w.stop_event.wait(3)
            except Exception as exc:
                w.state["errors"] += 1
                w.state["last_error"] = str(exc)
                log.exception("Error inesperado buscando jugadores en %s", self.platform)
                w.stop_event.wait(5)
        self._set("stopped")

    def _search_step(self, tiers: list[str]) -> None:
        """Una vuelta de la búsqueda: esperar, una tabla de los mejores de OP.GG o una tanda del ranking del elo que toca."""
        w = self.worker
        if self._wait_if_paused():
            return
        self._set("running")
        if self._pending_boards is None:
            pending = getattr(w, "leaderboard_pending", None)
            self._pending_boards = list(pending(self.platform)) if pending else []
        tier, full = self._next_tier(tiers)
        if tier is None and full:
            self._set("idle")
            w.stop_event.wait(SCOUT_BACKLOG_WAIT_S)
            return
        if self._board_step(tier):
            return
        if tier is None:
            self._set("idle")  # ranking ya revisado entero: se vuelve a mirar al refrescarse las ligas
            w.stop_event.wait(30)
            return
        if w._discover(self.platform, tier) == 0 and w._refill_seeds(self.platform, tier) == 0:
            self._cooldown[tier] = time.time() + EXHAUSTED_COOLDOWN_S

    def _wait_if_paused(self) -> bool:
        """Sin key válida o en partida: espera (``True``: la vuelta acaba aquí)."""
        w = self.worker
        if not w.svc.keys.get() or w.svc.keys.status == "invalid":
            self._set("waiting_key")
            w.svc.keys.wait_for_change(w.svc.keys.version, w.stop_event)
            return True
        if w.crawler.paused_reason():
            self._set("paused_game")
            w.stop_event.wait(5)
            return True
        return False

    def _next_tier(self, tiers: list[str]) -> tuple[str | None, set[str]]:
        """El elo que toca revisar en el ranking (o ``None``) y los que tienen jugadores de sobra esperando visita."""
        w = self.worker
        now = time.time()
        # Elos con jugadores de sobra esperando visita: no se buscan más (las descargas van a ~1 jugador por
        # minuto y región; revisar ~350 maestrías por minuto solo quitaba CPU y base de datos a las descargas).
        waiting = getattr(w, "waiting_by_tier", None) or {}
        full = {t for t in tiers if waiting.get((self.platform, t), 0) >= SCOUT_BACKLOG}
        tier = next((t for t in w.tier_order(self.platform, tiers) if self._cooldown.get(t, 0) <= now and t not in full), None)
        return tier, full

    def _board_step(self, tier: str | None) -> bool:
        """Turno de una tabla de los mejores de OP.GG: ``True`` si se pidió una (la vuelta acaba aquí)."""
        # Tablas de los mejores de OP.GG (Diamante o más) que faltan: se intercalan con la búsqueda en el ranking, una
        # cada dos vueltas en que toca un elo de los que salen en ellas. Antes se pedían las de todos los campeones al
        # arrancar (173 tablas y una llamada de account-v1 por jugador nuevo, del mismo cupo regional que las partidas):
        # durante ese rato ni se buscaba en Platino ni Esmeralda ni se bajaban partidas a buen ritmo.
        if self._pending_boards and (tier is None or tier in LEADERBOARD_TIERS):
            self._board_turn = not self._board_turn
            if tier is None or self._board_turn:
                self.worker._seed_from_leaderboards(self.platform, [self._pending_boards.pop(0)])
                return True
        return False


class Crawler:
    def __init__(self, svc) -> None:
        self.svc = svc
        self.stop_event = threading.Event()
        self._autostart: bool | None = None
        self.workers: dict[str, RegionWorker] = {}
        self.config: CrawlerConfig | None = None
        self.started_at: float | None = None
        self.total_stored = 0
        self._lock = threading.Lock()
        self._funnel_at = 0.0
        self._funnel_thread: threading.Thread | None = None
        self._rows_lock = threading.Lock()
        self._rows_cache: tuple | None = None  # (clave de la búsqueda, cuándo, filas de ``otp_rows``)
        self._rows_refresh: threading.Thread | None = None
        self._recent_visit_tiers: deque = deque(maxlen=RECENT_TIER_VISITS)  # elos de las últimas visitas (reparto por elo)
        self._recent_games: deque = deque(maxlen=RECENT_TIER_GAMES)  # elo de las últimas partidas analizadas
        self._active_visits: dict[str, list] = {}  # región -> [elo de la visita en curso, partidas ya recogidas, esperadas]
        self._tier_shares: dict[str, tuple[dict, dict]] = {}  # región -> (parte de cada elo, partidas analizadas)
        self._analyzed_noted: Counter = Counter()  # partidas analizadas por elo en este proceso (sin recuento de la BD)
        self._session_tiers: Counter = Counter()  # partidas recogidas por elo desde que arrancó (el reparto por elo)
        # Diagnóstico del reparto por elo (``visit_report``): qué da cada visita, qué había en la cola de cada región y
        # cuántas veces el elo más atrasado no tenía a nadie esperando en la cola de una región.
        self._visit_stats: dict[str, dict] = {}  # elo -> {visitas, analizadas, nuevas, s, completo} desde que arrancó
        self._ratio_stats: dict[str, dict] = {}  # pinta de especialista -> {visitas, descartados, nuevas} desde que arrancó
        self._visit_log: deque = deque(maxlen=VISIT_LOG_SIZE)
        self._pick_misses: dict[str, Counter] = {}  # elo más atrasado sin candidatos -> Counter(región)
        self._queue_by_tier: dict[str, dict] = {}  # región -> elo -> candidatos por tipo [nuevos, partidas nuevas, historial, sin análisis]
        # Búsqueda por partidas: partidas revisadas y, por ventana, las que encajan y las guardadas (desde que arrancó).
        self._pattern_stats: dict = {"revisadas": 0, "ventanas": {}, "en_bd": None}
        # Hasta que acaba la búsqueda en lo ya guardado, las regiones no visitan a nadie (``PATTERN_SCAN_WAIT_S`` como
        # mucho): lo que encuentra da una partida por cada 2 llamadas y una visita, una por cada ~30-40.
        self.pattern_scan_done = threading.Event()
        self.pattern_scan_done.set()
        self._load_recent()
        saved = svc.db.meta_get("crawler_config")
        if saved:
            try:
                self.config = CrawlerConfig.from_saved(json.loads(saved))
            except (TypeError, ValueError):
                self.config = None

    @property
    def running(self) -> bool:
        return any(w.is_alive() or any(sc.is_alive() for sc in w.scouts) for w in self.workers.values())

    def searching(self) -> bool:
        return any(w.searching() for w in self.workers.values())

    def _load_recent(self) -> None:
        """Las últimas partidas analizadas de la sesión anterior (el reparto sigue donde se quedó)."""
        try:
            saved = json.loads(self.svc.db.meta_get(RECENT_META_KEY) or "[]")
        # sin base de datos o un valor raro: se empieza de cero
        except Exception:  # noqa: BLE001
            return
        if isinstance(saved, list):
            self._recent_games.extend(str(t) for t in saved[-RECENT_TIER_GAMES:])
            if saved:
                log.info("Reparto por elo: se sigue con las últimas %d partidas analizadas de la sesión anterior (%s).",
                         len(self._recent_games), ", ".join(f"{t} {n}" for t, n in sorted(
                             Counter(self._recent_games).items(), key=lambda kv: TIER_INDEX.get(kv[0], 0))))

    def _save_recent(self) -> None:
        with self._rows_lock:
            games = list(self._recent_games)
        if not games:
            return
        try:
            self.svc.db.meta_set(RECENT_META_KEY, json.dumps(games))
        except Exception:  # noqa: BLE001
            log.debug("No se pudo guardar el reparto reciente por elo", exc_info=True)

    def visit_started(self, region: str, tier: str, expected: int = ACTIVE_VISIT_GAMES) -> None:
        """Una región empieza a visitar a un jugador de ese elo: cuenta como ``expected`` partidas hasta que acabe."""
        with self._rows_lock:
            self._active_visits[region] = [tier, 0, expected]
            self._recent_visit_tiers.append(tier)

    def visit_ended(self, region: str, info: dict | None = None) -> None:
        """Acaba la visita de esa región; ``info`` (elo, analizadas, nuevas, segundos, completo…) va al diagnóstico."""
        with self._rows_lock:
            self._active_visits.pop(region, None)
            if not info:
                return
            info = {"region": region, "at": int(time.time()), **info}
            self._visit_log.append(info)
            st = self._visit_stats.setdefault(info.get("elo") or "", {"visitas": 0, "analizadas": 0, "nuevas": 0, "s": 0.0, "completo": 0})
            st["visitas"] += 1
            st["analizadas"] += int(info.get("analizadas") or 0)
            st["nuevas"] += int(info.get("nuevas") or 0)
            st["s"] += float(info.get("s") or 0)
            st["completo"] += int(info.get("fin") == "historial completo")
            bucket = ratio_bucket(info.get("pinta"))
            if bucket and info.get("fin") in JUDGED_ENDS:
                rs = self._ratio_stats.setdefault(bucket, {"visitas": 0, "descartados": 0, "nuevas": 0})
                rs["visitas"] += 1
                rs["descartados"] += int(info.get("fin") == "pocas con esos campeones")
                rs["nuevas"] += int(info.get("nuevas") or 0)

    def note_pick(self, region: str, wanted: str | None, has_wanted: bool) -> None:
        """El elo más atrasado al elegir jugador y si había alguien de ese elo en la cola de la región."""
        if wanted and not has_wanted:
            with self._rows_lock:
                self._pick_misses.setdefault(wanted, Counter())[region] += 1

    def note_queue(self, region: str, counts: dict) -> None:
        """Candidatos en la cola de la región por elo y tipo (al elegir el último jugador)."""
        with self._rows_lock:
            self._queue_by_tier[region] = counts

    def visit_report(self) -> dict:
        """Para el embudo y el registro: visitas por elo desde que arrancó, la cola por elo (las regiones juntas), las
        veces que el elo más atrasado no tenía candidatos y las últimas visitas."""
        with self._rows_lock:
            stats = {t: dict(v) for t, v in self._visit_stats.items()}
            queues = dict(self._queue_by_tier)
            misses = {t: dict(c) for t, c in self._pick_misses.items()}
            last = list(self._visit_log)
            ratios = {b: dict(self._ratio_stats[b]) for b in RATIO_BUCKETS if b in self._ratio_stats}
        order = lambda t: TIER_INDEX.get(t, 0)  # noqa: E731
        visits = {}
        for t in sorted(stats, key=order):
            v = stats[t]
            n = max(1, v["visitas"])
            visits[t] = {
                "visitas": v["visitas"], "analizadas": v["analizadas"], "nuevas": v["nuevas"],
                "por_visita": round(v["analizadas"] / n, 1), "min_por_visita": round(v["s"] / n / 60, 1),
                "completo_pct": round(100 * v["completo"] / n),
            }
        queue: dict[str, list[int]] = {}
        for counts in queues.values():
            for t, c in counts.items():
                acc = queue.setdefault(t, [0, 0, 0, 0])
                for i in range(4):
                    acc[i] += c[i]
        queue = {t: queue[t] for t in sorted(queue, key=order)}
        return {
            "visitas_por_elo": visits,
            "cola_por_elo": {t: dict(zip(("nuevos", "partidas_nuevas", "historial", "sin_analisis"), c)) for t, c in queue.items()},
            "cola_por_region": queues,
            "sin_candidatos": {t: misses[t] for t in sorted(misses, key=order)},
            "por_pinta": ratios,
            "ultimas_visitas": last,
        }

    def _reset_session(self, config: CrawlerConfig) -> None:
        """«Desde que arrancó»: el diagnóstico de visitas y la búsqueda por partidas empiezan de cero (el reparto
        reciente por elo no)."""
        with self._rows_lock:
            self._visit_stats.clear()
            self._ratio_stats.clear()
            self._visit_log.clear()
            self._pick_misses.clear()
            self._queue_by_tier.clear()
            self._session_tiers.clear()
            self._pattern_stats = {
                "revisadas": 0, "ventanas": {w["id"]: {"encontradas": 0, "guardadas": 0, "de_bd": 0} for w in config.patterns}, "en_bd": None,
            }
        db = getattr(self.svc, "db", None)
        if db is not None:
            otp_details.session_reset(db)  # lo recogido en la sesión por clase (OTPs y resto), también

    # ------------------------------------------------------------ búsqueda por partidas
    def note_pattern_checked(self, found: list) -> None:
        """Una partida bajada y comprobada con las ventanas (``found``: con las que encaja)."""
        with self._rows_lock:
            st = self._pattern_stats
            st["revisadas"] += 1
            for w, _ in found:
                st["ventanas"].setdefault(w["id"], {"encontradas": 0, "guardadas": 0, "de_bd": 0})["encontradas"] += 1

    def note_pattern_saved(self, found: list, from_store: bool = False) -> None:
        with self._rows_lock:
            for w, _ in found:
                v = self._pattern_stats["ventanas"].setdefault(w["id"], {"encontradas": 0, "guardadas": 0, "de_bd": 0})
                v["guardadas"] += 1
                v["de_bd"] += int(from_store)

    def pattern_report(self) -> dict | None:
        """Para la web y el registro: partidas revisadas, por ventana las encontradas y guardadas (y en total, de la base
        de datos) y las ya guardadas que faltan por completar."""
        cfg = self.config
        if cfg is None or not cfg.patterns:
            return None
        with self._rows_lock:
            st = json.loads(json.dumps(self._pattern_stats))
        pending = sum(len(w.pattern_backlog) for w in self.workers.values()) if self.workers else 0
        totals = {}
        try:
            for r in self.svc.db.query(
                "SELECT window_id, COUNT(*) AS n, SUM(win_a) AS w, SUM(exact) AS e FROM pattern_games WHERE analyzed = 1 GROUP BY window_id"
            ):
                totals[r["window_id"]] = {"partidas": int(r["n"]), "ganadas_a": int(r["w"] or 0), "iguales": int(r["e"] or 0)}
        except Exception:  # noqa: BLE001
            pass
        return {
            "revisadas": st["revisadas"],
            "pendientes_bd": pending,
            "en_bd": st.get("en_bd"),
            "ventanas": {
                w["id"]: {**st["ventanas"].get(w["id"], {"encontradas": 0, "guardadas": 0, "de_bd": 0}),
                          "total": totals.get(w["id"], {"partidas": 0, "ganadas_a": 0, "iguales": 0})}
                for w in cfg.patterns
            },
        }

    def _scan_patterns(self, config: CrawlerConfig, stop: threading.Event) -> None:
        """Al arrancar: las partidas ya guardadas que encajan con alguna ventana van a la cola de su región."""
        try:
            self._scan_patterns_now(config, stop)
        finally:
            self.pattern_scan_done.set()
            for w in self.workers.values():
                w.wake()

    def _scan_patterns_now(self, config: CrawlerConfig, stop: threading.Event) -> None:
        t0 = time.time()
        try:
            queues = config.queue_ids()
            items = pattern_search.scan_stored(self.svc.db, config.patterns, config.platforms, queues)
        except Exception:
            log.exception("No se pudieron buscar las ventanas en las partidas ya guardadas")
            return
        if stop.is_set():
            return
        for item in items:
            w = self.workers.get(region_of(item["platform"]))
            if w is not None:
                w.pattern_backlog.append(item)
        with self._rows_lock:
            self._pattern_stats["en_bd"] = len(items)
        log.info("Búsqueda por partidas: %d partidas ya guardadas encajan con las ventanas (se completan con su timeline; %.0f s).",
                 len(items), time.time() - t0)

    def note_analyzed(self, tier: str, n: int = 1, region: str | None = None) -> None:
        """Partidas analizadas de ese elo (el reparto se mide en partidas, no en visitas)."""
        with self._rows_lock:
            self._recent_games.extend([tier] * n)
            self._analyzed_noted[tier] += n
        self.note_session(region, tier, n)

    def note_session(self, region: str | None, tier: str, n: int = 1) -> None:
        """Partidas recogidas de ese elo en esta sesión (analizadas; con todo el ranking, guardadas): el reparto por elo."""
        with self._rows_lock:
            self._session_tiers[tier] += n
            active = self._active_visits.get(region) if region else None
            if active is not None:
                active[1] += n

    def note_visit_tier(self, tier: str) -> None:
        """Una visita de otra región a ese elo (para los tests: como una visita en curso)."""
        self.note_analyzed(tier, ACTIVE_VISIT_GAMES)

    def recent_tiers(self) -> Counter:
        """Partidas analizadas recientes por elo más lo que se espera de las visitas en curso."""
        with self._rows_lock:
            load = Counter(self._recent_games)
            for tier, done, expected in self._active_visits.values():
                load[tier] += max(0, expected - done)  # lo que ya analizó ya está en ``_recent_games``
            return load

    def analyzed_noted(self) -> Counter:
        """Partidas analizadas por elo en este proceso (para sumarlas a un recuento hecho antes)."""
        with self._rows_lock:
            return Counter(self._analyzed_noted)

    def session_tiers(self) -> dict[str, int]:
        """Partidas recogidas por elo desde que arrancó el recolector (la gráfica del recolector de «Datos y API»: no
        vuelve a 0 al subir a GitHub)."""
        with self._rows_lock:
            return {t: n for t, n in self._session_tiers.items() if t}

    def session_load(self) -> dict[str, float]:
        """Partidas recogidas por elo en esta sesión más lo que se espera aún de las visitas en curso (las 4 regiones)."""
        with self._rows_lock:
            load: dict[str, float] = dict(self._session_tiers)
            for tier, done, expected in self._active_visits.values():
                load[tier] = load.get(tier, 0) + max(0, expected - done)
        return load

    def analyzed_by_tier(self) -> dict[str, int]:
        """Partidas analizadas por elo (todas: las que enseña «Datos y API»)."""
        try:
            return dict(otp_details.live_counts(self.svc.db).get("tiers") or {})
        except Exception:  # noqa: BLE001
            return {}

    def note_tier_shares(self, region: str, shares: dict, totals: dict) -> None:
        """El reparto por elo que usa cada región (para el embudo y el registro)."""
        with self._rows_lock:
            self._tier_shares[region] = (shares, totals)

    def tier_report(self) -> dict:
        """Por elo: parte de las partidas nuevas (``tier_shares``; media de las regiones), últimas partidas analizadas y
        partidas analizadas en total."""
        with self._rows_lock:
            per_region = dict(self._tier_shares)
            recent = Counter(self._recent_games)
        out: dict = {}
        for shares, totals in per_region.values():
            for t, sh in shares.items():
                e = out.setdefault(t, {"parte": 0.0, "regiones": 0, "analizadas": 0})
                e["parte"] += sh
                e["regiones"] += 1
                e["analizadas"] += totals.get(t, 0)
        n_recent = sum(recent.values()) or 1
        return {
            t: {"parte_pct": round(100 * e["parte"] / e["regiones"], 1), "recientes_pct": round(100 * recent.get(t, 0) / n_recent, 1),
                "analizadas": e["analizadas"] // max(1, e["regiones"])}
            for t, e in sorted(out.items(), key=lambda kv: TIER_INDEX.get(kv[0], 0))
        }

    def otp_rows_cached(self, cfg: CrawlerConfig, now: int | None = None) -> tuple[list, float]:
        """``otp_rows`` de todas las plataformas de la búsqueda, leído una vez para todas las regiones cada
        ``OTP_PLAN_TTL_S`` como mucho (con 280.000 jugadores revisados tarda ~3 s)."""
        key = (otp_list_key(cfg), tuple(cfg.platforms), tuple(cfg.tiers), cfg.focus, cfg.min_mastery, cfg.min_share)
        with self._rows_lock:
            cached = self._rows_cache
            # Vacía (al arrancar una búsqueda nueva aún no hay candidatos): se vuelve a leer a los 15 s, no a los 2 min. Si
            # no, las regiones se quedaban ~3 min sin visitar a nadie con candidatos ya encontrados (24-11-2026).
            fresh_s = OTP_ROWS_FRESH_S if cached is not None and cached[2] else min(OTP_PLAN_EMPTY_S, OTP_ROWS_FRESH_S)
            if cached is not None and cached[0] == key and (ROWS_REFRESH_IN_BACKGROUND or time.time() - cached[1] < fresh_s):
                if time.time() - cached[1] < fresh_s:
                    return cached[2], cached[1]
                # Caducada: se rehace en segundo plano y mientras tanto se sigue con la anterior. Con muchos jugadores
                # encontrados de elos bajos la consulta llegó a 52 s y la región que la pedía se quedaba parada.
                if self._rows_refresh is None or not self._rows_refresh.is_alive():
                    self._rows_refresh = threading.Thread(target=self._refresh_rows, args=(cfg, key), name="crawler-rows", daemon=True)
                    self._rows_refresh.start()
                return cached[2], cached[1]
            at = time.time()
            rows = otp_rows_for_plan(self.svc.db, cfg, now)
            self._rows_cache = (key, at, rows)
            return rows, at

    def _refresh_rows(self, cfg: CrawlerConfig, key: tuple) -> None:
        try:
            at = time.time()
            rows = otp_rows_for_plan(self.svc.db, cfg)
            with self._rows_lock:
                self._rows_cache = (key, at, rows)
        # se reintenta en la siguiente; mientras, la anterior
        except Exception:  # noqa: BLE001
            log.debug("No se pudo rehacer la lista de jugadores", exc_info=True)

    def write_funnel(self, force: bool = False) -> None:
        """Cada 5 min (lo llama cualquier región): el embudo de la búsqueda en ``data/otps/_busqueda.json``. En su propio
        hilo: con muchos jugadores revisados tarda segundos y no debe frenar las descargas ni el botón de detener."""
        cfg = self.config
        if cfg is None or not cfg.targets:
            return
        with self._lock:
            if not force and time.time() - self._funnel_at < FUNNEL_EVERY_S:
                return
            if self._funnel_thread is not None and self._funnel_thread.is_alive():
                return
            self._funnel_at = time.time()
            self._funnel_thread = threading.Thread(target=self._write_funnel, args=(cfg,), name="crawler-funnel", daemon=True)
            self._funnel_thread.start()

    def _search_funnel(self, cfg: CrawlerConfig) -> dict:
        """El embudo de la búsqueda (maestrías revisadas por plataforma), como mucho cada ``SEARCH_FUNNEL_EVERY_S``: lee
        todas las maestrías revisadas (~350.000) y tardaba hasta 16-25 s en Windows cada 5 min. Lo demás del informe (key,
        reparto por elo, visitas, dónde se va el tiempo) sigue cada 5 min."""
        key = (otp_list_key(cfg), tuple(cfg.platforms), tuple(cfg.tiers), cfg.focus, cfg.min_mastery, cfg.min_share)
        cached = getattr(self, "_search_funnel_cache", None)
        if cached is not None and cached[0] == key and time.time() - cached[1] < SEARCH_FUNNEL_EVERY_S:
            return dict(cached[2])
        funnel = search_funnel(self.svc.db, cfg)
        self._search_funnel_cache = (key, time.time(), dict(funnel))
        return funnel

    def _write_funnel(self, cfg: CrawlerConfig) -> None:
        try:
            # El uso de la key, antes que nada: el embudo tarda hasta ~20 s y al detener la ventana se iba vaciando
            # (salía «63 de 100» con el recolector a tope).
            limiter = getattr(self.svc, "limiter", None)
            limits = limiter.snapshot() if limiter is not None else None
            funnel = self._search_funnel(cfg)
            # Para «Datos y API» (no lo recalcula en cada petición): (configuración, cuándo, embudo).
            import json as _json

            self.last_funnel = (_json.dumps(asdict(cfg), sort_keys=True, default=str), time.time(), dict(funnel))
            funnel["config"] = self._funnel_config(cfg)
            funnel["workers"] = {r: self._funnel_worker(w) for r, w in self.workers.items()}
            # Base de datos: quién espera el candado de escritura y quién lo tiene cogido (s en total) y tamaño del WAL.
            db = self.svc.db
            funnel["db"] = self._funnel_db(db)
            # Jugadores encontrados por elo (todas las plataformas): para ver si la búsqueda reparte entre elos.
            funnel["found_by_tier"] = self._found_by_tier()
            # Uso del cupo de cada host de Riot en los últimos 2 minutos: si una región no llega al límite, el cuello
            # de botella no es la key (es la búsqueda de jugadores o el filtro); «foreign»: otra app con la misma key.
            if limits is not None:
                self._funnel_ratelimit(funnel, limits)
            funnel["rate"] = {"since_start": self.status().get("rate_per_min"), "recent": self.recent_rate(self._all_stored_at())}
            funnel["reparto_elo"] = self.tier_report()
            if funnel["reparto_elo"] and not cfg.patterns:  # buscando por partidas el elo no reparte: no se usa
                log.info("Reparto por elo (parte de las partidas nuevas / últimas partidas analizadas / analizadas en total): %s", ", ".join(
                    f"{t} {e['parte_pct']}%/{e['recientes_pct']}%/{e['analizadas']}" for t, e in funnel["reparto_elo"].items()
                ))
            # Cómo se reparte por elo y por qué: lo que da cada visita, la cola de cada región y el elo que se queda atrás.
            self._funnel_visits(funnel)
            self._save_recent()
            self._funnel_patterns(funnel, cfg)
            # Dónde se va el tiempo de cada grupo de hilos (últimos 5 min, ``app.diag``) y las consultas lentas.
            self._funnel_diagnostics(funnel, db)
            # Una línea por región en el registro (data/registro/app.log) cada 5 min: la evolución entre subidas.
            self._log_worker_states(funnel["workers"])
            base = Path(self.svc.settings.data_dir) / "otps"
            base.mkdir(parents=True, exist_ok=True)
            tmp = base / "_busqueda.json.tmp"
            tmp.write_text(json.dumps(funnel, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(base / "_busqueda.json")
        except Exception:  # es un informe: nunca debe parar la búsqueda
            log.exception("No se pudo escribir el estado de la búsqueda de jugadores")

    def _funnel_config(self, cfg: CrawlerConfig) -> dict:
        """La búsqueda, para el embudo (los campeones por su nombre)."""
        dd = getattr(self.svc, "dd", None)
        return {
            "champions": [dd.name(c) if dd is not None and hasattr(dd, "name") else c for c in cfg.targets],
            "champion_roles": {str(c): r for c, r in cfg.champion_roles.items()},
            "lines": cfg.roles,
            "focus": cfg.focus, "min_mastery": cfg.min_mastery, "min_share": cfg.min_share, "period": cfg.otp_depth,
            "platforms": cfg.platforms, "tiers": cfg.tiers,
        }

    def _funnel_worker(self, w) -> dict:
        """Una región en el embudo: sus contadores, su velocidad reciente, dónde se va su tiempo y su análisis."""
        return {
            **{k: w.state.get(k) for k in ("status", "search", "mastery_checked", "targets_found", "errors", "last_error", "stored", "duplicates", "details", "fetched")},
            "rate_recent": self.recent_rate(getattr(w, "stored_at", ())),
            # Segundos en cada fase («visitas» incluye riot, riot_listas y analizar_guardar; el resto es la base
            # de datos y la cola de cada visita).
            "timing": {k: round(v, 1) for k, v in (getattr(w, "timing", None) or {}).items()},
            # Partidas descargadas esperando su análisis y si el hilo que las analiza sigue vivo.
            "analisis": {
                "en_cola": w._save_q.qsize() if hasattr(w, "_save_q") else None,
                "vivo": bool(getattr(w, "_saver", None) and w._saver.is_alive()),
                "en_proceso": len(getattr(w, "_saving", ()) or ()),
            },
        }

    @staticmethod
    def _funnel_db(db) -> dict:
        """La base de datos en el embudo: tamaño del WAL, sus recortes y el candado de escritura (espera y uso, en s)."""
        return {
            "wal_mb": db.wal_mb() if hasattr(db, "wal_mb") else None,
            "wal_recorte": getattr(db, "wal_stats", None) or {},
            "candado": {
                k: {"esperando_s": round(v[0], 1), "cogido_s": round(v[1], 1), "escrituras": v[2]}
                for k, v in sorted((getattr(db, "lock_stats", None) or {}).items(), key=lambda kv: -kv[1][0])
            },
        }

    def _found_by_tier(self) -> dict[str, int]:
        """Jugadores encontrados por elo, con las plataformas de todas las regiones juntas."""
        by_tier: dict[str, int] = {}
        for w in self.workers.values():
            for (_, tier), n in (getattr(w, "found_by_tier", None) or {}).items():
                by_tier[tier] = by_tier.get(tier, 0) + n
        return by_tier

    @staticmethod
    def _funnel_ratelimit(funnel: dict, limits: dict) -> None:
        """El uso del cupo de cada host de Riot, al embudo y (el de las regiones) al registro."""
        keep = ("windows", "foreign", "rate_limited", "llamadas_min", "llamadas_min_recolector", "max_min_recolector",
                "reserva_pct", "espera_s")
        funnel["ratelimit"] = {host: {k: h.get(k) for k in keep} for host, h in limits.items()}
        regional = {h: v for h, v in limits.items() if h in REGIONS}
        if regional:
            log.info("Key de Riot (llamadas/min del recolector, de un máximo de): %s", ", ".join(
                f"{h} {v.get('llamadas_min_recolector')} de {v.get('max_min_recolector')} (reserva {v.get('reserva_pct')} %)"
                for h, v in sorted(regional.items())
            ))

    def _funnel_visits(self, funnel: dict) -> None:
        """Las visitas por elo, la cola por elo y el elo más atrasado sin candidatos, al embudo y al registro."""
        report = self.visit_report()
        funnel.update(report)
        if report["visitas_por_elo"]:
            log.info("Visitas por elo desde que arrancó (visitas · analizadas · por visita · min por visita · historial completo): %s",
                     ", ".join(f"{t} {v['visitas']}·{v['analizadas']}·{v['por_visita']}·{v['min_por_visita']}·{v['completo_pct']}%"
                               for t, v in report["visitas_por_elo"].items()))
        if report["cola_por_elo"]:
            log.info("Cola por elo, las regiones juntas (nuevos/partidas nuevas/historial/sin análisis): %s",
                     ", ".join(f"{t} {'/'.join(str(n) for n in c.values())}" for t, c in report["cola_por_elo"].items()))
        if report["por_pinta"]:
            log.info("Visitas por pinta de especialista (puntos del principal / del segundo: visitas · descartados · partidas nuevas): %s",
                     ", ".join(f"{b} {v['visitas']}·{v['descartados']}·{v['nuevas']}" for b, v in report["por_pinta"].items()))
        if report["sin_candidatos"]:
            log.info("El elo más atrasado no tenía a nadie en la cola de la región (veces al elegir): %s",
                     ", ".join(f"{t} {sum(r.values())} ({', '.join(f'{k} {n}' for k, n in r.items())})"
                               for t, r in report["sin_candidatos"].items()))

    def _funnel_patterns(self, funnel: dict, cfg: CrawlerConfig) -> None:
        """Buscando por partidas: lo revisado y, por ventana, lo encontrado y guardado, al embudo y al registro."""
        pat = self.pattern_report()
        if pat:
            funnel["busqueda_partidas"] = pat
            name = getattr(getattr(self.svc, "dd", None), "name", None)
            log.info("Búsqueda por partidas: %d partidas revisadas, %d ya guardadas por completar. %s", pat["revisadas"], pat["pendientes_bd"], " | ".join(
                f"{pattern_search.label(w, name)}: {v['encontradas']} encontradas, {v['guardadas']} guardadas ({v['total']['partidas']} en total, "
                f"gana el equipo A {round(100 * v['total']['ganadas_a'] / v['total']['partidas']) if v['total']['partidas'] else 0} %)"
                for w in cfg.patterns if (v := pat["ventanas"].get(w["id"]))
            ))

    @staticmethod
    def _funnel_diagnostics(funnel: dict, db) -> None:
        """Dónde se va el tiempo de cada grupo de hilos y las consultas lentas, al embudo y al registro."""
        from app import diag

        funnel["diagnostico"] = diag.sampler.snapshot()
        if hasattr(db, "slow_stats"):
            funnel["db"]["lentas"] = db.slow_stats()
        log.info("Dónde se va el tiempo (5 min): %s", diag.sampler.summary() or "sin muestras")
        for item in (funnel["db"].get("lentas") or [])[:3]:
            log.info("Base de datos lenta: %s — %d veces, %.1f s en total, la peor %.1f s", item["que"], item["veces"], item["s_total"], item["s_peor"])

    @staticmethod
    def _log_worker_states(workers: dict) -> None:
        """Una línea por región en el registro: partidas, velocidad, análisis, errores y dónde se va el tiempo."""
        for region, w in workers.items():
            t = w.get("timing") or {}
            log.info(
                "Estado %s: %s partidas nuevas (%s/min ahora) · %s analizadas · errores %s · tiempo: %s",
                region, w.get("stored"), "—" if w.get("rate_recent") is None else w["rate_recent"], w.get("details"), w.get("errors"),
                ", ".join(f"{k} {v:.0f}" for k, v in t.items() if isinstance(v, float)) or "—",
            )

    def paused_reason(self) -> str | None:
        if getattr(self.svc.settings, "pause_in_game", True):
            tracker = getattr(self.svc, "tracker", None)
            if tracker is not None and tracker.state.get("in_game"):
                return "in_game"
        return None

    @property
    def autostart_enabled(self) -> bool:
        if getattr(self, "_autostart", None) is not None:
            return self._autostart  # pulsaste detener o arrancar en esta sesión (se guarda en segundo plano)
        return self.svc.db.meta_get("crawler_autostart") == "1"

    def autostart(self) -> bool:
        """Al abrir la app: si lo dejaste recolectando, sigue solo (espera a la key si falta)."""
        if not self.autostart_enabled or self.running or self.config is None:
            return False
        try:
            self.start(self.config, require_key=False, remember=False)
        except (RuntimeError, ValueError) as exc:
            log.warning("No se pudo reanudar el recolector: %s", exc)
            return False
        log.info("Recolector reanudado automáticamente (lo dejaste en marcha).")
        return True

    def start(self, config: CrawlerConfig, require_key: bool = True, remember: bool = True) -> dict:
        config = self._with_role_pool(config.validated())
        with self._lock:
            if self.running:
                raise RuntimeError("El recolector ya está en marcha")
            if require_key and not self.svc.keys.get():
                raise PermissionError("Configura primero la RIOT_API_KEY")
            if remember:
                self._autostart = True
                self.svc.db.meta_set("crawler_autostart", "1")
            self.stop_event = threading.Event()
            self.config = config
            self.svc.db.meta_set("crawler_config", json.dumps(asdict(config)))
            by_region: dict[str, list[str]] = {}
            for platform in config.platforms:
                by_region.setdefault(region_of(platform), []).append(platform)
            self.workers = {
                region: RegionWorker(self, region, platforms, config)
                for region, platforms in by_region.items()
                if region in REGIONS
            }
            self.started_at = time.time()
            self.total_stored = 0
            self._reset_session(config)
            for worker in self.workers.values():
                worker.start()
            if config.patterns:
                self.pattern_scan_done.clear()
                threading.Thread(target=self._scan_patterns, args=(config, self.stop_event), name="crawler-patterns-scan", daemon=True).start()
            else:
                self.pattern_scan_done.set()
            if config.targets and config.save_details:
                otp_details.ensure_files(self.svc)  # carpeta de OTPs que falta o de otro formato: se regenera sola
        log.info("Recolector en marcha en %d regiones en paralelo.", len(self.workers))
        return self.status()

    def _with_role_pool(self, config: CrawlerConfig) -> CrawlerConfig:
        """La versión del juego y, con líneas enteras, sus campeones (de la tier list; sin red, los de la última vez)."""
        dd = getattr(self.svc, "dd", None)
        config = _config_with(config, season_version=getattr(dd, "version", None) or config.season_version)
        if not config.roles:
            return _config_with(config, role_champions=[], role_pools={}) if config.role_champions or config.role_pools else config
        pools = role_pools(self.svc, config.roles) or dict(config.role_pools)
        pool = role_pool_from(pools) or list(config.role_champions)
        if not pool:
            raise ValueError("Aún no se sabe qué campeones se juegan en esa línea: activa OP.GG o Lolalytics y vuelve a probar")
        return _config_with(config, role_champions=pool, role_pools=pools)

    def stop(self) -> dict:
        """Parada pedida por el usuario: tampoco arrancará solo la próxima vez.

        Responde al momento: avisa a los hilos y lo demás va en segundo plano. Antes apuntaba «no arrancar solo» en la
        base de datos antes de responder, y esa escritura esperaba al candado que el recolector tiene cogido más de la
        mitad del tiempo: el botón no hacía nada durante un buen rato (ni «Deteniendo…»)."""
        self.stop_event.set()
        for worker in self.workers.values():
            worker.wake()
        self._autostart = False
        self.stop_thread = threading.Thread(target=self._after_stop, args=(self.stop_event,), name="crawler-stop", daemon=True)
        self.stop_thread.start()
        return self.status()

    def wait_stopped(self, timeout: float = 10) -> None:
        """Para los tests (y al cerrar): espera a que acabe lo que se hace en segundo plano al detener."""
        t = getattr(self, "stop_thread", None)
        if t is not None:
            t.join(timeout)
        f = self._funnel_thread
        if f is not None:
            f.join(timeout)

    def _after_stop(self, stop_event: threading.Event) -> None:
        """Lo que no hace falta para responder al botón: guardar que no arranque solo, el embudo y, si tarda en parar,
        qué hilos siguen y dónde están (en el registro que se sube a GitHub)."""
        t0 = time.time()
        try:
            if self._autostart is False:
                self.svc.db.meta_set("crawler_autostart", "0")
        except Exception:  # noqa: BLE001
            log.debug("No se pudo guardar que el recolector no arranque solo", exc_info=True)
        self.write_funnel(force=True)
        try:
            from app.coach import ranking_stats

            stats = ranking_stats.of(self.svc)
            if stats is not None:
                stats.flush()
        except Exception:  # noqa: BLE001
            log.debug("No se pudieron volcar las medias del ranking", exc_info=True)
        next_report = STOP_REPORT_AFTER_S
        while self.running and stop_event is self.stop_event:
            waited = time.time() - t0
            if waited >= STOP_GIVE_UP_S:
                return
            if waited >= next_report:
                next_report += STOP_REPORT_EVERY_S
                log.info("Deteniendo el recolector (%d s): siguen %s", waited, self._alive_threads())
            time.sleep(1)
        if stop_event is self.stop_event:
            log.info("Recolector detenido en %.0f s.", time.time() - t0)
            self._push_after_stop()

    def _push_after_stop(self) -> None:
        """Con todo parado, lo último a GitHub en segundo plano (como «Subir ahora»): así no hace falta subirlo a mano.
        Solo si al arrancar se comprobó que git funciona (``git_ok``) y la subida automática está activada."""
        try:
            from app import autopush
            from app.coach import ranking_stats

            stats = ranking_stats.of(self.svc)
            if stats is not None:
                stats.flush()  # lo que sumaron las últimas partidas
            self.write_funnel(force=True)  # el embudo con las cifras finales
            thread = getattr(self, "_funnel_thread", None)
            if thread is not None:
                thread.join(60)
            auto = autopush.AutoPush.of(self.svc)
            if auto is None or not auto.public().get("enabled") or auto.state.get("git_ok") is not True:
                return
            otp_details.flush(self.svc)  # por si alguna región no llegó a pedirlos: todos los resúmenes, antes de subir
            if auto.start(wait_summaries=True):
                log.info("Recolector detenido: subiendo a GitHub lo recogido en segundo plano (tras rehacer los resúmenes).")
            else:
                log.info("Recolector detenido: hay otra subida a GitHub en marcha; lo recogido sale en cuanto acabe (en cola).")
        # subir es un extra: detener ya está hecho
        except Exception:  # noqa: BLE001
            log.exception("No se pudo subir a GitHub al detener el recolector")

    def _alive_threads(self) -> str:
        """Hilos del recolector que siguen vivos y la línea del proyecto en la que están."""
        import sys
        import traceback

        frames = sys._current_frames()
        out = []
        for w in self.workers.values():
            for t in [w, *w.scouts]:
                if not t.is_alive():
                    continue
                frame = frames.get(t.ident)
                stack = traceback.extract_stack(frame) if frame is not None else []
                mine = [f for f in stack if "/app/" in f.filename.replace("\\", "/")]
                last = (mine or stack or [None])[-1]
                where = f"{Path(last.filename).name}:{last.lineno} {last.name}" if last else "?"
                out.append(f"{t.name} ({where})")
        return ", ".join(out) or "ninguno"

    def shutdown(self) -> None:
        self.stop_event.set()
        for worker in self.workers.values():
            worker.wake()
        for worker in self.workers.values():
            worker.join(timeout=3)

    def _all_stored_at(self) -> list[float]:
        out: list[float] = []
        for w in self.workers.values():
            out += list(getattr(w, "stored_at", ()))
        return out

    def recent_rate(self, stamps) -> float | None:
        """Partidas por minuto de los últimos 5 minutos (la media desde que arrancó arrastra el arranque: al principio
        se buscan jugadores y Riot aún cuenta las llamadas de la sesión anterior)."""
        if not self.started_at or not self.running:
            return None
        stamps = list(stamps)  # copia de una vez: otro hilo puede estar añadiendo
        now = time.time()
        window = min(RECENT_RATE_S, now - self.started_at)
        if window < 60:
            return None
        return round(sum(1 for t in stamps if t >= now - window) / (window / 60), 1)

    def status(self) -> dict:
        elapsed = (time.time() - self.started_at) if self.started_at and self.running else None
        workers = {
            region: dict(w.state, alive=w.is_alive(), rate_recent=self.recent_rate(getattr(w, "stored_at", ()))) for region, w in self.workers.items()
        }
        return {
            "running": self.running,
            "stopping": self.stop_event.is_set() and self.running,
            "autostart": self.autostart_enabled,
            "paused": self.paused_reason() if self.running else None,
            "config": asdict(self.config) if self.config else asdict(CrawlerConfig()),
            "started_at": self.started_at,
            "stored": self.total_stored,
            "rate_per_min": round(self.total_stored / (elapsed / 60), 1) if elapsed and elapsed > 30 else None,
            "rate_recent": self.recent_rate(self._all_stored_at()),
            "workers": workers,
            "push": self._push_status(),
            "patterns": self.pattern_report() if self.config and self.config.patterns else None,
        }

    def _push_status(self) -> dict | None:
        """La subida a GitHub (para verla también con el recolector parado: al detener se sube lo recogido)."""
        try:
            from app import autopush

            auto = autopush.AutoPush.of(self.svc)
            if auto is None:
                return None
            p = auto.public()
            keep = ("enabled", "every", "pending", "running", "step", "last_push_at", "last_error", "last_push_s")
            return {k: p.get(k) for k in keep}
        except Exception:  # noqa: BLE001
            return None
