"""Contenedor de servicios compartidos (una instancia por proceso)."""

from __future__ import annotations

import threading
import time

from app.analytics.aggregates import Aggregates
from app.analytics.priors import Priors
from app.coaching.guides import GuideIndex
from app.config import ENV_PATH, Settings
from app.data.db import Database
from app.ddragon import DDragon
from app.external.opgg import Opgg
from app.ingest.legacy import import_legacy, legacy_status
from app.logbus import get_logger
from app.riot.client import RiotClient
from app.riot.keys import KeyStore
from app.riot.ratelimit import RateLimiter

log = get_logger("app")
LEGACY_FILE = "matchups_stats.json"


class Services:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self.keys = KeyStore(ENV_PATH)
        self.limiter = RateLimiter()
        self.riot = RiotClient(self.keys, self.limiter)
        self.dd = DDragon(settings.ddragon_dir, settings.ddragon_lang)
        self.agg = Aggregates(self.db)
        self.opgg = Opgg(self.db, self.dd, enabled=settings.opgg_enabled)
        self.priors = Priors(self.agg, self.opgg)
        self.guides = GuideIndex(settings.coachings_dir, self.dd)
        self.opgg_sync_thread: threading.Thread | None = None
        self.opgg_stop = threading.Event()
        self.shutdown_event = threading.Event()
        from app.coach.tracker import GameTracker
        from app.coaching.youtube import YouTubeJobs
        from app.ingest.crawler import Crawler

        self.crawler = Crawler(self)
        self.youtube = YouTubeJobs(self)
        self.tracker = GameTracker(self)
        self.startup_done = threading.Event()
        self.legacy_running = False

    def legacy_path(self):
        return self.settings.legacy_dir / LEGACY_FILE

    def startup(self) -> None:
        """Tareas de arranque en segundo plano (no bloquean el servidor)."""
        try:
            self.dd.load()
            if legacy_status(self.db).get("status") != "done" and self.legacy_path().exists():
                self.run_legacy_import()
            self.priors.get()
        except Exception:
            log.exception("Fallo en las tareas de arranque")
        finally:
            self.startup_done.set()
        # Seguimiento automático de tus partidas (no hace nada sin perfil guardado).
        self.tracker.start()
        # Precarga ligera de OP.GG (tier lists del parche actual y anterior).
        if self.opgg.enabled and self.dd.champions:
            try:
                versions = self.opgg.versions()
                jobs = [
                    (lambda r=role, p=patch: self.opgg.tier_list(r, "emerald_plus", "global", p))
                    for patch in versions[:2]
                    for role in ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")
                ]
                self.opgg.prefetch(jobs, timeout=60, workers=3)
                self.priors.invalidate()
                log.info("OP.GG listo (parches %s).", ", ".join(versions[:2]) or "?")
            except Exception as exc:
                log.warning("OP.GG no disponible ahora mismo: %s", exc)

    def schedule_key_recheck(self) -> None:
        """Vuelve a comprobar una key rechazada durante ~9 min.

        Una development key recién generada a veces tarda unos minutos en
        funcionar. Así el usuario no tiene que estar pulsando «Validar».
        """
        keys = self.keys
        if keys.rechecking:
            return
        version = keys.version
        keys.rechecking = True

        def run() -> None:
            try:
                for delay in (15, 30, 45, 60, 90, 120, 180):
                    keys.next_check_at = time.time() + delay
                    if self.shutdown_event.wait(delay):
                        return
                    if keys.version != version or keys.status == "valid":
                        return
                    result = self.riot.check_key()
                    if result["valid"]:
                        log.info("Riot ya acepta la API key: lista para usar.")
                        return
                    log.info("Riot todavía no acepta la key (%s). Vuelvo a probar en breve…", result["reason"])
                log.warning("Riot sigue rechazando la key tras ~9 min: comprueba que copiaste la última que generaste.")
            finally:
                keys.rechecking = False
                keys.next_check_at = None

        threading.Thread(target=run, name="key-recheck", daemon=True).start()

    def start_opgg_sync(self, tier: str, region: str, patches: list[str]) -> dict:
        if self.opgg_sync_thread and self.opgg_sync_thread.is_alive():
            raise RuntimeError("Ya hay una sincronización en curso")
        self.opgg_stop = threading.Event()

        def run():
            self.opgg.sync(tier, region, patches, stop=self.opgg_stop)
            self.priors.invalidate()

        self.opgg_sync_thread = threading.Thread(target=run, name="opgg-sync", daemon=True)
        self.opgg_sync_thread.start()
        return self.opgg.sync_state

    def run_legacy_import(self, force: bool = False) -> dict:
        if self.legacy_running:
            return {"status": "running"}
        self.legacy_running = True
        try:
            report = import_legacy(self.db, self.dd, self.legacy_path(), force=force)
            self.agg.bump()
            self.priors.invalidate()
            return report
        finally:
            self.legacy_running = False

    def shutdown(self) -> None:
        self.shutdown_event.set()
        self.opgg_stop.set()
        self.crawler.shutdown()
        self.tracker.shutdown()
