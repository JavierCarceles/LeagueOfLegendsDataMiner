"""Referencias de tu elo objetivo con partidas reales, en segundo plano.

Para que «Qué haces mal» no dependa de referencias estimadas: cada cierto tiempo se baja una tanda pequeña de
partidas de SoloQ de tu elo objetivo (league-v4 → match-v5 + timeline, con prioridad baja para no quitarle cupo a
nada y en pausa mientras juegas) y se analizan sus 10 jugadores con el mismo código que tus partidas. Sus métricas
van a ``peer_samples`` con la fuente ``tier:<ELO>``; ``benchmarks.References`` las prefiere a las de tus propias
partidas cuando hay al menos 20 de tu rol. Al ser partidas ajenas, no tienen el sesgo de tus rivales directos.
"""

from __future__ import annotations

import random
import threading

from app.coach import store
from app.coach.analyzer import ANALYSIS_VERSION, NotInMatch, analyze_game
from app.coach.insights import reliable
from app.logbus import get_logger
from app.riot.client import Cancelled, RiotAPIError, RiotKeyError
from app.riot.ratelimit import Priority
from app.riot.routing import QUEUE_SOLO

log = get_logger("progreso")

TARGET_GAMES = 80  # partidas del elo objetivo (≈ 160 jugadores de cada rol)
BATCH_GAMES = 6  # por tanda: ≈ 13 peticiones
INTERVAL_S = 600  # entre tandas
IDS_PER_PLAYER = 2
DIVISIONS = ("I", "II", "III", "IV")
APEX = ("MASTER", "GRANDMASTER", "CHALLENGER")


def source_for(tier: str) -> str:
    return f"tier:{tier}"


class TierCalibrator:
    def __init__(self, svc, enabled: bool = True) -> None:
        self.svc = svc
        self.enabled = enabled
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cursor = 0
        self.state: dict = {"status": "idle" if enabled else "disabled", "tier": None, "games": 0, "target": TARGET_GAMES, "last_error": None}

    # ------------------------------------------------------------ hilo
    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self.stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tier-calibrator", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self.stop_event.set()

    def status(self) -> dict:
        return dict(self.state)

    def _run(self) -> None:
        self.stop_event.wait(60)  # deja arrancar lo demás primero
        while not self.stop_event.is_set():
            try:
                self.run_batch()
            except Cancelled:
                break
            except RiotKeyError as exc:
                self.state.update(status="waiting_key", last_error=exc.message)
            except RiotAPIError as exc:
                self.state.update(status="idle", last_error=exc.message)
                log.debug("Calibración de referencias: %s", exc.message)
            except Exception as exc:  # nunca tumbar el hilo
                self.state.update(status="idle", last_error=str(exc))
                log.exception("Error inesperado al calibrar las referencias con partidas de tu elo objetivo")
            self.stop_event.wait(INTERVAL_S)

    # ------------------------------------------------------------ trabajo
    def run_batch(self, games: int = BATCH_GAMES) -> int:
        """Baja y analiza hasta ``games`` partidas nuevas de tu elo objetivo. Devuelve cuántas guardó."""
        svc = self.svc
        profile = store.get_profile(svc.db)
        if not profile or not svc.keys.get():
            self.state.update(status="idle")
            return 0
        if getattr(svc.settings, "pause_in_game", True) and (getattr(svc, "tracker", None) and svc.tracker.state.get("in_game")):
            self.state.update(status="paused")
            return 0
        tier, platform = profile["target_tier"], profile["platform"]
        source = source_for(tier)
        have = store.peer_games(svc.db, source, ANALYSIS_VERSION)
        self.state.update(tier=tier, games=have)
        if have >= TARGET_GAMES:
            self.state.update(status="done")
            return 0
        self.state.update(status="running")
        kw = {"priority": Priority.LOW, "stop": self.stop_event}
        done = 0
        for puuid in self._players(platform, tier, games * 2, kw):
            done = self._sample_player(platform, puuid, source, kw, games, have, done)
            if _batch_full(done, games, have):
                break
        self.state.update(status="done" if have + done >= TARGET_GAMES else "idle", last_error=None)
        if done:
            log.info("Referencias de %s: %d partidas reales analizadas (%d de %d).", tier, done, have + done, TARGET_GAMES)
        return done

    def _sample_player(self, platform: str, puuid: str, source: str, kw: dict, games: int, have: int, done: int) -> int:
        """Analiza las partidas nuevas de un jugador hasta llenar la tanda. Devuelve las guardadas en la tanda hasta ahora."""
        svc = self.svc
        for match_id in svc.riot.match_ids(platform, puuid, queue=QUEUE_SOLO, count=IDS_PER_PLAYER, **kw):
            if store.has_peer_match(svc.db, match_id):
                continue
            if self._sample(match_id, source, kw):
                done += 1
                self.state.update(games=have + done)
            if _batch_full(done, games, have):
                break
        return done

    def _players(self, platform: str, tier: str, n: int, kw: dict) -> list[str]:
        """Jugadores de SoloQ de ese elo (rotando divisiones y páginas entre tandas)."""
        riot = self.svc.riot
        self._cursor += 1
        if tier in APEX:
            entries = (riot.apex_league(platform, tier, **kw) or {}).get("entries") or []
        else:
            division = DIVISIONS[self._cursor % len(DIVISIONS)]
            page = 1 + (self._cursor // len(DIVISIONS)) % 3
            entries = riot.league_entries(platform, tier, division, page, **kw)
        puuids = [e["puuid"] for e in entries if isinstance(e, dict) and e.get("puuid")]
        random.Random(self._cursor).shuffle(puuids)
        return puuids[:n]

    def _sample(self, match_id: str, source: str, kw: dict) -> bool:
        svc = self.svc
        if svc.db.scalar("SELECT 1 FROM my_games WHERE match_id = ?", (match_id,)):
            return False  # una partida tuya: ya cuenta como «de tus partidas»
        match = svc.riot.match(match_id, **kw)
        info = (match or {}).get("info") or {}
        if not match or int(info.get("queueId") or 0) != QUEUE_SOLO or int(info.get("mapId") or 0) != 11:
            store.save_peer_samples(svc.db, match_id, None, ANALYSIS_VERSION, [], source=source)  # marcada: no se vuelve a pedir
            return False
        timeline = svc.riot.timeline(match_id, **kw)
        rows: list[dict] = []
        if timeline:
            rows = self._peer_rows(match, timeline, info)
        start = int(info.get("gameStartTimestamp") or 0) or None
        store.save_peer_samples(svc.db, match_id, start, ANALYSIS_VERSION, rows, source=source)
        return bool(rows)

    def _peer_rows(self, match: dict, timeline: dict, info: dict) -> list[dict]:
        """Las métricas de los 10 jugadores de la partida, analizados con el mismo código que tus partidas."""
        svc = self.svc
        rows: list[dict] = []
        items = svc.tracker.items_for(match) or svc.tracker.items()
        for p in info.get("participants") or []:
            pid = int(p.get("participantId") or 0)
            if not pid:
                continue
            try:
                a = analyze_game(match, timeline, pid=pid, items=items)
            except NotInMatch:
                continue
            rows.append({"pid": pid, "role": a.get("role"), "reliable": reliable(a) and bool(a.get("role")), "metrics": a.get("metrics") or {}})
        return rows


def _batch_full(done: int, games: int, have: int) -> bool:
    """La tanda ya tiene sus ``games`` partidas o se llegó al objetivo de partidas del elo."""
    return done >= games or have + done >= TARGET_GAMES
