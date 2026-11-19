"""Recolector por campeones: OTPs y jugadores con mucha maestría (sin red)."""

from __future__ import annotations

import pytest

from app.ingest.crawler import CrawlerConfig
from tests.factories import AATROX, GAREN, FakeRiotClient, make_match
from tests.test_crawler import keyed_svc, wait_until  # noqa: F401  (fixture reutilizada)

START = 1_763_000_000_000
MASTERY = {
    "seed-0": [[AATROX, 900_000], [GAREN, 100_000]],  # OTP de Aatrox
    "seed-1": [[GAREN, 2_000_000], [AATROX, 400_000]],  # mucha maestría con Aatrox, pero su main es Garen
    "seed-2": [[24, 500_000]],  # nada que ver
}


class MasteryRiot(FakeRiotClient):
    def top_masteries(self, platform, puuid, count=3, **kw):
        self._record("top_masteries", platform, puuid)
        return [{"championId": c, "championPoints": p} for c, p in MASTERY.get(puuid, [])][:count]

    def account_by_puuid(self, puuid, platform, **kw):
        self._record("account_by_puuid", puuid)
        return {"puuid": puuid, "gameName": f"OTP {puuid}", "tagLine": "KR1"}


def world():
    ladder = {
        "seed-0": ["EUW1_1003", "EUW1_1002", "EUW1_1001"],
        "seed-1": ["EUW1_2002", "EUW1_2001"],
        "seed-2": ["EUW1_3001"],
    }
    payloads = {
        "EUW1_1001": make_match("EUW1_1001", puuids={1: "seed-0"}, game_start=START),
        "EUW1_1002": make_match("EUW1_1002", puuids={1: "seed-0"}, blue={"TOP": GAREN}, game_start=START + 3_600_000),
        "EUW1_1003": make_match("EUW1_1003", puuids={1: "seed-0"}, game_start=START + 7_200_000),
        "EUW1_2001": make_match("EUW1_2001", puuids={1: "seed-1"}, game_start=START + 10_800_000),
        "EUW1_2002": make_match("EUW1_2002", puuids={1: "seed-1"}, blue={"TOP": GAREN}, game_start=START + 14_400_000),
        "EUW1_3001": make_match("EUW1_3001", puuids={1: "seed-2"}, game_start=START + 18_000_000),
    }
    return ladder, payloads


def run(svc, **cfg):
    ladder, payloads = world()
    fake = MasteryRiot(ladder, payloads)
    svc.riot = fake
    svc.crawler.start(CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], days=60, **cfg))
    worker = svc.crawler.workers["europe"]
    assert wait_until(lambda: worker.state["status"] == "idle"), worker.state
    svc.crawler.stop()
    worker.join(timeout=5)
    return worker, fake


def stored(svc):
    return {r[0] for r in svc.db.query("SELECT match_id FROM matches")}


def test_qualifies_otp_vs_mastery():
    otp = CrawlerConfig(champions=[AATROX], focus="otp", min_mastery=300_000).validated()
    mastery = CrawlerConfig(champions=[AATROX], focus="mastery", min_mastery=300_000).validated()
    assert otp.qualifies(MASTERY["seed-0"]) and mastery.qualifies(MASTERY["seed-0"])
    assert not otp.qualifies(MASTERY["seed-1"]) and mastery.qualifies(MASTERY["seed-1"])
    assert not otp.qualifies(MASTERY["seed-2"]) and not mastery.qualifies(MASTERY["seed-2"])
    assert not otp.qualifies([[AATROX, 900_000], [GAREN, 800_000]])  # no domina: no es OTP
    assert not otp.qualifies([])


def test_validated_cleans_champion_options():
    cfg = CrawlerConfig(champions=[AATROX, AATROX, "x", -1], focus="raro", min_mastery=5).validated()
    assert cfg.champions == [AATROX] and cfg.focus == "otp" and cfg.min_mastery == 10_000


def test_otp_mode_collects_only_otp_games_with_target_champion(keyed_svc):
    worker, fake = run(keyed_svc, champions=[AATROX], focus="otp", min_mastery=300_000, only_target_games=True)
    assert stored(keyed_svc) == {"EUW1_1001", "EUW1_1003"}  # la de Garen se descarta
    assert worker.state["targets_found"] == 1
    assert worker.state["mastery_checked"] == 3
    assert worker.state["target_games"] == 2 and worker.state["other_champ_skipped"] == 1
    # no se piden partidas de quien no es OTP
    assert not [c for c in fake.calls_to("match_ids") if c[2] in ("seed-1", "seed-2")]
    # la descartada queda recordada y no se vuelve a pedir
    assert keyed_svc.db.scalar("SELECT reason FROM skipped_matches WHERE match_id = 'EUW1_1002'") == "otro campeón"


