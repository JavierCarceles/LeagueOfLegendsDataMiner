"""Recogida automática de tus partidas (sin grabar repeticiones).

Con un perfil guardado, un hilo en segundo plano:

1. Cada ``poll_minutes`` (3 por defecto) pide a match-v5 tus últimas partidas.
   La primera vez recorre hacia atrás hasta ``backfill`` partidas.
2. De cada partida nueva de la Grieta (normales, clasificatorias, Swiftplay o
   Clash) descarga el detalle y el **timeline**: posiciones de los 10
   jugadores minuto a minuto y todos los eventos con coordenadas. Es lo que
   necesita el análisis; no hace falta la repetición (.rofl).
3. La analiza (:mod:`app.coach.analyzer`) y guarda el JSON crudo comprimido
   para poder re-analizarla cuando mejore el análisis.

Si el cliente de LoL está abierto en este ordenador, detecta el final de cada
partida y la descarga en cuanto Riot la publica (1-3 min), sin esperar al
siguiente ciclo. Todas las llamadas van con prioridad baja: tus búsquedas en
la web siguen respondiendo rápido. Si la key caduca, se pausa y continúa sola
al pegar la nueva.
"""

from __future__ import annotations

import threading
import time

from app import lcu
from app.coach import store
from app.coach.analyzer import ANALYSIS_VERSION, NotInMatch, analyze_game, duration_seconds, find_me, player_role
from app.data.db import now_s
from app.logbus import get_logger
from app.riot.client import Cancelled, RiotAPIError, RiotKeyError
from app.riot.ratelimit import Priority
from app.riot.routing import PLATFORMS, SUMMONERS_RIFT

log = get_logger("progreso")

# Colas de la Grieta con jugadores reales.
TRACKED_QUEUES = {400, 420, 430, 440, 480, 490, 700}
MAX_TIMELINE_TRIES = 6
IN_GAME = {"InProgress", "Reconnect"}
CLIENT_POLL_S = 20
ROLE_LABEL = {"TOP": "Top", "JUNGLE": "Jungla", "MID": "Mid", "ADC": "ADC", "SUPPORT": "Support"}


class ProfileNotFound(ValueError):
    pass


def digest(analysis: dict) -> dict:
    """Resumen ligero de un análisis (lo que necesitan las estadísticas agregadas)."""
    by_pid = {p["pid"]: p for p in analysis.get("players") or []}
    jungle = analysis.get("jungle")
    moments = analysis.get("moments") or []
    build = analysis.get("build")
    return {
        "m": analysis.get("metrics") or {},
        "team": analysis.get("team"),
        "has_timeline": analysis.get("has_timeline", False),
        "deaths": [{k: d.get(k) for k in ("t", "x", "y", "tags", "early", "severity")} for d in analysis.get("deaths") or []],
        "jungle": {k: jungle.get(k) for k in ("start_camp", "start_side", "presence", "path")} if jungle else None,
        "build": build_digest(build),
        "opponent_champ": (by_pid.get(analysis.get("opponent")) or {}).get("champ"),
        "enemy_jungler_champ": (by_pid.get(analysis.get("enemy_jungler")) or {}).get("champ"),
        "issues": {
            "high": sum(1 for m in moments if m.get("severity") == "high"),
            "medium": sum(1 for m in moments if m.get("severity") == "medium"),
        },
    }


def analysis_version(analysis: dict) -> int:
    """Versión guardada. Sin catálogo de objetos se guarda una menos para repetirlo cuando lo haya."""
    if analysis.get("has_timeline") and not analysis.get("items_known"):
        return ANALYSIS_VERSION - 1
    return ANALYSIS_VERSION


def build_digest(build: dict | None) -> dict | None:
    """Lo mínimo de la build para compararla entre partidas: objetos terminados, botas y habilidades."""
    if not build:
        return None
    order = build.get("order") or []
    return {
        "items": [o["item"] for o in order if o.get("kind") == "legendary"],
        "boots": next((o["item"] for o in order if o.get("kind") == "boots"), None),
        "skill_max": build.get("skill_max") or [],
    }


def resolve_account(svc, game_name: str, tag_line: str, platform: str, **kw) -> dict:
    """Riot ID -> PUUID (de esta key), plataforma activa, icono y nivel."""
    account = svc.riot.account_by_riot_id(game_name, tag_line, platform, **kw)
    if not account or not account.get("puuid"):
        raise ProfileNotFound(f"No existe el Riot ID {game_name}#{tag_line}")
    puuid = account["puuid"]
    try:
        actual = svc.riot.active_platform(puuid, platform, **kw)
        if actual in PLATFORMS:
            platform = actual
    except RiotKeyError:
        raise
    except RiotAPIError:
        pass
    summoner = {}
    try:
        summoner = svc.riot.summoner_by_puuid(platform, puuid, **kw) or {}
    except RiotKeyError:
        raise
    except RiotAPIError:
        pass
    return {
        "puuid": puuid,
        "game_name": account.get("gameName") or game_name,
        "tag_line": account.get("tagLine") or tag_line,
        "platform": platform,
        "profile_icon": summoner.get("profileIconId"),
        "summoner_level": summoner.get("summonerLevel"),
    }


