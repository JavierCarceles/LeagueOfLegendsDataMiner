"""Recolector por campeones: OTPs y jugadores con mucha maestría (sin red)."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from app.ingest.crawler import CrawlerConfig
from tests.factories import AATROX, DARIUS, GAREN, FakeRiotClient, make_match, make_timeline
from tests.test_crawler import wait_until

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
    cfg.setdefault("save_details", False)
    svc.crawler.start(CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], **cfg))
    worker = svc.crawler.workers["europe"]
    assert wait_until(lambda: worker.state["status"] == "idle"), worker.state
    svc.crawler.stop()
    worker.join(timeout=5)
    svc.crawler.wait_stopped()  # el embudo, que se escribe en segundo plano al detener, escribe en data/otps
    return worker, fake


def stored(svc):
    return {r[0] for r in svc.db.query("SELECT match_id FROM matches")}


def test_qualifies_otp_vs_mastery():
    otp = CrawlerConfig(champions=[AATROX], focus="otp", min_mastery=300_000).validated()
    mastery = CrawlerConfig(champions=[AATROX], focus="mastery", min_mastery=300_000).validated()
    # Candidatos por maestría (entre sus 3 primeros, con el mínimo): igual buscando OTPs o mucha maestría…
    assert otp.qualifies(MASTERY["seed-0"]) and mastery.qualifies(MASTERY["seed-0"])
    assert otp.qualifies(MASTERY["seed-1"]) and mastery.qualifies(MASTERY["seed-1"])
    assert not otp.qualifies(MASTERY["seed-2"]) and not mastery.qualifies(MASTERY["seed-2"])
    assert not otp.qualifies([])
    # …y especialista solo si sus partidas lo confirman: 300 K con Lee Sin y 130 K con Yuumi no hacen a nadie OTP.
    assert otp.min_share == 0.5 and mastery.min_share == 0
    assert otp.share_ok(10, 1)  # aún sin muestra: se le sigue revisando
    assert otp.share_ok(30, 15) and not otp.share_ok(30, 14)
    assert mastery.share_ok(30, 0)  # mucha maestría: da igual lo que juegue
    strict = CrawlerConfig(champions=[AATROX], focus="otp", min_share=0.75).validated()
    assert strict.min_share == 0.75 and not strict.share_ok(40, 29)
    assert CrawlerConfig(champions=[AATROX], focus="otp", min_share=0).validated().focus == "mastery"


def test_validated_cleans_champion_options():
    cfg = CrawlerConfig(champions=[AATROX, AATROX, "x", -1], focus="raro", min_mastery=5).validated()
    assert cfg.champions == [AATROX] and cfg.focus == "otp" and cfg.min_mastery == 10_000


def test_otp_mode_collects_only_otp_games_with_target_champion(keyed_svc):
    worker, fake = run(keyed_svc, champions=[AATROX], focus="otp", min_mastery=300_000, only_target_games=True)
    # seed-1 (Aatrox entre sus 3 primeros) también es candidato: con tan pocas partidas aún no se le puede descartar.
    assert stored(keyed_svc) == {"EUW1_1001", "EUW1_1003", "EUW1_2001"}  # las de Garen se descartan
    assert worker.state["targets_found"] == 2
    assert worker.state["mastery_checked"] == 3
    assert worker.state["target_games"] == 3 and worker.state["other_champ_skipped"] == 2
    # no se piden partidas de quien no tiene el campeón entre sus 3 primeros
    assert not [c for c in fake.calls_to("match_ids") if c[2] == "seed-2"]
    # Sin muestra suficiente, la carpeta de OTPs solo recibe al que lo tiene de principal con mucha ventaja.
    kinds = dict(keyed_svc.db.query("SELECT puuid, kind FROM otp_games GROUP BY puuid"))
    assert kinds.get("seed-0") in (None, "otp") and kinds.get("seed-1") in (None, "player")
    # la descartada queda recordada solo para ese OTP (con quién jugó), no para todo el programa
    assert keyed_svc.db.scalar("SELECT champ FROM otp_skips WHERE puuid = 'seed-0' AND match_id = 'EUW1_1002'") == GAREN
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM skipped_matches WHERE match_id = 'EUW1_1002'") == 0


def test_mastery_mode_includes_high_mastery_players(keyed_svc):
    worker, _ = run(keyed_svc, champions=[AATROX], focus="mastery", min_mastery=300_000, only_target_games=True)
    assert stored(keyed_svc) == {"EUW1_1001", "EUW1_1003", "EUW1_2001"}
    assert worker.state["targets_found"] == 2


def test_all_games_of_otps_when_not_filtering(keyed_svc):
    run(keyed_svc, champions=[AATROX], focus="otp", min_mastery=300_000, only_target_games=False)
    assert stored(keyed_svc) == {"EUW1_1001", "EUW1_1002", "EUW1_1003", "EUW1_2001", "EUW1_2002"}


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

    cfg = CrawlerConfig(champions=[AATROX], focus="otp", min_mastery=300_000, otp_depth="30").validated()
    now = now_s()
    assert cfg.qualifies([[AATROX, 900_000, now - 3 * 86400], [GAREN, 100_000, now]], now)
    assert not cfg.qualifies([[AATROX, 900_000, now - 90 * 86400], [GAREN, 100_000, now]], now)  # lo dejó hace meses
    mastery = CrawlerConfig(champions=[AATROX], focus="mastery", min_mastery=300_000, otp_depth="90").validated()
    assert not mastery.qualifies([[GAREN, 2_000_000, now], [AATROX, 400_000, now - 200 * 86400]], now)


def test_found_otps_get_their_riot_id_from_their_games(keyed_svc):
    _, fake = run(keyed_svc, champions=[AATROX], focus="otp")
    # El Riot ID llega con sus partidas (``store_match``): pedirlo a account-v1 gastaba el cupo regional de las partidas
    # (~100 llamadas por minuto con muchos jugadores válidos).
    assert fake.calls_to("account_by_puuid") == []
    assert keyed_svc.db.scalar("SELECT game_name FROM players WHERE puuid = 'seed-0'")
    assert keyed_svc.db.scalar("SELECT game_name FROM players WHERE puuid = 'seed-2'") is None  # no es OTP: no se visita


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
from app.data.db import now_s as _now_s
from app.ingest.crawler import RegionWorker, otp_history_state, otp_list_key, season_start


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
    cfg.setdefault("save_details", False)  # el micro/macro tiene sus propias pruebas (cambia el reparto por visita)
    svc.riot = fake
    svc.crawler.workers.clear()
    svc.crawler.start(CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], **cfg))
    worker = svc.crawler.workers["europe"]
    assert wait_until(lambda: worker.state["status"] == "idle", timeout), worker.state
    svc.crawler.stop()
    worker.join(timeout=5)
    svc.crawler.wait_stopped()  # el embudo, que se escribe en segundo plano al detener, escribe en data/otps
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
    run_history(keyed_svc, fake, timeout=60, champions=[AATROX], focus="mastery", only_target_games=True)
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM matches") == 40
    state = progress(keyed_svc, "otp-a")
    # Dos páginas seguidas casi sin Aatrox, pasadas sus 200 más recientes: ahí para (no se piden las dos últimas).
    assert state["back_done_reason"] == "share" and state["back_offset"] == 300
    assert [c[4] for c in fake.calls_to("ids") if c[3] is not None] == [0, 100, 200]


def test_a_recent_break_from_the_champion_does_not_stop_the_history(keyed_svc):
    """Sus últimas 100 con otro campeón (de descanso de Aatrox) y las 150 anteriores con Aatrox: se bajan todas."""
    ids, payloads = history("otp-a", 250, prefix="1", champ_for=lambda i: GAREN if i < 100 else AATROX, every_h=3)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, timeout=60, champions=[AATROX], focus="mastery", only_target_games=True)
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM matches") == 150
    assert progress(keyed_svc, "otp-a")["back_done_reason"] == "end"


def test_specialists_are_checked_with_their_own_games(keyed_svc):
    """Buscando OTPs (la mitad de sus partidas con el campeón): con 900 K de maestría pero 1 de cada 5 partidas con
    Aatrox, tras 20 revisadas se le deja de seguir; quien de verdad lo juega sigue hasta el final."""
    ids_a, pay_a = history("casual", 200, prefix="1", champ_for=lambda i: AATROX if i % 5 == 0 else GAREN, every_h=3)
    ids_b, pay_b = history("main", 60, prefix="2", champ_for=lambda i: AATROX if i % 4 else GAREN, every_h=3)
    fake = HistoryRiot({"casual": ids_a, "main": ids_b}, {**pay_a, **pay_b}, {"casual": [(AATROX, 900_000)], "main": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, timeout=60, champions=[AATROX], focus="otp", only_target_games=True)
    casual, main = progress(keyed_svc, "casual"), progress(keyed_svc, "main")
    assert casual["back_done_reason"] == "share" and casual["seen"] == 20 and casual["hits"] == 4
    assert main["back_done_reason"] == "end" and main["hits"] == 45
    # Sus partidas con Aatrox no ensucian la carpeta de OTPs: con muestra, OTP = lo que juega.
    assert otp_details.classify(keyed_svc.db, "casual", AATROX) == "player"
    assert otp_details.classify(keyed_svc.db, "main", AATROX) == "otp"
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], champions=[AATROX], focus="otp").validated()
    assert [r["puuid"] for r, _ in otp_rows(keyed_svc.db, cfg)] == ["main"]  # ya no cuenta como encontrado


def test_changing_the_filter_fetches_the_games_skipped_for_that_otp(keyed_svc):
    ids, payloads = history("otp-a", 20, prefix="1", champ_for=lambda i: AATROX if i % 2 else GAREN)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], focus="mastery", only_target_games=True)
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM matches") == 10
    fake2 = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake2, champions=[AATROX], focus="mastery", only_target_games=False)
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
    # La primera de la temporada anterior se guarda y ahí se para (la siguiente puede haberse descargado ya mientras se
    # analizaba esa: se descarta).
    assert 16 <= len(fake.calls_to("match")) <= 16 + crawler_mod.PREFETCH_AHEAD
    assert len(stored(keyed_svc)) == 16
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


def test_el_resumen_de_datos_y_api_sale_de_la_cola_sin_contar_partidas_por_jugador(keyed_svc, monkeypatch):
    """«Datos y API» solo enseña los totales: antes cada petición contaba las partidas de cada jugador con cada campeón
    (~26 s con 30.000 jugadores, pedida cada ~10 s). El resumen sale de la cola del recolector y del embudo."""
    from fastapi.testclient import TestClient

    from app.api import collector
    from app.main import create_app

    ids, payloads = history("otp-a", 130, prefix="1", champ_for=lambda i: AATROX if i % 10 else GAREN)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], only_target_games=True)
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc

    def heavy(*_a, **_k):
        raise AssertionError("el resumen no debe contar las partidas de cada jugador")

    monkeypatch.setattr(collector, "_otp_entries", heavy)
    with TestClient(app) as client:
        data = client.get("/api/crawler/otps", params={"summary": 1}).json()
    assert data["found"] == 1 and data["complete"] == 1 and data["games"] == 117 and data["players"] == []


def test_el_embudo_de_la_busqueda_se_rehace_cada_30_min(keyed_svc, monkeypatch):
    """Lee todas las maestrías revisadas (hasta 16-25 s en Windows): cada 30 min, no cada 5."""
    import time

    from app.ingest import crawler as crawler_mod

    calls = []
    monkeypatch.setattr(crawler_mod, "search_funnel", lambda db, cfg, now=None: calls.append(1) or {"stages": [], "total": {"active": 5}})
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["GOLD"], champions=[AATROX]).validated()
    crawler = keyed_svc.crawler
    assert crawler._search_funnel(cfg)["total"]["active"] == 5
    crawler._search_funnel(cfg)
    assert len(calls) == 1
    crawler._search_funnel_cache = (crawler._search_funnel_cache[0], time.time() - crawler_mod.SEARCH_FUNNEL_EVERY_S - 1,
                                    crawler._search_funnel_cache[2])
    crawler._search_funnel(cfg)
    assert len(calls) == 2


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


# ------------------------------------------------------------------ rol de cada campeón
from app.ingest.crawler import target_sql

ROLES_ALL = ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")


def role_history(puuid, n, *, prefix, role_for):
    """Como ``history`` pero el jugador (pid 1, Aatrox) juega en el rol que diga ``role_for(i)``."""
    ids, payloads = history(puuid, n, prefix=prefix)
    pid_of = {"TOP": 1, "JUNGLE": 2, "MID": 3, "ADC": 4, "SUPPORT": 5}
    for i, match_id in enumerate(ids):
        role = role_for(i)
        if role != "TOP":  # se intercambia la posición con quien la tenía: los roles siguen siendo únicos
            positions = {1: {"JUNGLE": "JUNGLE", "MID": "MIDDLE", "ADC": "BOTTOM", "SUPPORT": "UTILITY"}[role], pid_of[role]: "TOP"}
            payloads[match_id] = make_match(
                match_id, puuids={1: puuid}, positions=positions, game_start=payloads[match_id]["info"]["gameStartTimestamp"]
            )
    return ids, payloads


def test_validated_cleans_champion_roles():
    cfg = CrawlerConfig(
        champions=[AATROX, GAREN],
        champion_roles={str(AATROX): ["mid", "JUNGLE", "JUNGLE", "raro"], GAREN: list(ROLES_ALL), 999: ["TOP"]},
    ).validated()
    assert cfg.champion_roles == {AATROX: ["JUNGLE", "MID"]}  # orden de los roles; los 5 = cualquiera; 999 no se busca
    assert cfg.is_target(AATROX, "MID") and not cfg.is_target(AATROX, "TOP") and not cfg.is_target(AATROX, None)
    assert cfg.is_target(GAREN, "SUPPORT") and cfg.is_target(GAREN, None) and not cfg.is_target(DARIUS, "TOP")



def test_roles_change_the_history_key_only_when_set():
    base = CrawlerConfig(champions=[AATROX]).validated()
    roled = CrawlerConfig(champions=[AATROX], champion_roles={AATROX: ["JUNGLE"]}).validated()
    assert otp_list_key(base) == f"420|{AATROX}|1"  # sin roles, la clave de siempre: no se rehace el historial
    assert otp_list_key(roled) != otp_list_key(base)
    sql, params = target_sql(CrawlerConfig(champions=[AATROX, GAREN], champion_roles={AATROX: ["JUNGLE", "MID"]}).validated(), "pa")
    assert sql == "(pa.champ IN (?) OR (pa.champ IN (?) AND pa.role IN (?,?)))" and params == [GAREN, AATROX, "JUNGLE", "MID"]


def test_only_games_in_the_chosen_role_are_kept(keyed_svc):
    ids, payloads = role_history("otp-a", 12, prefix="1", role_for=lambda i: "JUNGLE" if i % 3 == 0 else "TOP")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    worker = run_history(keyed_svc, fake, champions=[AATROX], champion_roles={AATROX: ["JUNGLE"]}, only_target_games=True)
    assert stored(keyed_svc) == {ids[i] for i in range(12) if i % 3 == 0}
    assert keyed_svc.db.scalar("SELECT COUNT(*) FROM participants WHERE champ = ? AND role = 'JUNGLE'", (AATROX,)) == 4
    assert worker.state["other_champ_skipped"] == 8
    # el descarte recuerda campeón y rol: con el mismo campeón en otro rol ya no se vuelve a pedir
    assert keyed_svc.db.scalar("SELECT role FROM otp_skips WHERE puuid = 'otp-a' AND match_id = ?", (ids[1],)) == "TOP"
    assert progress(keyed_svc, "otp-a")["hits"] == 4

    # Añadir top: se piden solo las descartadas por el rol (lo guardado no se vuelve a descargar).
    fake2 = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake2, champions=[AATROX], champion_roles={AATROX: ["JUNGLE", "TOP"]}, only_target_games=True)
    assert stored(keyed_svc) == set(ids)
    assert len(fake2.calls_to("match")) == 8


def test_old_skips_without_role_are_checked_again_for_a_role_search(keyed_svc):
    ids, payloads = role_history("otp-a", 4, prefix="1", role_for=lambda i: "JUNGLE")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], CrawlerConfig(champions=[AATROX], champion_roles={AATROX: ["JUNGLE"]}).validated())
    keyed_svc.riot = fake
    with keyed_svc.db.write() as conn:
        conn.execute("INSERT INTO otp_skips (puuid, match_id, champ, role) VALUES ('otp-a', ?, ?, NULL)", (ids[0], AATROX))
        conn.execute("INSERT INTO otp_skips (puuid, match_id, champ, role) VALUES ('otp-a', ?, ?, 'TOP')", (ids[1], AATROX))
        conn.execute("INSERT INTO otp_skips (puuid, match_id, champ, role) VALUES ('otp-a', ?, ?, NULL)", (ids[2], GAREN))
    assert worker._known_for("otp-a", ids) == {ids[1], ids[2]}  # el de antes de los roles con Aatrox se vuelve a mirar


def test_start_endpoint_resolves_champion_roles(keyed_svc, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    started = {}
    monkeypatch.setattr(keyed_svc.crawler, "start", lambda config, **kw: started.setdefault("cfg", config) and {"running": True})
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    with TestClient(app) as client:
        body = {"platforms": ["kr"], "tiers": ["CHALLENGER"], "champions": ["Aatrox"], "champion_roles": {"Aatrox": ["JUNGLE", "MID"]}}
        res = client.post("/api/crawler/start", json=body)
        assert res.status_code == 200, res.text
        bad = client.post("/api/crawler/start", json={**body, "champion_roles": {"NoExiste": ["TOP"]}})
        assert bad.status_code == 400
    assert started["cfg"].validated().champion_roles == {AATROX: ["JUNGLE", "MID"]}


def test_otps_endpoint_counts_only_games_in_the_chosen_role(keyed_svc):
    from fastapi.testclient import TestClient

    from app.main import create_app

    ids, payloads = role_history("otp-a", 6, prefix="1", role_for=lambda i: "JUNGLE" if i < 2 else "TOP")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], champion_roles={AATROX: ["JUNGLE"]}, only_target_games=False)
    assert len(stored(keyed_svc)) == 6  # sin filtrar se guardan todas…
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    with TestClient(app) as client:
        data = client.get("/api/crawler/otps").json()
    (player,) = data["players"]
    assert player["total"] == 2 and {g["role"] for g in player["games"]} == {"JUNGLE"}  # …pero cuentan las de jungla
    assert data["champion_roles"] == {str(AATROX): ["JUNGLE"]}


# ------------------------------------------------------------------ panel por campeón y ventana de jugadores
def _seed_otps(svc, rows):
    """rows: [(puuid, platform, tier, lp, name, top)] como OTPs ya encontrados (sin partidas)."""
    now = _now_s()
    with svc.db.write() as conn:
        for puuid, platform, tier, lp, name, top in rows:
            conn.execute(
                "INSERT INTO players (puuid, platform, tier, is_seed, lp, game_name, tag_line) VALUES (?, ?, ?, 1, ?, ?, 'X')",
                (puuid, platform, tier, lp, name),
            )
            conn.execute("INSERT INTO mastery_checks VALUES (?, ?, ?, ?)", (puuid, platform, now, json.dumps([[c, p, now] for c, p in top])))


def _client(svc):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(svc.settings, run_startup=False)
    app.state.svc = svc
    return TestClient(app)


def test_otps_endpoint_summarises_each_champion(keyed_svc):
    ids, payloads = role_history("otp-a", 6, prefix="1", role_for=lambda i: "JUNGLE" if i < 4 else "TOP")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)], "otp-b": [(GAREN, 800_000)]})
    run_history(keyed_svc, fake, champions=[AATROX, GAREN], only_target_games=True)
    with _client(keyed_svc) as client:
        data = client.get("/api/crawler/otps").json()
    by = {c["champ"]: c for c in data["by_champion"]}
    assert list(by) == [AATROX, GAREN]  # en el orden en que los elegiste, aunque uno no tenga OTPs aún
    assert by[AATROX]["found"] == 1 and by[AATROX]["games"] == 6 and by[AATROX]["roles"] == {"JUNGLE": 4, "TOP": 2}
    assert by[AATROX]["leaders"][0]["total"] == 6 and by[AATROX]["best"]["tier"] == "CHALLENGER"
    assert by[GAREN]["found"] == 0 and by[GAREN]["best"] is None
    assert data["players"][0]["roles"] == {"JUNGLE": 4, "TOP": 2}


def test_otps_endpoint_filters_sorts_and_pages_one_champion(keyed_svc):
    _seed_otps(
        keyed_svc,
        [
            ("p1", "euw1", "CHALLENGER", 1500, "Alpha", [(AATROX, 900_000)]),
            ("p2", "euw1", "GRANDMASTER", 700, "Bravo", [(AATROX, 2_000_000)]),
            ("p3", "kr", "CHALLENGER", 2000, "Charlie", [(AATROX, 500_000)]),
            ("p4", "kr", "MASTER", 100, "Delta", [(GAREN, 900_000)]),
        ],
    )
    keyed_svc.crawler.config = CrawlerConfig(platforms=["euw1", "kr"], tiers=["MASTER", "GRANDMASTER", "CHALLENGER"], champions=[AATROX, GAREN]).validated()
    with _client(keyed_svc) as client:
        elo = client.get("/api/crawler/otps", params={"champ": AATROX, "sort": "elo"}).json()
        page = client.get("/api/crawler/otps", params={"champ": AATROX, "sort": "points", "limit": 2, "offset": 1}).json()
        kr = client.get("/api/crawler/otps", params={"champ": AATROX, "platform": "kr"}).json()
        q = client.get("/api/crawler/otps", params={"q": "brav"}).json()
    assert [p["game_name"] for p in elo["players"]] == ["Charlie", "Alpha", "Bravo"] and elo["matched"] == 3
    assert [p["game_name"] for p in page["players"]] == ["Alpha", "Charlie"] and page["matched"] == 3
    assert [p["game_name"] for p in kr["players"]] == ["Charlie"]
    assert [p["game_name"] for p in q["players"]] == ["Bravo"]
    assert {c["champ"]: c["found"] for c in elo["by_champion"]} == {AATROX: 3, GAREN: 1}
    assert elo["by_champion"][0]["platforms"] == {"euw1": 2, "kr": 1}


def test_mastery_focus_lists_a_player_under_each_champion(keyed_svc):
    _seed_otps(keyed_svc, [("p1", "euw1", "CHALLENGER", 1500, "Alpha", [(AATROX, 900_000), (GAREN, 600_000), (DARIUS, 100_000)])])
    keyed_svc.crawler.config = CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], champions=[AATROX, GAREN, DARIUS], focus="mastery").validated()
    with _client(keyed_svc) as client:
        data = client.get("/api/crawler/otps").json()
    assert {c["champ"]: c["found"] for c in data["by_champion"]} == {AATROX: 1, GAREN: 1, DARIUS: 0}  # Darius < 300 K
    assert data["found"] == 1 and sorted(p["champ"] for p in data["players"]) == sorted([AATROX, GAREN])


# ------------------------------------------------------------------ micro y macro de los OTPs
import gzip

from app.coach.store import unpack
from app.ingest import otp_details


def details(svc):
    return {r["match_id"]: r for r in svc.db.query("SELECT * FROM otp_games")}


def test_champion_games_mode_also_saves_the_minute_by_minute_analysis(keyed_svc):
    """Con «Cualquiera que lo juegue» no llegaba ninguna partida a data/otps (la versión anterior apagaba el análisis
    minuto a minuto en ese modo): ahora sus partidas con el campeón también se analizan."""
    ids, payloads = history("otp-a", 4, prefix="1", champ_for=lambda i: AATROX if i % 2 else GAREN)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(GAREN, 900_000), (AATROX, 400_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], focus="games", min_mastery=20_000, save_details=True)
    saved = details(keyed_svc)
    assert len(saved) == 2 and all(unpack(r["dossier"])["champ"] == AATROX for r in saved.values())  # solo con Aatrox


def test_otp_summaries_leave_out_games_with_an_afk(keyed_svc):
    """Como tus medias: una partida con alguien AFK (CS/min de 20 minutos, objetivos regalados…) no cuenta."""
    ids, payloads = history("otp-a", 3, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], save_details=True)
    dossiers = [unpack(r["dossier"]) | {"puuid": r["puuid"]} for r in details(keyed_svc).values()]
    assert otp_details.summarize(dossiers)["games"] == 3
    dossiers[0]["analysis"]["metrics"]["riot_afk"] = 1
    summary = otp_details.summarize(dossiers)
    assert summary["games"] == 2 and summary["excluded_afk"] == 1


def test_who_counts_as_an_otp_when_classifying_a_game(keyed_svc):
    now = _now_s()
    db = keyed_svc.db
    tops = {
        "main": [[AATROX, 300_000, now], [GAREN, 100_000, now]],  # principal, ≥ 100 K y ≥ 1,5× el segundo
        "close": [[AATROX, 300_000, now], [GAREN, 250_000, now]],  # no llega a 1,5×
        "low": [[AATROX, 60_000, now], [GAREN, 10_000, now]],  # pocos puntos
        "second": [[GAREN, 900_000, now], [AATROX, 400_000, now]],  # lo juega, pero no es su principal
    }
    with db.write() as conn:
        for puuid, top in tops.items():
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps(top)))
        conn.execute("INSERT INTO otp_sources (puuid, champ, platform, source, fetched_at) VALUES ('board', ?, 'euw1', 'opgg', ?)", (AATROX, now))
    kinds = {p: otp_details.classify(db, p, AATROX) for p in [*tops, "board", "unknown"]}
    assert kinds == {"main": "otp", "close": "player", "low": "player", "second": "player", "board": "otp", "unknown": "player"}


def test_champion_games_of_non_otps_go_to_their_own_folder(keyed_svc):
    """«Cualquiera que lo juegue»: un jugador con Aatrox de segundo no es OTP; sus partidas analizadas van a
    data/partidas_campeon y no a data/otps (ni a la página de OTPs, ni a los resúmenes de OTPs)."""
    ids, payloads = history("otp-a", 4, prefix="1", champ_for=lambda i: AATROX if i % 2 else GAREN)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(GAREN, 900_000), (AATROX, 400_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], focus="games", save_details=True)
    assert {r["kind"] for r in details(keyed_svc).values()} == {"player"}
    otp_details.rebuild_all(keyed_svc)
    players_dir = keyed_svc.settings.data_dir / "partidas_campeon" / keyed_svc.dd.name(AATROX)
    assert len(read_games(next((players_dir / "TOP").iterdir()) / "partidas.jsonl.gz")) == 2
    assert not (otps_dir(keyed_svc) / keyed_svc.dd.name(AATROX)).exists()
    assert otp_details.counts(keyed_svc.db)["otp_games"] == 0
    with _client(keyed_svc) as client:
        assert client.get("/api/otps/library").json()["totals"]["games"] == 0


def test_saved_games_without_a_mark_are_classified_on_startup(keyed_svc):
    """Lo guardado antes de separar (sin marca) se reparte solo al arrancar, sin descargar nada."""
    ids, payloads = history("otp-a", 3, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], save_details=True)
    for kind in ("otp", "player"):  # el resumen que se rehace al parar escribe en la carpeta: que acabe antes
        f = otp_details.Files.of(keyed_svc, kind)
        if f is not None and f.thread is not None:
            f.thread.join(10)
    now = _now_s()
    with keyed_svc.db.write() as conn:
        conn.execute("UPDATE otp_games SET kind = NULL")
        conn.execute("UPDATE mastery_checks SET top = ? WHERE puuid = 'otp-a'", (json.dumps([[GAREN, 900_000, now], [AATROX, 400_000, now]]),))
    calls = len(fake.calls)
    thread = otp_details.start_reanalysis(keyed_svc)
    thread.join(20)
    assert {r["kind"] for r in details(keyed_svc).values()} == {"player"} and len(fake.calls) == calls
    assert (keyed_svc.settings.data_dir / "partidas_campeon" / keyed_svc.dd.name(AATROX)).is_dir()
    assert not (otps_dir(keyed_svc) / keyed_svc.dd.name(AATROX)).exists()


def test_rebuild_works_when_windows_refuses_to_rename_the_folder(keyed_svc, monkeypatch):
    """Windows: renombrar data/otps falla (WinError 5) si otro programa tiene algo abierto dentro. Antes la carpeta se
    regeneraba intercambiándola por otra (rename) y la app daba PermissionError; ahora no se renombra nunca."""
    import pathlib

    ids, payloads = history("otp-a", 3, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], save_details=True)
    base = otps_dir(keyed_svc)
    (base / "_busqueda.json").write_text("{}", encoding="utf-8")  # el informe de la búsqueda no se pierde
    (base / "Viejo").mkdir()
    (base / "Viejo" / "resumen.json").write_text("{}", encoding="utf-8")  # lo que ya no existe se borra

    def refuse(self, target):
        raise PermissionError(5, "Acceso denegado", str(self))

    monkeypatch.setattr(pathlib.Path, "rename", refuse)
    result = otp_details.Files.of(keyed_svc).rebuild()
    assert result["games"] == 3 and result["failed"] == 0
    assert (base / "_busqueda.json").exists() and not (base / "Viejo").exists()
    assert not base.with_name("otps.tmp").exists()
    (folder,) = (base / keyed_svc.dd.name(AATROX) / "TOP").iterdir()
    assert len(read_games(folder / "partidas.jsonl.gz")) == 3


def test_a_locked_file_does_not_break_the_rebuild_and_is_retried_later(keyed_svc, monkeypatch):
    ids, payloads = history("otp-a", 2, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], save_details=True)
    files = otp_details.Files.of(keyed_svc)
    monkeypatch.setattr(otp_details, "REPLACE_RETRIES", 1)
    real_replace, real_copy = otp_details.os.replace, otp_details.shutil.copyfile

    def locked(src, dst):
        if str(dst).endswith("resumen.json") and "otps.tmp" not in str(dst):  # el de la carpeta real, abierto en otro programa
            raise PermissionError(32, "El archivo está siendo utilizado por otro proceso", str(dst))
        return real_replace(src, dst)

    def locked_copy(src, dst):
        if str(dst).endswith("resumen.json") and "otps.tmp" not in str(dst):  # el de la carpeta real, abierto en otro programa
            raise PermissionError(32, "El archivo está siendo utilizado por otro proceso", str(dst))
        return real_copy(src, dst)

    monkeypatch.setattr(otp_details.os, "replace", locked)
    monkeypatch.setattr(otp_details.shutil, "copyfile", locked_copy)
    result = files.rebuild()  # no lanza
    assert result["failed"] == 1 and files.needs_rebuild()  # sin marca de formato: se reintenta al arrancar
    monkeypatch.setattr(otp_details.os, "replace", real_replace)
    monkeypatch.setattr(otp_details.shutil, "copyfile", real_copy)
    assert files.rebuild()["failed"] == 0 and not files.needs_rebuild()


def test_a_folder_left_half_done_is_rebuilt_on_startup(keyed_svc):
    """Como tras el WinError 5: todo clasificado y al día en la BD, pero la carpeta con el formato viejo (o sin marca)."""
    ids, payloads = history("otp-a", 2, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], save_details=True)
    marker = otps_dir(keyed_svc) / ".formato"
    marker.write_text("3", encoding="utf-8")
    thread = otp_details.start_reanalysis(keyed_svc)
    assert thread is not None
    thread.join(20)
    assert marker.read_text(encoding="utf-8").strip() == str(otp_details.LAYOUT_VERSION)


def test_otp_games_get_their_micro_and_macro(keyed_svc):
    ids, payloads = history("otp-a", 6, prefix="1", champ_for=lambda i: AATROX if i % 3 else GAREN)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    worker = run_history(keyed_svc, fake, champions=[AATROX], only_target_games=True, save_details=True)
    saved = details(keyed_svc)
    wanted = {ids[i] for i in range(6) if i % 3}
    assert set(saved) == wanted and worker.state["details"] == 4
    assert {c[1] for c in fake.calls_to("timeline")} == wanted  # solo se gasta la llamada en las que interesan
    row = saved[ids[1]]
    assert row["puuid"] == "otp-a" and row["champ"] == AATROX and row["role"] == "TOP" and row["has_timeline"] == 1
    # La partida y el timeline en bruto van a su fichero (data/raw_otp), no a la base de datos (llenaban el WAL).
    assert row["match_raw"] is None and row["timeline_raw"] is None
    match, timeline = otp_details.load_raw(keyed_svc.db, keyed_svc, ids[1], "otp-a")
    assert match["metadata"]["matchId"] == ids[1] and timeline["info"]["frames"]
    d = unpack(row["dossier"])
    assert d["analysis"]["has_timeline"] and d["analysis"]["metrics"]["cs_min"] > 0 and "replay" not in d["analysis"]
    assert len(d["frames"]) == 31 and d["frames"][5]["zone"] and d["opponent_frames"]
    assert d["events"]["items"] and d["runes"]["keystone"] and d["spells"] and d["track"]
    assert d["allies"]["TOP"] == AATROX and d["map_events"]["kills"]


def test_details_are_backfilled_for_games_saved_before(keyed_svc):
    ids, payloads = history("otp-a", 5, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], save_details=False)
    assert not details(keyed_svc)
    fake2 = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake2, champions=[AATROX], save_details=True)
    assert set(details(keyed_svc)) == set(ids)  # aunque su historial ya estuviera completo
    assert len(fake2.calls_to("timeline")) == 5 and len(fake2.calls_to("match")) == 5


def test_details_only_for_games_in_the_chosen_role(keyed_svc):
    ids, payloads = role_history("otp-a", 6, prefix="1", role_for=lambda i: "JUNGLE" if i < 2 else "TOP")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], champion_roles={AATROX: ["JUNGLE"]}, only_target_games=False, save_details=True)
    assert len(stored(keyed_svc)) == 6 and set(details(keyed_svc)) == set(ids[:2])
    assert {r["role"] for r in details(keyed_svc).values()} == {"JUNGLE"}


def otps_dir(svc):
    return svc.settings.data_dir / otp_details.FILES_DIR


def read_games(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_each_game_is_written_to_its_otp_folder_automatically(keyed_svc):
    ids, payloads = role_history("otp-a", 5, prefix="1", role_for=lambda i: "JUNGLE" if i < 3 else "TOP")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], only_target_games=False, save_details=True)
    champ_dir = otps_dir(keyed_svc) / keyed_svc.dd.name(AATROX)
    (jungle,) = (champ_dir / "JUNGLE").iterdir()
    (top,) = (champ_dir / "TOP").iterdir()
    assert jungle.name.endswith("_otp-a") and jungle.name.startswith("euw1_")  # su rol claro: una carpeta por rol
    games = read_games(jungle / "partidas.jsonl.gz")
    assert len(games) == 3 and {g["role"] for g in games} == {"JUNGLE"} and len(read_games(top / "partidas.jsonl.gz")) == 2
    lp = keyed_svc.db.scalar("SELECT lp FROM players WHERE puuid = 'otp-a'")
    assert games[0]["tier"] == "CHALLENGER" and games[0]["division"] == "I" and games[0]["lp"] == lp > 0  # elo de cada partida
    assert games[0]["events"]["items"] and "skills" in games[0]["events"]  # la build entera, compra a compra
    profile = json.loads((jungle / "perfil.json").read_text(encoding="utf-8"))
    assert profile["role"] == "JUNGLE" and profile["games"] == 3 and profile["by_tier"]["CHALLENGER"]["games"] == 3
    assert profile["elo_now"] == {"tier": "CHALLENGER", "division": "I", "lp": lp}
    # El resumen del campeón se rehace solo al parar (y cada pocos minutos mientras entran partidas).
    files = otp_details.Files.of(keyed_svc)
    assert wait_until(lambda: files.thread is not None and not files.thread.is_alive() and not files.dirty, 15)
    summary = json.loads((champ_dir / "resumen.json").read_text(encoding="utf-8"))
    assert summary["games"] == 5 and set(summary["roles"]) == {"JUNGLE", "TOP"}
    jg = summary["roles"]["JUNGLE"]
    assert jg["all"]["games"] == 3 and jg["by_tier"]["CHALLENGER"]["games"] == 3 and jg["all"]["metrics"]["cs_min"]["n"] == 3
    assert (otps_dir(keyed_svc) / "LEEME.md").exists()


def test_a_game_is_written_once_and_a_new_one_appends(keyed_svc):
    from types import SimpleNamespace

    svc = SimpleNamespace(db=keyed_svc.db, dd=keyed_svc.dd, settings=keyed_svc.settings)
    elo = {"tier": "MASTER", "division": "I", "lp": 50}
    m1, m2 = make_match("EUW1_501", puuids={1: "p-x"}), make_match("EUW1_502", puuids={1: "p-x"})
    assert otp_details.save(keyed_svc.db, svc, m1, None, "p-x", elo, kind="otp")
    assert otp_details.save(keyed_svc.db, svc, m1, None, "p-x", elo, kind="otp")  # repetida: se actualiza en la BD, no se duplica
    assert otp_details.save(keyed_svc.db, svc, m2, None, "p-x", {"tier": "GRANDMASTER", "division": "I", "lp": 400}, kind="otp")
    (folder,) = (otps_dir(keyed_svc) / keyed_svc.dd.name(AATROX) / "TOP").iterdir()
    games = read_games(folder / "partidas.jsonl.gz")
    assert [g["match_id"] for g in games] == ["EUW1_501", "EUW1_502"] and [g["tier"] for g in games] == ["MASTER", "GRANDMASTER"]
    profile = json.loads((folder / "perfil.json").read_text(encoding="utf-8"))
    assert profile["by_tier"] == {"GRANDMASTER": {"games": 1, "wins": 1, "wr": 100.0}, "MASTER": {"games": 1, "wins": 1, "wr": 100.0}}


def test_players_with_the_same_name_get_their_own_folder(keyed_svc):
    from types import SimpleNamespace

    svc = SimpleNamespace(db=keyed_svc.db, dd=keyed_svc.dd, settings=keyed_svc.settings)
    for i, puuid in enumerate(("same-aaaa1", "same-bbbb2")):
        assert otp_details.save(keyed_svc.db, svc, make_match(f"EUW1_60{i}", puuids={1: puuid}), None, puuid, kind="otp")
    assert len(list((otps_dir(keyed_svc) / keyed_svc.dd.name(AATROX) / "TOP").iterdir())) == 2


def test_folder_is_rebuilt_from_the_database_when_missing(keyed_svc):
    import shutil

    ids, payloads = history("otp-a", 3, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], save_details=True)
    files = otp_details.Files.of(keyed_svc)
    if files.thread is not None:
        files.thread.join(10)  # el resumen que se rehace al parar
    shutil.rmtree(otps_dir(keyed_svc))
    assert files.needs_rebuild()
    assert files.rebuild()["games"] == 3
    (folder,) = (otps_dir(keyed_svc) / keyed_svc.dd.name(AATROX) / "TOP").iterdir()
    assert len(read_games(folder / "partidas.jsonl.gz")) == 3 and (folder.parent.parent / "resumen.json").exists()
    assert not files.needs_rebuild()


def test_reanalyze_and_api_state(keyed_svc):
    with _client(keyed_svc) as client:
        assert client.post("/api/crawler/otps/files/rebuild").status_code == 409  # nada guardado aún
    ids, payloads = history("otp-a", 2, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], save_details=True)
    with keyed_svc.db.write() as conn:
        conn.execute("UPDATE otp_games SET version = 0, dossier = NULL")
    assert otp_details.reanalyze(keyed_svc.db, keyed_svc) == 2  # desde el bruto, sin llamar a Riot
    assert all(unpack(r["dossier"]) for r in details(keyed_svc).values())
    with _client(keyed_svc) as client:
        data = client.get("/api/crawler/otps").json()
    assert data["details"]["games"] == 2 and data["details"]["enabled"] is True
    assert data["details"]["path"].endswith("otps") and "CHALLENGER" in data["details"]["tiers"]


def test_otp_games_are_reanalyzed_on_startup_when_the_analysis_changes(keyed_svc):
    """Las partidas de OTPs se analizaban una vez y se quedaban en su versión (había 894 en la 7 y 450 en la 9 con el
    análisis en la 10): «Tú frente a los OTPs» comparaba con reglas distintas. Ahora se rehacen solas al arrancar."""
    import threading

    ids, payloads = history("otp-a", 3, prefix="1")
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], save_details=True)
    assert otp_details.start_reanalysis(keyed_svc) is None  # todo al día: ni se arranca
    with keyed_svc.db.write() as conn:
        conn.execute("UPDATE otp_games SET version = 2007")  # analizadas con la versión 7
    assert otp_details.outdated(keyed_svc.db) == 3
    calls_before = len(fake.calls)
    # Mientras juegas, espera; al cerrar la app, para.
    keyed_svc.tracker.state["in_game"] = True
    stop = threading.Event()
    waiter = threading.Thread(target=lambda: otp_details.reanalyze(keyed_svc.db, keyed_svc, stop=stop, pause=lambda: keyed_svc.tracker.state["in_game"]))
    waiter.start()
    waiter.join(0.3)
    assert waiter.is_alive() and otp_details.outdated(keyed_svc.db) == 3
    stop.set()
    waiter.join(6)
    assert not waiter.is_alive()
    keyed_svc.tracker.state["in_game"] = False
    thread = otp_details.start_reanalysis(keyed_svc)
    thread.join(20)
    assert otp_details.outdated(keyed_svc.db) == 0 and len(fake.calls) == calls_before  # sin llamar a Riot
    assert (otps_dir(keyed_svc) / ".formato").read_text(encoding="utf-8").strip() == str(otp_details.LAYOUT_VERSION)


def test_each_game_keeps_the_draft_context(keyed_svc):
    """Para planes de partida: las 5 líneas, por dónde anda cada jungla y quién coge cada objetivo."""
    match = make_match("EUW1_700", puuids={2: "jg-otp"})  # pid 2 = jungla azul (Lee Sin)
    d = otp_details.build_dossier(match, make_timeline(match), "jg-otp", None, elo={"tier": "CHALLENGER", "division": "I", "lp": 900})
    ctx = d["context"]
    assert set(ctx["lanes"]) == {"TOP", "JUNGLE", "MID", "ADC", "SUPPORT"}
    top = ctx["lanes"]["TOP"]
    assert top["ally"] == AATROX and top["enemy"] == DARIUS and set(top["diff"]) == {"5", "10", "15", "20"}
    assert set(top["diff"]["10"]) == {"gold", "xp", "cs"}
    jg = ctx["jungle"]["ally"]
    assert sum(jg["positions"].values()) > 0 and jg["takedowns_pre15"]  # el gank azul en bot del escenario de pruebas
    assert ctx["first"]["dragon"]["ally"] is True and ctx["objectives"]
    assert d["tier"] == "CHALLENGER" and d["lp"] == 900

    summary = otp_details.summarize([d | {"puuid": "jg-otp"}])
    block = summary["roles"]["JUNGLE"]["all"]
    assert block["matchups"][0]["enemy_id"] == make_match()["info"]["participants"][6]["championId"]  # su jungla rival
    assert set(block["lanes_impact"]) >= {"TOP", "MID"} and block["objectives_first"]["dragon"]["ally_first_pct"] == 100.0
    assert block["jungle_presence"]["minutes_at"] and "first_takedown_where" in block
    assert summary["roles"]["JUNGLE"]["by_tier"]["CHALLENGER"]["games"] == 1


# ------------------------------------------------------------------ búsqueda de OTPs más rápida
from app.ingest import crawler as crawler_mod
from app.ingest.crawler import OtpScout, otp_rows


def test_one_search_per_platform_in_the_same_region(keyed_svc):
    keyed_svc.riot = MasteryRiot(*world())
    keyed_svc.crawler.start(CrawlerConfig(platforms=["euw1", "eun1"], tiers=["CHALLENGER"], champions=[AATROX], save_details=False))
    worker = keyed_svc.crawler.workers["europe"]
    assert wait_until(lambda: len(worker.scouts) == 2)
    assert {sc.platform for sc in worker.scouts} == {"euw1", "eun1"}  # cada plataforma tiene su propio cupo
    keyed_svc.crawler.stop()
    worker.join(timeout=5)


def test_search_goes_from_the_lowest_elo_up_when_no_elo_has_data(keyed_svc):
    from types import SimpleNamespace

    order = []
    stop = __import__("threading").Event()
    worker = SimpleNamespace(
        config=CrawlerConfig(tiers=["DIAMOND", "MASTER", "CHALLENGER"], champions=[AATROX]).validated(),
        stop_event=stop,
        svc=keyed_svc,
        crawler=SimpleNamespace(paused_reason=lambda: None),
        state={"errors": 0},
        scouts=[],
        wake=lambda: None,
        _seed_from_leaderboards=lambda platform: 0,
        _discover=lambda platform, tier: order.append(tier) or 0,
        _refill_seeds=lambda platform, tier: 0,
        found_by_tier={},
        _tier_need=lambda tiers: {},
    )
    worker.tier_order = lambda platform, tiers: crawler_mod.RegionWorker.tier_order(worker, platform, tiers)
    scout = OtpScout(worker, "euw1")
    worker.scouts.append(scout)
    real_set = scout._set
    scout._set = lambda status: (real_set(status), status == "idle" and stop.set())
    scout.run()
    assert order == ["DIAMOND", "MASTER", "CHALLENGER"]  # sin datos en ninguno: del más bajo al más alto


def test_search_goes_to_the_elo_with_fewest_otps_first():
    """A igualdad de partidas, la siguiente tanda del ranking va al elo con menos OTPs encontrados (luego, el más bajo)."""
    from types import SimpleNamespace

    worker = SimpleNamespace(found_by_tier={("euw1", "CHALLENGER"): 9, ("euw1", "MASTER"): 2, ("kr", "DIAMOND"): 50}, _tier_need=lambda tiers: {})
    order = crawler_mod.RegionWorker.tier_order(worker, "euw1", ["CHALLENGER", "MASTER", "DIAMOND"])
    assert order == ["DIAMOND", "MASTER", "CHALLENGER"]  # en EUW, Diamante aún no tiene ninguno


def test_players_seen_with_the_champion_are_checked_first(keyed_svc, monkeypatch):
    with keyed_svc.db.write() as conn:
        for puuid, lp in (("seed-a", 1500), ("seed-b", 100)):
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', 'CHALLENGER', 1, ?)", (puuid, lp))
        b = conn.execute("SELECT id FROM players WHERE puuid = 'seed-b'").fetchone()[0]
        conn.execute("INSERT INTO participants (match_id, pid, player_id, team, champ, win) VALUES ('EUW1_1', 1, ?, 100, ?, 1)", (b, AATROX))
    worker = crawler_mod.RegionWorker(keyed_svc.crawler, "europe", ["euw1"], CrawlerConfig(champions=[AATROX]).validated())
    checked = []
    monkeypatch.setattr(worker, "_check_mastery", lambda platform, puuid: checked.append(puuid))
    monkeypatch.setattr(crawler_mod, "MASTERY_BATCH", 1)
    worker._discover("euw1", "CHALLENGER")
    assert checked == ["seed-b"]  # aunque tenga menos LP: ya se le ha visto jugando Aatrox


class FakeOpgg:
    enabled = True

    def __init__(self):
        self.calls = []

    def mcp_name(self, champ):
        return "AATROX"

    def mcp_call(self, tool, arguments):
        self.calls.append((tool, arguments["region"], arguments["champion"]))
        stat = lambda tier, div, lp: [{"game_type": "SOLORANKED", "tier_info": {"tier": tier, "division": div, "lp": lp}}]
        return {
            "leaderboard": [
                {"rank": 1, "summoner": {"game_name": "Uno", "tagline": "EUW", "league_stats": stat("CHALLENGER", 1, 1500)}, "most_champion_stat": {"play": 900, "win": 500}},
                {"rank": 2, "summoner": {"game_name": "Dos", "tagline": "EUW", "league_stats": stat("MASTER", 1, 200)}, "most_champion_stat": {"play": 400, "win": 210}},
            ]
        }


class LeaderboardRiot(MasteryRiot):
    def account_by_riot_id(self, game_name, tag_line, platform, **kw):
        self._record("account_by_riot_id", game_name)
        return {"puuid": f"lb-{game_name}", "gameName": game_name, "tagLine": tag_line}

    def top_masteries(self, platform, puuid, count=3, **kw):
        self._record("top_masteries", platform, puuid)
        if puuid == "lb-Dos":  # juega mucho Aatrox pero su principal es otro: cuenta igual por la tabla
            return [{"championId": GAREN, "championPoints": 2_000_000}, {"championId": AATROX, "championPoints": 600_000}]
        return [{"championId": AATROX, "championPoints": 900_000}]


def test_leaderboard_gives_otps_without_scanning_the_ladder(keyed_svc):
    opgg = FakeOpgg()
    keyed_svc.opgg = opgg
    keyed_svc.riot = LeaderboardRiot({}, {})
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["MASTER", "CHALLENGER"], champions=[AATROX]).validated()
    worker = crawler_mod.RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    assert worker._seed_from_leaderboards("euw1") == 2
    assert opgg.calls == [("lol_list_champion_leaderboard", "EUW", "AATROX")]
    rows = {row["puuid"]: row for row, _ in otp_rows(keyed_svc.db, cfg, ["euw1"], cfg.tiers)}
    assert set(rows) == {"lb-Uno", "lb-Dos"}  # «Dos» no es OTP estricto por maestría, pero sale en la tabla
    assert rows["lb-Uno"]["tier"] == "CHALLENGER" and rows["lb-Uno"]["lp"] == 1500 and rows["lb-Uno"]["game_name"] == "Uno"
    assert len(keyed_svc.riot.calls_to("account_by_riot_id")) == 2 and len(keyed_svc.riot.calls_to("top_masteries")) == 2
    # Al día siguiente sí; hoy no se vuelve a pedir.
    assert worker._seed_from_leaderboards("euw1") == 0 and len(opgg.calls) == 1
    with _client(keyed_svc) as client:
        keyed_svc.crawler.config = cfg
        data = client.get("/api/crawler/otps").json()
    assert {p["game_name"] for p in data["players"]} == {"Uno", "Dos"} and data["by_champion"][0]["found"] == 2



# ------------------------------------------------------------------ variedad antes que cantidad
def _seed_otp(svc, puuid="otp-a"):
    with svc.db.write() as conn:
        conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', 'CHALLENGER', 1, 900)", (puuid,))
        conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, _now_s(), json.dumps([[AATROX, 900_000, _now_s()]])))


def test_while_searching_each_otp_only_gets_its_most_recent_games(keyed_svc):
    from types import SimpleNamespace

    ids, payloads = history("otp-a", 80, prefix="1")
    keyed_svc.riot = HistoryRiot({"otp-a": ids}, payloads, {})
    _seed_otp(keyed_svc)
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], champions=[AATROX], games_per_otp=20, save_details=False).validated()
    worker = crawler_mod.RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    worker.scouts = [SimpleNamespace(status="running")]  # la búsqueda sigue encontrando OTPs
    worker._visit_otp(worker._next_otp())
    assert stored(keyed_svc) == set(ids[:20])  # sus 20 más recientes, no 80
    assert worker._next_otp() is None  # con su tope: el resto, cuando acabe la búsqueda
    worker.scouts = [SimpleNamespace(status="idle")]  # ya no queda nadie por revisar
    row = worker._next_otp()
    assert row is not None and row["puuid"] == "otp-a"
    worker._visit_otp(row)
    assert len(stored(keyed_svc)) == 80


def test_otps_panel_says_when_an_otp_is_at_its_cap(keyed_svc, monkeypatch):
    ids, payloads = history("otp-a", 30, prefix="1")
    keyed_svc.riot = HistoryRiot({"otp-a": ids}, payloads, {})
    _seed_otp(keyed_svc)
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], champions=[AATROX], games_per_otp=10, save_details=False).validated()
    worker = crawler_mod.RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    from types import SimpleNamespace

    worker.scouts = [SimpleNamespace(status="running")]
    worker._visit_otp(worker._next_otp())
    keyed_svc.crawler.config = cfg
    monkeypatch.setattr(type(keyed_svc.crawler), "running", property(lambda self: True))
    monkeypatch.setattr(keyed_svc.crawler, "searching", lambda: True)
    with _client(keyed_svc) as client:
        data = client.get("/api/crawler/otps").json()
    assert data["players"][0]["history"] == "capped" and data["players"][0]["total"] == 10 and data["searching"]


def test_games_per_otp_is_validated():
    assert CrawlerConfig(games_per_otp=0).validated().games_per_otp == 0
    assert CrawlerConfig(games_per_otp=2).validated().games_per_otp == 5
    assert CrawlerConfig().validated().games_per_otp == 30


def test_leaderboard_only_adds_players_of_your_elos(keyed_svc):
    opgg = FakeOpgg()
    keyed_svc.opgg = opgg
    keyed_svc.riot = LeaderboardRiot({}, {})
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["DIAMOND", "MASTER"], champions=[AATROX]).validated()
    worker = crawler_mod.RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg)
    assert worker._seed_from_leaderboards("euw1") == 1  # «Dos» (Maestro) sí; «Uno» (Aspirante) no es de tus elos
    assert [c[1] for c in keyed_svc.riot.calls_to("account_by_riot_id")] == ["Dos"]  # ni se gasta la llamada
    low = CrawlerConfig(platforms=["euw1"], tiers=["PLATINUM", "EMERALD"], champions=[AATROX]).validated()
    assert crawler_mod.RegionWorker(keyed_svc.crawler, "europe", ["euw1"], low)._seed_from_leaderboards("euw1") == 0
    assert len(opgg.calls) == 1  # en Platino–Esmeralda la tabla (Maestro+) ni se pide


# ------------------------------------------------------------------ embudo de la búsqueda
from app.ingest.crawler import search_funnel


def test_search_funnel_shows_where_players_are_dropped(keyed_svc):
    now = _now_s()
    old = now - 90 * 86400
    rows = [
        ("f-1", [[AATROX, 900_000, now], [GAREN, 100_000, now]]),  # OTP
        ("f-2", [[GAREN, 900_000, now], [AATROX, 400_000, now]]),  # top 3, no principal
        ("f-3", [[AATROX, 50_000, now]]),  # pocos puntos
        ("f-4", [[AATROX, 900_000, old]]),  # no lo juega desde hace meses
        ("f-5", [[24, 900_000, now]]),  # nada que ver
    ]
    with keyed_svc.db.write() as conn:
        for puuid, top in rows:
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', 'EMERALD', 1, 50)", (puuid,))
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps(top)))
        conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES ('f-6', 'euw1', 'EMERALD', 1, 50)")  # sin revisar
    mastery = CrawlerConfig(platforms=["euw1"], tiers=["EMERALD"], champions=[AATROX], focus="mastery", min_mastery=100_000, otp_depth="30").validated()
    f = search_funnel(keyed_svc.db, mastery, now)
    assert f["stages"] == ["checked", "champ", "points", "active"]
    assert f["total"] == {"checked": 5, "champ": 4, "points": 3, "active": 2, "seeds": 6, "last_hour": 5}
    otp = CrawlerConfig(platforms=["euw1"], tiers=["EMERALD"], champions=[AATROX], focus="otp", min_mastery=100_000, otp_depth="30").validated()
    with keyed_svc.db.write() as conn:  # f-1 juega casi siempre Aatrox; f-2 (Garen de principal), casi nunca
        conn.execute(
            "INSERT INTO otp_progress (puuid, list_key, anchor, seen, hits, updated_at) VALUES ('f-1', 'k', ?, 30, 24, ?), ('f-2', 'k', ?, 30, 4, ?)",
            (now, now, now, now),
        )
    f = search_funnel(keyed_svc.db, otp, now)
    assert f["stages"] == ["checked", "champ", "points", "active", "share"]
    t = f["total"]
    assert (t["champ"], t["points"], t["active"], t["share"]) == (4, 3, 2, 1)
    # Coincide con los OTPs que cuenta el recolector (f-2 ya revisado y descartado).
    assert [r["puuid"] for r, _ in otp_rows(keyed_svc.db, otp, ["euw1"], ["EMERALD"], now)] == ["f-1"]


def test_funnel_is_written_for_review(keyed_svc):
    keyed_svc.crawler.config = CrawlerConfig(platforms=["euw1"], tiers=["EMERALD"], champions=[AATROX]).validated()
    keyed_svc.crawler.write_funnel(force=True)
    keyed_svc.crawler._funnel_thread.join(10)  # se escribe en su propio hilo
    data = json.loads((keyed_svc.settings.data_dir / "otps" / "_busqueda.json").read_text(encoding="utf-8"))
    assert data["config"]["focus"] == "otp" and "total" in data and data["config"]["champions"]


def test_the_period_decides_who_counts_and_how_far_back(keyed_svc):
    """Un solo «Periodo»: cuenta quien jugó el campeón en él (antes «Jugado en los últimos», que podía contradecir al
    «Historial de cada OTP»). Con 30 días se quedaba fuera más de la mitad de los candidatos: se puede ampliar."""
    now = _now_s()
    top = [[AATROX, 300_000, now - 70 * 86400], [GAREN, 50_000, now]]  # lo jugó hace 70 días
    month = CrawlerConfig(champions=[AATROX], focus="mastery", min_mastery=100_000, otp_depth="30").validated()
    quarter = CrawlerConfig(champions=[AATROX], focus="mastery", min_mastery=100_000, otp_depth="90").validated()
    assert not month.qualifies(top, now) and quarter.qualifies(top, now)
    assert CrawlerConfig(otp_depth="all").validated().active_since(now) == 0
    assert CrawlerConfig(otp_depth="45").validated().otp_depth == "season"  # solo los valores del desplegable
    # Una configuración guardada por una versión anterior (con active_days) se sigue cargando.
    old = CrawlerConfig.from_saved({"champions": [AATROX], "active_days": 180, "otp_depth": "90", "unknown": 1})
    assert old.champions == [AATROX] and old.otp_depth == "90"
    with keyed_svc.db.write() as conn:
        conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES ('a-1', 'euw1', 'EMERALD', 1, 50)")
        conn.execute("INSERT INTO mastery_checks VALUES ('a-1', 'euw1', ?, ?)", (now, json.dumps(top)))
    base = {"platforms": ["euw1"], "tiers": ["EMERALD"], "champions": [AATROX], "focus": "mastery", "min_mastery": 100_000}
    assert search_funnel(keyed_svc.db, CrawlerConfig(**base, otp_depth="30").validated(), now)["total"]["active"] == 0
    assert search_funnel(keyed_svc.db, CrawlerConfig(**base, otp_depth="90").validated(), now)["total"]["active"] == 1


def test_the_elo_with_fewest_games_goes_first_in_every_mode(keyed_svc):
    """Aunque Garen aún no tenga a nadie, primero el elo con menos partidas recogidas (Esmeralda), con líneas enteras y
    también buscando OTPs de campeones sueltos (antes ahí iba primero el campeón sin nadie, 24-11-2026). Dentro del
    mismo elo, la variedad de campeones sigue mandando."""
    now = _now_s()
    db = keyed_svc.db
    lines = CrawlerConfig(platforms=["euw1"], tiers=["EMERALD", "DIAMOND"], roles=["TOP"], role_champions=[AATROX, GAREN], role_pools={"TOP": [AATROX, GAREN]}, focus="games", min_mastery=10_000).validated()
    with db.write() as conn:
        rows = [("d-old", "DIAMOND", AATROX, 40), ("e-new", "EMERALD", AATROX, None), ("d-garen", "DIAMOND", GAREN, None)]
        for puuid, tier, champ, hits in rows:
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', ?, 1, 50)", (puuid, tier))
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps([[champ, 900_000, now]])))
            if hits is not None:  # ya visitado: 40 partidas de top en Diamante
                conn.execute(
                    "INSERT INTO otp_progress (puuid, list_key, anchor, back_offset, back_done_at, back_done_start, back_done_reason, seen, hits, updated_at) "
                    "VALUES (?, ?, ?, 100, ?, 0, 'end', 40, ?, ?)",
                    (puuid, otp_list_key(lines), now - 3600, now - 3600, hits, now - 3600),
                )
                conn.execute("UPDATE players SET last_crawled_at = ? WHERE puuid = ?", (now, puuid))
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], lines)
    assert worker._next_otp()["puuid"] == "e-new"
    champs = CrawlerConfig(platforms=["euw1"], tiers=["EMERALD", "DIAMOND"], champions=[AATROX, GAREN], focus="mastery", min_mastery=10_000).validated()
    champs_worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], champs)
    assert champs_worker._next_otp()["puuid"] == "e-new"
    keyed_svc.crawler.note_visit_tier("EMERALD")  # Esmeralda ya alcanza a Diamante (25 + 40 analizadas): le toca a él
    keyed_svc.crawler.note_visit_tier("EMERALD")
    assert champs_worker._next_otp()["puuid"] == "d-garen"



def test_las_regiones_se_turnan_los_elos(keyed_svc):
    """Antes las 4 regiones iban todas al elo con menos partidas en total (entre una subida y otra salían 3-4 elos):
    ahora el siguiente jugador es del elo menos visitado últimamente (entre las 4 regiones); a igualdad, el de menos."""
    now = _now_s()
    db = keyed_svc.db
    lines = CrawlerConfig(platforms=["euw1"], tiers=["SILVER", "EMERALD", "DIAMOND"], roles=["TOP"], role_champions=[AATROX],
                          role_pools={"TOP": [AATROX]}, focus="games", min_mastery=10_000).validated()
    with db.write() as conn:
        for puuid, tier in (("s1", "SILVER"), ("s2", "SILVER"), ("e1", "EMERALD"), ("d1", "DIAMOND")):
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', ?, 1, 50)", (puuid, tier))
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps([[AATROX, 900_000, now]])))
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], lines)
    first = worker._next_otp()
    assert first["tier"] == "SILVER"  # a igualdad, el elo más bajo
    keyed_svc.crawler.note_visit_tier("SILVER")  # otra región acaba de empezar con uno de Plata
    assert worker._next_otp()["tier"] == "EMERALD"
    keyed_svc.crawler.note_visit_tier("EMERALD")
    assert worker._next_otp()["tier"] == "DIAMOND"


def test_el_reparto_da_mas_parte_al_elo_con_menos_y_ninguno_se_queda_sin_partidas():
    from app.ingest.crawler import MIN_TIER_SHARE, tier_need, tier_shares

    shares = tier_shares({"GOLD": 0, "DIAMOND": 1000}, ["GOLD", "DIAMOND"])
    assert shares["GOLD"] > 0.9 and MIN_TIER_SHARE <= shares["DIAMOND"] < 0.1 and abs(sum(shares.values()) - 1) < 1e-9
    even = tier_shares({"GOLD": 5, "DIAMOND": 5}, ["GOLD", "DIAMOND"])
    assert {t: round(v, 6) for t, v in even.items()} == {"GOLD": 0.5, "DIAMOND": 0.5}
    # Al arrancar (nada recogido en la sesión) va primero el de más parte.
    need = tier_need({}, shares)
    assert max(need, key=need.get) == "GOLD"


def test_con_las_cifras_reales_va_primero_bronce_y_luego_oro():
    """25-11-2026: con todo el ranking iba primero Hierro y Bronce (contaba la tabla ``matches``) aunque en las partidas
    analizadas los que menos tenían eran Bronce (2.419) y Oro (2.458). Y ningún elo se queda sin partidas nuevas."""
    from app.ingest.crawler import MIN_TIER_SHARE, tier_need, tier_shares

    real = {"IRON": 2573, "BRONZE": 2419, "SILVER": 2524, "GOLD": 2458, "PLATINUM": 2574, "EMERALD": 2825,
            "DIAMOND": 17487, "MASTER": 5210, "GRANDMASTER": 3330, "CHALLENGER": 7007}
    shares = tier_shares(real, list(real))
    assert sorted(shares, key=shares.get, reverse=True)[:2] == ["BRONZE", "GOLD"]
    assert min(shares.values()) >= MIN_TIER_SHARE and min(shares, key=shares.get) == "DIAMOND"
    session: dict[str, int] = {}
    picks = []
    for _ in range(2):
        need = tier_need(session, shares)
        tier = max(need, key=need.get)
        picks.append(tier)
        session[tier] = session.get(tier, 0) + 20
    assert picks == ["BRONZE", "GOLD"]


def test_la_cola_sigue_el_reparto_por_elo(keyed_svc):
    """Diamante con 1.000 partidas y Oro con 500: Oro se lleva la mayoría de las visitas, pero Diamante también recibe."""
    now = _now_s()
    db = keyed_svc.db
    lines = CrawlerConfig(platforms=["euw1"], tiers=["GOLD", "DIAMOND"], roles=["TOP"], role_champions=[AATROX],
                          role_pools={"TOP": [AATROX]}, focus="games", min_mastery=10_000).validated()
    with db.write() as conn:
        for puuid, tier in (("g1", "GOLD"), ("g2", "GOLD"), ("g-old", "GOLD"), ("d1", "DIAMOND"), ("d-old", "DIAMOND")):
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', ?, 1, 50)", (puuid, tier))
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps([[AATROX, 900_000, now]])))
        for puuid, hits in (("d-old", 1000), ("g-old", 500)):
            conn.execute(
                "INSERT INTO otp_progress (puuid, list_key, anchor, back_offset, back_done_at, back_done_start, back_done_reason, seen, hits, updated_at) "
                "VALUES (?, ?, ?, 100, ?, 0, 'end', ?, ?, ?)",
                (puuid, otp_list_key(lines), now - 3600, now - 3600, hits, hits, now - 3600),
            )
            conn.execute("UPDATE players SET last_crawled_at = ? WHERE puuid = ?", (now, puuid))
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], lines)
    crawler = keyed_svc.crawler
    picks = []
    for _ in range(12):
        tier = worker._next_otp()["tier"]
        picks.append(tier)
        crawler.note_visit_tier(tier)
    assert picks[0] == "GOLD" and picks.count("GOLD") >= 2 * picks.count("DIAMOND") and "DIAMOND" in picks
    report = crawler.tier_report()
    assert report["GOLD"]["parte_pct"] > report["DIAMOND"]["parte_pct"] >= 3


def test_el_reparto_por_elo_se_aplica_aunque_la_cola_este_guardada(keyed_svc, monkeypatch):
    """La cola se calcula cada 5 min; antes se consumía en el orden de cuando se calculó y las regiones repetían elos
    (Bronce, Maestro y Aspirante una y otra vez). Ahora cada jugador se elige con las visitas de ese momento."""
    from app.ingest import crawler as crawler_mod

    monkeypatch.setattr(crawler_mod, "OTP_PLAN_TTL_S", 300)
    now = _now_s()
    db = keyed_svc.db
    lines = CrawlerConfig(platforms=["euw1"], tiers=["BRONZE", "GOLD", "MASTER"], roles=["TOP"], role_champions=[AATROX],
                          role_pools={"TOP": [AATROX]}, focus="games", min_mastery=10_000).validated()
    with db.write() as conn:
        for puuid, tier in (("b1", "BRONZE"), ("b2", "BRONZE"), ("b3", "BRONZE"), ("g1", "GOLD"), ("m1", "MASTER")):
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', ?, 1, 50)", (puuid, tier))
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps([[AATROX, 900_000, now]])))
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], lines)
    calls = []
    real = worker._plan_otps
    monkeypatch.setattr(worker, "_plan_otps", lambda: calls.append(1) or real())
    picked = []
    for _ in range(3):
        row = worker._next_otp()
        picked.append(row["tier"])
        keyed_svc.crawler.note_visit_tier(row["tier"])  # lo que hace la visita al empezar
    assert len(calls) == 1  # la cola se calculó una vez
    assert sorted(picked) == ["BRONZE", "GOLD", "MASTER"]  # un elo de cada, no los 3 de Bronce seguidos


def test_el_elo_atrasado_va_antes_aunque_solo_tenga_historiales_a_medias(keyed_svc, monkeypatch):
    """Oro sin jugadores nuevos (solo uno con el historial a medias) y Maestro con jugadores nuevos: antes iban siempre
    los nuevos de Maestro primero y Oro se quedaba a 0. Ahora manda el reparto por elo."""
    from app.ingest import crawler as crawler_mod

    monkeypatch.setattr(crawler_mod, "OTP_PLAN_TTL_S", 300)
    now = _now_s()
    db = keyed_svc.db
    lines = CrawlerConfig(platforms=["euw1"], tiers=["GOLD", "MASTER"], roles=["TOP"], role_champions=[AATROX],
                          role_pools={"TOP": [AATROX]}, focus="games", min_mastery=10_000).validated()
    with db.write() as conn:
        for puuid, tier in (("g-half", "GOLD"), ("m1", "MASTER"), ("m2", "MASTER"), ("m-old", "MASTER")):
            conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES (?, 'euw1', ?, 1, 50)", (puuid, tier))
            conn.execute("INSERT INTO mastery_checks VALUES (?, 'euw1', ?, ?)", (puuid, now, json.dumps([[AATROX, 900_000, now]])))
        # Oro: uno a medias (historial aún por bajar); Maestro: uno con 1.000 partidas ya analizadas.
        for puuid, hits, reason in (("g-half", 10, None), ("m-old", 1000, "end")):
            conn.execute(
                "INSERT INTO otp_progress (puuid, list_key, anchor, back_offset, back_done_at, back_done_start, back_done_reason, seen, hits, updated_at) "
                "VALUES (?, ?, ?, 100, ?, 0, ?, ?, ?, ?)",
                (puuid, otp_list_key(lines), now - 3600, now - 3600 if reason else None, reason, hits, hits, now - 3600),
            )
            conn.execute("UPDATE players SET last_crawled_at = ? WHERE puuid = ?", (now, puuid))
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], lines)
    assert worker._next_otp()["puuid"] == "g-half"

def test_el_reparto_reciente_sigue_tras_reiniciar_y_las_visitas_en_curso_no_cuentan_doble(keyed_svc):
    """Cada sesión duraba 5-15 min (unas 10 visitas) y el reparto empezaba de cero: ahora se guarda. Una visita en curso
    cuenta ``ACTIVE_VISIT_GAMES`` menos lo que ya ha analizado (eso ya está en las partidas recientes)."""
    from app.ingest.crawler import ACTIVE_VISIT_GAMES, Crawler

    crawler = keyed_svc.crawler
    crawler.note_analyzed("GOLD", 5)
    crawler.visit_started("americas", "MASTER")
    crawler.note_analyzed("MASTER", 10, region="americas")
    assert crawler.recent_tiers()["MASTER"] == ACTIVE_VISIT_GAMES  # 10 analizadas + 15 que se esperan aún
    crawler.visit_ended("americas")
    assert crawler.recent_tiers()["MASTER"] == 10
    crawler._save_recent()
    again = Crawler(keyed_svc)
    assert again.recent_tiers() == {"GOLD": 5, "MASTER": 10}


def test_el_diagnostico_dice_que_elo_se_queda_sin_candidatos_y_lo_que_da_cada_visita(keyed_svc):
    """Oro va por detrás pero en la cola de la región solo hay Diamante: se apunta (el registro dice por qué Oro no
    sube) y cada visita queda en el embudo con lo que dio y por qué acabó."""
    now = _now_s()
    db = keyed_svc.db
    lines = CrawlerConfig(platforms=["euw1"], tiers=["GOLD", "DIAMOND"], roles=["TOP"], role_champions=[AATROX],
                          role_pools={"TOP": [AATROX]}, focus="games", min_mastery=10_000).validated()
    with db.write() as conn:
        conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES ('d1', 'euw1', 'DIAMOND', 1, 50)")
        conn.execute("INSERT INTO mastery_checks VALUES ('d1', 'euw1', ?, ?)", (now, json.dumps([[AATROX, 900_000, now]])))
    crawler = keyed_svc.crawler
    crawler.note_analyzed("DIAMOND", 300)  # Diamante muy por encima: el más atrasado es Oro
    worker = RegionWorker(crawler, "europe", ["euw1"], lines)
    assert worker._next_otp()["tier"] == "DIAMOND"
    assert worker._pick == {"mas_atrasado": "GOLD", "hay_de_ese": False, "tipo": "nuevo"}
    crawler.visit_started("europe", "DIAMOND")
    crawler.visit_ended("europe", {"elo": "DIAMOND", "analizadas": 50, "nuevas": 52, "s": 120, "fin": "tope de la visita"})
    crawler.visit_ended("europe", {"elo": "DIAMOND", "analizadas": 10, "nuevas": 10, "s": 60, "fin": "historial completo"})
    report = crawler.visit_report()
    assert report["sin_candidatos"] == {"GOLD": {"europe": 1}}
    assert report["cola_por_elo"]["DIAMOND"]["nuevos"] == 1
    assert report["visitas_por_elo"]["DIAMOND"] == {
        "visitas": 2, "analizadas": 60, "nuevas": 62, "por_visita": 30.0, "min_por_visita": 1.5, "completo_pct": 50,
    }
    assert report["ultimas_visitas"][-1]["fin"] == "historial completo"


def test_cada_elo_recibe_su_parte_aunque_unos_den_menos_partidas_por_visita():
    """Con las cifras reales y lo que da cada visita, tras 300 visitas cada elo tiene su parte (a menos de 1,5 puntos) y
    ninguno se queda a 0, tampoco Diamante, con 17.400."""
    from app.ingest.crawler import tier_need, tier_shares

    real = {"CHALLENGER": 6900, "GRANDMASTER": 3000, "MASTER": 5100, "DIAMOND": 17400, "EMERALD": 2300, "PLATINUM": 1800,
            "GOLD": 1550, "SILVER": 1700, "BRONZE": 1450, "IRON": 1800}
    shares = tier_shares(real, list(real))
    yield_of = {"IRON": 25, "BRONZE": 20, "SILVER": 25, "GOLD": 17}  # los elos bajos dan menos partidas por visita
    session: dict[str, int] = {}
    for _ in range(300):
        need = tier_need(session, shares)
        tier = max(need, key=need.get)
        session[tier] = session.get(tier, 0) + yield_of.get(tier, 50)
    total = sum(session.values())
    assert all(session.get(t, 0) > 0 for t in real)
    assert all(abs(session[t] / total - shares[t]) < 0.015 for t in real)


def test_reclassified_players_move_folder_alone_without_rebuilding_everything(keyed_svc):
    """Al arrancar, quien ya tiene partidas revisadas y no juega el campeón lo bastante pasa de otps a partidas_campeon:
    solo su carpeta (antes se regeneraban las dos enteras, con el recolector bloqueado minutos)."""
    ids, payloads = history("otp-a", 4, prefix="1", champ_for=lambda i: AATROX)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], focus="games", save_details=True)
    db = keyed_svc.db
    with db.write() as conn:  # lo daban por OTP; con 40 partidas revisadas, solo 4 con Aatrox: no lo es
        conn.execute("UPDATE otp_games SET kind = 'otp'")
        conn.execute("UPDATE otp_progress SET seen = 40, hits = 4 WHERE puuid = 'otp-a'")
    otp_details.rebuild_all(keyed_svc)
    champ_dir = otps_dir(keyed_svc) / keyed_svc.dd.name(AATROX)
    assert any(champ_dir.rglob("partidas.jsonl.gz"))
    moved = otp_details.reclassify(db)
    assert moved == [("otp-a", AATROX)]
    assert otp_details.relocate(keyed_svc, moved) == 1
    assert not any(champ_dir.rglob("partidas.jsonl.gz"))
    players_dir = keyed_svc.settings.data_dir / "partidas_campeon" / keyed_svc.dd.name(AATROX)
    assert len(read_games(next(players_dir.rglob("partidas.jsonl.gz")))) == 4
    assert otp_details.reclassify(db) == []  # ya está donde toca


def test_stop_answers_at_once_even_if_the_search_report_is_slow(keyed_svc, monkeypatch):
    """Con decenas de miles de jugadores revisados, el embudo de la búsqueda tarda segundos: antes se calculaba antes
    de avisar a los hilos y el botón «Detener» parecía no responder."""
    import time as _time

    from app.ingest import crawler as crawler_mod

    def slow_funnel(db, cfg, now=None):
        _time.sleep(1.5)
        return {"stages": [], "total": {}, "platforms": {}}

    monkeypatch.setattr(crawler_mod, "search_funnel", slow_funnel)
    keyed_svc.crawler.config = CrawlerConfig(platforms=["euw1"], tiers=["EMERALD"], champions=[AATROX]).validated()
    t0 = _time.monotonic()
    keyed_svc.crawler.stop()
    assert _time.monotonic() - t0 < 0.5 and keyed_svc.crawler.stop_event.is_set()
    # El embudo y guardar que no arranque solo van después, en segundo plano.
    deadline = _time.monotonic() + 5
    while keyed_svc.crawler._funnel_thread is None and _time.monotonic() < deadline:
        _time.sleep(0.05)
    keyed_svc.crawler._funnel_thread.join(5)
    assert not keyed_svc.crawler.autostart_enabled


def test_detener_responde_aunque_el_candado_de_escritura_este_ocupado(keyed_svc):
    """Antes, «Detener» apuntaba en la base de datos que no arrancara solo antes de responder, y esperaba al candado de
    escritura (cogido más de la mitad del tiempo por el recolector): el botón no hacía nada durante un buen rato."""
    import threading as _th
    import time as _time

    db = keyed_svc.db
    held = _th.Event()
    release = _th.Event()

    def hold():
        with db.write():
            held.set()
            release.wait(5)

    t = _th.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        t0 = _time.monotonic()
        status = keyed_svc.crawler.stop()
        assert _time.monotonic() - t0 < 0.5 and status["autostart"] is False
    finally:
        release.set()
        t.join()


def test_las_tablas_de_opgg_se_intercalan_con_la_busqueda_por_elo(keyed_svc):
    """Con muchos campeones, las tablas de los mejores (solo Diamante+) no pueden ir todas antes de buscar en Platino:
    se intercalan, una cada dos vueltas en que toca un elo alto."""
    from types import SimpleNamespace

    calls = []
    stop = __import__("threading").Event()
    boards = [1, 2, 3, 4, 5, 6]
    found = {}

    def discover(platform, tier):
        calls.append(tier)
        found[(platform, tier)] = found.get((platform, tier), 0) + 1  # cada elo revisado encuentra uno
        if len(calls) > 20:
            stop.set()
        return 1

    worker = SimpleNamespace(
        config=CrawlerConfig(tiers=["PLATINUM", "CHALLENGER"], champions=[AATROX]).validated(),
        stop_event=stop, svc=keyed_svc, crawler=SimpleNamespace(paused_reason=lambda: None), state={"errors": 0},
        scouts=[], wake=lambda: None, leaderboard_pending=lambda platform: list(boards),
        _seed_from_leaderboards=lambda platform, champs: calls.append(("board", champs[0])) or 0,
        _discover=discover, _refill_seeds=lambda platform, tier: 0, found_by_tier=found, _tier_need=lambda tiers: {},
    )
    worker.tier_order = lambda platform, tiers: crawler_mod.RegionWorker.tier_order(worker, platform, tiers)
    OtpScout(worker, "euw1").run()
    first_plat = calls.index("PLATINUM")
    assert sum(1 for c in calls[:first_plat] if isinstance(c, tuple)) <= 1  # Platino no espera a las 6 tablas
    assert [c[1] for c in calls if isinstance(c, tuple)] == boards  # y todas se piden, en orden


def test_con_lineas_enteras_no_se_piden_las_tablas_de_opgg(keyed_svc):
    """Las tablas de los mejores solo traen Diamante+ y cada jugador nuevo cuesta una llamada del cupo de las partidas:
    con líneas enteras o «cualquiera que lo juegue» no se piden."""
    games = CrawlerConfig(tiers=["GOLD", "DIAMOND"], champions=[AATROX], focus="games").validated()
    lines = CrawlerConfig(tiers=["GOLD", "DIAMOND"], champions=[AATROX], roles=["TOP"]).validated()
    for cfg in (games, lines):
        assert crawler_mod.RegionWorker(keyed_svc.crawler, "europe", ["euw1"], cfg).leaderboard_pending("euw1") == []


def test_no_se_busca_mas_en_un_elo_con_jugadores_de_sobra_esperando(keyed_svc):
    """Con cientos de jugadores de un elo aún sin visitar, buscar más solo quita CPU y base de datos a las descargas."""
    from types import SimpleNamespace

    calls = []
    stop = __import__("threading").Event()

    def discover(platform, tier):
        calls.append(tier)
        if len(calls) >= 3:
            stop.set()
        return 1

    worker = SimpleNamespace(
        config=CrawlerConfig(tiers=["GOLD", "DIAMOND"], champions=[AATROX], focus="games").validated(),
        stop_event=stop, svc=keyed_svc, crawler=SimpleNamespace(paused_reason=lambda: None), state={"errors": 0},
        scouts=[], wake=lambda: None, leaderboard_pending=lambda platform: [],
        _seed_from_leaderboards=lambda platform, champs: 0, _discover=discover, _refill_seeds=lambda platform, tier: 0,
        found_by_tier={}, waiting_by_tier={("euw1", "DIAMOND"): crawler_mod.SCOUT_BACKLOG}, _tier_need=lambda tiers: {},
    )
    worker.tier_order = lambda platform, tiers: crawler_mod.RegionWorker.tier_order(worker, platform, tiers)
    OtpScout(worker, "euw1").run()
    assert calls and set(calls) == {"GOLD"}


def test_la_siguiente_partida_se_descarga_mientras_se_analiza_sin_repetir_llamadas(keyed_svc):
    """Con la descarga adelantada, cada partida y cada timeline se piden una sola vez y se guarda lo mismo."""
    ids, payloads = history("otp-a", 8, prefix="1", champ_for=lambda i: AATROX if i % 2 else GAREN)
    fake = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
    run_history(keyed_svc, fake, champions=[AATROX], save_details=True)
    matches = [c[1] for c in fake.calls_to("match")]
    timelines = [c[1] for c in fake.calls_to("timeline")]
    wanted = {ids[i] for i in range(8) if i % 2}
    assert len(matches) == len(set(matches)) == 8
    assert sorted(timelines) == sorted(wanted)  # solo las suyas con Aatrox, una vez cada una
    assert set(details(keyed_svc)) == wanted


def test_el_umbral_para_dejar_de_buscar_cabe_en_la_cola():
    """La cola solo carga PLAN_UNVISITED_PER_TIER sin visitar por plataforma y elo: si el umbral fuera mayor, el contador
    nunca llegaría y las búsquedas no pararían nunca."""
    assert crawler_mod.SCOUT_BACKLOG < crawler_mod.PLAN_UNVISITED_PER_TIER


def test_reanalizar_lee_la_partida_en_bruto_de_la_base_de_datos_o_de_su_fichero(keyed_svc):
    """Las partidas guardadas antes tienen el bruto en la base de datos; las nuevas, en data/raw_otp."""
    from app.coach.store import pack

    db = keyed_svc.db
    match, timeline = {"metadata": {"matchId": "EUW1_1"}}, {"info": {"frames": [1]}}
    with db.write() as conn:
        conn.execute(
            "INSERT INTO otp_games (match_id, puuid, champ, win, version, fetched_at, match_raw, timeline_raw) "
            "VALUES ('EUW1_0', 'p', 1, 1, 1, 1, ?, ?)",
            (pack({"metadata": {"matchId": "EUW1_0"}}), pack(timeline)),
        )
        conn.execute("INSERT INTO otp_games (match_id, puuid, champ, win, version, fetched_at) VALUES ('EUW1_1', 'p', 1, 1, 1, 1)")
    assert otp_details.save_raw(keyed_svc, "EUW1_1", match, timeline)
    assert otp_details.raw_path(keyed_svc, "EUW1_1").exists()
    assert otp_details.load_raw(db, keyed_svc, "EUW1_0", "p")[0]["metadata"]["matchId"] == "EUW1_0"
    assert otp_details.load_raw(db, keyed_svc, "EUW1_1", "p") == (match, timeline)
    assert otp_details.load_raw(db, keyed_svc, "EUW1_9", "p") == (None, None)


def test_al_detener_se_sube_lo_recogido_cuando_todo_esta_parado(keyed_svc, monkeypatch):
    """«Detener» sube a GitHub en segundo plano lo último recogido (antes había que hacer push a mano), pero solo con
    todo parado y si al arrancar se comprobó que git funciona."""
    from app import autopush

    class FakePush:
        def __init__(self, git_ok):
            self.state = {"git_ok": git_ok}
            self.started = []

        def public(self):
            return {"enabled": True}

        def start(self, wait_summaries=False):
            self.started.append(keyed_svc.crawler.running)  # ¿seguía algún hilo del recolector?
            assert wait_summaries  # al detener, la subida espera a los resúmenes por campeón
            return True

    for git_ok, expected in ((True, [False]), (None, [])):
        fake = FakePush(git_ok)
        monkeypatch.setattr(autopush.AutoPush, "of", classmethod(lambda cls, svc, f=fake: f))
        ids, payloads = history("otp-a", 2, prefix="1")
        fake_riot = HistoryRiot({"otp-a": ids}, payloads, {"otp-a": [(AATROX, 900_000)]})
        run_history(keyed_svc, fake_riot, champions=[AATROX])
        assert fake.started == expected


def test_una_lista_de_jugadores_vacia_se_vuelve_a_leer_pronto(keyed_svc, monkeypatch):
    """Al arrancar una búsqueda nueva aún no hay candidatos: la lista vacía se guardaba 2 min y las regiones se quedaban
    ~3 min sin visitar a nadie aunque la búsqueda ya los hubiera encontrado (prueba real del 24-11-2026). Ahora una vacía
    se vuelve a leer a los ``OTP_PLAN_EMPTY_S``; una con jugadores, a los ``OTP_ROWS_FRESH_S``."""
    from app.ingest import crawler as crawler_mod

    monkeypatch.setattr(crawler_mod, "OTP_ROWS_FRESH_S", 120)
    monkeypatch.setattr(crawler_mod, "OTP_PLAN_EMPTY_S", 0)
    now = _now_s()
    cfg = CrawlerConfig(platforms=["euw1"], tiers=["CHALLENGER"], champions=[AATROX], focus="mastery", min_mastery=10_000).validated()
    crawler = keyed_svc.crawler
    assert crawler.otp_rows_cached(cfg)[0] == []
    with keyed_svc.db.write() as conn:
        conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES ('p1', 'euw1', 'CHALLENGER', 1, 900)")
        conn.execute("INSERT INTO mastery_checks VALUES ('p1', 'euw1', ?, ?)", (now, json.dumps([[AATROX, 900_000, now]])))
    first = crawler.otp_rows_cached(cfg)[0]
    assert [row["puuid"] for row, _top in first] == ["p1"]  # la vacía no se reutiliza
    with keyed_svc.db.write() as conn:
        conn.execute("INSERT INTO players (puuid, platform, tier, is_seed, lp) VALUES ('p2', 'euw1', 'CHALLENGER', 1, 800)")
        conn.execute("INSERT INTO mastery_checks VALUES ('p2', 'euw1', ?, ?)", (now, json.dumps([[AATROX, 800_000, now]])))
    assert crawler.otp_rows_cached(cfg)[0] is first  # con jugadores, se reutiliza hasta los 2 min


def test_la_busqueda_en_el_ranking_va_primero_al_elo_con_menos_datos(keyed_svc):
    """24-11-2026: la búsqueda iba al elo con menos jugadores encontrados (a igualdad, el más alto): tras encontrar 250
    de Platino buscaba en Oro aunque Bronce tuviera menos partidas. Ahora sigue el mismo reparto que las visitas."""
    lines = CrawlerConfig(platforms=["euw1"], tiers=["BRONZE", "GOLD", "PLATINUM"], roles=["TOP"], role_champions=[AATROX],
                          role_pools={"TOP": [AATROX]}, focus="games", min_mastery=10_000).validated()
    worker = RegionWorker(keyed_svc.crawler, "europe", ["euw1"], lines)
    worker._plan_totals = {"BRONZE": 50, "GOLD": 100, "PLATINUM": 300}
    worker.found_by_tier = {("euw1", "BRONZE"): 500, ("euw1", "PLATINUM"): 250}
    assert worker.tier_order("euw1", list(lines.tiers)) == ["BRONZE", "GOLD", "PLATINUM"]
    worker._plan_totals = {"BRONZE": 100, "GOLD": 100, "PLATINUM": 100}  # a igualdad: menos jugadores encontrados
    assert worker.tier_order("euw1", list(lines.tiers)) == ["GOLD", "PLATINUM", "BRONZE"]


def test_el_buscador_de_la_grafica_del_total_cuenta_las_partidas_de_un_campeon_por_elo(keyed_svc):
    """«Partidas por elo (total)» con un campeón elegido: sus partidas analizadas por elo, de OTPs y de otros jugadores."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    rows = [("m1", AATROX, "GOLD", "otp"), ("m2", AATROX, "GOLD", "player"), ("m3", AATROX, "GOLD", None),
            ("m4", AATROX, "DIAMOND", "otp"), ("m5", GAREN, "GOLD", "otp")]
    with keyed_svc.db.write() as conn:
        for mid, champ, tier, kind in rows:
            conn.execute("INSERT INTO otp_games (match_id, puuid, champ, win, tier, kind, version, fetched_at) VALUES (?, 'p', ?, 1, ?, ?, 1, 1)",
                         (mid, champ, tier, kind))
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    with TestClient(app) as client:
        res = client.get(f"/api/crawler/otps/by-tier?champ={AATROX}")
    assert res.status_code == 200, res.text
    assert res.json() == {"champ": AATROX, "tiers": {"GOLD": 3, "DIAMOND": 1}, "tiers_otp": {"GOLD": 1, "DIAMOND": 1}, "tiers_player": {"GOLD": 1}}
    # Bajo cada elo del total: el campeón con más partidas (todas) y el que más tiene de OTPs. En Oro, Aatrox tiene 3
    # (1 de OTP) y Garen 2, las dos de OTP.
    with keyed_svc.db.write() as conn:
        conn.execute("INSERT INTO otp_games (match_id, puuid, champ, win, tier, kind, version, fetched_at) VALUES ('m6', 'q', ?, 1, 'GOLD', 'otp', 1, 1)", (GAREN,))
    top = otp_details.counts(keyed_svc.db)["top_champs"]
    assert top == {"GOLD": {"all": AATROX, "otp": GAREN}, "DIAMOND": {"all": AATROX, "otp": AATROX}}


