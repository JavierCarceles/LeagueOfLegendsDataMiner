"""Contenedor de servicios compartidos (una instancia por proceso)."""

from __future__ import annotations

import json
import threading
import time

from app.analytics.aggregates import Aggregates
from app.analytics.priors import Priors
from app.coaching.guides import GuideIndex
from app.config import ENV_PATH, Settings
from app.data.db import Database
from app.ddragon import DDragon
from app.external.lolalytics import Lolalytics
from app.external.opgg import Opgg
from app.external.patchnotes import PatchNotes
from app.ingest.legacy import import_legacy, legacy_status
from app.logbus import get_logger
from app.riot.client import RiotClient
from app.riot.keys import KeyStore
from app.riot.ratelimit import RateLimiter
from app.warmer import Warmer

log = get_logger("app")

LIMITER_STATE = ".ratelimit.json"  # en data/ (ignorado por git): el cupo de la API entre reinicios
LEGACY_FILE = "matchups_stats.json"


class Services:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self.keys = KeyStore(ENV_PATH, state_path=settings.data_dir / "riot_key_state.json")
        self.limiter = RateLimiter()
        self._load_limiter()
        self.riot = RiotClient(self.keys, self.limiter)
        self.dd = DDragon(settings.ddragon_dir, settings.ddragon_lang)
        self.agg = Aggregates(self.db)
        self.warmer = Warmer(self, enabled=settings.warmer_enabled)
        self.opgg = Opgg(self.db, self.dd, enabled=settings.opgg_enabled, warmer=self.warmer)
        self.lolalytics = Lolalytics(self.db, self.dd, enabled=settings.lolalytics_enabled, warmer=self.warmer, latest_patch=self._latest_patch)
        self.patchnotes = PatchNotes(self.db, self.dd, enabled=settings.patchnotes_enabled, warmer=self.warmer, lang=settings.ddragon_lang)
        self.priors = Priors(self.agg, self.opgg)
        self.guides = GuideIndex(settings.coachings_dir, self.dd)
        self.opgg_sync_thread: threading.Thread | None = None
        self.opgg_stop = threading.Event()
        self.shutdown_event = threading.Event()
        from app.coach.replay_client import ClientReplays
        from app.coach.tracker import GameTracker
        from app.ingest.crawler import Crawler

        self.client_replays = ClientReplays()
        from app.replays_local import LocalReplays

        self.local_replays = LocalReplays(self)
        from app.coach.files import MyGamesFiles

        self.my_games_files = MyGamesFiles(self)
        self.crawler = Crawler(self)
        self.tracker = GameTracker(self)
        from app.draft_live import DraftLive

        self.draft_live = DraftLive(self)
        from app.coach.calibrate import TierCalibrator

        self.calibrator = TierCalibrator(self, enabled=settings.calibrate_enabled)
        from app.videos.collector import VideoCollector

        self.videos = VideoCollector(self)
        self.startup_done = threading.Event()
        self.legacy_running = False

    def _limiter_path(self):
        return self.settings.data_dir / LIMITER_STATE

    def _load_limiter(self) -> None:
        """El cupo de la API que Riot aún cuenta del arranque anterior (la app se reinició: versión nueva de GitHub)."""
        try:
            state = json.loads(self._limiter_path().read_text(encoding="utf-8"))
            loaded = self.limiter.import_state(state) if isinstance(state, dict) else 0
        except (OSError, ValueError, TypeError):
            return
        if loaded:
            log.info("Cupo de la API: %d llamadas de los últimos 2 min (arranque anterior) siguen contando.", loaded)

    def _save_limiter(self) -> None:
        try:
            path = self._limiter_path()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self.limiter.export_state()), encoding="utf-8")
            tmp.replace(path)
        except (OSError, ValueError):
            log.debug("No se pudo guardar el cupo de la API", exc_info=True)

    def legacy_path(self):
        return self.settings.legacy_dir / LEGACY_FILE

    def _latest_patch(self) -> str | None:
        versions = self.opgg.versions(allow_fetch=False) if self.opgg.enabled else []
        if versions:
            return versions[0]
        parts = str(self.dd.version or "").split(".")
        return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else None

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
        # El registro de escrituras de SQLite se vuelca al fichero principal en segundo plano (no dentro de cada escritura).
        self.db.start_checkpointer(getattr(self, "shutdown_event", None))
        # Muestreo de dónde está cada hilo (por qué va lento: en el embudo y el registro que se suben a GitHub).
        from app import diag

        diag.sampler.start(getattr(self, "shutdown_event", None))
        # Seguimiento automático de tus partidas (no hace nada sin perfil guardado).
        self.tracker.start()
        # Draft en vivo: la selección de campeones del cliente de LoL, y al terminar, tu build al cliente.
        self.draft_live.start()
        # Precarga de OP.GG, Lolalytics y notas de parche en segundo plano.
        self.warmer.start()
        # Referencias de tu elo objetivo con partidas reales (tandas pequeñas, prioridad baja).
        self.calibrator.start()
        # Tus partidas escritas en data/mis_partidas (para subirlas y revisarlas), al día cada minuto.
        self.my_games_files.start()
        # Partidas de OTPs analizadas con una versión anterior del análisis: se rehacen solas (sin Riot).
        try:
            from app.ingest import otp_details

            otp_details.start_reanalysis(self)
        except Exception:
            log.exception("No se pudo empezar a re-analizar las partidas de OTPs")
        # Errores por elo y referencias por campeón (data/otps): unos segundos en segundo plano, con caché en disco.
        try:
            from app.coach.elo_profile import EloProfile

            EloProfile.of(self).refresh()
        except Exception:
            log.exception("No se pudo calcular el perfil de errores por elo")
        # Repeticiones (.rofl) de este ordenador: carpeta recordada por sistema operativo, se indexa al arrancar.
        self.local_replays.start()
        # Subida automática de data/ a GitHub: se comprueba git ya (el aviso sale en «Datos y API» si algo falla).
        try:
            from app import autopush

            autopush.start(self)
        except Exception:
            log.exception("No se pudo comprobar la subida automática a GitHub")
        # Código nuevo en GitHub: se baja con la subida de datos y la app se reinicia sola sin perder lo que hacía.
        try:
            from app import updater

            updater.start(self)
        except Exception:
            log.exception("No se pudo empezar a vigilar las actualizaciones de GitHub")
        # El recolector vuelve a arrancar solo si lo dejaste en marcha.
        try:
            self.crawler.autostart()
        except Exception:
            log.exception("No se pudo reanudar el recolector")
        # Vídeos de YouTube pendientes (sección «Vídeos»): siguen solos.
        try:
            self.videos.autostart()
        except Exception:
            log.exception("No se pudo reanudar el recolector de vídeos")
        # Validación de la reconstrucción de recorridos con tus partidas: sola, unos minutos después de abrir
        # (data/registro/track_check.txt, con fecha y versión: se ve si mejora con cada cambio del análisis).
        try:
            from app.coach import track_check_job

            track_check_job.start(self)
        except Exception:
            log.exception("No se pudo programar la validación de recorridos")
        self.check_key_on_startup()

    def check_key_on_startup(self) -> None:
        """Comprueba sola la key del ``.env`` (1 petición) para no quedarse en «Key sin validar»."""
        if not self.keys.get() or self.keys.status != "unknown":
            return
        try:
            result = self.riot.check_key()
        except Exception:
            log.exception("No se pudo comprobar la API key al arrancar")
            return
        if result["valid"] is False:
            age_h = self.keys.public()["age_hours"]
            if age_h is not None and age_h < 0.25:
                self.schedule_key_recheck()  # recién generada: Riot puede tardar unos minutos en activarla
            else:
                log.warning("Riot rechaza la API key guardada (%s). Pega una nueva en «Datos y API».", result["reason"])

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
        self.warmer.shutdown()
        self.crawler.shutdown()
        self.tracker.shutdown()
        self.calibrator.shutdown()
        self.draft_live.shutdown()
        self.videos.shutdown()
        self.local_replays.stop()
        self.my_games_files.stop()
        from app.external.base import shutdown_fetches

        shutdown_fetches()
        self._save_limiter()
