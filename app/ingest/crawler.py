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
from collections import deque
import threading
import time
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from app.data.db import now_s
from app.data.repository import known_match_ids, mark_skipped, store_match, upsert_seed_players
from app.ingest import otp_details
from app.ingest.extractor import parse_match
from app.logbus import get_logger
from app.riot.client import Cancelled, RiotAPIError, RiotKeyError
from app.riot.ratelimit import Priority
from app.riot.routing import APEX_TIERS, DIVISIONS, PLATFORMS, QUEUE_SOLO, REGIONS, ROLES, TIER_INDEX, region_of

log = get_logger("crawler")

APEX_REFRESH_S = 12 * 3600
LADDER_RESET_S = 24 * 3600
EXHAUSTED_COOLDOWN_S = 20 * 60
MASTERY_TTL_S = 7 * 86400
MASTERY_BATCH = 25
OTP_RATIO = otp_details.OTP_RATIO  # (solo para clasificar jugadores aún sin partidas revisadas)
FOCUSES = ("otp", "mastery", "games")
GAMES_MIN_MASTERY = 10_000  # mínimo de puntos con el campeón (entre sus 3 primeros) para cualquier búsqueda
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
FUNNEL_EVERY_S = 300  # cada cuánto se escribe data/otps/_busqueda.json
LEADERBOARD_TTL_S = 24 * 3600  # la tabla de los mejores de cada campeón (OP.GG) se vuelve a pedir una vez al día
LEADERBOARD_TIERS = {"DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"}  # elos que salen en esa tabla
LEADERBOARD_MAX = 20  # jugadores por tabla: cada uno nuevo cuesta una llamada de account-v1 (cupo regional)
OPGG_REGION = {p: label for p, (_region, label, _name) in PLATFORMS.items()} | {"sg2": "SG"}
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
    queues = "ranked" if cfg.include_flex else str(QUEUE_SOLO)
    return f"{queues}|{','.join(str(c) for c in sorted(cfg.champions))}|{int(cfg.only_target_games)}{_roles_key(cfg)}"


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
        stored = row["config_key"] if "config_key" in row.keys() else None  # noqa: SIM118  (sqlite3.Row no admite «in»)
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

    def validated(self) -> CrawlerConfig:
        platforms = [p for p in dict.fromkeys(p.lower() for p in self.platforms) if p in PLATFORMS]
        tiers = sorted({t.upper() for t in self.tiers if t.upper() in TIER_INDEX}, key=TIER_INDEX.get)
        if not platforms:
            raise ValueError("Selecciona al menos una plataforma válida")
        if not tiers:
            raise ValueError("Selecciona al menos un tier")
        focus = self.focus if self.focus in FOCUSES else "otp"
        try:
            share = float(self.min_share)
        except (TypeError, ValueError):
            share = DEFAULT_SHARE
        share = min(SHARE_OPTIONS, key=lambda o: abs(o - share))
        if focus == "otp" and share <= 0:
            focus = "mastery"  # especialistas sin mínimo de partidas = mucha maestría
        if focus != "otp":
            share = 0.0
        roles = [r for r in ROLES if r in {str(x).upper() for x in (self.roles or [])}]
        pools: dict[str, list[int]] = {}
        for lane, champs in (self.role_pools or {}).items():
            lane = str(lane).upper()
            if lane in roles:
                pools[lane] = list(dict.fromkeys(int(c) for c in champs or [] if str(c).isdigit() and int(c) > 0))
        return CrawlerConfig(
            platforms=platforms,
            tiers=tiers,
            include_flex=bool(self.include_flex),
            matches_per_player=max(5, min(int(self.matches_per_player), 100)),
            recrawl_hours=max(1.0, min(float(self.recrawl_hours), 168.0)),
            champions=list(dict.fromkeys(int(c) for c in (self.champions or []) if str(c).isdigit() and int(c) > 0)),
            focus=focus,
            min_mastery=max(GAMES_MIN_MASTERY, min(int(self.min_mastery or (GAMES_MIN_MASTERY if focus == "games" else 300_000)), 10_000_000)),
            min_share=share,
            only_target_games=bool(self.only_target_games) or self.focus == "games",
            otp_depth=str(self.otp_depth) if str(self.otp_depth) in OTP_DEPTHS else "season",
            champion_roles=self._clean_roles(),
            save_details=bool(self.save_details),
            games_per_otp=0 if not int(self.games_per_otp or 0) else max(5, min(int(self.games_per_otp), 1000)),
            roles=roles,
            role_champions=list(dict.fromkeys(int(c) for c in (self.role_champions or []) if str(c).isdigit() and int(c) > 0)) if roles else [],
            role_pools=pools,
            season_version=str(self.season_version) if self.season_version else None,
        )

    @classmethod
    def from_saved(cls, data: dict) -> CrawlerConfig:
        """Configuración guardada (``meta``), aunque traiga campos de versiones anteriores de la app."""
        known = {f.name for f in fields(cls)}
        data = dict(data or {})
        data["save_details"] = True  # ya no es opcional: una configuración antigua con él apagado se reanuda con él
        return cls(**{k: v for k, v in data.items() if k in known}).validated()

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
        cutoff = self.active_since(now)

        def active(entry) -> bool:
            last = int(entry[2]) if len(entry) > 2 and entry[2] else None
            return last is None or last >= cutoff

        return any(int(e[0]) in targets and int(e[1]) >= self.min_mastery and active(e) for e in top[:3])

    def share_ok(self, seen: int | None, hits: int | None) -> bool:
        """Con sus partidas revisadas: ¿juega esos campeones lo bastante? (Sin muestra aún, se le da el beneficio de
        la duda.) Solo filtra buscando especialistas con un mínimo de partidas."""
        seen, hits = int(seen or 0), int(hits or 0)
        if self.focus != "otp" or self.min_share <= 0 or seen < SHARE_MIN_SEEN:
            return True
        return hits >= self.min_share * seen


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
    from app.analytics.evidence import ext_scope  # aquí: evita un import circular al arrancar
    from app.analytics.filters import Filters

    pools: dict[str, list[int]] = {}
    scope = None
    try:
        scope = ext_scope(svc, Filters())
    except Exception:  # noqa: BLE001  (sin parches conocidos o webs desactivadas)
        scope = None
    for role in roles:
        data: dict = {}
        for name in ("opgg", "lolalytics"):
            src = getattr(svc, name, None)
            if scope is None or src is None or not getattr(src, "enabled", False):
                continue
            try:
                data = src.tier_list(role, scope.tier, scope.region, scope.patches[0]) or {}
            except Exception as exc:  # noqa: BLE001  (web caída: se prueba con la siguiente)
                log.warning("Tier list de %s (%s): %s", role, name, exc)
                data = {}
            if data:
                break
        rows = [(int(c), float(r.get("pick") or 0)) for c, r in data.items() if (r.get("role_rate") or 0) >= ROLE_MIN_RATE]
        if not rows:  # sin webs: lo que dicen tus partidas guardadas
            rows = [
                (int(r["champ"]), float(r["n"]))
                for r in svc.db.query(
                    "SELECT champ, SUM(role = ?) AS n, COUNT(*) AS total FROM participants GROUP BY champ "
                    "HAVING n >= 20 AND n >= ? * total",
                    (role, ROLE_MIN_RATE),
                )
            ]
        if rows:
            pools[role] = [c for c, _ in sorted(rows, key=lambda kv: -kv[1])]
    return pools


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
        "otp": ["checked", "champ", "points", "active", "share"],
        "mastery": ["checked", "champ", "points", "active"],
        "games": ["checked", "champ", "points", "active"],
    }[cfg.focus]
    per: dict[str, dict] = {}

    def bucket(platform: str) -> dict:
        return per.setdefault(platform, {k: 0 for k in ("checked", "champ", "points", "active", "share", "seeds", "last_hour")})

    for r in db.query(
        f"SELECT platform, COUNT(*) AS n FROM players WHERE is_seed = 1 AND platform IN ({pm}) AND tier IN ({tm}) GROUP BY platform",
        (*platforms, *tiers),
    ):
        bucket(r["platform"])["seeds"] = int(r["n"])
    for r in db.query(
        f"SELECT p.platform, m.top, m.checked_at, o.seen, o.hits FROM mastery_checks m JOIN players p ON p.puuid = m.puuid "
        f"LEFT JOIN otp_progress o ON o.puuid = p.puuid "
        f"WHERE p.is_seed = 1 AND p.platform IN ({pm}) AND p.tier IN ({tm})",
        (*platforms, *tiers),
    ):
        b = bucket(r["platform"])
        b["checked"] += 1
        b["last_hour"] += int((r["checked_at"] or 0) >= now - 3600)
        try:
            top = [e for e in json.loads(r["top"]) if len(e) >= 2]
        except (TypeError, ValueError):
            continue
        entry = next((e for e in top[:3] if int(e[0]) in targets), None)
        if entry is None:
            continue
        b["champ"] += 1
        if int(entry[1]) < cfg.min_mastery:
            continue
        b["points"] += 1
        last = int(entry[2]) if len(entry) > 2 and entry[2] else None
        if last is not None and last < cutoff:
            continue
        b["active"] += 1
        # Especialista comprobado: con sus partidas revisadas, juega el campeón lo bastante (sin muestra aún, no cuenta).
        if cfg.focus == "otp" and int(r["seen"] or 0) >= SHARE_MIN_SEEN and cfg.share_ok(r["seen"], r["hits"]):
            b["share"] += 1
    total = {k: sum(b[k] for b in per.values()) for k in (*stages, "seeds", "last_hour")}
    shown = {p: {k: b[k] for k in (*stages, "seeds", "last_hour")} for p, b in per.items()}  # solo las fases de este modo
    return {"stages": stages, "total": total, "platforms": dict(sorted(shown.items(), key=lambda kv: -kv[1]["checked"])), "at": now}


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


