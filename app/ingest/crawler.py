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
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field

from app.data.db import now_s
from app.data.repository import known_match_ids, mark_skipped, store_match, upsert_seed_players
from app.ingest.extractor import parse_match
from app.logbus import get_logger
from app.riot.client import Cancelled, RiotAPIError, RiotKeyError
from app.riot.ratelimit import Priority
from app.riot.routing import APEX_TIERS, DIVISIONS, PLATFORMS, QUEUE_SOLO, REGIONS, TIER_INDEX, region_of

log = get_logger("crawler")

APEX_REFRESH_S = 12 * 3600
LADDER_RESET_S = 24 * 3600
EXHAUSTED_COOLDOWN_S = 20 * 60


@dataclass
class CrawlerConfig:
    platforms: list[str] = field(default_factory=lambda: ["euw1", "kr", "na1"])
    tiers: list[str] = field(default_factory=lambda: ["EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"])
    include_flex: bool = False
    days: int = 14
    matches_per_player: int = 20
    recrawl_hours: float = 12.0

    def validated(self) -> "CrawlerConfig":
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
        )


class RegionWorker(threading.Thread):
    def __init__(self, crawler: "Crawler", region: str, platforms: list[str], config: CrawlerConfig) -> None:
        super().__init__(name=f"crawler-{region}", daemon=True)
        self.crawler = crawler
        self.svc = crawler.svc
        self.region = region
        self.platforms = platforms
        self.config = config
        self.stop_event = crawler.stop_event
        self._cooldown: dict[tuple[str, str], float] = {}
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
            "last_error": None,
            "started_at": time.time(),
            "last_activity": time.time(),
        }

    # ------------------------------------------------------------------ bucle
    def run(self) -> None:
        combos = [(p, t) for t in reversed(self.config.tiers) for p in self.platforms]
        index = 0
        log.info("Recolector %s iniciado (%s).", self.region, ", ".join(PLATFORMS[p][1] for p in self.platforms))
        while not self.stop_event.is_set():
            try:
                if not self.svc.keys.get() or self.svc.keys.status == "invalid":
                    self._wait_for_key()
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
                self._crawl_player(player)
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
        self.state["status"] = "stopped"
        log.info("Recolector %s detenido. Guardadas %d partidas.", self.region, self.state["stored"])

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
            log.info("Semillas %s %s: +%d jugadores.", PLATFORMS[platform][1], tier, added)
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
    def _crawl_player(self, player) -> None:
        svc, cfg = self.svc, self.config
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        platform = self.state["platform"]
        newest_known = player["newest_match_at"] or 0
        newest = newest_known
        start_time = newest_known // 1000 + 1 if newest_known else now_s() - cfg.days * 86400
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
            stored_now = 0
            # De la más antigua a la más reciente: si se cancela a mitad, la
            # siguiente pasada recoge las que faltan (startTime > última vista).
            for match_id in reversed(new_ids):
                payload = svc.riot.match(match_id, **kw)
                self.state["fetched"] += 1
                self.state["last_activity"] = time.time()
                if payload is None:
                    mark_skipped(svc.db, match_id, "404")
                    continue
                record = parse_match(payload)
                if record is None:
                    mark_skipped(svc.db, match_id, "formato desconocido")
                    continue
                result = store_match(svc.db, record, seed_tier=player["tier"], stats_queues=svc.settings.stats_queues)
                newest = max(newest, record.game_start)
                if result == "stored":
                    stored_now += 1
                    self.state["stored"] += 1
                    self.crawler.total_stored += 1
                elif result == "duplicate":
                    self.state["duplicates"] += 1
                elif result == "remake":
                    self.state["remakes"] += 1
                else:
                    self.state["ignored"] += 1
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


class Crawler:
    def __init__(self, svc) -> None:
        self.svc = svc
        self.stop_event = threading.Event()
        self.workers: dict[str, RegionWorker] = {}
        self.config: CrawlerConfig | None = None
        self.started_at: float | None = None
        self.total_stored = 0
        self._lock = threading.Lock()
        saved = svc.db.meta_get("crawler_config")
        if saved:
            try:
                self.config = CrawlerConfig(**json.loads(saved)).validated()
            except (TypeError, ValueError):
                self.config = None

    @property
    def running(self) -> bool:
        return any(w.is_alive() for w in self.workers.values())

    def start(self, config: CrawlerConfig) -> dict:
        config = config.validated()
        with self._lock:
            if self.running:
                raise RuntimeError("El recolector ya está en marcha")
            if not self.svc.keys.get():
                raise PermissionError("Configura primero la RIOT_API_KEY")
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
        log.info("Recolector en marcha en %d regiones en paralelo.", len(self.workers))
        return self.status()

    def stop(self) -> dict:
        self.stop_event.set()
        for worker in list(self.workers.values()):
            worker.join(timeout=0.1)
        return self.status()

    def shutdown(self) -> None:
        self.stop_event.set()
        for worker in list(self.workers.values()):
            worker.join(timeout=3)

    def status(self) -> dict:
        elapsed = (time.time() - self.started_at) if self.started_at and self.running else None
        workers = {region: dict(w.state, alive=w.is_alive()) for region, w in self.workers.items()}
        return {
            "running": self.running,
            "stopping": self.stop_event.is_set() and self.running,
            "config": asdict(self.config) if self.config else asdict(CrawlerConfig()),
            "started_at": self.started_at,
            "stored": self.total_stored,
            "rate_per_min": round(self.total_stored / (elapsed / 60), 1) if elapsed and elapsed > 30 else None,
            "workers": workers,
        }