def test_mastery_mode_includes_high_mastery_players(keyed_svc):
    worker, _ = run(keyed_svc, champions=[AATROX], focus="mastery", min_mastery=300_000, only_target_games=True)
    assert stored(keyed_svc) == {"EUW1_1001", "EUW1_1003", "EUW1_2001"}
    assert worker.state["targets_found"] == 2


def test_all_games_of_otps_when_not_filtering(keyed_svc):
    run(keyed_svc, champions=[AATROX], focus="otp", min_mastery=300_000, only_target_games=False)
    assert stored(keyed_svc) == {"EUW1_1001", "EUW1_1002", "EUW1_1003"}


def test_mastery_is_remembered_between_runs(keyed_svc):
    _, fake = run(keyed_svc, champions=[AATROX], focus="otp")
    first = len(fake.calls_to("top_masteries"))
    _, fake2 = run(keyed_svc, champions=[AATROX], focus="mastery")
    assert first == 3 and len(fake2.calls_to("top_masteries")) == 0  # 7 días de caché


@pytest.mark.parametrize("value", [AATROX, "Aatrox", "266"])
def test_start_endpoint_resolves_champion_names(keyed_svc, monkeypatch, value):
    from fastapi.testclient import TestClient

    from app.main import create_app

    started = {}
    monkeypatch.setattr(keyed_svc.crawler, "start", lambda config, **kw: started.setdefault("cfg", config) and {"running": True})
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    with TestClient(app) as client:
        res = client.post("/api/crawler/start", json={"platforms": ["kr"], "tiers": ["CHALLENGER"], "champions": [value], "focus": "otp"})
        assert res.status_code == 200, res.text
    assert started["cfg"].champions == [AATROX]


def test_otp_must_still_play_the_champion():
    from app.data.db import now_s

    cfg = CrawlerConfig(champions=[AATROX], focus="otp", min_mastery=300_000).validated()
    now = now_s()
    assert cfg.qualifies([[AATROX, 900_000, now - 3 * 86400], [GAREN, 100_000, now]], now)
    assert not cfg.qualifies([[AATROX, 900_000, now - 90 * 86400], [GAREN, 100_000, now]], now)  # lo dejó hace meses
    mastery = CrawlerConfig(champions=[AATROX], focus="mastery", min_mastery=300_000).validated()
    assert not mastery.qualifies([[GAREN, 2_000_000, now], [AATROX, 400_000, now - 200 * 86400]], now)


def test_found_otps_get_their_riot_id(keyed_svc):
    _, fake = run(keyed_svc, champions=[AATROX], focus="otp")
    assert [c[1] for c in fake.calls_to("account_by_puuid")] == ["seed-0"]  # solo los OTPs, no todo el ranking
    # Tiene nombre (el de account-v1 o, si ya hay partidas guardadas, el Riot ID que trae la propia partida).
    assert keyed_svc.db.scalar("SELECT game_name FROM players WHERE puuid = 'seed-0'")
    assert keyed_svc.db.scalar("SELECT game_name FROM players WHERE puuid = 'seed-2'") is None  # no es OTP: no se gasta la llamada


def test_otps_endpoint_lists_found_players_with_both_mastery_formats(keyed_svc):
    import json

    from fastapi.testclient import TestClient

    from app.data.db import now_s
    from app.main import create_app

    run(keyed_svc, champions=[AATROX], focus="mastery", min_mastery=300_000)
    with keyed_svc.db.write() as conn:  # una fila antigua sin fecha y otra nueva con fecha
        conn.execute("UPDATE mastery_checks SET top = ? WHERE puuid = 'seed-0'", (json.dumps([[AATROX, 900_000]]),))
        conn.execute("UPDATE mastery_checks SET top = ? WHERE puuid = 'seed-1'", (json.dumps([[GAREN, 2_000_000, now_s()], [AATROX, 400_000, now_s()]]),))
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    with TestClient(app) as client:
        res = client.get("/api/crawler/otps")
        assert res.status_code == 200, res.text
        players = res.json()["players"]
    assert {p["points"] for p in players} == {900_000, 400_000}
    assert all(p["champ"] == AATROX for p in players)
