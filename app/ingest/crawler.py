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
import json
import threading
import time
from dataclasses import asdict, dataclass, field
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
OTP_RATIO = 1.5  # el campeón principal debe tener ≥ 1,5× los puntos del segundo
ACTIVE_DAYS = 30  # y haberlo jugado en el último mes (la maestría es acumulada de por vida)

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
OTP_DEPTHS = ("season", "90", "180", "365", "all")
FUNNEL_EVERY_S = 300  # cada cuánto se escribe data/otps/_busqueda.json
LEADERBOARD_TTL_S = 24 * 3600  # la tabla de los mejores de cada campeón (OP.GG) se vuelve a pedir una vez al día
LEADERBOARD_TIERS = {"DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"}  # elos que salen en esa tabla
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
    """Roles pedidos por campeón («|r:266:TOP;517:JUNGLE+MID»); vacío sin roles (así las claves de antes no cambian)."""
    roles = cfg.champion_roles or {}
    if not roles:
        return ""
    return "|r:" + ";".join(f"{c}:{'+'.join(roles[c])}" for c in sorted(roles))


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
    return f"{','.join(str(c) for c in sorted(cfg.champions))}|{int(cfg.only_target_games)}{_roles_key(cfg)}"


def target_sql(cfg: CrawlerConfig, alias: str = "") -> tuple[str, list]:
    """Condición SQL «partida con uno de los campeones elegidos, en su rol» sobre ``participants`` y sus parámetros."""
    col = f"{alias}." if alias else ""
    roles = cfg.champion_roles or {}
    free = [c for c in cfg.champions if c not in roles]
    parts, params = [], []
    if free:
        parts.append(f"{col}champ IN ({','.join('?' * len(free))})")
        params.extend(free)
    for champ in cfg.champions:
        if champ in roles:
            parts.append(f"({col}champ = ? AND {col}role IN ({','.join('?' * len(roles[champ]))}))")
            params.extend([champ, *roles[champ]])
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
        stored = row["config_key"] if "config_key" in row.keys() else None
        return "loading" if config_key is not None and stored != config_key else "share"
    if depth_start < (row["back_done_start"] or 0):
        return "loading"
    return "done"


@dataclass
class CrawlerConfig:
    platforms: list[str] = field(default_factory=lambda: ["euw1", "kr", "na1"])
    tiers: list[str] = field(default_factory=lambda: ["EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"])
    include_flex: bool = False
    days: int = 14
    matches_per_player: int = 20
    recrawl_hours: float = 12.0
    # Búsqueda por campeones: OTPs («otp») o jugadores con mucha maestría («mastery»).
    champions: list[int] = field(default_factory=list)
    focus: str = "otp"
    min_mastery: int = 300_000
    only_target_games: bool = True
    # Hasta dónde se baja el historial de cada OTP: temporada actual, N días o todo lo que tenga Riot.
    otp_depth: str = "season"
    # Rol buscado de cada campeón ({campeón: [roles]}): sin entrada = cualquier rol. Ej.: Sylas solo jungla o mid.
    champion_roles: dict[int, list[str]] = field(default_factory=dict)
    # Micro y macro de cada partida de los OTPs con esos campeones (timeline: 1 llamada más por partida).
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
        return CrawlerConfig(
            platforms=platforms,
            tiers=tiers,
            include_flex=bool(self.include_flex),
            days=max(1, min(int(self.days), 60)),
            matches_per_player=max(5, min(int(self.matches_per_player), 100)),
            recrawl_hours=max(1.0, min(float(self.recrawl_hours), 168.0)),
            champions=list(dict.fromkeys(int(c) for c in (self.champions or []) if str(c).isdigit() and int(c) > 0))[:10],
            focus=self.focus if self.focus in ("otp", "mastery") else "otp",
            min_mastery=max(10_000, min(int(self.min_mastery or 300_000), 10_000_000)),
            only_target_games=bool(self.only_target_games),
            otp_depth=str(self.otp_depth) if str(self.otp_depth) in OTP_DEPTHS else "season",
            champion_roles=self._clean_roles(),
            save_details=bool(self.save_details),
            games_per_otp=0 if not int(self.games_per_otp or 0) else max(5, min(int(self.games_per_otp), 1000)),
        )

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
        if champ is None or champ not in self.champions:
            return False
        roles = self.champion_roles.get(champ)
        return not roles or role in roles

    def qualifies(self, top: list, now: int | None = None) -> bool:
        """¿Este jugador es OTP (o tiene mucha maestría) de alguno de los campeones elegidos y lo juega aún?

        ``top``: [[campeón, puntos, última partida (s, opcional)], …] de más a menos puntos.
        """
        targets = set(self.champions)
        if not targets or not top:
            return False
        cutoff = (now or now_s()) - ACTIVE_DAYS * 86400

        def active(entry) -> bool:
            last = int(entry[2]) if len(entry) > 2 and entry[2] else None
            return last is None or last >= cutoff

        if self.focus == "mastery":
            return any(int(e[0]) in targets and int(e[1]) >= self.min_mastery and active(e) for e in top)
        champ, points = int(top[0][0]), int(top[0][1])
        second = int(top[1][1]) if len(top) > 1 else 0
        return champ in targets and points >= self.min_mastery and points >= OTP_RATIO * second and active(top[0])


