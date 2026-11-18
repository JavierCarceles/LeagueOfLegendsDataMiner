"""app/ingest/crawler.py con un cliente de Riot falso (sin red).

Mundo simulado (una plataforma, ``euw1``):

* 3 semillas de la liga Challenger;
* 9 partidas válidas (EUW1_1004 la comparten dos semillas), 1 remake, 1 ARAM
  y 1 id que devuelve 404.
"""

from __future__ import annotations

import time
from collections import Counter

import pytest

from app.data.db import now_s
from app.ingest.crawler import CrawlerConfig
from tests.factories import FakeRiotClient, TEST_KEY, build_services, make_match

SEEDS = ["seed-0", "seed-1", "seed-2"]


def build_world() -> tuple[dict, dict]:
    ladder = {
        "seed-0": ["EUW1_1004", "EUW1_1003", "EUW1_1002", "EUW1_1001"],
        "seed-1": ["EUW1_2003", "EUW1_2002", "EUW1_2001", "EUW1_1004"],  # A4 compartida
        "seed-2": ["EUW1_3404", "EUW1_3303", "EUW1_3202", "EUW1_3101", "EUW1_3001"],
    }
    payloads: dict = {}
    start = 1_763_000_000_000
    normal = ["EUW1_1001", "EUW1_1002", "EUW1_1003", "EUW1_1004", "EUW1_2001", "EUW1_2002", "EUW1_2003", "EUW1_3001", "EUW1_3101"]
    for i, match_id in enumerate(normal):
        seed = next(s for s, ids in ladder.items() if match_id in ids)
        puuids = {1: seed}
        if match_id == "EUW1_1004":
            puuids = {1: "seed-0", 6: "seed-1"}
        payloads[match_id] = make_match(match_id, puuids=puuids, blue_win=i % 2 == 0, game_start=start + i * 3_600_000)
    payloads["EUW1_3202"] = make_match("EUW1_3202", puuids={1: "seed-2"}, duration=190, game_start=start + 20 * 3_600_000)
    payloads["EUW1_3303"] = make_match("EUW1_3303", puuids={1: "seed-2"}, map_id=12, queue=450, game_start=start + 21 * 3_600_000)
    payloads["EUW1_3404"] = None  # 404
    return ladder, payloads


def wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def keyed_svc(tmp_path, monkeypatch):
    services = build_services(tmp_path, monkeypatch, key=TEST_KEY)
    yield services
    services.shutdown()
    for worker in services.crawler.workers.values():
        worker.join(timeout=5)
    services.db.close()


def run_crawler(svc, fake, tiers):
    svc.riot = fake
    status = svc.crawler.start(CrawlerConfig(platforms=["euw1"], tiers=tiers))
    assert status["running"] is True
    worker = svc.crawler.workers["europe"]
    # Cuando ya no quedan semillas por recorrer el hilo se queda "idle".
    assert wait_until(lambda: worker.state["status"] == "idle"), worker.state
    svc.crawler.stop()
    worker.join(timeout=5)
    assert not worker.is_alive()
    return worker