def test_con_cualquier_combinacion_de_elos_todos_reciben_partidas_y_mas_los_que_menos_tienen():
    """Las 1.023 combinaciones de elos que se pueden elegir, con las cifras reales del 25-11-2026 y lo que da cada visita
    (los elos bajos, menos partidas por visita): ningún elo elegido se queda sin partidas nuevas (ni Diamante, con
    17.487), el que menos tiene recibe más y cada uno se lleva su parte (a menos de 2 puntos)."""
    from itertools import combinations

    from app.ingest.crawler import MIN_TIER_SHARE, tier_need, tier_shares

    real = {"IRON": 2573, "BRONZE": 2419, "SILVER": 2524, "GOLD": 2458, "PLATINUM": 2574, "EMERALD": 2825,
            "DIAMOND": 17487, "MASTER": 5210, "GRANDMASTER": 3330, "CHALLENGER": 7007}
    yield_of = {"IRON": 12, "BRONZE": 9, "SILVER": 15, "GOLD": 17, "PLATINUM": 20, "EMERALD": 25}
    for k in range(1, len(real) + 1):
        for chosen in combinations(real, k):
            shares = tier_shares(real, list(chosen))
            assert abs(sum(shares.values()) - 1) < 1e-9
            assert all(shares[t] >= min(MIN_TIER_SHARE, 1 / k) - 1e-12 for t in chosen)
            by_games = sorted(chosen, key=lambda t: real[t])  # de menos a más partidas: de más a menos parte
            assert all(shares[a] >= shares[b] for a, b in zip(by_games, by_games[1:]))
            session: dict[str, int] = {}
            for _ in range(400):
                need = tier_need(session, shares)
                tier = max(need, key=need.get)
                session[tier] = session.get(tier, 0) + yield_of.get(tier, 50)
            total = sum(session.values())
            assert all(session.get(t, 0) > 0 for t in chosen), chosen
            assert all(abs(session[t] / total - shares[t]) < 0.02 for t in chosen), chosen


