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
from app.coach.insights import reliable
from app.data.db import now_s
from app.logbus import get_logger
from app.riot.client import Cancelled, RiotAPIError, RiotKeyError
from app.riot.ratelimit import Priority
from app.riot.routing import PLATFORMS, SUMMONERS_RIFT

log = get_logger("progreso")

# Colas de la Grieta con jugadores reales.
TRACKED_QUEUES = {400, 420, 430, 440, 480, 490, 700}
MAX_TIMELINE_TRIES = 6
PEER_BATCH = 40  # partidas por pasada al analizar a los otros 9 jugadores (~5 ms por jugador)
IN_GAME = {"InProgress", "Reconnect"}
CLIENT_POLL_S = 20
WATCH_S = 3  # el cliente se mira cada 3 s: al entrar en la selección de campeones lo pesado se para enseguida
RANK_TTL_S = 3600
ROLE_LABEL = {"TOP": "Top", "JUNGLE": "Jungla", "MID": "Mid", "ADC": "ADC", "SUPPORT": "Support"}


class ProfileNotFound(ValueError):
    pass


DIGEST_VERSION = 10  # 2: datos para ``champ_checks`` (habilidades, primera vuelta, primer objeto); 3: movimiento; 4: roams y
# contraganks; 5: reglas medibles de las guías (``rule_metrics``: pinks por vuelta, visión, roams, TP); 6: destinos de los
# roams y visión útil (wards del objetivo, de la jungla rival, pinks); 7: visión y jungla exactas de Riot (``rule_metrics``);
# 8: hábitos de ``macro_habits`` (objetivos que acaban en torre, ritmo del jungla, Farsight); 9: muertes caras y constancia;
# 10: rutas iniciales de jungla (``route_macro.early_route``)


def skill_times(timeline: dict | None, pid: int | None) -> list[float] | None:
    """Segundo de cada subida de habilidad (para análisis guardados antes de que se guardara)."""
    if not timeline or not pid:
        return None
    out = []
    for frame in (timeline.get("info") or {}).get("frames") or []:
        for e in frame.get("events") or []:
            if e.get("type") == "SKILL_LEVEL_UP" and e.get("participantId") == pid and (e.get("levelUpType") or "NORMAL") == "NORMAL" and e.get("skillSlot") in (1, 2, 3, 4):
                out.append(round((e.get("timestamp") or 0) / 1000, 1))
    return sorted(out) or None


def _route_macro(analysis: dict) -> dict | None:
    from app.coach import route_macro

    try:
        return route_macro.from_analysis(analysis) or None
    # un análisis raro no puede romper el resumen
    except Exception:  # noqa: BLE001
        log.debug("Sin roams ni contraganks para %s", analysis.get("match_id"), exc_info=True)
        return None


def _rules(analysis: dict) -> dict | None:
    from app.coach import rule_metrics

    try:
        return rule_metrics.from_analysis(analysis) or None
    # un análisis raro no puede romper el resumen
    except Exception:  # noqa: BLE001
        log.debug("Sin reglas medibles para %s", analysis.get("match_id"), exc_info=True)
        return None


def checks_digest(analysis: dict, skill_t: list[float] | None = None) -> dict | None:
    """Lo que necesita ``champ_checks`` de una partida, en pequeño (solo la primera vuelta de las compras)."""
    from app.coach import champ_checks

    if not analysis.get("build"):
        return None
    rec = champ_checks.record_from_analysis(analysis, skill_t)
    back = champ_checks.first_back(rec["backs"])
    return {
        "skills": rec["skills"][: champ_checks.SKILL_LEVELS],
        "skill_t": (rec["skill_t"] or [])[: champ_checks.SKILL_LEVELS] or None,
        "backs": [back] if back else [],
        "first_item": rec["first_item"],
        "first_item_t": rec["first_item_t"],
        "second_item_t": rec["second_item_t"],
        "items_reliable": rec["items_reliable"],
    }