PLAN_UNVISITED_PER_TIER = 100  # sin visitar que entran en la cola por plataforma y elo (los de más LP)


def otp_rows_for_plan(db, cfg: CrawlerConfig, now: int | None = None) -> list:
    """Como ``otp_rows`` pero solo lo que necesita la cola de visitas: todos los ya visitados (o de las tablas de OP.GG)
    y, de los que nunca se han visitado, los ``PLAN_UNVISITED_PER_TIER`` de más LP de cada plataforma y elo. Con ~350.000
    jugadores encontrados, ordenarlos todos costaba 15-19 s por región en Windows cada vez que se rehacía la cola; al
    visitar unos, entran los siguientes."""
    targets = cfg.targets
    platforms, tiers = list(cfg.platforms), list(cfg.tiers)
    if not targets or not platforms or not tiers:
        return []
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
    fresh = db.query(
        f"SELECT * FROM (SELECT {cols}, ROW_NUMBER() OVER (PARTITION BY p.platform, p.tier ORDER BY p.lp DESC) AS rn "
        f"{base} AND o.puuid IS NULL AND p.puuid NOT IN (SELECT puuid FROM otp_sources)) WHERE rn <= ?",
        (*platforms, *tiers, PLAN_UNVISITED_PER_TIER),
    )
    now = now or now_s()
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
    entries = [(int(e[0]), int(e[1])) for e in top[:3] if len(e) >= 2]
    found = [c for c, pts in entries if c in targets and pts >= cfg.min_mastery]
    return list(dict.fromkeys([*found, *sorted((sourced or set()) & targets)]))


def likely_specialist(cfg: CrawlerConfig, top: list, row) -> int:
    """0 si tiene pinta de especialista (en la tabla de los mejores de OP.GG, o su principal con 1,5 veces los puntos
    del segundo); 1 si no (se comprueba igual, pero después)."""
    if sourced_champs(row) & set(cfg.targets):
        return 0
    entries = [(int(e[0]), int(e[1])) for e in top if len(e) >= 2]
    if entries and entries[0][0] in set(cfg.targets):
        second = entries[1][1] if len(entries) > 1 else 0
        if entries[0][1] >= OTP_RATIO * second:
            return 0
    return 1


def cap_count(cfg: CrawlerConfig, hits: int, seen: int) -> int:
    """Lo que cuenta para el tope «Primero, de cada jugador, sus N partidas»: buscando partidas de un campeón, todas
    las suyas que se bajan (casi todas se guardan: con una línea entera, en casi cualquier partida hay alguien con un
    campeón de ella); buscando OTPs, solo las suyas con ese campeón (las demás ni se guardan)."""
    return int(seen or 0) if cfg.focus == "games" else int(hits or 0)


def sourced_champs(row) -> set[int]:
    """Campeones por los que ese jugador sale en la tabla de los mejores (columna ``sourced`` de ``otp_rows``)."""
    raw = row["sourced"] if "sourced" in row.keys() else None  # noqa: SIM118  (sqlite3.Row no admite «in»)
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