def test_a_ranking_game_found_again_as_otp_moves_to_the_yellow_bar_at_once(keyed_svc):
    """Con «todo el ranking» la partida ya contaba como «resto» (azul). Si la búsqueda de OTPs la vuelve a guardar como
    OTP, tiene que pasar a la barra amarilla de «Partidas por elo (total)» al momento (antes, en el recuento de 5 min)."""
    from types import SimpleNamespace

    from app.ingest import otp_details

    db = keyed_svc.db
    svc = SimpleNamespace(db=db, dd=keyed_svc.dd, settings=keyed_svc.settings)
    match = make_match("EUW1_701", puuids={1: "p-otp"})
    otp_details.live_counts(db)  # la gráfica abierta: contadores al momento
    assert otp_details.save_ranking_game(db, svc, match, None, "GOLD") >= 1
    live = otp_details.live_counts(db)
    otp, rest, total = live["tiers_otp"].get("GOLD", 0), live["tiers_player"].get("GOLD", 0), live["tiers"]["GOLD"]
    assert rest >= 1
    assert otp_details.save(db, svc, match, None, "p-otp", {"tier": "GOLD", "division": "I", "lp": 10}, kind="otp")
    live = otp_details.live_counts(db)
    assert live["tiers_otp"].get("GOLD", 0) == otp + 1 and live["tiers_player"].get("GOLD", 0) == rest - 1
    assert live["tiers"]["GOLD"] == total and live["otp_games"] >= 1  # la misma partida: el total no cambia
    assert live["top_champs"]["GOLD"]["otp"] == AATROX
    # Y el recuento de la tabla da lo mismo
    fresh = otp_details.counts(db)
    assert fresh["tiers_otp"].get("GOLD", 0) == otp + 1 and fresh["tiers_player"].get("GOLD", 0) == rest - 1