def search_funnel(db, cfg: CrawlerConfig, now: int | None = None) -> dict:
    """Dónde se quedan los jugadores revisados (por plataforma y en total): para ver por qué salen pocos OTPs.

    Fases (las de tu configuración): revisados → con el campeón entre sus 3 primeros (o de principal, buscando OTPs)
    → con el mínimo de puntos → OTP estricto (1,5 veces el segundo; solo buscando OTPs) → jugado en el último mes.
    """
    now = now or now_s()
    targets = set(cfg.champions or [])
    platforms, tiers = list(cfg.platforms), list(cfg.tiers)
    if not targets or not platforms or not tiers:
        return {"stages": [], "platforms": {}}
    pm, tm = ",".join("?" * len(platforms)), ",".join("?" * len(tiers))
    cutoff = now - ACTIVE_DAYS * 86400
    stages = ["checked", "champ", "points", "ratio", "active"] if cfg.focus == "otp" else ["checked", "champ", "points", "active"]
    per: dict[str, dict] = {}

    def bucket(platform: str) -> dict:
        return per.setdefault(platform, {k: 0 for k in (*stages, "seeds", "last_hour")})

    for r in db.query(
        f"SELECT platform, COUNT(*) AS n FROM players WHERE is_seed = 1 AND platform IN ({pm}) AND tier IN ({tm}) GROUP BY platform",
        (*platforms, *tiers),
    ):
        bucket(r["platform"])["seeds"] = int(r["n"])
    for r in db.query(
        f"SELECT p.platform, m.top, m.checked_at FROM mastery_checks m JOIN players p ON p.puuid = m.puuid "
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
        if cfg.focus == "otp":
            entry = top[0] if top and int(top[0][0]) in targets else None
        else:
            entry = next((e for e in top if int(e[0]) in targets), None)
        if entry is None:
            continue
        b["champ"] += 1
        if int(entry[1]) < cfg.min_mastery:
            continue
        b["points"] += 1
        if cfg.focus == "otp":
            second = int(top[1][1]) if len(top) > 1 else 0
            if int(entry[1]) < OTP_RATIO * second:
                continue
            b["ratio"] += 1
        last = int(entry[2]) if len(entry) > 2 and entry[2] else None
        if last is None or last >= cutoff:
            b["active"] += 1
    total = {k: sum(b[k] for b in per.values()) for k in (*stages, "seeds", "last_hour")}
    return {"stages": stages, "total": total, "platforms": dict(sorted(per.items(), key=lambda kv: -kv[1]["checked"])), "at": now}


def otp_rows(db, cfg: CrawlerConfig, platforms: list[str] | None = None, tiers: list[str] | None = None, now: int | None = None) -> list:
    """Jugadores del ranking que cumplen la búsqueda por campeones, con su maestría y su progreso: [(fila, top)].

    El filtro por campeón va en SQL (``json_extract`` sobre los 3 primeros de la maestría), así que no depende de
    cuántos jugadores se hayan revisado: antes solo se miraban 300 filas y, con muchos revisados que no eran OTP, los
    OTPs ya encontrados dejaban de volver a visitarse.
    """
    targets = list(cfg.champions or [])
    if not targets:
        return []
    marks = ",".join("?" * len(targets))
    slots = ("$[0][0]",) if cfg.focus == "otp" else ("$[0][0]", "$[1][0]", "$[2][0]")
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
        if cfg.qualifies(top, now) or sourced_champs(row) & wanted:
            out.append((row, top))
    return out


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
        self.scouts: list[OtpScout] = []  # una búsqueda de OTPs por plataforma (cada una tiene su propio cupo)
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

    def wake(self) -> None:
        self._wake.set()

    # ------------------------------------------------------------------ bucle
    def run(self) -> None:
        if self.config.champions:
            self._run_otp()
        else:
            self._run_ladder()
        self.state["status"] = "stopped"
        if self.config.champions and self.config.save_details:
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
            "Recolector %s de OTPs iniciado (%s): historial %s, sin tope de partidas.",
            self.region,
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
                        self._wake.wait(OTP_IDLE_WAIT_S)
                        continue
                    self.state.update(status="running", platform=target["platform"], tier=target["tier"])
                    self._visit_otp(target)
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
        champs = list(self.config.champions) or [0]
        marks = ",".join("?" * len(champs))
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

    def _seed_from_leaderboards(self, platform: str) -> int:
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
        for champ in self.config.champions:
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
            for entry in entries:
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
                "Tabla de los mejores de %s en %s (OP.GG): %d OTPs de tus elos añadidos.", champ_name, PLATFORMS[platform][1], champ_added
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
            self._resolve_name(platform, puuid)
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
        """El OTP con más prioridad que tenga algo pendiente (o ``None``).

        1. Recién encontrados (aún sin visitar), de más a menos elo y LP.
        2. Partidas nuevas: no se visitan desde hace ``recrawl_hours`` o quedaron partidas nuevas por bajar.
        3. Historial antiguo: primero el de quien menos lleva bajado, para que todos tengan lo reciente antes de
           profundizar en uno solo.
        """
        cfg = self.config
        now = now_s()
        depth_start, _ = self._depth()
        list_key = self._list_key()
        recrawl = int(cfg.recrawl_hours * 3600)
        best = None
        known = complete = 0
        rows = otp_rows(self.svc.db, cfg, self.platforms, cfg.tiers, now)
        lacking = self._lacking_details([row["id"] for row, _ in rows]) if cfg.save_details else set()
        cap = self._games_cap()
        for row, _top in rows:
            known += 1
            state = otp_history_state(row, list_key, depth_start, otp_config_key(cfg))
            if state in ("done", "share"):
                complete += 1
            if self._otp_fails.get(row["puuid"], (0, 0.0))[1] > time.time():
                continue  # falló varias veces seguidas: descansa un rato
            rank = (-TIER_INDEX.get(row["tier"] or "", 0), -(row["lp"] or 0))
            if state == "queued":
                key = (0, 0, *rank)
            elif row["fwd_more"] or (row["last_crawled_at"] or 0) < now - recrawl:
                key = (1, row["last_crawled_at"] or 0, *rank)
            elif state == "loading" and not (cap and int(row["hits"] or 0) >= cap):
                key = (2, row["back_offset"] or 0, *rank)  # sin pasar del tope mientras se buscan más OTPs
            elif row["id"] in lacking:
                key = (3, 0, *rank)  # partidas guardadas sin micro/macro (de antes de activarlo o que fallaron)
            else:
                continue
            if best is None or key < best[0]:
                best = (key, row)
        self.state.update(otps_known=known, otps_complete=complete)
        return best[1] if best else None

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
        if svc.db.scalar("SELECT 1 FROM otp_games WHERE match_id = ? AND puuid = ?", (match_id, player["puuid"])):
            return False
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        try:
            if payload is None:
                payload = svc.riot.match(match_id, **kw)
                if payload is None:
                    return False
            timeline = svc.riot.timeline(match_id, **kw)
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
        try:
            saved = otp_details.save(svc.db, svc, payload, timeline, player["puuid"], elo)
        except Exception:
            log.exception("No se pudo analizar la partida %s del OTP", match_id)
            return False
        if saved:
            self.state["details"] += 1
        return saved

    def _visit_otp(self, row) -> None:
        """Una visita a un OTP: sus partidas nuevas y, con lo que quede de presupuesto, su historial hacia atrás."""
        svc, cfg, db = self.svc, self.config, self.svc.db
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        platform, puuid = row["platform"], row["puuid"]
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
            if not row["game_name"]:
                self._resolve_name(platform, puuid)
        try:
            # 1) Partidas nuevas (posteriores al ancla), de la más antigua a la más reciente, con su propio cursor:
            #    si se corta a mitad, la siguiente visita sigue desde la última recorrida.
            after = max(prog["anchor"], prog["fwd_newest"] // 1000)
            prog["fwd_more"] = 1  # hasta terminar esta pasada
            ids: list[str] = []
            for page in range(OTP_FORWARD_PAGES):
                chunk = svc.riot.match_ids(
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
            for match_id in reversed(ids):
                if match_id in known:
                    prog["fwd_newest"] = max(prog["fwd_newest"], starts.get(match_id, 0))
                    continue
                if budget <= 0:
                    finished = False
                    break
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
                and not (cap and prog["hits"] >= cap)  # variedad: el resto, cuando la búsqueda termine
            ):
                base = prog["offset"]
                ids = svc.riot.match_ids(
                    platform, puuid, queue=queue, ranked_only=cfg.include_flex, start_time=depth_start or None,
                    end_time=prog["anchor"], start=base, count=OTP_PAGE, **kw,
                )
                pages += 1
                self.state["otp_pages"] += 1
                self.state["ids_seen"] += len(ids)
                known = self._known_for(puuid, ids)
                consumed = page_seen = page_hits = 0
                old_season = False
                for match_id in ids:
                    if match_id not in known:
                        if budget <= 0:
                            break
                        got = ingest(match_id)
                        page_seen += got.valid
                        page_hits += got.valid and got.mine_target
                        if season_major and got.patch_major and got.patch_major < season_major:
                            old_season = True  # primera partida de la temporada anterior: hasta aquí
                    consumed += 1
                    prog["offset"] = base + consumed
                    if old_season or (cap and prog["hits"] >= cap):
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
            save(final=True)
        stored_now = self.state["stored"] - stored_before
        if stored_now:
            svc.agg.bump()
        log.info(
            "%s · %s · OTP: %d partidas nuevas (historial: %d revisadas%s).",
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
        if rest and cfg.only_target_games and cfg.champions:
            marks = ",".join("?" * len(rest))
            for r in self.svc.db.query(
                f"SELECT match_id, champ, role FROM otp_skips WHERE puuid = ? AND match_id IN ({marks})", (puuid, *rest)
            ):
                champ, role = int(r["champ"] or 0), r["role"]
                if role is None and cfg.champion_roles.get(champ):
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
        """¿Otro OTP buscado jugó esta partida con uno de los campeones elegidos? Entonces también interesa."""
        cfg = self.config
        others = [p.puuid for p in record.participants if p.puuid and p.puuid != puuid and cfg.is_target(p.champ, p.role)]
        if not others:
            return False
        marks = ",".join("?" * len(others))
        champs = list(cfg.champions)
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
        if cfg.champions:
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
        got.result = store_match(svc.db, record, seed_tier=player["tier"], stats_queues=svc.settings.stats_queues)
        if cfg.champions and cfg.save_details and got.mine_target and got.result in ("stored", "duplicate"):
            got.detailed = self._detail(match_id, payload, player)
        if got.result == "stored":
            self.state["stored"] += 1
            self.crawler.total_stored += 1
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
            if self.config.champions:
                log.info(
                    "Ranking %s %s: +%d jugadores por revisar (candidatos; solo cuentan los que resulten OTPs al mirar su maestría).",
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
        """Modo normal: las partidas recientes de un jugador del ranking (hasta ``matches_per_player``)."""
        svc, cfg = self.svc, self.config
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        newest_known = player["newest_match_at"] or 0
        newest = newest_known
        start_time = newest_known // 1000 + 1 if newest_known else now_s() - cfg.days * 86400
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
    después el ranking **de más a menos elo**: no se pasa al siguiente elo hasta revisar entero el anterior.
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
        tiers = list(reversed(w.config.tiers))  # de más a menos elo
        cooldown: dict[str, float] = {}
        leaderboard_done = False
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
                if not leaderboard_done:
                    leaderboard_done = True
                    w._seed_from_leaderboards(self.platform)
                    continue
                now = time.time()
                tier = next((t for t in tiers if cooldown.get(t, 0) <= now), None)
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
                log.warning("Búsqueda de OTPs %s: %s", self.platform, exc.message)
                w.stop_event.wait(3)
            except Exception as exc:
                w.state["errors"] += 1
                w.state["last_error"] = str(exc)
                log.exception("Error inesperado buscando OTPs en %s", self.platform)
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
        saved = svc.db.meta_get("crawler_config")
        if saved:
            try:
                self.config = CrawlerConfig(**json.loads(saved)).validated()
            except (TypeError, ValueError):
                self.config = None

    @property
    def running(self) -> bool:
        return any(w.is_alive() or any(sc.is_alive() for sc in w.scouts) for w in self.workers.values())

    def searching(self) -> bool:
        return any(w.searching() for w in self.workers.values())

    def write_funnel(self, force: bool = False) -> None:
        """Cada 5 min (lo llama cualquier región): el embudo de la búsqueda en ``data/otps/_busqueda.json``."""
        cfg = self.config
        if cfg is None or not cfg.champions:
            return
        with self._lock:
            if not force and time.time() - self._funnel_at < FUNNEL_EVERY_S:
                return
            self._funnel_at = time.time()
        try:
            funnel = search_funnel(self.svc.db, cfg)
            dd = getattr(self.svc, "dd", None)
            funnel["config"] = {
                "champions": [dd.name(c) if dd is not None and hasattr(dd, "name") else c for c in cfg.champions],
                "champion_roles": {str(c): r for c, r in cfg.champion_roles.items()},
                "focus": cfg.focus, "min_mastery": cfg.min_mastery, "platforms": cfg.platforms, "tiers": cfg.tiers,
            }
            funnel["workers"] = {r: {k: w.state.get(k) for k in ("status", "search", "mastery_checked", "targets_found", "errors", "last_error")} for r, w in self.workers.items()}
            base = Path(self.svc.settings.data_dir) / "otps"
            base.mkdir(parents=True, exist_ok=True)
            tmp = base / "_busqueda.json.tmp"
            tmp.write_text(json.dumps(funnel, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(base / "_busqueda.json")
        except Exception:  # es un informe: nunca debe parar la búsqueda
            log.exception("No se pudo escribir el estado de la búsqueda de OTPs")

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
            if config.champions and config.save_details:
                otp_details.ensure_files(self.svc)  # carpeta de OTPs que falta o de otro formato: se regenera sola
        log.info("Recolector en marcha en %d regiones en paralelo.", len(self.workers))
        return self.status()

    def stop(self) -> dict:
        """Parada pedida por el usuario: tampoco arrancará solo la próxima vez."""
        self.svc.db.meta_set("crawler_autostart", "0")
        self.write_funnel(force=True)
        self.stop_event.set()
        for worker in list(self.workers.values()):
            worker.wake()
        for worker in list(self.workers.values()):
            worker.join(timeout=0.1)
        return self.status()

    def shutdown(self) -> None:
        self.stop_event.set()
        for worker in list(self.workers.values()):
            worker.wake()
        for worker in list(self.workers.values()):
            worker.join(timeout=3)

    def status(self) -> dict:
        elapsed = (time.time() - self.started_at) if self.started_at and self.running else None
        workers = {region: dict(w.state, alive=w.is_alive()) for region, w in self.workers.items()}
        return {
            "running": self.running,
            "stopping": self.stop_event.is_set() and self.running,
            "autostart": self.autostart_enabled,
            "paused": self.paused_reason() if self.running else None,
            "config": asdict(self.config) if self.config else asdict(CrawlerConfig()),
            "started_at": self.started_at,
            "stored": self.total_stored,
            "rate_per_min": round(self.total_stored / (elapsed / 60), 1) if elapsed and elapsed > 30 else None,
            "workers": workers,
        }