OTP_PLAN_TTL_S = 300  # la cola de OTPs se recalcula cada 5 min (o antes si se vacía, tras OTP_PLAN_EMPTY_S); ~10 s en Windows
OTP_PLAN_EMPTY_S = 15
OTP_ROWS_FRESH_S = 120  # las regiones que recalculan su cola en esos 2 min comparten la lectura de jugadores
SCOUT_BACKLOG = 200  # jugadores sin visitar de un elo (por plataforma) a partir de los que no se buscan más
SCOUT_BACKLOG_WAIT_S = 60
SAVE_QUEUE_MAX = 4  # partidas descargadas esperando su análisis (por región)
# Analizar en otro hilo mientras se descarga: desactivado (con él, el recolector se quedó parado en Windows el
# 24-11-2026 a las ~22 partidas; hasta saber por qué, se analiza en el mismo hilo, como cuando iba a 36/min).
SAVE_IN_BACKGROUND = False
PREFETCH = True  # descargar la siguiente partida mientras se analiza la actual (solo llamadas a Riot, ver _Prefetch)
RECENT_RATE_S = 300  # «velocidad actual»: partidas guardadas en los últimos 5 minutos


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
            self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"crawler-dl-{self.w.region}")
        self.futures[match_id] = self.pool.submit(self._fetch, match_id, puuid)

    def _fetch(self, match_id: str, puuid: str):
        w = self.w
        kw = {"priority": Priority.LOW, "stop": w.stop_event}
        payload = w.svc.riot.match(match_id, **kw)
        timeline = None
        cfg = w.config
        if payload is not None and cfg.targets and cfg.save_details:
            record = parse_match(payload)
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
        except Exception:  # noqa: BLE001  (error de Riot o parada: se repite en el hilo de la región, que sabe tratarlo)
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
        self._wake = threading.Event()
        self._otp_fails: dict[str, tuple[int, float]] = {}  # puuid -> (fallos seguidos, descansa hasta)
        self._plan: list = []  # cola de OTPs (``_next_otp``)
        self._plan_at = 0.0
        self._plan_rows_at = 0.0
        self._visited: dict[str, float] = {}  # puuid -> cuándo empezó su última visita
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
        combos = [(p, t) for t in reversed(self.config.tiers) for p in self.platforms]
        index = 0
        log.info("Recolector %s iniciado (%s).", self.region, ", ".join(PLATFORMS[p][1] for p in self.platforms))
        while not self.stop_event.is_set():
            try:
                if not self.svc.keys.get() or self.svc.keys.status == "invalid":
                    self._wait_for_key()
                    continue
                if self.crawler.paused_reason():
                    # Estás en partida: no te quito ancho de banda; sigue al terminar.
                    self.state["status"] = "paused_game"
                    self.stop_event.wait(5)
                    continue
                now = time.time()
                available = [c for c in combos if self._cooldown.get(c, 0) <= now]
                if not available:
                    self.state["status"] = "idle"
                    self.stop_event.wait(30)
                    continue
                platform, tier = available[index % len(available)]
                index += 1
                self.state.update(status="running", platform=platform, tier=tier)
                player = self._next_player(platform, tier)
                if player is None:
                    added = self._refill_seeds(platform, tier)
                    if added == 0:
                        self._cooldown[(platform, tier)] = time.time() + EXHAUSTED_COOLDOWN_S
                    continue
                self._crawl_player(player, platform)
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
                    if not self.svc.keys.get() or self.svc.keys.status == "invalid":
                        self._wait_for_key()
                        continue
                    if self.crawler.paused_reason():
                        self.state["status"] = "paused_game"
                        self.stop_event.wait(5)
                        continue
                    # Se lee el estado de la búsqueda ANTES de buscar trabajo: si ya había terminado, todo lo que
                    # encontró está guardado y «no hay nada pendiente» es cierto.
                    search_done = all(sc.status in ("idle", "stopped") for sc in self.scouts)
                    self._wake.clear()
                    target = self._next_otp()
                    if target is None:
                        self.state["status"] = "idle" if search_done else "searching"
                        with self.timed("esperando"):
                            self._wake.wait(OTP_IDLE_WAIT_S)
                        continue
                    self.state.update(status="running", platform=target["platform"], tier=target["tier"])
                    with self.timed("visitas"):
                        self._visit_otp(target)
                    self.timing["n_visitas"] = self.timing.get("n_visitas", 0) + 1
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

    def _wait_for_key(self) -> None:
        self.state["status"] = "waiting_key"
        self.svc.keys.wait_for_change(self.svc.keys.version, self.stop_event)

    # ---------------------------------------------------------------- semillas
    def _next_player(self, platform: str, tier: str):
        cutoff = now_s() - int(self.config.recrawl_hours * 3600)
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
        svc, db = self.svc, self.svc.db
        opgg = getattr(svc, "opgg", None)
        region = OPGG_REGION.get(platform)
        if opgg is None or not getattr(opgg, "enabled", False) or not region:
            return 0
        if not set(self.config.tiers) & LEADERBOARD_TIERS:
            return 0  # la tabla solo trae Diamante alto, Maestro o más: con elos más bajos no aporta nadie
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        added = 0
        for champ in self.config.targets if champs is None else champs:
            champ_added = 0
            if self.stop_event.is_set():
                break
            fresh = db.scalar(
                "SELECT MAX(fetched_at) FROM otp_sources WHERE champ = ? AND platform = ? AND source = 'opgg'", (champ, platform)
            )
            if fresh and now_s() - int(fresh) < LEADERBOARD_TTL_S:
                continue
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
            except Exception as exc:  # noqa: BLE001  (OP.GG caído o sin ese campeón: se sigue con el ranking de Riot)
                log.warning("Tabla de los mejores de %s en %s (OP.GG): %s", champ, platform, exc)
                continue
            entries = (data.get("leaderboard") or []) if isinstance(data, dict) else []
            for entry in entries[:LEADERBOARD_MAX]:
                if self.stop_event.is_set():
                    break
                summ = entry.get("summoner") or {}
                name, tag = summ.get("game_name"), summ.get("tagline")
                if not name or not tag:
                    continue
                solo = next((ls.get("tier_info") or {} for ls in summ.get("league_stats") or [] if ls.get("game_type") == "SOLORANKED"), {})
                if str(solo.get("tier") or "").upper() not in self.config.tiers:
                    continue  # no es de los elos que buscas: ni se gasta la llamada
                puuid = db.scalar(
                    "SELECT puuid FROM players WHERE platform = ? AND lower(game_name) = lower(?) AND lower(tag_line) = lower(?)",
                    (platform, name, tag),
                )
                if not puuid:
                    try:
                        account = svc.riot.account_by_riot_id(name, tag, platform, **kw) or {}
                    except RiotKeyError:
                        raise
                    except RiotAPIError:
                        continue  # cambió de nombre: ya aparecerá por el ranking
                    puuid = account.get("puuid")
                    if not puuid:
                        continue
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
                added += 1
                champ_added += 1
                self.state["targets_found"] += 1
                self.wake()
            dd = getattr(svc, "dd", None)
            champ_name = (dd.name(champ) if dd is not None and hasattr(dd, "name") else None) or f"campeón {champ}"
            log.info(
                "Tabla de los mejores de %s en %s (OP.GG): %d jugadores de tus elos añadidos.", champ_name, PLATFORMS[platform][1], champ_added
            )
        return added

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
        now_t = time.time()
        if now_t - self._plan_at >= OTP_PLAN_TTL_S or (not self._plan and now_t - self._plan_at >= OTP_PLAN_EMPTY_S):
            with self.timed("cola"):
                self._plan = self._plan_otps()  # fija ``_plan_rows_at``: cuándo se leyeron los jugadores
            self._plan.reverse()  # se sacan del final (pop) en orden de prioridad
            self._plan_at = now_t
        while self._plan:
            _key, row = self._plan.pop()
            if self._visited.get(row["puuid"], 0.0) >= self._plan_rows_at:
                continue  # ya visitado con esta cola: su progreso ha cambiado, vuelve en la siguiente
            if self._otp_fails.get(row["puuid"], (0, 0.0))[1] > time.time():
                continue  # falló varias veces seguidas: descansa un rato
            return row
        return None

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
        lacking = self._lacking_details([row["id"] for row, _ in rows]) if cfg.save_details else set()
        cap = self._games_cap()
        # Cobertura: OTPs ya visitados por campeón y por (campeón, elo), y partidas por campeón.
        info = []
        top_of = {row["puuid"]: top for row, top in rows}
        champ_otps: dict[int, int] = {}
        champ_tier: dict[tuple[int, str], int] = {}
        champ_games: dict[int, int] = {}
        found_by_tier: dict[tuple[str, str], int] = {}
        waiting_by_tier: dict[tuple[str, str], int] = {}  # encontrados aún sin visitar
        # Partidas ya recogidas por (línea, elo): con líneas enteras o «cualquiera que lo juegue», lo primero es tener
        # muchas partidas de cada elo en cada línea (para comparar elos), no un jugador de cada uno de 150 campeones.
        lane_tier_games: dict[tuple[str, str], int] = {}
        by_elo = bool(cfg.roles) or cfg.focus == "games"

        def lane_of(champ: int) -> str:
            lanes = cfg.roles_for(champ) or []
            return lanes[0] if len(lanes) == 1 else "ANY"

        for row, top in rows:
            champ = (otp_champs(cfg, top, sourced_champs(row)) or [0])[0]
            state = otp_history_state(row, list_key, depth_start, config_key)
            tier = row["tier"] or ""
            info.append((row, champ, state))
            found_by_tier[(row["platform"], tier)] = found_by_tier.get((row["platform"], tier), 0) + 1
            if state == "queued" and row["anchor"] is None:
                waiting_by_tier[(row["platform"], tier)] = waiting_by_tier.get((row["platform"], tier), 0) + 1
            # Lo ya recogido cuenta siempre, también si cambió la búsqueda (otros campeones o líneas: el jugador vuelve a
            # «queued» para recorrer su historial otra vez). Antes solo contaban los no «queued»: al cambiar la búsqueda
            # todas las líneas y elos quedaban a 0 partidas, el desempate era «el elo más alto primero» y miles de
            # jugadores de Diamante+ ya visitados pasaban por delante de los de Hierro a Esmeralda recién encontrados.
            if state != "queued" or row["anchor"] is not None:
                champ_otps[champ] = champ_otps.get(champ, 0) + 1
                champ_tier[(champ, tier)] = champ_tier.get((champ, tier), 0) + 1
                champ_games[champ] = champ_games.get(champ, 0) + int(row["hits"] or 0)
                key_lt = (lane_of(champ), tier)
                lane_tier_games[key_lt] = lane_tier_games.get(key_lt, 0) + int(row["hits"] or 0)
        self.found_by_tier = found_by_tier  # la búsqueda en el ranking reparte por elos con esto
        self.waiting_by_tier = waiting_by_tier  # y no busca más en un elo con jugadores de sobra esperando visita
        plan = []
        complete = 0
        for row, champ, state in info:
            if state in ("done", "share"):
                complete += 1
            tier = row["tier"] or ""
            rank = (-TIER_INDEX.get(tier, 0), -(row["lp"] or 0))
            if state == "queued":
                in_tier, in_champ = champ_tier.get((champ, tier), 0), champ_otps.get(champ, 0)
                level = 0 if not in_champ else 1 if in_tier < ELO_MIN_OTPS else 2
                likely = 0 if cfg.focus != "otp" else likely_specialist(cfg, top_of[row["puuid"]], row)
                if by_elo:
                    # Primero la línea y el elo con menos partidas; la variedad de campeones, después. Los nunca visitados
                    # antes que los que vuelven a la cola por un cambio de búsqueda (de esos ya hay partidas).
                    # A igualdad (muchas líneas y elos a 0 partidas), el elo más bajo: de los altos hay mucho más guardado
                    # y las tablas de OP.GG ya traen jugadores de Diamante+ nada más arrancar.
                    again = int(row["anchor"] is not None)
                    low_first = (TIER_INDEX.get(tier, 0), -(row["lp"] or 0))
                    key = (0, lane_tier_games.get((lane_of(champ), tier), 0), again, low_first, level, likely, in_tier)
                else:
                    # Buscando especialistas, primero los que más pinta tienen (comprobar a uno cuesta ~20 descargas).
                    key = (0, level, likely, in_tier, in_champ, *rank)
            elif row["fwd_more"] or (row["last_crawled_at"] or 0) < now - recrawl:
                key = (1, row["last_crawled_at"] or 0, 0, 0, *rank)
            elif state == "loading" and not (cap and cap_count(cfg, row["hits"], row["seen"]) >= cap):
                first = lane_tier_games.get((lane_of(champ), tier), 0) if by_elo else champ_games.get(champ, 0)
                key = (2, first, int(row["hits"] or 0), row["back_offset"] or 0, *rank)
            elif row["id"] in lacking:
                key = (3, champ_games.get(champ, 0), 0, 0, *rank)  # sin análisis minuto a minuto (de antes o que fallaron)
            else:
                continue
            plan.append((key, row))
        self.state.update(otps_known=len(info), otps_complete=complete)
        plan.sort(key=lambda kr: kr[0])
        return plan

    def tier_order(self, platform: str, tiers: list[str]) -> list[str]:
        """Elos por revisar en el ranking: primero el que tiene menos OTPs encontrados (a igualdad, el más alto), para
        tener OTPs de todos los elos antes que muchos de uno solo."""
        found = getattr(self, "found_by_tier", {}) or {}
        return sorted(tiers, key=lambda t: (found.get((platform, t), 0), -TIER_INDEX.get(t, 0)))

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

    def _detail(self, match_id: str, payload: dict | None, player) -> bool:
        """Baja el timeline (y la partida si no se tiene) y guarda el micro/macro de ese OTP. ``True`` si se guardó."""
        svc = self.svc
        if (match_id, player["puuid"]) in self._saving:
            return False  # ya descargada, esperando su análisis
        if svc.db.scalar("SELECT 1 FROM otp_games WHERE match_id = ? AND puuid = ?", (match_id, player["puuid"])):
            return False
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        try:
            with self.timed("riot"):
                if payload is None:
                    payload = svc.riot.match(match_id, **kw)
                    if payload is None:
                        return False
                timeline = self._prefetch.take_timeline(match_id) or svc.riot.timeline(match_id, **kw)
        except RiotKeyError:
            raise
        except RiotAPIError as exc:
            if exc.status not in (400, 403, 404, 422):
                raise  # red o Riot caído: se reintenta en otra visita
            timeline = None  # Riot no tiene el timeline: se guarda lo que da la partida
            if payload is None:
                return False
        info = otp_details.player_info(svc.db, player["puuid"])  # elo al bajarla (el de la liga ahora mismo)
        elo = {"tier": info.get("tier") or player.get("tier"), "division": info.get("division"), "lp": info.get("lp")}
        if not SAVE_IN_BACKGROUND:
            try:
                # OTP o no lo decide ``classify`` (lo que juega, con sus partidas revisadas): ser candidato no basta.
                with self.timed("analizar_guardar"):
                    saved = otp_details.save(svc.db, svc, payload, timeline, player["puuid"], elo)
            except Exception:
                log.exception("No se pudo analizar la partida %s del jugador", match_id)
                return False
            if saved:
                self.state["details"] += 1
            return saved
        if self._saver is None or not self._saver.is_alive():
            self._saver = threading.Thread(target=self._save_loop, name=f"crawler-save-{self.region}", daemon=True)
            self._saver.start()
        item = (match_id, payload, timeline, player["puuid"], elo)
        self._saving.add((match_id, player["puuid"]))
        with self.timed("esperando_analisis"):  # si el análisis va por detrás, se espera (no se acumulan en memoria)
            while True:
                try:
                    self._save_q.put(item, timeout=0.5)
                    break
                except queue.Full:
                    if self.stop_event.is_set():
                        self._saving.discard((match_id, player["puuid"]))
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
        svc, cfg, db = self.svc, self.config, self.svc.db
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        platform, puuid = row["platform"], row["puuid"]
        self._visited[puuid] = time.time()
        player = {"id": row["id"], "puuid": puuid, "tier": row["tier"]}
        queue = None if cfg.include_flex else QUEUE_SOLO
        list_key, config_key = self._list_key(), otp_config_key(cfg)
        depth_start, season_major = self._depth()
        now = now_s()
        fresh = row["anchor"] is None or row["list_key"] != list_key
        prog = {
            # El ancla va unas horas atrás: lo posterior (la partida en curso o aún sin publicar) entra por «nuevas».
            "anchor": now - OTP_ANCHOR_LAG_S if fresh else int(row["anchor"]),
            "offset": 0 if fresh else int(row["back_offset"] or 0),
            "done_at": None if fresh else row["back_done_at"],
            "done_start": None if fresh else row["back_done_start"],
            "reason": None if fresh else row["back_done_reason"],
            "fwd_more": 0 if fresh else int(row["fwd_more"] or 0),
            "fwd_newest": 0 if fresh else int(row["fwd_newest"] or 0),
            "low_pages": 0 if fresh else int(row["low_pages"] or 0),
            "config_key": None if fresh else row["config_key"],
            "seen": int(row["seen"] or 0),
            "hits": int(row["hits"] or 0),
        }
        newest = int(row["newest_match_at"] or 0)
        if prog["done_at"] and otp_history_state(row, list_key, depth_start, config_key) == "loading":
            # Se pide más historial que la última vez, o cambiaron los campeones o el filtro: se sigue bajando.
            prog.update(done_at=None, done_start=None, reason=None, low_pages=0)
        budget = OTP_VISIT_MATCHES
        stored_before = self.state["stored"]
        ingested = 0

        def save(final: bool = False) -> None:
            # Al empezar, cada 25 partidas y al terminar (aunque falle o se pare a mitad): la siguiente visita sigue
            # justo donde se quedó y la web ve el avance.
            with db.write() as conn:
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
                        puuid, list_key, prog["anchor"], prog["offset"], prog["done_at"], prog["done_start"],
                        prog["reason"], prog["fwd_more"], prog["fwd_newest"] or None, prog["low_pages"],
                        prog["config_key"], prog["seen"], prog["hits"], now_s(),
                    ),
                )
                conn.execute(
                    "UPDATE players SET newest_match_at = MAX(COALESCE(newest_match_at, 0), ?)"
                    + (", last_crawled_at = ?" if final else "")
                    + " WHERE id = ?",
                    (newest, now_s(), row["id"]) if final else (newest, row["id"]),
                )

        def ingest(match_id: str) -> Ingested:
            nonlocal budget, ingested, newest
            with self.timed("partidas"):  # todo lo de cada partida (lo no desglosado: base de datos, progreso…)
                got = self._ingest(match_id, player)
            budget -= 1 + got.detailed
            ingested += 1
            newest = max(newest, got.game_start)
            prog["seen"] += got.valid
            prog["hits"] += got.valid and got.mine_target
            if ingested % 25 == 0:
                save()
            return got

        if fresh:
            self.state["players"] += 1
            save()
            # Su nombre llega con su primera partida (``store_match`` guarda el Riot ID de los 10): pedirlo aparte gastaba
            # una llamada de account-v1 del mismo cupo regional que las partidas por cada jugador nuevo.
        try:
            # 1) Partidas nuevas (posteriores al ancla), de la más antigua a la más reciente, con su propio cursor:
            #    si se corta a mitad, la siguiente visita sigue desde la última recorrida.
            after = max(prog["anchor"], prog["fwd_newest"] // 1000)
            prog["fwd_more"] = 1  # hasta terminar esta pasada
            ids: list[str] = []
            for page in range(OTP_FORWARD_PAGES):
                chunk = self._list_ids(
                    platform, puuid, queue=queue, ranked_only=cfg.include_flex, start_time=after,
                    start=page * OTP_PAGE, count=OTP_PAGE, **kw,
                )
                ids.extend(chunk)
                if len(chunk) < OTP_PAGE:
                    break
            else:
                # Más de 1.000 partidas nuevas (mucho tiempo sin abrir la app): se reancla el historial para no
                # dejar un hueco; lo ya guardado no se vuelve a descargar.
                prog.update(anchor=now - OTP_ANCHOR_LAG_S, offset=0, done_at=None, done_start=None, reason=None, low_pages=0, fwd_newest=0)
                ids = []
            ids = list(dict.fromkeys(ids))
            self.state["ids_seen"] += len(ids)
            known = self._known_for(puuid, ids)
            starts = self._stored_starts([m for m in ids if m in known])
            finished = True
            todo = [m for m in reversed(ids) if m not in known]
            after_of = dict(zip(todo, todo[1:]))
            for match_id in reversed(ids):
                if self.stop_event.is_set():
                    finished = False
                    break
                if match_id in known:
                    prog["fwd_newest"] = max(prog["fwd_newest"], starts.get(match_id, 0))
                    continue
                if budget <= 0 or not cfg.share_ok(prog["seen"], prog["hits"]):
                    finished = False
                    break
                self._prefetch.hint(match_id, puuid)
                if budget > 1:
                    self._prefetch.hint(after_of.get(match_id), puuid)  # la siguiente, mientras se analiza esta
                got = ingest(match_id)
                prog["fwd_newest"] = max(prog["fwd_newest"], got.game_start)
            if finished:
                prog["fwd_more"] = 0
            # 2) Historial hacia atrás con ``endTime`` fijo (el ancla): la lista no se mueve aunque juegue más
            #    partidas, así que ``start`` (cuántas se llevan recorridas) siempre apunta al mismo sitio.
            pages = 0
            cap = self._games_cap()
            while (
                prog["done_at"] is None and budget > 0 and pages < OTP_BACK_PAGES and not self.stop_event.is_set()
                and not (cap and cap_count(cfg, prog["hits"], prog["seen"]) >= cap)  # variedad: el resto, al terminar la búsqueda
                and cfg.share_ok(prog["seen"], prog["hits"])  # no es especialista: no se gastan más llamadas en él
            ):
                base = prog["offset"]
                ids = self._list_ids(
                    platform, puuid, queue=queue, ranked_only=cfg.include_flex, start_time=depth_start or None,
                    end_time=prog["anchor"], start=base, count=OTP_PAGE, **kw,
                )
                pages += 1
                self.state["otp_pages"] += 1
                self.state["ids_seen"] += len(ids)
                known = self._known_for(puuid, ids)
                consumed = page_seen = page_hits = 0
                old_season = False
                todo = [m for m in ids if m not in known]
                after_of = dict(zip(todo, todo[1:]))
                for match_id in ids:
                    if self.stop_event.is_set():
                        break
                    if match_id not in known:
                        if budget <= 0 or not cfg.share_ok(prog["seen"], prog["hits"]):
                            break
                        self._prefetch.hint(match_id, puuid)
                        if budget > 1:
                            self._prefetch.hint(after_of.get(match_id), puuid)
                        got = ingest(match_id)
                        page_seen += got.valid
                        page_hits += got.valid and got.mine_target
                        if season_major and got.patch_major and got.patch_major < season_major:
                            old_season = True  # primera partida de la temporada anterior: hasta aquí
                    consumed += 1
                    prog["offset"] = base + consumed
                    if old_season or (cap and cap_count(cfg, prog["hits"], prog["seen"]) >= cap):
                        break
                if old_season or (consumed == len(ids) and len(ids) < OTP_PAGE):
                    prog.update(done_at=now_s(), done_start=depth_start, reason="end")
                elif cfg.only_target_games and page_seen >= OTP_LOW_SHARE_MIN:
                    low = page_hits <= OTP_LOW_SHARE * page_seen
                    prog["low_pages"] = prog["low_pages"] + 1 if low else 0
                    # Sus partidas más recientes nunca paran el historial (puede estar de descanso del campeón);
                    # más atrás, dos páginas seguidas casi sin esos campeones sí: bajar más sería gastar llamadas en
                    # partidas que se descartan.
                    if prog["low_pages"] >= OTP_LOW_PAGES and base >= OTP_LOW_SKIP:
                        prog.update(done_at=now_s(), done_start=depth_start, reason="share", config_key=config_key)
                if consumed < len(ids):
                    break  # se acabó el presupuesto de la visita a mitad de página
            if not cfg.share_ok(prog["seen"], prog["hits"]):
                prog.update(done_at=now_s(), done_start=depth_start, reason="share", config_key=config_key)
                log.info(
                    "%s no es especialista: %d de %d partidas revisadas con esos campeones (pides %d %%).",
                    row["game_name"] or puuid[:8], prog["hits"], prog["seen"], round(100 * cfg.min_share),
                )
            # 3) Micro/macro de sus partidas ya guardadas que aún no lo tienen (de antes de activarlo o que fallaron).
            if cfg.save_details and budget > 0 and not self.stop_event.is_set():
                cond, params = target_sql(cfg, "p")
                for match_id in otp_details.missing(db, puuid, row["id"], cond, params, min(budget, OTP_DETAIL_BACKFILL)):
                    if self.stop_event.is_set():
                        break
                    self._detail(match_id, None, player)
                    budget -= 1
            self._otp_fails.pop(puuid, None)
        except RiotAPIError as exc:
            if not isinstance(exc, RiotKeyError):
                fails = self._otp_fails.get(puuid, (0, 0.0))[0] + 1
                self._otp_fails[puuid] = (fails, time.time() + OTP_FAIL_COOLDOWN_S if fails >= 3 else 0.0)
            raise
        finally:
            self._prefetch.drop()
            save(final=True)
        stored_now = self.state["stored"] - stored_before
        if stored_now:
            svc.agg.bump()
        log.info(
            "%s · %s · jugador: %d partidas nuevas (historial: %d revisadas%s).",
            PLATFORMS[platform][1],
            row["tier"],
            stored_now,
            prog["offset"],
            ", completo" if prog["done_at"] else "",
        )

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

    def _ingest(self, match_id: str, player) -> Ingested:
        """Descarga una partida y la guarda (o la recuerda como descartada para no volver a pedirla)."""
        svc, cfg = self.svc, self.config
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
            # Error propio de esa partida: se recuerda para no atascar el historial en ella.
            mark_skipped(svc.db, match_id, f"error {exc.status}")
            self.state["errors"] += 1
            return Ingested("unknown")
        self.state["fetched"] += 1
        self.state["last_activity"] = time.time()
        if payload is None:
            mark_skipped(svc.db, match_id, "404")
            return Ingested("missing")
        record = parse_match(payload)
        if record is None:
            mark_skipped(svc.db, match_id, "formato desconocido")
            return Ingested("unknown")
        mine = next((pr for pr in record.participants if pr.puuid == player["puuid"]), None)
        valid = mine is not None and record.is_rift and not record.is_remake
        got = Ingested("", record.game_start, _patch_major(record.patch), valid)
        if cfg.targets:
            got.mine_target = mine is not None and cfg.is_target(mine.champ, mine.role)
            keep = got.mine_target or self._other_target(record, player["puuid"])
            if keep:
                self.state["target_games"] += 1
            elif cfg.only_target_games:
                # Solo interesan sus partidas con los campeones elegidos (en su rol): se recuerda para su recorrido (no
                # para todo el programa: otro OTP o su perfil sí pueden necesitarla).
                with svc.db.write() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO otp_skips (puuid, match_id, champ, role) VALUES (?, ?, ?, ?)",
                        (player["puuid"], match_id, mine.champ if mine is not None else 0, (mine.role or "") if mine is not None else ""),
                    )
                self.state["other_champ_skipped"] += 1
                got.result = "other"
                return got
        with self.timed("guardar_partida"):
            got.result = store_match(svc.db, record, seed_tier=player["tier"], stats_queues=svc.settings.stats_queues)
        if cfg.targets and cfg.save_details and got.mine_target and got.result in ("stored", "duplicate"):
            got.detailed = self._detail(match_id, payload, player)
        if got.result == "stored" or got.detailed:
            from app import autopush  # cada AUTO_PUSH_GAMES partidas nuevas, commit «datos» y push

            autopush.note_game(svc)
        if got.result == "stored":
            self.state["stored"] += 1
            self.crawler.total_stored += 1
            self.stored_at.append(time.time())
        elif got.result == "duplicate":
            self.state["duplicates"] += 1
        elif got.result == "remake":
            self.state["remakes"] += 1
        else:
            self.state["ignored"] += 1
        return got

    def _refill_seeds(self, platform: str, tier: str) -> int:
        riot, db = self.svc.riot, self.svc.db
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        now = now_s()
        if tier in APEX_TIERS:
            row = db.one(
                "SELECT refreshed_at FROM seed_pages WHERE platform = ? AND queue = ? AND tier = ? AND division = 'I'",
                (platform, QUEUE_SOLO, tier),
            )
            if row and row["refreshed_at"] and now - row["refreshed_at"] < APEX_REFRESH_S:
                return 0
            league = riot.apex_league(platform, tier, **kw) or {}
            entries = [dict(e, tier=tier) for e in league.get("entries") or []]
            added = upsert_seed_players(db, platform, entries, tier)
            self._save_page(platform, tier, "I", 1, None, now)
        else:
            pages = {
                r["division"]: r
                for r in db.query(
                    "SELECT division, next_page, exhausted_at FROM seed_pages WHERE platform = ? AND queue = ? AND tier = ?",
                    (platform, QUEUE_SOLO, tier),
                )
            }
            candidates = []
            for division in DIVISIONS:
                info = pages.get(division)
                if info and info["exhausted_at"]:
                    if now - info["exhausted_at"] < LADDER_RESET_S:
                        continue
                    candidates.append((1, division))  # refrescar la escalera desde el principio
                else:
                    candidates.append((info["next_page"] if info else 1, division))
            if not candidates:
                return 0
            page, division = min(candidates)
            entries = riot.league_entries(platform, tier, division, page, **kw)
            if not entries:
                self._save_page(platform, tier, division, page, now, now)
                return 0
            added = upsert_seed_players(db, platform, entries, tier, division)
            self._save_page(platform, tier, division, page + 1, None, now)
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
                queue=None if cfg.include_flex else QUEUE_SOLO,
                ranked_only=cfg.include_flex,
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
        cooldown: dict[str, float] = {}
        # Tablas de los mejores de OP.GG (Diamante o más) que faltan: se intercalan con la búsqueda en el ranking, una
        # cada dos vueltas en que toca un elo de los que salen en ellas. Antes se pedían las de todos los campeones al
        # arrancar (173 tablas y una llamada de account-v1 por jugador nuevo, del mismo cupo regional que las partidas):
        # durante ese rato ni se buscaba en Platino ni Esmeralda ni se bajaban partidas a buen ritmo.
        pending_boards: list[int] | None = None
        board_turn = False
        while not w.stop_event.is_set():
            try:
                if not w.svc.keys.get() or w.svc.keys.status == "invalid":
                    self._set("waiting_key")
                    w.svc.keys.wait_for_change(w.svc.keys.version, w.stop_event)
                    continue
                if w.crawler.paused_reason():
                    self._set("paused_game")
                    w.stop_event.wait(5)
                    continue
                self._set("running")
                if pending_boards is None:
                    pending = getattr(w, "leaderboard_pending", None)
                    pending_boards = list(pending(self.platform)) if pending else []
                now = time.time()
                # Elos con jugadores de sobra esperando visita: no se buscan más (las descargas van a ~1 jugador por
                # minuto y región; revisar ~350 maestrías por minuto solo quitaba CPU y base de datos a las descargas).
                waiting = getattr(w, "waiting_by_tier", None) or {}
                full = {t for t in tiers if waiting.get((self.platform, t), 0) >= SCOUT_BACKLOG}
                tier = next((t for t in w.tier_order(self.platform, tiers) if cooldown.get(t, 0) <= now and t not in full), None)
                if tier is None and full:
                    self._set("idle")
                    w.stop_event.wait(SCOUT_BACKLOG_WAIT_S)
                    continue
                if pending_boards and (tier is None or tier in LEADERBOARD_TIERS):
                    board_turn = not board_turn
                    if tier is None or board_turn:
                        w._seed_from_leaderboards(self.platform, [pending_boards.pop(0)])
                        continue
                if tier is None:
                    self._set("idle")  # todo el ranking revisado: se vuelve a mirar al refrescarse las ligas
                    w.stop_event.wait(30)
                    continue
                if w._discover(self.platform, tier) == 0 and w._refill_seeds(self.platform, tier) == 0:
                    cooldown[tier] = time.time() + EXHAUSTED_COOLDOWN_S
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