def test_games_saved_before_knowing_what_he_plays_move_to_the_yellow_bar_when_his_review_ends(keyed_svc):
    """Buscando OTPs de 50.000 puntos en Bronce y Plata: sus primeras partidas se guardan antes de tener 20 revisadas y
    sin 100.000 puntos se marcaban «resto» (azul) hasta reiniciar la app. Al terminar de revisarlo, si la mitad de lo
    que juega es ese campeón, pasan a OTP (barra amarilla) al momento."""
    from types import SimpleNamespace

    from app.ingest import otp_details

    db = keyed_svc.db
    svc = SimpleNamespace(db=db, dd=keyed_svc.dd, settings=keyed_svc.settings)
    otp_details.live_counts(db)  # la gráfica abierta: contadores al momento
    for i in range(12):
        match = make_match(f"EUW1_80{i:02d}", puuids={1: "p-low"})
        assert otp_details.save(db, svc, match, None, "p-low", {"tier": "SILVER", "division": "II", "lp": 40})
    live = otp_details.live_counts(db)
    assert live["tiers_player"].get("SILVER", 0) == 12 and live["tiers_otp"].get("SILVER", 0) == 0
    assert otp_details.reclassify_player(db, "p-low") == []  # aún sin 20 partidas revisadas: no se sabe
    with db.write() as conn:
        conn.execute("INSERT INTO otp_progress (puuid, list_key, anchor, seen, hits, updated_at) VALUES ('p-low', '420', 0, 20, 12, 0)")
    assert otp_details.reclassify_player(db, "p-low") == [("p-low", AATROX)]
    live = otp_details.live_counts(db)
    assert live["tiers_otp"].get("SILVER", 0) == 12 and live["tiers_player"].get("SILVER", 0) == 0
    assert live["tiers"]["SILVER"] == 12 and live["otp_games"] == 12
    assert db.scalar("SELECT COUNT(*) FROM otp_games WHERE puuid = 'p-low' AND kind = 'otp'") == 12
    assert otp_details.reclassify_player(db, "p-low") == []  # ya está bien