class GameTracker:
    def __init__(self, svc) -> None:
        self.svc = svc
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._sync_lock = threading.Lock()
        self._scheduled: list[float] = []
        self._next_client = 0.0
        self._last_phase: str | None = None
        self.state: dict = {
            "status": "stopped",
            "last_check": None,
            "next_check": None,
            "last_new_at": None,
            "queue": 0,
            "processed": 0,
            "new_total": 0,
            "errors": 0,
            "last_error": None,
            "client": False,
            "phase": None,
            "in_game": False,
            "waiting_after_game": False,
        }

    # ---------------------------------------------------------------- control
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self.stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="game-tracker", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self.stop_event.set()
        self.wake.set()
        if self._thread:
            self._thread.join(timeout=3)

    def trigger(self, delay: float = 0.0) -> None:
        """Comprueba ya (o dentro de ``delay`` s) si hay partidas nuevas."""
        self._scheduled.append(time.time() + delay)
        self.wake.set()

    def _set(self, **values) -> None:
        self.state.update(values)

    def _sleep(self, seconds: float) -> None:
        self.wake.wait(max(0.2, seconds))
        self.wake.clear()

    def status(self) -> dict:
        db = self.svc.db
        profile = store.get_profile(db)
        puuids = store.known_puuids(profile)
        settings = (profile or {}).get("settings") or {}
        return {
            **self.state,
            "running": self.running,
            "enabled": bool(profile) and bool(settings.get("enabled", True)),
            "poll_minutes": settings.get("poll_minutes"),
            "backfill": settings.get("backfill"),
            "scanned": settings.get("scanned"),
            "counts": store.counts(db, puuids),
            "last_game": store.last_game(db, puuids),
            "lcu": lcu.available(),
        }

    # ------------------------------------------------------------------ bucle
    def _run(self) -> None:
        self._set(status="starting")
        try:
            self.reanalyze_outdated()
        except Exception:
            log.exception("No se pudieron re-analizar las partidas antiguas")
        next_poll = 0.0
        while not self.stop_event.is_set():
            try:
                profile = store.get_profile(self.svc.db)
                if not profile:
                    self._set(status="no_profile", next_check=None)
                    self._sleep(30)
                    continue
                if not profile["settings"].get("enabled", True):
                    self._set(status="paused", next_check=None)
                    self._sleep(30)
                    continue
                self._watch_client()
                if not self.svc.keys.get() or self.svc.keys.status == "invalid":
                    self._set(status="waiting_key", next_check=None)
                    self._sleep(10)
                    continue
                now = time.time()
                if now >= next_poll or any(t <= now for t in self._scheduled):
                    self._scheduled = [t for t in self._scheduled if t > now]
                    self.sync(profile)
                    next_poll = time.time() + max(1, int(profile["settings"].get("poll_minutes") or 3)) * 60
                upcoming = min([next_poll, *self._scheduled])
                self._set(status="idle", next_check=upcoming, waiting_after_game=bool(self._scheduled))
                self._sleep(min(CLIENT_POLL_S, upcoming - time.time()))
            except Cancelled:
                break
            except RiotKeyError as exc:
                self._set(status="waiting_key", last_error=exc.message)
                log.warning("Seguimiento de partidas en pausa: %s", exc.message)
                self._sleep(10)
            except ProfileNotFound as exc:
                self._set(status="error", last_error=str(exc))
                log.warning("%s. Revisa tu perfil en «Mi progreso».", exc)
                next_poll = time.time() + 600
                self._sleep(30)
            except RiotAPIError as exc:
                self._set(errors=self.state["errors"] + 1, last_error=exc.message, status="idle")
                log.warning("Seguimiento de partidas: %s", exc.message)
                next_poll = time.time() + 90
                self._sleep(5)
            except Exception as exc:  # nunca tumbar el hilo
                self._set(errors=self.state["errors"] + 1, last_error=str(exc), status="idle")
                log.exception("Error inesperado en el seguimiento de partidas")
                next_poll = time.time() + 180
                self._sleep(10)
        self._set(status="stopped")

    def _watch_client(self) -> None:
        """Si el cliente está abierto, descarga cada partida en cuanto termina."""
        now = time.time()
        if now < self._next_client or not lcu.available():
            return
        self._next_client = now + CLIENT_POLL_S
        phase = lcu.gameflow_phase()
        previous, self._last_phase = self._last_phase, phase
        self._set(client=phase is not None, phase=phase, in_game=phase in IN_GAME)
        if previous in IN_GAME and phase not in IN_GAME:
            log.info("Partida terminada: la analizo en cuanto Riot la publique (1-3 min).")
            for delay in (75, 200, 480):
                self.trigger(delay)

    # ------------------------------------------------------------ sincronizar
    def sync(self, profile: dict | None = None) -> dict:
        with self._sync_lock:
            db = self.svc.db
            profile = profile or store.get_profile(db)
            if not profile:
                return {"status": "no_profile"}
            kw = {"priority": Priority.LOW, "stop": self.stop_event}
            if not profile.get("puuid"):
                profile = self._resolve(profile, kw)
            settings = profile["settings"]
            target = max(20, min(int(settings.get("backfill") or 50), 300))
            deep = int(settings.get("scanned") or 0) < target
            self._set(status="syncing")
            since = None if deep else store.newest_start(db, store.known_puuids(profile))
            if since:
                # Todas las posteriores a la última guardada (hasta 100): recupera también las que
                # jugaste con la web cerrada, con una sola llamada.
                ids, profile = self._match_ids(profile, 100, kw, start_time=since // 1000 - 3600)
            else:
                ids, profile = self._match_ids(profile, target if deep else 20, kw)
            for match_id in store.retry_candidates(db, store.known_puuids(profile), MAX_TIMELINE_TRIES):
                if match_id not in ids:
                    ids.append(match_id)
            states = store.game_states(db, ids)
            todo = []
            for match_id in ids:
                st = states.get(match_id)
                if st is None:
                    todo.append(match_id)
                elif st["status"] == "pending_timeline" and st["tries"] < MAX_TIMELINE_TRIES:
                    todo.append(match_id)
                elif st["status"] == "error" and st["tries"] < 3:
                    todo.append(match_id)
            new = 0
            for index, match_id in enumerate(todo):
                self._set(queue=len(todo) - index)
                result = self._process(profile, match_id, states.get(match_id), kw)
                self._set(processed=self.state["processed"] + 1)
                if result == "new":
                    new += 1
                    self._set(new_total=self.state["new_total"] + 1, last_new_at=time.time())
            if deep:
                store.update_profile(db, settings={"scanned": target})
            self._set(queue=0, last_check=time.time(), last_error=None)
            if todo:
                log.info("Seguimiento: %d partidas revisadas, %d nuevas analizadas.", len(todo), new)
            return {"checked": len(ids), "processed": len(todo), "new": new}

    def _resolve(self, profile: dict, kw: dict) -> dict:
        info = resolve_account(self.svc, profile["game_name"], profile["tag_line"], profile["platform"], **kw)
        log.info("Perfil vinculado: %s#%s (%s).", info["game_name"], info["tag_line"], PLATFORMS.get(info["platform"], ("", "?"))[1])
        return store.update_profile(self.svc.db, **info)

    def _match_ids(self, profile: dict, depth: int, kw: dict, start_time: int | None = None) -> tuple[list[str], dict]:
        ids: list[str] = []
        start = 0
        retried = False
        while len(ids) < depth:
            count = min(100, depth - len(ids))
            try:
                batch = self.svc.riot.match_ids(profile["platform"], profile["puuid"], start=start, count=count, start_time=start_time, **kw)
            except RiotAPIError as exc:
                # 400: el PUUID guardado no corresponde a esta key (se cifra por key): se vuelve a resolver.
                if exc.status == 400 and not isinstance(exc, RiotKeyError) and not retried:
                    retried = True
                    profile = self._resolve(profile, kw)
                    continue
                raise
            if not batch:
                break
            ids.extend(m for m in batch if m not in ids)
            start += len(batch)
            if len(batch) < count:
                break
        return ids, profile

    def _process(self, profile: dict, match_id: str, state: dict | None, kw: dict) -> str:
        db = self.svc.db
        tries = (state or {}).get("tries") or 0
        match = timeline = None
        if state and state.get("has_match"):
            match, timeline, _pid = store.raw_game(db, match_id)
        if match is None:
            match = self.svc.riot.match(match_id, **kw)
            if match is None:
                store.save_game(db, {"match_id": match_id, "puuid": profile["puuid"], "status": "error", "reason": "Riot no encuentra la partida", "tries": tries + 1})
                return "error"
        info = match.get("info") or {}
        try:
            me = find_me(match, puuid=profile["puuid"], game_name=profile["game_name"], tag_line=profile["tag_line"])
        except NotInMatch:
            store.save_game(db, {"match_id": match_id, "puuid": profile["puuid"], "status": "ignored", "reason": "no apareces en la partida"})
            return "ignored"
        queue = int(info.get("queueId") or 0)
        duration = duration_seconds(info)
        base = {
            "match_id": match_id,
            "puuid": profile["puuid"],
            "pid": int(me.get("participantId") or 0) or None,
            "queue": queue,
            "game_start": int(info.get("gameStartTimestamp") or info.get("gameCreation") or 0),
            "duration": duration,
            "champ": int(me.get("championId") or 0),
            "role": player_role(me),
            "team": int(me.get("teamId") or 0),
            "win": int(bool(me.get("win"))),
        }
        if int(info.get("mapId") or 0) != SUMMONERS_RIFT or queue not in TRACKED_QUEUES:
            store.save_game(db, {**base, "status": "ignored", "reason": "otro modo de juego"})
            return "ignored"
        if duration < 300 or me.get("gameEndedInEarlySurrender"):
            store.save_game(db, {**base, "status": "ignored", "reason": "remake"})
            return "ignored"
        self._store_general(match)
        if timeline is None:
            timeline = self.svc.riot.timeline(match_id, **kw)
        analysis = analyze_game(match, timeline, pid=base["pid"], items=self.items())
        if timeline:
            status, tries_out = "ready", tries
        else:
            tries_out = tries + 1
            status = "pending_timeline" if tries_out < MAX_TIMELINE_TRIES else "no_timeline"
        store.save_game(
            db,
            {
                **base,
                "status": status,
                "reason": None if timeline else "Riot aún no ha publicado el timeline",
                "tries": tries_out,
                "match_raw": match,
                "timeline_raw": timeline,
                "analysis": analysis,
                "digest": digest(analysis),
                "version": analysis_version(analysis),
                "analyzed_at": now_s(),
            },
        )
        if status == "ready":
            self._prefetch_build(base["champ"], base["role"])
            high = sum(1 for m in analysis["moments"] if m["severity"] == "high")
            log.info(
                "Partida analizada: %s (%s) · %s · %d/%d/%d · %d errores graves.",
                self.svc.dd.name(base["champ"]),
                ROLE_LABEL.get(base["role"] or "", "sin rol"),
                "victoria" if base["win"] else "derrota",
                analysis["metrics"]["kills"],
                analysis["metrics"]["deaths"],
                analysis["metrics"]["assists"],
                high,
            )
        return "new" if not state or state.get("status") != "ready" else "updated"

    def items(self):
        """Catálogo de objetos de Data Dragon (vacío si aún no ha cargado)."""
        catalog = getattr(self.svc.dd, "item_catalog", None)
        return catalog if catalog else None

    def _prefetch_build(self, champ: int, role: str | None) -> None:
        """Deja en caché la build más jugada de ese campeón (para comparar tu build)."""
        opgg = getattr(self.svc, "opgg", None)
        if not role or not opgg or not opgg.enabled:
            return
        try:
            opgg.champion_build(champ, role)
        except Exception:  # OP.GG es opcional
            log.debug("Sin build de referencia para %s (%s)", champ, role, exc_info=True)

    def _store_general(self, match: dict) -> None:
        """La partida también va a la BD general (vista «Partida», agregados si es ranked)."""
        from app.data.repository import store_match
        from app.ingest.extractor import parse_match

        try:
            record = parse_match(match)
            if record is None:
                return
            if self.svc.db.one("SELECT 1 FROM participants WHERE match_id = ? LIMIT 1", (record.match_id,)):
                return
            if store_match(self.svc.db, record, seed_tier=None, stats_queues=self.svc.settings.stats_queues) == "stored":
                self.svc.agg.bump()
        except Exception:  # no es imprescindible para el seguimiento
            log.debug("No se pudo guardar %s en la BD general", match.get("metadata", {}).get("matchId"), exc_info=True)

    # ------------------------------------------------------------ re-análisis
    def reanalyze_outdated(self, force: bool = False) -> int:
        db = self.svc.db
        items = self.items()
        if force:
            ids = [r[0] for r in db.query("SELECT match_id FROM my_games WHERE match_raw IS NOT NULL AND status IN ('ready', 'no_timeline', 'pending_timeline')")]
        else:
            ids = store.outdated(db, ANALYSIS_VERSION, with_items=bool(items))
        done = 0
        for match_id in ids:
            match, timeline, pid = store.raw_game(db, match_id)
            if not match:
                continue
            try:
                analysis = analyze_game(match, timeline, pid=pid, items=items)
            except NotInMatch:
                continue
            store.update_analysis(db, match_id, analysis, digest(analysis), analysis_version(analysis))
            done += 1
        if done:
            log.info("Re-analizadas %d partidas con la versión %d del análisis.", done, ANALYSIS_VERSION)
        return done