def test_crawler_stores_matches_and_seeds(keyed_svc):
    svc = keyed_svc
    assert svc.keys.get() == TEST_KEY and svc.keys.status != "invalid"
    ladder, payloads = build_world()
    fake = FakeRiotClient(ladder, payloads)
    since = now_s() - 14 * 86400
    worker = run_crawler(svc, fake, ["CHALLENGER"])
    db = svc.db

    # Semillas de la liga apex.
    seeds = db.query("SELECT puuid, platform, tier, division, lp, last_crawled_at, newest_match_at FROM players WHERE is_seed = 1")
    assert sorted(r["puuid"] for r in seeds) == SEEDS
    assert all(r["platform"] == "euw1" and r["tier"] == "CHALLENGER" and r["division"] == "I" for r in seeds)
    assert all(r["last_crawled_at"] and r["newest_match_at"] for r in seeds)
    assert db.scalar("SELECT COUNT(*) FROM seed_pages WHERE platform = 'euw1' AND tier = 'CHALLENGER'") == 1
    assert fake.calls_to("apex_league")[0][1:3] == ("euw1", "CHALLENGER")

    # Partidas guardadas (A4 una sola vez) y agregadas.
    assert db.scalar("SELECT COUNT(*) FROM matches WHERE is_remake = 0") == 9
    assert db.scalar("SELECT COUNT(*) FROM matches WHERE aggregated = 1") == 9
    assert db.scalar("SELECT COUNT(*) FROM matches WHERE is_remake = 1") == 1
    assert db.scalar("SELECT COUNT(*) FROM participants") == 90
    assert db.scalar("SELECT matches FROM agg_totals") == 9
    assert {r[0] for r in db.query("SELECT DISTINCT tier FROM matches")} == {"CHALLENGER"}
    skipped = {r["match_id"]: r["reason"] for r in db.query("SELECT match_id, reason FROM skipped_matches")}
    assert set(skipped) == {"EUW1_3303", "EUW1_3404"}
    assert skipped["EUW1_3404"] == "404"

    # Cada partida se pide como mucho una vez (deduplicación antes de descargar).
    fetched = Counter(call[1] for call in fake.calls_to("match"))
    assert max(fetched.values()) == 1
    assert set(fetched) == set(payloads)

    # Solo ranked SoloQ y ventana de `days` días.
    for _, platform, puuid, queue, ranked_only, start_time, count in fake.calls_to("match_ids"):
        assert platform == "euw1" and puuid in SEEDS
        assert queue == 420 and ranked_only is False and count == 20
        assert abs(start_time - since) <= 60

    assert worker.state["stored"] == 9 and svc.crawler.total_stored == 9
    assert worker.state["remakes"] == 1 and worker.state["ignored"] == 1
    assert worker.state["seeds_added"] == 3 and worker.state["players"] == 3
    status = svc.crawler.status()
    assert status["running"] is False and status["stored"] == 9


def test_crawler_uses_league_pages_below_master(keyed_svc):
    svc = keyed_svc
    ladder, payloads = build_world()
    fake = FakeRiotClient(ladder, payloads)
    run_crawler(svc, fake, ["DIAMOND"])
    first = fake.calls_to("league_entries")[0]
    assert first[1:] == ("euw1", "DIAMOND", "I", 1)
    rows = svc.db.query("SELECT tier, division FROM players WHERE is_seed = 1")
    assert len(rows) == 3 and all((r["tier"], r["division"]) == ("DIAMOND", "I") for r in rows)
    pages = {r["division"]: dict(r) for r in svc.db.query("SELECT * FROM seed_pages WHERE tier = 'DIAMOND'")}
    assert pages["I"]["next_page"] == 2 and pages["I"]["exhausted_at"] is None
    assert svc.db.scalar("SELECT COUNT(*) FROM matches WHERE aggregated = 1") == 9


def test_crawler_resumes_incrementally(keyed_svc):
    svc = keyed_svc
    ladder, payloads = build_world()
    run_crawler(svc, FakeRiotClient(ladder, payloads), ["CHALLENGER"])
    newest = {r["puuid"]: r["newest_match_at"] for r in svc.db.query("SELECT puuid, newest_match_at FROM players WHERE is_seed = 1")}
    # Segunda pasada: los jugadores ya recorridos piden solo partidas posteriores a la última vista.
    with svc.db.write() as conn:
        conn.execute("UPDATE players SET last_crawled_at = NULL WHERE is_seed = 1")
    svc.crawler.workers.clear()
    second = FakeRiotClient(ladder, payloads)
    run_crawler(svc, second, ["CHALLENGER"])
    calls = {call[2]: call[5] for call in second.calls_to("match_ids")}
    assert set(calls) == set(SEEDS)
    for puuid, start_time in calls.items():
        assert start_time == newest[puuid] // 1000 + 1
    assert second.calls_to("match") == []  # todo era ya conocido
    assert svc.db.scalar("SELECT COUNT(*) FROM matches WHERE aggregated = 1") == 9


def test_crawler_refuses_to_start_without_key(svc):
    assert svc.keys.get() == ""
    with pytest.raises(PermissionError):
        svc.crawler.start(CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"]))
    assert not svc.crawler.running


def test_crawler_config_validation():
    cfg = CrawlerConfig(platforms=["EUW1", "kr", "xx1", "euw1"], tiers=["challenger", "gold", "bogus"], days=999, matches_per_player=1, recrawl_hours=0).validated()
    assert cfg.platforms == ["euw1", "kr"]
    assert cfg.tiers == ["GOLD", "CHALLENGER"]
    assert (cfg.days, cfg.matches_per_player, cfg.recrawl_hours) == (60, 5, 1.0)
    with pytest.raises(ValueError):
        CrawlerConfig(platforms=["xx1"]).validated()
    with pytest.raises(ValueError):
        CrawlerConfig(tiers=["bogus"]).validated()
