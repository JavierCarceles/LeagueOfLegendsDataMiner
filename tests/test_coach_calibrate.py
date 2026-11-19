"""Referencias de tu elo objetivo con partidas reales (``calibrate.TierCalibrator``) con un cliente de Riot falso."""

from __future__ import annotations

import copy

import pytest

from app.coach import store
from app.coach.analyzer import ANALYSIS_VERSION
from app.coach.benchmarks import References
from app.coach.calibrate import TARGET_GAMES, TierCalibrator, source_for
from app.riot.client import Cancelled
from tests.factories import TEST_KEY, make_match, make_timeline


class FakeLeagueRiot:
    """league-v4 con 30 jugadores, 2 partidas de SoloQ por jugador (todas distintas) y sus timelines."""

    def __init__(self, players: int = 30, queue: int = 420):
        self.calls: list[tuple] = []
        self.matches: dict[str, dict] = {}
        self.ids: dict[str, list[str]] = {}
        for i in range(players):
            puuid = f"p{i}"
            self.ids[puuid] = []
            for j in range(2):
                mid = f"EUW1_{8_000_000 + i * 10 + j}"
                self.matches[mid] = make_match(mid, queue=queue)
                self.ids[puuid].append(mid)

    def league_entries(self, platform, tier, division, page=1, queue=420, **kw):
        self.calls.append(("league", tier, division, page, kw.get("priority")))
        return [{"puuid": p, "tier": tier, "rank": division} for p in self.ids]

    def apex_league(self, platform, tier, queue=420, **kw):
        self.calls.append(("apex", tier))
        return {"entries": [{"puuid": p} for p in self.ids]}

    def match_ids(self, platform, puuid, queue=None, count=20, **kw):
        self.calls.append(("ids", puuid, queue, kw.get("priority")))
        return self.ids.get(puuid, [])[:count]

    def match(self, match_id, **kw):
        self.calls.append(("match", match_id))
        return copy.deepcopy(self.matches.get(match_id))

    def timeline(self, match_id, **kw):
        self.calls.append(("timeline", match_id))
        m = self.matches.get(match_id)
        return make_timeline(m) if m else None


@pytest.fixture
def world(svc, monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "")
    svc.keys.set(TEST_KEY)
    fake = FakeLeagueRiot()
    svc.riot = fake
    store.save_profile(svc.db, game_name="Tempu", tag_line="EUW", platform="euw1", puuid="yo", target_tier="EMERALD")
    return svc, fake


def test_batch_samples_real_games_of_the_target_tier_with_low_priority(world):
    svc, fake = world
    cal = TierCalibrator(svc)
    assert cal.run_batch(games=4) == 4
    source = source_for("EMERALD")
    assert store.peer_games(svc.db, source, ANALYSIS_VERSION) == 4
    assert svc.db.scalar("SELECT COUNT(*) FROM peer_samples WHERE source = ? AND pid > 0", (source,)) == 40  # los 10 jugadores
    assert all(c[-1] == 1 for c in fake.calls if c[0] in ("league", "ids"))  # Priority.LOW
    assert cal.status()["games"] == 4 and cal.status()["tier"] == "EMERALD"
    # La siguiente tanda no repite partidas.
    before = {c[1] for c in fake.calls if c[0] == "match"}
    cal.run_batch(games=4)
    after = [c[1] for c in fake.calls if c[0] == "match"]
    assert len(after) == len(set(after)) and len(set(after) - before) == 4


def test_references_prefer_real_games_of_the_target_tier(world):
    svc, _fake = world
    cal = TierCalibrator(svc)
    while cal.run_batch(games=10):
        if store.peer_games(svc.db, source_for("EMERALD"), ANALYSIS_VERSION) >= 25:
            break
    refs = References(svc, "EMERALD", current="PLATINUM")
    assert refs.source("TOP") == "tier" and refs.info("TOP")["tier_samples"] >= 20
    assert refs.info("TOP")["from_tier"] is None  # no se escalan: ya son de ese elo
    # Otro elo objetivo no usa esas muestras.
    assert References(svc, "DIAMOND", current="PLATINUM").source("TOP") == "static"