class Crawler:
    def __init__(self, svc) -> None:
        self.svc = svc
        self.stop_event = threading.Event()
        self.workers: dict[str, RegionWorker] = {}
        self.config: CrawlerConfig | None = None
        self.started_at: float | None = None
        self.total_stored = 0
        self._lock = threading.Lock()
        self._funnel_at = 0.0
        self._funnel_thread: threading.Thread | None = None
        self._rows_lock = threading.Lock()
        self._rows_cache: tuple | None = None  # (clave de la búsqueda, cuándo, filas de ``otp_rows``)
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

    def otp_rows_cached(self, cfg: CrawlerConfig, now: int | None = None) -> tuple[list, float]:
        """``otp_rows`` de todas las plataformas de la búsqueda, leído una vez para todas las regiones cada
        ``OTP_PLAN_TTL_S`` como mucho (con 280.000 jugadores revisados tarda ~3 s)."""
        key = (otp_list_key(cfg), tuple(cfg.platforms), tuple(cfg.tiers), cfg.focus, cfg.min_mastery, cfg.min_share)
        with self._rows_lock:
            cached = self._rows_cache
            if cached is not None and cached[0] == key and time.time() - cached[1] < OTP_ROWS_FRESH_S:
                return cached[2], cached[1]
            at = time.time()
            rows = otp_rows_for_plan(self.svc.db, cfg, now)
            self._rows_cache = (key, at, rows)
            return rows, at

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

    def _write_funnel(self, cfg: CrawlerConfig) -> None:
        try:
            funnel = search_funnel(self.svc.db, cfg)
            dd = getattr(self.svc, "dd", None)
            funnel["config"] = {
                "champions": [dd.name(c) if dd is not None and hasattr(dd, "name") else c for c in cfg.targets],
                "champion_roles": {str(c): r for c, r in cfg.champion_roles.items()},
                "lines": cfg.roles,
                "focus": cfg.focus, "min_mastery": cfg.min_mastery, "min_share": cfg.min_share, "period": cfg.otp_depth,
                "platforms": cfg.platforms, "tiers": cfg.tiers,
            }
            funnel["workers"] = {
                r: {
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
                for r, w in self.workers.items()
            }
            # Jugadores encontrados por elo (todas las plataformas): para ver si la búsqueda reparte entre elos.
            by_tier: dict[str, int] = {}
            for w in self.workers.values():
                for (_platform, tier), n in (getattr(w, "found_by_tier", None) or {}).items():
                    by_tier[tier] = by_tier.get(tier, 0) + n
            funnel["found_by_tier"] = by_tier
            # Uso del cupo de cada host de Riot en los últimos 2 minutos: si una región no llega al límite, el cuello
            # de botella no es la key (es la búsqueda de jugadores o el filtro); «foreign»: otra app con la misma key.
            limiter = getattr(self.svc, "limiter", None)
            if limiter is not None:
                funnel["ratelimit"] = {
                    host: {"windows": h.get("windows"), "foreign": h.get("foreign"), "rate_limited": h.get("rate_limited")}
                    for host, h in limiter.snapshot().items()
                }
            funnel["rate"] = {"since_start": self.status().get("rate_per_min"), "recent": self.recent_rate(self._all_stored_at())}
            # Una línea por región en el registro (data/registro/app.log) cada 5 min: la evolución entre subidas.
            for region, w in funnel["workers"].items():
                t = w.get("timing") or {}
                log.info(
                    "Estado %s: %s partidas nuevas (%s/min ahora) · %s analizadas · errores %s · tiempo: %s",
                    region, w.get("stored"), w.get("rate_recent"), w.get("details"), w.get("errors"),
                    ", ".join(f"{k} {v:.0f}" for k, v in t.items() if isinstance(v, float)) or "—",
                )
            base = Path(self.svc.settings.data_dir) / "otps"
            base.mkdir(parents=True, exist_ok=True)
            tmp = base / "_busqueda.json.tmp"
            tmp.write_text(json.dumps(funnel, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(base / "_busqueda.json")
        except Exception:  # es un informe: nunca debe parar la búsqueda
            log.exception("No se pudo escribir el estado de la búsqueda de jugadores")

    def paused_reason(self) -> str | None:
        if getattr(self.svc.settings, "pause_in_game", True):
            tracker = getattr(self.svc, "tracker", None)
            if tracker is not None and tracker.state.get("in_game"):
                return "in_game"
        return None

    @property
    def autostart_enabled(self) -> bool:
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
        config = config.validated()
        dd = getattr(self.svc, "dd", None)
        config = replace(config, season_version=getattr(dd, "version", None) or config.season_version)
        if config.roles:
            pools = role_pools(self.svc, config.roles) or dict(config.role_pools)  # sin red: las listas de la última vez
            pool = role_pool_from(pools) or list(config.role_champions)
            if not pool:
                raise ValueError("Aún no se sabe qué campeones se juegan en esa línea: activa OP.GG o Lolalytics y vuelve a probar")
            config = replace(config, role_champions=pool, role_pools=pools)
        elif config.role_champions or config.role_pools:
            config = replace(config, role_champions=[], role_pools={})
        with self._lock:
            if self.running:
                raise RuntimeError("El recolector ya está en marcha")
            if require_key and not self.svc.keys.get():
                raise PermissionError("Configura primero la RIOT_API_KEY")
            if remember:
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
            for worker in self.workers.values():
                worker.start()
            if config.targets and config.save_details:
                otp_details.ensure_files(self.svc)  # carpeta de OTPs que falta o de otro formato: se regenera sola
        log.info("Recolector en marcha en %d regiones en paralelo.", len(self.workers))
        return self.status()

    def stop(self) -> dict:
        """Parada pedida por el usuario: tampoco arrancará solo la próxima vez.

        Lo primero, avisar a los hilos (antes se escribía el embudo de la búsqueda, que con decenas de miles de jugadores
        revisados tarda segundos, y el botón parecía no responder); el embudo, después y en segundo plano."""
        self.stop_event.set()
        for worker in list(self.workers.values()):
            worker.wake()
        self.svc.db.meta_set("crawler_autostart", "0")
        self.write_funnel(force=True)
        return self.status()

    def shutdown(self) -> None:
        self.stop_event.set()
        for worker in list(self.workers.values()):
            worker.wake()
        for worker in list(self.workers.values()):
            worker.join(timeout=3)

    def _all_stored_at(self) -> list[float]:
        out: list[float] = []
        for w in list(self.workers.values()):
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
        }