def test_games_of_a_player_still_under_review_count_as_unclassified_so_the_yellow_keeps_its_share(keyed_svc):
    """Mientras se revisa a un jugador sin 20 partidas, su clase es provisional: sus partidas cuentan como sin clasificar
    (la web las reparte con el % de OTPs de su elo) y el amarillo crece con el total. Antes entraban en azul («resto») y
    el amarillo se quedaba quieto mientras la barra crecía. Al acabar de revisarlo pasan a su clase, en vivo y en el
    recuento de cada 5 min."""
    from types import SimpleNamespace

    from app.ingest import otp_details

    db = keyed_svc.db
    svc = SimpleNamespace(db=db, dd=keyed_svc.dd, settings=keyed_svc.settings)
    with db.write() as conn:  # el recolector lo está revisando: 5 partidas vistas, historial sin acabar
        conn.execute("INSERT INTO otp_progress (puuid, list_key, anchor, seen, hits, updated_at) VALUES ('p-mid', '420', 0, 5, 5, 0)")
    otp_details.live_counts(db)  # la gráfica abierta: contadores al momento
    for i in range(12):
        match = make_match(f"EUW1_81{i:02d}", puuids={1: "p-mid"})
        assert otp_details.save(db, svc, match, None, "p-mid", {"tier": "SILVER", "division": "II", "lp": 40})
    for counted in (otp_details.live_counts(db), otp_details.counts(db)):  # en vivo y en el recuento, lo mismo
        assert counted["tiers"]["SILVER"] == 12
        assert counted["tiers_player"].get("SILVER", 0) == 0 and counted["tiers_otp"].get("SILVER", 0) == 0
    with db.write() as conn:
        conn.execute("UPDATE otp_progress SET seen = 20 WHERE puuid = 'p-mid'")
    assert otp_details.reclassify_player(db, "p-mid") == [("p-mid", AATROX)]
    live = otp_details.live_counts(db)
    assert live["tiers_otp"]["SILVER"] == 12 and live["tiers_player"].get("SILVER", 0) == 0 and live["tiers"]["SILVER"] == 12
    assert otp_details.counts(db)["tiers_otp"]["SILVER"] == 12
    assert otp_details.classify_ex(db, "p-mid", AATROX) == ("otp", True)