def test_calibration_stops_at_the_target_and_skips_what_it_must(world, monkeypatch):
    svc, fake = world
    cal = TierCalibrator(svc)
    monkeypatch.setattr("app.coach.calibrate.TARGET_GAMES", 3)
    assert cal.run_batch(games=10) == 3
    assert cal.status()["status"] == "done" and cal.run_batch() == 0
    # Mientras juegas, en pausa (no pide nada).
    monkeypatch.setattr("app.coach.calibrate.TARGET_GAMES", TARGET_GAMES)
    svc.tracker.state["in_game"] = True
    calls = len(fake.calls)
    assert cal.run_batch() == 0 and cal.status()["status"] == "paused" and len(fake.calls) == calls
    svc.tracker.state["in_game"] = False
    # Sin perfil o sin key, nada.
    store.delete_profile(svc.db)
    assert cal.run_batch() == 0


def test_other_queues_are_marked_and_not_requested_again(svc, monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "")
    svc.keys.set(TEST_KEY)
    fake = FakeLeagueRiot(players=2, queue=400)  # normales: no sirven de referencia de SoloQ
    svc.riot = fake
    store.save_profile(svc.db, game_name="Tempu", tag_line="EUW", platform="euw1", puuid="yo", target_tier="MASTER")
    cal = TierCalibrator(svc)
    assert cal.run_batch(games=5) == 0
    assert ("apex", "MASTER") in fake.calls
    assert not [c for c in fake.calls if c[0] == "timeline"]
    first = len([c for c in fake.calls if c[0] == "match"])
    cal.run_batch(games=5)
    assert len([c for c in fake.calls if c[0] == "match"]) == first  # ya marcadas


def test_disabled_calibrator_never_starts(svc):
    cal = TierCalibrator(svc, enabled=False)
    cal.start()
    assert cal._thread is None and cal.status()["status"] == "disabled"


def test_cancelled_requests_end_the_batch(world):
    svc, fake = world

    def boom(*a, **kw):
        raise Cancelled()

    fake.match_ids = boom
    with pytest.raises(Cancelled):
        TierCalibrator(svc).run_batch()


def test_rare_count_metrics_need_enough_events_to_be_calibrated(svc):
    from app.coach.benchmarks import MIN_REF_EVENTS, static_refs

    for i in range(24):  # 24 junglas con 2 muertes 1 contra 1 en total: media 0,08 muy poco fiable
        metrics = {"deaths_solo": 1 if i < 2 else 0, "deaths": 6, "cs10": 70}
        store.save_peer_samples(svc.db, f"EUW1_{i}", 1_700_000_000_000 + i, ANALYSIS_VERSION,
                                [{"pid": 7, "role": "JUNGLE", "reliable": True, "metrics": metrics}], source=source_for("EMERALD"))
    refs = References(svc, "EMERALD", current="PLATINUM")
    jungle = refs.for_role("JUNGLE")
    assert jungle["deaths_solo"] == static_refs("JUNGLE", "EMERALD")["deaths_solo"]  # 2 < MIN_REF_EVENTS: la orientativa
    assert jungle["deaths"] == 6 and jungle["cs10"] == 70  # 144 muertes: sí
    assert MIN_REF_EVENTS > 2
    assert refs.sample_size("JUNGLE", "deaths") == 24 and refs.sample_size("JUNGLE", "deaths_solo") is None


def test_reference_uncertainty_counts_in_weaknesses():
    from app.coach import insights

    # Tú 0,23 por partida en 26; referencia 0,12. Contra una referencia fija se marcaría; contra una de solo 20
    # jugadores (≈ 2-3 sucesos), su propia incertidumbre basta para no afirmarlo.
    assert insights._count_beyond(0.23, 0.08, 26, "lower")
    assert not insights._count_beyond(0.23, 0.12, 26, "lower", ref_n=20)
    assert insights._count_beyond(0.6, 0.12, 26, "lower", ref_n=200)