def digest(analysis: dict, skill_t: list[float] | None = None) -> dict:
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
        # Errores concretos (para «Errores que repites»): [tipo, gravedad, segundo, etiquetas, título].
        "mk": [
            [m.get("kind"), m.get("severity"), m.get("t"), list(m.get("tags") or []), (m.get("title") or "")[:90]]
            for m in moments
            if m.get("severity") in ("high", "medium", "low") and m.get("kind") not in ("trade", "afk")
        ],
        "cc": checks_digest(analysis, skill_t),
        "rm": _route_macro(analysis),
        "rl": _rules(analysis),
        # Movimiento (posiciones reconstruidas cada 5 s): el tuyo y el de tu rival directo (de jungla, el otro jungla).
        "mv": _movement_digest(analysis),
        "dv": DIGEST_VERSION,
        "issues": {
            "high": sum(1 for m in moments if m.get("severity") == "high"),
            "medium": sum(1 for m in moments if m.get("severity") == "medium"),
            "low": sum(1 for m in moments if m.get("severity") == "low"),
            "good": sum(1 for m in moments if m.get("severity") == "good"),
            "info": sum(1 for m in moments if m.get("severity") == "info"),
        },
    }


def _movement_digest(analysis: dict) -> dict | None:
    """Tus posiciones y las de tu rival directo para el resumen (None si el análisis no trae movimiento)."""
    movement = analysis.get("movement")
    if not movement:
        return None
    rival = analysis.get("enemy_jungler") if analysis.get("role") == "JUNGLE" else analysis.get("opponent")
    return {"me": movement.get(str(analysis.get("me"))), "opp": movement.get(str(rival))}


def refresh_digests(db) -> int:
    """Pone al día los resúmenes ligeros de un formato anterior (sin volver a analizar: sale del análisis guardado)."""
    try:
        rows = db.query(
            "SELECT match_id, pid, analysis, timeline_raw IS NOT NULL AS has_tl, match_raw IS NOT NULL AS has_match FROM my_games "
            "WHERE analysis IS NOT NULL "
            "AND (CASE WHEN json_valid(digest) THEN json_extract(digest, '$.dv') END) IS NOT ?",
            (DIGEST_VERSION,),
        )
    # BD en migración: se intenta en la siguiente
    except Exception:  # noqa: BLE001
        log.debug("No se pudieron poner al día los resúmenes", exc_info=True)
        return 0
    done = 0
    for r in rows:
        if _refresh_digest(db, r):
            done += 1
    if done:
        log.info("Resúmenes de %d partidas puestos al día.", done)
    return done


def _refresh_digest(db, r) -> bool:
    """Rehace el resumen de una fila de ``refresh_digests`` (False si su análisis guardado no se puede leer)."""
    try:
        analysis = store.unpack(r["analysis"])
    # blob dañado: se queda como estaba
    except Exception:  # noqa: BLE001
        return False
    if not analysis:
        return False
    pid = r["pid"] or analysis.get("me")
    skill_t = None
    if not (analysis.get("build") or {}).get("skill_t") and r["has_tl"]:
        skill_t = skill_times(store.raw_timeline(db, r["match_id"]), pid)
    if r["has_match"] and _lacks_riot_rules(analysis):
        # Analizada antes de guardar ``riot_rules`` (o de que llevaran todos los retos que se miden): se sacan del
        # participante de Riot (sin volver a analizar).
        analysis["riot_rules"] = _riot_rules(store.raw_game(db, r["match_id"])[0], pid)
    if "my_wards" not in analysis and r["has_tl"]:
        # Analizada antes de guardar tus wards: salen de la línea de tiempo (sin volver a analizar).
        analysis["my_wards"] = my_wards(store.raw_timeline(db, r["match_id"]), pid)
    store.update_digest(db, r["match_id"], digest(analysis, skill_t))
    return True


def _lacks_riot_rules(analysis: dict) -> bool:
    """Si al análisis le faltan las estadísticas de Riot (``riot_rules``) o alguno de los retos que se miden."""
    have = (analysis.get("riot_rules") or {}).get("ch") or {}
    return "riot_rules" not in analysis or not all(k in have for k in ("controlWardsPlaced", "visionScorePerMinute"))


def _events(timeline: dict):
    """Los eventos de la línea de tiempo de Riot, minuto a minuto y en orden."""
    for frame in (timeline.get("info") or {}).get("frames") or []:
        yield from frame.get("events") or []


def my_wards(timeline: dict | None, pid: int | None) -> list | None:
    """Tus wards de la línea de tiempo de Riot, [segundo, tipo] (como ``my_wards`` del análisis)."""
    if not timeline or not pid:
        return None
    out = []
    for e in _events(timeline):
        if e.get("type") == "WARD_PLACED" and e.get("creatorId") == pid:
            kind = (e.get("wardType") or "").upper()
            if kind not in ("UNDEFINED", "TEEMO_MUSHROOM", ""):
                out.append([round((e.get("timestamp") or 0) / 1000, 1), kind])
    return out