def test_a_finished_review_without_enough_games_keeps_the_class_it_was_saved_with(keyed_svc):
    """Con el historial completo y menos de 10 partidas no se puede juzgar: lo provisional se queda como se guardó (ya no
    va a cambiar), también en la gráfica."""
    from types import SimpleNamespace

    from app.ingest import otp_details

    db = keyed_svc.db
    svc = SimpleNamespace(db=db, dd=keyed_svc.dd, settings=keyed_svc.settings)
    with db.write() as conn:
        conn.execute("INSERT INTO otp_progress (puuid, list_key, anchor, seen, hits, updated_at) VALUES ('p-few', '420', 0, 4, 4, 0)")
    otp_details.live_counts(db)
    for i in range(4):
        assert otp_details.save(db, svc, make_match(f"EUW1_82{i:02d}", puuids={1: "p-few"}), None, "p-few", {"tier": "BRONZE", "division": "I", "lp": 1})
    assert otp_details.live_counts(db)["tiers_player"].get("BRONZE", 0) == 0  # aún provisional
    with db.write() as conn:
        conn.execute("UPDATE otp_progress SET back_done_at = 1, back_done_reason = 'end' WHERE puuid = 'p-few'")
    assert otp_details.reclassify_player(db, "p-few") == []
    assert otp_details.live_counts(db)["tiers_player"]["BRONZE"] == 4 and otp_details.counts(db)["tiers_player"]["BRONZE"] == 4


