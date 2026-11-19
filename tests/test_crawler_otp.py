"""Recolector por campeones: OTPs y jugadores con mucha maestría (sin red)."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from app.ingest.crawler import CrawlerConfig
from tests.factories import AATROX, DARIUS, GAREN, FakeRiotClient, make_match
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
    # la descartada queda recordada solo para ese OTP (con quién jugó), no para todo el programa
    assert keyed_svc.db.scalar("SELECT champ FROM otp_skips WHERE puuid = 'seed-0' AND match_id = 'EUW1_1002'") == GAREN
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM skipped_matches WHERE match_id = 'EUW1_1002'") == 0


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


# ------------------------------------------------------------------ historial completo de los OTPs
from app.data.db import now_s as _now_s  # noqa: E402
from app.ingest.crawler import RegionWorker, otp_history_state, otp_list_key, season_start  # noqa: E402


class HistoryRiot(FakeRiotClient):
    """Riot falso con historiales largos: filtra por ``startTime``/``endTime`` (s) y pagina como match-v5."""

    def __init__(self, ladder, payloads, mastery):
        super().__init__(ladder, payloads)
        self.mastery = mastery

    def top_masteries(self, platform, puuid, count=3, **kw):
        self._record("top_masteries", platform, puuid)
        last = _now_s() * 1000
        return [{"championId": c, "championPoints": p, "lastPlayTime": last} for c, p in self.mastery.get(puuid, [])][:count]

    def account_by_puuid(self, puuid, platform, **kw):
        self._record("account_by_puuid", puuid)
        return {"puuid": puuid, "gameName": f"OTP {puuid}", "tagLine": "EUW"}

    def match_ids(self, platform, puuid, *, queue=None, ranked_only=False, start_time=None, end_time=None, start=0, count=20, **kw):
        assert 1 <= count <= 100, "Riot no da más de 100 ids por petición"
        self._record("ids", puuid, start_time, end_time, start, count)
        ids = []
        for match_id in self.ladder.get(puuid, []):
            payload = self.payloads.get(match_id)
            begin = (payload["info"]["gameStartTimestamp"] // 1000) if payload else 0
            if start_time and begin < start_time:
                continue
            if end_time and begin > end_time:
                continue
            ids.append(match_id)
        return ids[start : start + count]


def history(puuid, n, *, prefix, every_h=8, champ_for=None, newest_s=None):
    """``n`` partidas de ``puuid`` (la primera, la más reciente), una cada ``every_h`` horas."""
    newest_s = newest_s or _now_s() - 3 * 3600  # antes del ancla (que va 2 h atrás): todo es historial
    ids, payloads = [], {}
    for i in range(n):
        match_id = f"EUW1_{prefix}{i:04d}"
        champ = champ_for(i) if champ_for else AATROX
        payloads[match_id] = make_match(
            match_id, puuids={1: puuid}, blue={"TOP": champ}, game_start=(newest_s - i * every_h * 3600) * 1000
        )
        ids.append(match_id)
    return ids, payloads


def run_history(svc, fake, timeout=30, **cfg):
    svc.riot = fake
    svc.crawler.workers.clear()
    svc.crawler.start(CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], **cfg))
    worker = svc.crawler.workers["europe"]
    assert wait_until(lambda: worker.state["status"] == "idle", timeout), worker.state
    svc.crawler.stop()
    worker.join(timeout=5)
    return worker


def progress(svc, puuid):
    row = svc.db.one("SELECT * FROM otp_progress WHERE puuid = ?", (puuid,))
    return dict(row) if row else None


def test_otp_history_has_no_100_game_cap(keyed_svc):
    ids, payloads = history("otp-a", 250, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000), (GAREN, 50_000)]})
    worker = run_history(keyed_svc, fake, champions=[AATROX], only_target_games=False)
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM matches") == 250  # antes: 100 como mucho (y solo 60 días)
    pages = [c for c in fake.calls_to("ids") if c[3] is not None]  # historial (con endTime)
    assert [c[4] for c in pages] == [0, 100, 200]  # paginado de 100 en 100 hacia atrás
    assert len({c[3] for c in pages}) == 1  # mismo endTime (ancla): la lista no se mueve
    forward = [c for c in fake.calls_to("ids") if c[3] is None]
    assert all(c[2] >= pages[0][3] for c in forward)  # «partidas nuevas»: solo lo posterior al ancla
    state = progress(keyed_svc, "otp-a")
    assert state["back_offset"] == 250 and state["back_done_reason"] == "end" and state["back_done_at"]
    assert state["seen"] == 250 and state["hits"] == 250
    assert worker.state["otps_known"] == 1 and worker.state["otps_complete"] == 1
    assert max(Counter(c[1] for c in fake.calls_to("match")).values()) == 1  # nada se descarga dos veces


def test_otp_history_respects_depth(keyed_svc):
    ids, payloads = history("otp-a", 150, prefix="1", every_h=24)  # 150 días de historial
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], otp_depth="90")
    stored = keyed_svc.db.scalar("SELECT COUNT(*) FROM matches")
    assert 88 <= stored <= 91  # solo los últimos 90 días
    starts = {c[2] for c in fake.calls_to("ids") if c[3] is not None}
    assert len(starts) == 1 and abs(starts.pop() - (_now_s() - 90 * 86400)) < 120


def test_otp_new_games_after_history_come_from_forward(keyed_svc):
    ids, payloads = history("otp-a", 30, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX])
    anchor = progress(keyed_svc, "otp-a")["anchor"]
    new_ids, new_payloads = history("otp-a", 3, prefix="2", every_h=1, newest_s=anchor + 4 * 3600)
    fake2 = HistoryRiot({"otp-a": new_ids + ids}, {**payloads, **new_payloads}, {"otp-a": [(AATROX, 900_000)]})
    with keyed_svc.db.write() as conn:  # ya toca revisitarlo (recrawl_hours)
        conn.execute("UPDATE players SET last_crawled_at = ? WHERE puuid = 'otp-a'", (_now_s() - 13 * 3600,))
    run_history(keyed_svc, fake2, champions=[AATROX])
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM matches") == 33
    assert sorted(c[1] for c in fake2.calls_to("match")) == sorted(new_ids)  # solo las nuevas
    calls = fake2.calls_to("ids")
    assert all(c[3] is None and c[2] >= anchor for c in calls)  # solo «partidas nuevas»: el historial ya estaba completo


def test_otp_scheduler_gives_everyone_recent_games_first(keyed_svc):
    ids_a, pay_a = history("otp-a", 250, prefix="1")
    ids_b, pay_b = history("otp-b", 250, prefix="2")
    mastery = {"otp-a": [(AATROX, 900_000)], "otp-b": [(AATROX, 800_000)]}
    fake = HistoryRiot({"otp-a": ids_a, "otp-b": ids_b}, {**pay_a, **pay_b}, mastery)
    run_history(keyed_svc, fake, timeout=60, champions=[AATROX])
    order = [c[1] for c in fake.calls_to("match")]
    assert len(order) == 500
    owner = {m: "a" for m in ids_a} | {m: "b" for m in ids_b}
    seq = [owner[m] for m in order]
    # Las 100 más recientes de los dos antes de que ninguno pase de 200.
    third_a = [i for i, who in enumerate(seq) if who == "a"][200]
    third_b = [i for i, who in enumerate(seq) if who == "b"][200]
    last_first_page = max([i for i, who in enumerate(seq) if who == "a"][99], [i for i, who in enumerate(seq) if who == "b"][99])
    assert min(third_a, third_b) > last_first_page


def test_otp_history_stops_when_they_no_longer_played_the_champion(keyed_svc):
    ids, payloads = history("otp-a", 500, prefix="1", champ_for=lambda i: AATROX if i < 40 else GAREN, every_h=4)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, timeout=60, champions=[AATROX], only_target_games=True)
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM matches") == 40
    state = progress(keyed_svc, "otp-a")
    # Dos páginas seguidas casi sin Aatrox, pasadas sus 200 más recientes: ahí para (no se piden las dos últimas).
    assert state["back_done_reason"] == "share" and state["back_offset"] == 300
    assert [c[4] for c in fake.calls_to("ids") if c[3] is not None] == [0, 100, 200]


def test_a_recent_break_from_the_champion_does_not_stop_the_history(keyed_svc):
    """Sus últimas 100 con otro campeón (de descanso de Aatrox) y las 150 anteriores con Aatrox: se bajan todas."""
    ids, payloads = history("otp-a", 250, prefix="1", champ_for=lambda i: GAREN if i < 100 else AATROX, every_h=3)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, timeout=60, champions=[AATROX], only_target_games=True)
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM matches") == 150
    assert progress(keyed_svc, "otp-a")["back_done_reason"] == "end"


def test_changing_the_filter_fetches_the_games_skipped_for_that_otp(keyed_svc):
    ids, payloads = history("otp-a", 20, prefix="1", champ_for=lambda i: AATROX if i % 2 else GAREN)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], only_target_games=True)
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM matches") == 10
    fake2 = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake2, champions=[AATROX], only_target_games=False)
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM matches") == 20  # ahora también las de Garen
    assert len(fake2.calls_to("match")) == 10  # y solo esas se vuelven a pedir


def test_season_depth_stops_at_previous_season_patch(keyed_svc, monkeypatch):
    keyed_svc.dd.version = "16.22.1"
    ids, payloads = history("otp-a", 20, prefix="1")
    # Las 5 más antiguas son del parche 15.24 (temporada anterior) aunque caigan después del 1 de enero.
    for match_id in ids[15:]:
        payloads[match_id]["info"]["gameVersion"] = "15.24.1.1"
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX])
    assert len(fake.calls_to("match")) == 16  # la primera de la temporada anterior se guarda y ahí se para
    assert progress(keyed_svc, "otp-a")["back_done_reason"] == "end"


def test_otps_are_found_even_with_many_checked_players_that_are_not(keyed_svc):
    """Antes solo se miraban 300 jugadores revisados: con más, los OTPs ya visitados no volvían a visitarse."""
    now = _now_s()
    db = keyed_svc.db
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], champions=[AATROX], otp_depth="all").validated()
    with db.write() as conn:
        for i in range(400):
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', 'CHALLENGER', 1, ?)", (f"x{i}", 900))
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (f"x{i}", now, json.dumps([[24, 900_000, now]])))
        conn.execute(
            "INSERT INTO players (puuid, platform, tier, is_seed, lp, last_crawled_at) VALUES ('otp-a', 'euw1', 'CHALLENGER', 1, 100, ?)",
            (now - 13 * 3600,),
        )
        conn.execute("INSERT INTO mastery_checks VALUES ('otp-a', 'euw1', ?, ?)", (now, json.dumps([[AATROX, 900_000, now]])))
        conn.execute(
            "INSERT INTO otp_progress (puuid, list_key, anchor, back_offset, back_done_at, back_done_start, back_done_reason, updated_at) "
            "VALUES ('otp-a', ?, ?, 50, ?, 0, 'end', ?)",
            (otp_list_key(cfg), now - 86400, now - 86400, now - 86400),
        )
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    target = worker._next_otp()
    assert target is not None and target["puuid"] == "otp-a"  # le tocan sus partidas nuevas
    assert worker.state["otps_known"] == 1 and worker.state["otps_complete"] == 1


def test_game_of_another_wanted_otp_is_kept(keyed_svc):
    """La OTP de Aatrox jugó Garen, pero en esa partida un OTP de Darius (también buscado) jugó Darius: se guarda."""
    now = _now_s()
    with keyed_svc.db.write() as conn:
        conn.execute("INSERT INTO mastery_checks VALUES ('otp-darius', 'euw1', ?, ?)", (now, json.dumps([[DARIUS, 900_000, now]])))
    payload = make_match("EUW1_9001", puuids={1: "otp-a", 6: "otp-darius"}, blue={"TOP": GAREN})
    keyed_svc.riot = HistoryRiot({}, {"EUW1_9001": payload}, {})
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], champions=[AATROX, DARIUS], only_target_games=True).validated()
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    got = worker._ingest("EUW1_9001", {"id": None, "puuid": "otp-a", "tier": "CHALLENGER"})
    assert got.result == "stored" and got.valid and not got.mine_target
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM skipped_matches") == 0


def test_match_specific_error_does_not_block_the_history(keyed_svc):
    from app.riot.client import RiotAPIError

    class Broken(HistoryRiot):
        def match(self, match_id, **kw):
            if match_id == "EUW1_10002":
                raise RiotAPIError(403, "Acceso denegado a match-v5.match")
            return super().match(match_id, **kw)

    ids, payloads = history("otp-a", 5, prefix="1")
    fake = Broken({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX])
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM matches") == 4
    assert keyed_svc.db.scalar("SELECT reason FROM skipped_matches WHERE match_id = 'EUW1_10002'") == "error 403"
    assert progress(keyed_svc, "otp-a")["back_done_reason"] == "end"


def test_history_state_reopens_for_deeper_depth_and_queue_change():
    base = {"anchor": 100, "list_key": "420", "back_done_at": 200, "back_done_start": 1_767_225_600, "back_done_reason": "end", "config_key": "266|1"}
    assert otp_history_state(base, "420", 1_767_225_600) == "done"
    assert otp_history_state(base, "420", 0) == "loading"  # de «temporada» a «todo lo disponible»: sigue bajando
    assert otp_history_state(base, "ranked", 1_767_225_600) == "queued"  # con Flex la lista es otra
    assert otp_history_state({**base, "back_done_reason": "share"}, "420", 0) == "share"
    assert otp_history_state({**base, "anchor": None}, "420", 0) == "queued"
    assert otp_history_state({**base, "back_done_at": None}, "420", 0) == "loading"


def test_season_start_from_patch_numbering():
    jan_2026, jan_2025 = 1_767_225_600, 1_735_689_600
    now = jan_2026 + 300 * 86400
    assert season_start("16.22.1", now) == (jan_2026, 16)
    assert season_start("15.24.1", now) == (jan_2025, 15)  # aún sin el parche 16.1: sigue la temporada anterior
    assert season_start(None, now) == (jan_2026, None)
    assert season_start("99.1.1", now) == (jan_2026, None)  # versión rara: año en curso


def test_validated_otp_depth():
    assert CrawlerConfig(otp_depth="all").validated().otp_depth == "all"
    assert CrawlerConfig(otp_depth=365).validated().otp_depth == "365"
    assert CrawlerConfig(otp_depth="bogus").validated().otp_depth == "season"


def test_otps_endpoint_reports_totals_and_history(keyed_svc):
    from fastapi.testclient import TestClient

    from app.main import create_app

    ids, payloads = history("otp-a", 130, prefix="1", champ_for=lambda i: AATROX if i % 10 else GAREN)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], only_target_games=True)
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    with TestClient(app) as client:
        data = client.get("/api/crawler/otps").json()
    assert data["found"] == 1 and data["complete"] == 1 and data["otp_depth"] == "season" and data["games"] == 117
    otp = data["players"][0]
    assert otp["total"] == 117 and otp["history"] == "done" and otp["history_checked"] == 130
    assert otp["share"] == 0.9 and len(otp["games"]) == 8 and otp["game_name"]  # Riot ID de account-v1 o de la partida


def test_otps_endpoint_only_lists_the_selected_servers_and_tiers(keyed_svc):
    """Un OTP de otro servidor (de una búsqueda anterior) no se visitará con esta configuración: no se muestra."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    now = _now_s()
    with keyed_svc.db.write() as conn:
        for puuid, platform in (("otp-euw", "euw1"), ("otp-na", "na1")):
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, ?, 'CHALLENGER', 1, 1000)", (puuid, platform))
            conn.execute("INSERT INTO mastery_checks VALUES (?, ?, ?, ?)", (puuid, platform, now, json.dumps([[AATROX, 900_000, now]])))
    keyed_svc.crawler.config = CrawlerConfig(platforms=["na1"], tiers=["CHALLENGER"], champions=[AATROX]).validated()
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    with TestClient(app) as client:
        data = client.get("/api/crawler/otps").json()
    assert [p["platform"] for p in data["players"]] == ["na1"] and data["players"][0]["history"] == "queued"