def _riot_rules(match: dict | None, pid: int | None) -> dict | None:
    from app.coach import rule_metrics

    for p in ((match or {}).get("info") or {}).get("participants") or []:
        if p.get("participantId") == pid:
            return rule_metrics.raw_fields(p)
    return None


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


def _pending(st: dict | None) -> bool:
    """Si una partida está por procesar: nueva, a la espera del timeline o con un error que aún se reintenta."""
    return (
        st is None
        or st["status"] == "pending_timeline" and st["tries"] < MAX_TIMELINE_TRIES
        or st["status"] == "error" and st["tries"] < 3
    )


def _game_base(match_id: str, puuid: str, info: dict, me: dict) -> dict:
    """Lo que se guarda de cualquier partida tuya, también de las que no se analizan (cola, duración, campeón…)."""
    queue = int(info.get("queueId") or 0)
    duration = duration_seconds(info)
    return {
        "match_id": match_id,
        "puuid": puuid,
        "pid": int(me.get("participantId") or 0) or None,
        "queue": queue,
        "game_start": int(info.get("gameStartTimestamp") or info.get("gameCreation") or 0),
        "duration": duration,
        "champ": int(me.get("championId") or 0),
        "role": player_role(me),
        "team": int(me.get("teamId") or 0),
        "win": int(bool(me.get("win"))),
    }


def _ignore_reason(info: dict, me: dict, base: dict) -> str | None:
    """Por qué no se analiza una partida (otro modo de juego o remake); None si se analiza."""
    if int(info.get("mapId") or 0) != SUMMONERS_RIFT or base["queue"] not in TRACKED_QUEUES:
        return "otro modo de juego"
    if base["duration"] < 300 or me.get("gameEndedInEarlySurrender"):
        return "remake"
    return None


def _timeline_status(timeline: dict | None, tries: int) -> tuple[str, int]:
    """(estado, intentos) con que se guarda una partida analizada: lista con timeline; sin él, un intento más."""
    if timeline:
        return "ready", tries
    tries_out = tries + 1
    return ("pending_timeline" if tries_out < MAX_TIMELINE_TRIES else "no_timeline"), tries_out