def test_a_short_but_complete_history_is_enough_to_judge_an_otp(keyed_svc):
    """En Bronce y Plata muchos juegan 10-15 partidas por temporada y nunca llegaban a 20 revisadas: con su historial del
    periodo entero revisado, bastan 10 para decir si es OTP (son todas las que tiene, no una muestra)."""
    from types import SimpleNamespace

    from app.ingest import otp_details

    db = keyed_svc.db
    svc = SimpleNamespace(db=db, dd=keyed_svc.dd, settings=keyed_svc.settings)
    for i in range(8):
        match = make_match(f"EUW1_81{i:02d}", puuids={1: "p-few"})
        assert otp_details.save(db, svc, match, None, "p-few", {"tier": "BRONZE", "division": "I", "lp": 5})
    with db.write() as conn:
        conn.execute("INSERT INTO otp_progress (puuid, list_key, anchor, seen, hits, updated_at) VALUES ('p-few', '420', 0, 12, 8, 0)")
    assert otp_details.reclassify_player(db, "p-few") == []  # 12 revisadas y quedan más: aún no se sabe
    with db.write() as conn:
        conn.execute("UPDATE otp_progress SET back_done_at = 1, back_done_reason = 'end' WHERE puuid = 'p-few'")
    assert otp_details.reclassify_player(db, "p-few") == [("p-few", AATROX)]  # todas las que tiene: 8 de 12
    assert otp_details.judgeable(9, "end") is False and otp_details.judgeable(19, "share") is False


def test_the_total_by_elo_is_counted_from_an_index_not_the_whole_table(keyed_svc):
    """Los recuentos de «Partidas por elo (total)» no leen la tabla con los análisis (10 s cada 5 min en el PC del
    usuario): todas sus consultas salen del índice ``idx_otp_games_counts``."""
    db = keyed_svc.db
    queries = [
        "SELECT COUNT(*) AS games, COUNT(DISTINCT puuid) AS players, SUM(has_timeline) AS timelines FROM otp_games",
        "SELECT tier, COUNT(*) AS n FROM otp_games GROUP BY tier",
        "SELECT COUNT(*) AS games, COUNT(DISTINCT puuid) AS players FROM otp_games WHERE kind = 'otp'",
        "SELECT tier, champ, kind, COUNT(*) AS n FROM otp_games GROUP BY tier, champ, kind",
    ]
    for sql in queries:
        plan = " ".join(str(r[-1]) for r in db.query("EXPLAIN QUERY PLAN " + sql))
        assert "COVERING INDEX idx_otp_games_counts" in plan, (sql, plan)


def test_games_lacking_analysis_are_found_from_an_index_not_the_table(keyed_svc):
    """«Qué partidas de estos 400 jugadores con los campeones buscados faltan por analizar» sale del índice (1,7 s por
    consulta en el PC del usuario cuando leía cada fila de ``participants``)."""
    from app.ingest.crawler import CrawlerConfig, target_sql

    cfg = CrawlerConfig(champions=[AATROX], focus="otp", champion_roles={AATROX: ["TOP"]}).validated()
    cond, params = target_sql(cfg, "p")
    sql = (
        "SELECT DISTINCT p.player_id FROM participants p JOIN players pl ON pl.id = p.player_id "
        "LEFT JOIN otp_games o ON o.match_id = p.match_id AND o.puuid = pl.puuid "
        f"WHERE p.player_id IN (?, ?, ?) AND {cond} AND o.match_id IS NULL"
    )
    plan = " ".join(str(r[-1]) for r in keyed_svc.db.query("EXPLAIN QUERY PLAN " + sql, (1, 2, 3, *params)))
    assert "COVERING INDEX idx_participants_player_champ" in plan, plan