def _peer_row(match: dict, timeline: dict, pid: int, items) -> dict | None:
    """Rol y métricas de un jugador de la partida para calibrar las referencias (None si no se le encuentra)."""
    try:
        a = analyze_game(match, timeline, pid=pid, items=items)
    except NotInMatch:
        return None
    return {"pid": pid, "role": a.get("role"), "reliable": reliable(a) and bool(a.get("role")), "metrics": a.get("metrics") or {}}


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
            "busy": False,  # eligiendo campeón, cargando o jugando (lcu.BUSY_PHASES)
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
        # El cliente de LoL se vigila en su propio hilo: una sincronización larga (minutos con el límite de la API) no
        # puede retrasar la pausa de las descargas de fondo cuando empiezas una partida.
        self._client_thread = threading.Thread(target=self._watch_loop, name="lol-client-watch", daemon=True)
        self._client_thread.start()

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
        self._reanalyze_safe()
        self._sample_peers_safe()
        next_poll = 0.0
        while not self.stop_event.is_set():
            try:
                # El cliente se vigila en su propio hilo (``_watch_loop``), haya perfil o no: el precargador, el
                # recolector y la calibración se pausan con ``state["busy"]`` (de la selección de campeones al final).
                profile = store.get_profile(self.svc.db)
                if self._waiting(profile):
                    continue
                next_poll = self._poll(profile, next_poll)
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

    def _reanalyze_safe(self) -> None:
        """``reanalyze_outdated`` sin excepciones: si falla, el seguimiento arranca igual."""
        try:
            self.reanalyze_outdated()
        except Exception:
            log.exception("No se pudieron re-analizar las partidas antiguas")

    def _waiting(self, profile: dict | None) -> bool:
        """Sin perfil, en pausa o sin key válida: lo deja en el estado, espera un poco y devuelve True."""
        if not profile:
            self._set(status="no_profile", next_check=None)
            self._sleep(CLIENT_POLL_S)
            return True
        if not profile["settings"].get("enabled", True):
            self._set(status="paused", next_check=None)
            self._sleep(CLIENT_POLL_S)
            return True
        if not self.svc.keys.get() or self.svc.keys.status == "invalid":
            self._set(status="waiting_key", next_check=None)
            self._sleep(10)
            return True
        return False

    def _poll(self, profile: dict, next_poll: float) -> float:
        """Sincroniza si toca (por el reloj o por un aviso del cliente al acabar una partida); devuelve la siguiente hora."""
        now = time.time()
        if now >= next_poll or any(t <= now for t in self._scheduled):
            self._scheduled = [t for t in self._scheduled if t > now]
            self.sync(profile)
            self._sample_peers_safe()
            next_poll = time.time() + max(1, int(profile["settings"].get("poll_minutes") or 3)) * 60
        return next_poll

    def _watch_loop(self) -> None:
        while not self.stop_event.is_set():
            self._watch_client_safe()
            self.stop_event.wait(WATCH_S)

    def _watch_client_safe(self) -> None:
        """``_watch_client`` sin excepciones: el cliente de LoL es opcional y nunca debe tumbar el bucle."""
        try:
            self._watch_client()
        except Exception:
            log.debug("No se pudo consultar el cliente de LoL", exc_info=True)

    def _watch_client(self) -> None:
        """Si el cliente está abierto, descarga cada partida en cuanto termina."""
        now = time.time()
        if now < self._next_client or not lcu.available():
            return
        self._next_client = now + WATCH_S
        phase = lcu.gameflow_phase()
        previous, self._last_phase = self._last_phase, phase
        self._set(client=phase is not None, phase=phase, in_game=phase in IN_GAME, busy=phase in lcu.BUSY_PHASES)
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
            # Tus partidas van por delante del recolector (LOW): con él a tope se siguen analizando.
            kw = {"priority": Priority.NORMAL, "stop": self.stop_event}
            if not profile.get("puuid"):
                profile = self._resolve(profile, kw)
            settings = profile["settings"]
            target = max(20, min(int(settings.get("backfill") or 50), 300))
            deep = int(settings.get("scanned") or 0) < target
            self._set(status="syncing")
            ids, profile = self._sync_ids(profile, target, deep, kw)
            states = store.game_states(db, ids)
            todo = [match_id for match_id in ids if _pending(states.get(match_id))]
            new = self._process_todo(profile, todo, states, kw)
            if deep:
                store.update_profile(db, settings={"scanned": target})
            self._refresh_rank(store.get_profile(db) or profile, kw)
            self._set(queue=0, last_check=time.time(), last_error=None)
            if todo:
                log.info("Seguimiento: %d partidas revisadas, %d nuevas analizadas.", len(todo), new)
            return {"checked": len(ids), "processed": len(todo), "new": new}

    def _sync_ids(self, profile: dict, target: int, deep: bool, kw: dict) -> tuple[list[str], dict]:
        """Partidas que revisar: las recientes (o hasta ``target`` en la pasada hacia atrás) y las que quedaron a medias."""
        db = self.svc.db
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
        return ids, profile

    def _process_todo(self, profile: dict, todo: list[str], states: dict, kw: dict) -> int:
        """Procesa las partidas pendientes llevando la cuenta en el estado; devuelve cuántas eran nuevas."""
        new = 0
        for index, match_id in enumerate(todo):
            self._set(queue=len(todo) - index)
            result = self._process(profile, match_id, states.get(match_id), kw)
            self._set(processed=self.state["processed"] + 1)
            if result == "new":
                new += 1
                self._set(new_total=self.state["new_total"] + 1, last_new_at=time.time())
        return new

    def _refresh_rank(self, profile: dict, kw: dict) -> None:
        """Tu liga actual en SoloQ (1 petición a la hora como mucho): para compararte con la que quieres alcanzar."""
        rank = (profile.get("settings") or {}).get("rank") or {}
        if not profile.get("puuid") or time.time() - (rank.get("checked_at") or 0) < RANK_TTL_S:
            return
        try:
            entries = self.svc.riot.league_entries_by_puuid(profile["platform"], profile["puuid"], **kw)
        except RiotKeyError:
            raise
        except RiotAPIError:
            return  # no es imprescindible: se reintenta en la siguiente vuelta
        solo = next((e for e in entries or [] if e.get("queueType") == "RANKED_SOLO_5x5"), None)
        value = {"tier": None, "checked_at": now_s()}
        if solo:
            value.update(
                tier=solo.get("tier"),
                division=solo.get("rank"),
                lp=solo.get("leaguePoints"),
                wins=solo.get("wins"),
                losses=solo.get("losses"),
            )
        store.update_profile(self.svc.db, settings={"rank": value})

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
        match, timeline = self._fetch(match_id, state, kw)
        if match is None:
            store.save_game(db, {"match_id": match_id, "puuid": profile["puuid"], "status": "error", "reason": "Riot no encuentra la partida", "tries": tries + 1})
            return "error"
        info = match.get("info") or {}
        try:
            me = find_me(match, puuid=profile["puuid"], game_name=profile["game_name"], tag_line=profile["tag_line"])
        except NotInMatch:
            store.save_game(db, {"match_id": match_id, "puuid": profile["puuid"], "status": "ignored", "reason": "no apareces en la partida"})
            return "ignored"
        base = _game_base(match_id, profile["puuid"], info, me)
        reason = _ignore_reason(info, me, base)
        if reason:
            store.save_game(db, {**base, "status": "ignored", "reason": reason})
            return "ignored"
        self._store_general(match)
        if timeline is None:
            timeline = self.svc.riot.timeline(match_id, **kw)
        analysis = analyze_game(match, timeline, pid=base["pid"], items=self.items_for(match))
        # El rol del análisis (None si teamPosition venía vacío o repetido): con él se eligen las referencias.
        base["role"] = analysis.get("role")
        status, tries_out = _timeline_status(timeline, tries)
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
            self._log_analyzed(base, analysis)
        return "new" if not state or state.get("status") != "ready" else "updated"

    def _fetch(self, match_id: str, state: dict | None, kw: dict) -> tuple[dict | None, dict | None]:
        """(match, timeline): los guardados si ya se descargó (falta el timeline) o el match de Riot (None si no está)."""
        match = timeline = None
        if state and state.get("has_match"):
            match, timeline, _ = store.raw_game(self.svc.db, match_id)
        if match is None:
            match = self.svc.riot.match(match_id, **kw)
        return match, timeline

    def _log_analyzed(self, base: dict, analysis: dict) -> None:
        """Deja en el registro el resumen de una partida recién analizada."""
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

    def items_for(self, match: dict):
        """Catálogo del parche de esa partida (o el actual si no se puede conseguir)."""
        version = (match.get("info") or {}).get("gameVersion")
        catalog = None
        finder = getattr(self.svc.dd, "catalog_for", None)
        if finder is not None:
            try:
                catalog = finder(version)
            # sin el catálogo de ese parche se usa el actual
            except Exception as exc:  # noqa: BLE001
                log.warning("Catálogo de objetos del parche %s: %s", version, exc)
                catalog = None
        return catalog or self.items()

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
    def _sample_peers_safe(self) -> None:
        try:
            self.sample_peers()
        except Exception:
            log.exception("No se pudieron analizar los jugadores de tus partidas (referencias calibradas)")

    def sample_peers(self, limit: int = PEER_BATCH) -> int:
        """Analiza a los otros 9 jugadores de tus partidas (sin peticiones: el match y el timeline ya están guardados).

        Con sus métricas se calibran las referencias de tu elo real (``benchmarks.References``).
        """
        db = self.svc.db
        done = 0
        for match_id in store.peers_missing(db, ANALYSIS_VERSION, limit):
            if self.stop_event.is_set():
                break
            match, timeline, my_pid = store.raw_game(db, match_id)
            rows = self._peer_rows(match, timeline, my_pid)
            start = int((match or {}).get("info", {}).get("gameStartTimestamp") or 0) or None
            store.save_peer_samples(db, match_id, start, ANALYSIS_VERSION, rows)
            done += 1
        if done:
            log.info("Analizados los jugadores de %d partidas tuyas (referencias calibradas con tu elo).", done)
        return done

    def _peer_rows(self, match: dict | None, timeline: dict | None, my_pid: int | None) -> list[dict]:
        """Rol y métricas de los otros 9 jugadores de una partida guardada (ninguno si falta el match o el timeline)."""
        if not match or not timeline:
            return []
        items = self.items_for(match) or self.items()
        info = match.get("info") or {}
        rows: list[dict] = []
        for p in info.get("participants") or []:
            pid = int(p.get("participantId") or 0)
            if not pid or pid == my_pid:
                continue
            row = _peer_row(match, timeline, pid, items)
            if row is not None:
                rows.append(row)
        return rows

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
                analysis = analyze_game(match, timeline, pid=pid, items=self.items_for(match) or items)
            except NotInMatch:
                continue
            store.update_analysis(db, match_id, analysis, digest(analysis), analysis_version(analysis))
            done += 1
        if done:
            log.info("Re-analizadas %d partidas con la versión %d del análisis.", done, ANALYSIS_VERSION)
        return done
