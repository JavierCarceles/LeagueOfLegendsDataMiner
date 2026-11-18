"""Seguimiento de tus partidas: geometría, análisis, conclusiones, recolector y API."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

import app.services
from app.coach import geometry as geo
from app.coach import insights, store
from app.coach.analyzer import NotInMatch, analyze_game, find_me
from app.coach.benchmarks import References, static_refs
from app.coach.tracker import GameTracker, digest
from app.main import create_app
from app.riot.client import RiotAPIError
from tests.factories import (
    AATROX,
    GAME_START_MS,
    LEE_SIN,
    TEST_KEY,
    VIEGO,
    make_match,
    make_settings,
    make_timeline,
    write_ddragon_cache,
    write_env,
)

ME = "puuid-yo"
ME_OVERRIDES = {1: {"deaths": 4, "kills": 1, "totalTimeSpentDead": 140, "riotIdGameName": "Tempu", "riotIdTagline": "EUW"}}


def my_match(match_id: str, *, start_offset_h: int = 0, **kw) -> dict:
    return make_match(
        match_id,
        duration=1800,
        puuids={1: ME},
        overrides=copy.deepcopy(ME_OVERRIDES),
        game_start=GAME_START_MS + start_offset_h * 3_600_000,
        **kw,
    )


@pytest.fixture(scope="module")
def scenario():
    match = my_match("EUW1_9100000001")
    timeline = make_timeline(match)
    return match, timeline, analyze_game(match, timeline, pid=1)


# ------------------------------------------------------------------ geometría
@pytest.mark.parametrize(
    "point, expected",
    [
        ((400, 420), "base_100"),
        ((14340, 14390), "base_200"),
        ((981, 10441), "top"),
        ((5846, 6396), "mid"),
        ((13866, 4505), "bot"),
        (geo.PITS["baron"], "river"),
        (geo.PITS["dragon"], "river"),
        (geo.CAMPS[(100, "blue")], "jungle_100_top"),
        (geo.CAMPS[(100, "krugs")], "jungle_100_bot"),
        (geo.CAMPS[(200, "red")], "jungle_200_top"),
        (geo.CAMPS[(200, "gromp")], "jungle_200_bot"),
    ],
)
def test_zones(point, expected):
    assert geo.zone(point) == expected


def test_map_is_symmetric_and_overextension_uses_turrets():
    assert geo.mirror(geo.PITS["baron"]) == pytest.approx(geo.PITS["dragon"], abs=120)
    assert geo.nearest_camp(geo.CAMPS[(200, "wolves")]) == (200, "wolves")
    assert geo.overextended(100, "top", (2200, 13300))
    assert not geo.overextended(100, "top", (1200, 9000))
    assert geo.overextended(200, "top", (1200, 9000))


# ------------------------------------------------------------------- análisis
def test_deaths_are_classified(scenario):
    _, _, a = scenario
    deaths = {d["t"]: d for d in a["deaths"]}
    assert set(deaths) == {310.0, 690.0, 1440.0, 1545.0}
    gank = deaths[310.0]
    assert {"jungler", "gank", "overextended", "no_ward", "before_objective"} <= set(gank["tags"])
    assert gank["severity"] == "high" and gank["early"] and gank["zone"] == "top"
    assert {"solo", "no_ward"} <= set(deaths[690.0]["tags"]) and "gank" not in deaths[690.0]["tags"]
    assert {"enemy_jungle", "unspent_gold", "isolated"} <= set(deaths[1440.0]["tags"])
    assert "before_objective" in deaths[1545.0]["tags"]
    moment = next(m for m in a["moments"] if m["t"] == 310.0)
    assert moment["title"] == f"Gank de {{champ:{VIEGO}}} (jungla rival)"
    assert "adelantado" in moment["detail"] and "larvas" in moment["detail"]


def test_laner_metrics(scenario):
    _, _, a = scenario
    m = a["metrics"]
    expected = {
        "deaths": 4, "deaths_pre14": 2, "deaths_to_jungler_pre14": 1, "deaths_ganked": 1, "deaths_overextended": 1,
        "deaths_no_vision_pre14": 2, "deaths_solo": 2, "deaths_enemy_jungle": 1, "deaths_unspent_gold": 1,
        "deaths_before_objective": 2, "deaths_isolated_late": 1, "obj_lost_dead": 1, "objectives_team": 1,
        "obj_participation": 0.0, "gold_hoard_min": 3, "idle_min": 3, "cs10": 60, "cs_diff10": -15,
        "gold_diff15": -1200, "wards_pre14": 3, "control_wards_placed": 1, "recalls": 2, "first_recall_min": 18.5,
        "control_wards_bought": 1,
    }
    assert {k: m[k] for k in expected} == expected
    assert m["time_dead_pct"] == pytest.approx(140 / 1800 * 100, abs=0.1)
    kinds = {mo["kind"] for mo in a["moments"]}
    assert {"death", "gold", "idle", "objective", "lane"} <= kinds
    assert [mo["t"] for mo in a["moments"]] == sorted(mo["t"] for mo in a["moments"])


def test_grubs_are_merged_and_replay_is_compact(scenario):
    _, _, a = scenario
    replay = a["replay"]
    assert [o[1] for o in replay["objectives"]] == ["grubs", "dragon", "baron"]
    assert len(replay["tracks"]) == 10
    me = replay["tracks"]["1"]
    assert len(me["dead"]) == 4
    times = [p[0] for p in me["a"]]
    assert times == sorted(times)
    # Mientras está muerto no hay puntos de paso de frames (solo la muerte y la reaparición).
    start, end = me["dead"][0][:2]
    assert all(not (start < p[0] < end) for p in me["a"])
    assert a["series"]["minute"][:3] == [0.0, 1.0, 2.0]
    assert a["series"]["gold"][15] - a["series"]["opp_gold"][15] == -1200


def test_jungle_analysis():
    match = my_match("EUW1_9100000002")
    a = analyze_game(match, make_timeline(match), pid=2)
    assert a["role"] == "JUNGLE" and a["champ"] == LEE_SIN
    m = a["metrics"]
    assert m["ganks_pre10"] == 1 and m["first_gank_min"] == 6.5
    assert m["enemy_ganks_far"] == 1  # el jungla rival ganqueó top mientras pid 2 estaba abajo
    assert m["obj_participation"] == 100.0
    assert a["jungle"]["start_camp"] == "red" and a["jungle"]["start_side"] == "bot"
    assert a["jungle"]["path"][0]["camp"] == "red"


def test_without_timeline_only_summary_metrics():
    match = my_match("EUW1_9100000003")
    a = analyze_game(match, None, pid=1)
    assert a["has_timeline"] is False
    assert a["metrics"]["deaths"] == 4 and a["metrics"]["cs_min"] > 0
    assert a["metrics"]["deaths_pre14"] is None
    assert "replay" not in a


def test_find_me_by_puuid_or_riot_id():
    match = my_match("EUW1_9100000004")
    assert find_me(match, puuid=ME)["participantId"] == 1
    # Con otra key el PUUID cifrado cambia: se reconoce por el Riot ID.
    assert find_me(match, puuid="otro", game_name="tempu", tag_line="euw")["participantId"] == 1
    with pytest.raises(NotInMatch):
        find_me(match, puuid="otro", game_name="Nadie", tag_line="X")


# ---------------------------------------------------------------- conclusiones
def _rows(n: int) -> list[dict]:
    rows = []
    for i in range(n):
        match = my_match(f"EUW1_92000000{i:02d}", start_offset_h=-i)
        a = analyze_game(match, make_timeline(match), pid=1)
        d = digest(a)
        rows.append(
            {
                "match_id": a["match_id"], "queue": 420, "game_start": a["game_start"], "duration": a["duration"], "champ": a["champ"],
                "role": a["role"], "team": a["team"], "win": a["win"], "status": "ready", "metrics": d["m"], "deaths": d["deaths"],
                "jungle": d["jungle"], "has_timeline": True, "issues": d["issues"],
            }
        )
    return rows


def test_summary_finds_weaknesses_with_tips_and_goals():
    games = _rows(4)
    refs = References(None, "PLATINUM")
    goal = {"id": 1, "metric": "deaths_pre14", "op": "<=", "target": 1, "label": "x", "role": None, "active": True}
    out = insights.summarize(games, refs, [goal])
    ids = [w["id"] for w in out["weaknesses"]]
    assert "deaths_to_jungler_pre14" in ids and "obj_participation" in ids and "gold_hoard_min" in ids
    assert out["weaknesses"] == sorted(out["weaknesses"], key=lambda w: w["severity"], reverse=True)
    gank = next(w for w in out["weaknesses"] if w["id"] == "deaths_to_jungler_pre14")
    assert gank["tip"] and gank["goal"]["metric"] == "deaths_to_jungler_pre14" and gank["goal"]["target"] == 0
    assert "sin ward" in gank["detail"]  # contexto de las muertes tempranas
    g = out["goals"][0]
    assert g["games"] == 4 and g["rate"] == 0 and len(g["history"]) == 4  # 2 muertes antes del 14 en cada partida
    assert out["readiness"]["ready"] is False and out["readiness"]["missing_games"] == 6
    assert out["deaths"]["total"] == 16 and out["deaths"]["tags"]["gank"]["count"] == 4
    assert out["champions"][0]["champ"] == AATROX and out["champions"][0]["games"] == 4


def test_references_scale_with_target_tier():
    gold, emerald = static_refs("TOP", "GOLD"), static_refs("TOP", "EMERALD")
    assert gold["cs_min"] < emerald["cs_min"]
    assert gold["deaths_pre14"] > emerald["deaths_pre14"]
    assert gold["cs_diff10"] == emerald["cs_diff10"] == 0
    assert "cs_min" not in static_refs("SUPPORT", "GOLD")


def test_fundamentals_score_is_bounded():
    refs = static_refs("TOP", "PLATINUM")
    assert insights.fundamentals_score(dict(refs), refs, "TOP") == 5.0
    great = {**refs, "deaths_pre14": 0, "deaths_to_jungler_pre14": 0, "cs_min": 10, "kp": 80, "idle_pct": 0, "gold_hoard_min": 0}
    assert 5 < insights.fundamentals_score(great, refs, "TOP") <= 10


# --------------------------------------------------------------------- store
def test_profile_roundtrip_and_account_change(db):
    p = store.save_profile(db, game_name="Tempu", tag_line="EUW", platform="euw1", puuid="a")
    assert p["settings"]["backfill"] == 50 and store.known_puuids(p) == ["a"]
    p = store.save_profile(db, game_name="tempu", tag_line="euw", platform="euw1", puuid="b")  # misma cuenta, otra key
    assert store.known_puuids(p) == ["a", "b"]
    p = store.save_profile(db, game_name="Otra", tag_line="EUW", platform="euw1", puuid="c")
    assert store.known_puuids(p) == ["c"] and p["settings"]["scanned"] == 0
    assert store.update_profile(db, target_tier="gold", settings={"backfill": 100})["target_tier"] == "GOLD"
    store.delete_profile(db)
    assert store.get_profile(db) is None


def test_goals_crud_and_no_duplicates(db):
    g = store.add_goal(db, metric="deaths_pre14", op="<=", target=1, label="Morir 1")
    again = store.add_goal(db, metric="deaths_pre14", op="<=", target=0, label="Morir 0")
    assert again["id"] == g["id"] and again["target"] == 0
    with pytest.raises(ValueError):
        store.add_goal(db, metric="nope", op="<=", target=1, label="x")
    assert store.update_goal(db, g["id"], active=False)["active"] is False
    assert store.list_goals(db, active_only=True) == []
    assert store.delete_goal(db, g["id"]) and not store.delete_goal(db, g["id"])


# ------------------------------------------------------------------ tracker
class FakeCoachRiot:
    def __init__(self, ids: list[str], matches: dict, timelines: dict) -> None:
        self.puuid = ME
        self.ids = ids
        self.matches = matches
        self.timelines = timelines
        self.calls: list[tuple] = []

    def account_by_riot_id(self, game_name, tag_line, platform, **kw):
        self.calls.append(("account", game_name, tag_line))
        return {"puuid": self.puuid, "gameName": "Tempu", "tagLine": "EUW"}

    def active_platform(self, puuid, platform_hint="euw1", **kw):
        return "euw1"

    def summoner_by_puuid(self, platform, puuid, **kw):
        return {"profileIconId": 29, "summonerLevel": 321}

    def match_ids(self, platform, puuid, *, queue=None, ranked_only=False, start_time=None, start=0, count=20, **kw):
        self.calls.append(("ids", puuid, start, count, start_time))
        if puuid != self.puuid:
            raise RiotAPIError(400, "Exception decrypting")
        ids = [i for i in self.ids if not start_time or self.matches[i]["info"]["gameStartTimestamp"] >= start_time * 1000]
        return ids[start : start + count]

    def match(self, match_id, **kw):
        self.calls.append(("match", match_id))
        payload = self.matches.get(match_id)
        return copy.deepcopy(payload) if payload else None

    def timeline(self, match_id, **kw):
        self.calls.append(("timeline", match_id))
        payload = self.timelines.get(match_id)
        return copy.deepcopy(payload) if payload else None

    def count(self, kind: str) -> int:
        return sum(1 for c in self.calls if c[0] == kind)


def _fake_world() -> FakeCoachRiot:
    sr = [my_match(f"EUW1_930000000{i}", start_offset_h=-i, queue=420 if i % 2 else 400) for i in range(1, 5)]
    aram = make_match("EUW1_9300000009", puuids={1: ME}, queue=450, map_id=12)
    remake = make_match("EUW1_9300000008", puuids={1: ME}, duration=200)
    pending = my_match("EUW1_9300000007", start_offset_h=-10)
    matches = {m["metadata"]["matchId"]: m for m in [*sr, aram, remake, pending]}
    timelines = {m["metadata"]["matchId"]: make_timeline(m) for m in sr}
    ids = [m["metadata"]["matchId"] for m in [*sr, aram, remake, pending]]
    return FakeCoachRiot(ids, matches, timelines)


def test_tracker_sync_is_incremental(svc):
    fake = _fake_world()
    svc.riot = fake
    store.save_profile(svc.db, game_name="Tempu", tag_line="EUW", platform="euw1")
    tracker = GameTracker(svc)
    result = tracker.sync()
    assert result["new"] == 5  # 4 con timeline + 1 a la espera del timeline
    assert ("ids", ME, 0, 50, None) in fake.calls  # primera pasada: hasta 50 partidas
    states = {r["match_id"]: r["status"] for r in svc.db.query("SELECT match_id, status FROM my_games")}
    assert states["EUW1_9300000009"] == "ignored" and states["EUW1_9300000008"] == "ignored"
    assert states["EUW1_9300000007"] == "pending_timeline"
    assert sum(1 for s in states.values() if s == "ready") == 4
    profile = store.get_profile(svc.db)
    assert profile["puuid"] == ME and profile["profile_icon"] == 29 and profile["settings"]["scanned"] == 50
    # También queda en la BD general (vista «Partida») sin pedir nada más.
    assert svc.db.scalar("SELECT COUNT(*) FROM matches") == 5

    matches_before = fake.count("match")
    fake.timelines["EUW1_9300000007"] = make_timeline(fake.matches["EUW1_9300000007"])
    # Una partida jugada con la web cerrada (más nueva que la última guardada).
    newer = my_match("EUW1_9300000005", start_offset_h=2)
    fake.matches[newer["metadata"]["matchId"]] = newer
    fake.timelines[newer["metadata"]["matchId"]] = make_timeline(newer)
    fake.ids.insert(0, newer["metadata"]["matchId"])
    result = tracker.sync()
    assert result["new"] == 2  # la nueva + la que esperaba el timeline (reintentada aunque sea antigua)
    assert fake.count("match") == matches_before + 1  # solo se descarga la nueva
    last_ids = [c for c in fake.calls if c[0] == "ids"][-1]
    assert last_ids[3] == 100 and last_ids[4] is not None  # todas desde la última guardada
    assert svc.db.scalar("SELECT status FROM my_games WHERE match_id = 'EUW1_9300000007'") == "ready"
    assert store.counts(svc.db, [ME]) == {"ready": 6, "pending": 0, "partial": 0, "ignored": 2}


def test_tracker_resolves_puuid_again_after_key_change(svc):
    fake = _fake_world()
    svc.riot = fake
    store.save_profile(svc.db, game_name="Tempu", tag_line="EUW", platform="euw1", puuid="puuid-de-otra-key")
    GameTracker(svc).sync()
    assert fake.count("account") == 1
    profile = store.get_profile(svc.db)
    assert profile["puuid"] == ME and store.known_puuids(profile) == ["puuid-de-otra-key", ME]


def test_reanalyze_outdated(svc):
    fake = _fake_world()
    svc.riot = fake
    store.save_profile(svc.db, game_name="Tempu", tag_line="EUW", platform="euw1", puuid=ME)
    tracker = GameTracker(svc)
    tracker.sync()
    with svc.db.write() as conn:
        conn.execute("UPDATE my_games SET version = 0 WHERE status IN ('ready', 'pending_timeline')")
    assert tracker.reanalyze_outdated() == 5
    assert tracker.reanalyze_outdated() == 0
    # El re-análisis no toca el estado: la partida sin timeline se sigue reintentando.
    assert svc.db.scalar("SELECT status FROM my_games WHERE match_id = 'EUW1_9300000007'") == "pending_timeline"


# ---------------------------------------------------------------------- API
@pytest.fixture
def api(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(app.services, "ENV_PATH", write_env(tmp_path))
    application = create_app(settings, run_startup=False)
    svc = application.state.svc
    write_ddragon_cache(settings.ddragon_dir)
    assert svc.dd.load(allow_network=False)
    with TestClient(application) as client:
        yield client, svc
    svc.db.close()


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_profile_without_key_is_saved_and_linked_later(api):
    client, svc = api
    body = ok(client.post("/api/profile", json={"riot_id": "Tempu#EUW", "platform": "euw1"}))
    assert body["profile"]["linked"] is False and "Sin API key" in body["note"]
    assert ok(client.get("/api/system/status"))["tracker"]["profile"]["game_name"] == "Tempu"
    assert client.post("/api/profile", json={"riot_id": "SinTag"}).status_code == 400
    assert client.post("/api/profile", json={"riot_id": "A#B", "platform": "xx"}).status_code == 400


def test_full_flow(api, monkeypatch):
    client, svc = api
    monkeypatch.setenv("RIOT_API_KEY", "")  # KeyStore.set() la exporta al entorno: así se restaura al acabar
    svc.keys.set(TEST_KEY)
    fake = _fake_world()
    svc.riot = fake
    body = ok(client.post("/api/profile", json={"riot_id": "tempu#euw", "platform": "euw1", "target_tier": "GOLD", "backfill": 100}))
    assert body["profile"]["linked"] and body["profile"]["target_tier"] == "GOLD" and body["profile"]["settings"]["backfill"] == 100
    svc.tracker.sync()

    summary = ok(client.get("/api/coach/summary", params={"last": 30}))
    # 4 con timeline + 1 que aún espera el timeline (cuenta con las métricas del resumen de la partida).
    assert summary["games"] == 5 and summary["target_tier"] == "GOLD"
    assert summary["weaknesses"] and summary["recent"][0]["match_id"] == "EUW1_9300000001"
    assert [r["has_timeline"] for r in summary["recent"]].count(False) == 1
    assert summary["refs_source"] == {"TOP": "static"}
    assert ok(client.get("/api/coach/summary", params={"queue": "ranked"}))["games"] == 3
    assert ok(client.get("/api/coach/summary", params={"queue": "normal"}))["games"] == 2
    assert ok(client.get("/api/coach/summary", params={"role": "JUNGLE"}))["games"] == 0

    detail = ok(client.get("/api/coach/games/euw1_9300000001"))
    assert detail["replay"]["tracks"] and detail["refs"]["cs_min"] > 0 and detail["score"] is not None
    assert client.get("/api/coach/games/EUW1_1").status_code == 404
    # La vista «Partida» reutiliza el timeline guardado: no gasta otra llamada.
    timelines_before = fake.count("timeline")
    assert ok(client.get("/api/matches/EUW1_9300000001/timeline"))["series"]
    assert fake.count("timeline") == timelines_before

    catalog = ok(client.get("/api/coach/goals"))["catalog"]
    assert any(c["metric"] == "deaths_to_jungler_pre14" for c in catalog)
    goal = ok(client.post("/api/coach/goals", json={"metric": "deaths_pre14", "op": "<=", "target": 1}))["goal"]
    assert goal["label"] == "Morir como máximo 1 vez antes del minuto 14"
    progress = ok(client.get("/api/coach/summary"))["goals"]
    assert progress[0]["id"] == goal["id"] and progress[0]["games"] == 4  # la partida sin timeline no tiene ese dato
    assert ok(client.patch(f"/api/coach/goals/{goal['id']}", json={"active": False}))["goal"]["active"] is False
    assert ok(client.delete(f"/api/coach/goals/{goal['id']}"))["deleted"] == goal["id"]
    assert client.post("/api/coach/goals", json={"metric": "foo", "op": "<=", "target": 1}).status_code == 400

    batch = ok(client.post("/api/draft/suggest-batch", params={"min_games": 0}, json={"ally": {}, "enemy": {}, "slots": [{"side": "ally", "role": "TOP"}, {"side": "enemy", "role": "MID"}]}))
    assert set(batch["slots"]) == {"ally:TOP", "enemy:MID"}
    assert batch["pool"]["TOP"][0]["champ"] == AATROX and batch["pool"]["TOP"][0]["games"] == 5
    assert ok(client.post("/api/coach/reanalyze"))["reanalyzed"] == 5

    assert ok(client.delete("/api/profile", params={"purge": True}))["purged"] is True
    assert svc.db.scalar("SELECT COUNT(*) FROM my_games") == 0
    assert ok(client.get("/api/coach/summary"))["profile"] is None


def test_detect_without_client(api, monkeypatch):
    client, _ = api
    from app import lcu

    monkeypatch.setattr(lcu, "INSTALL_DIRS", [])
    monkeypatch.setattr(lcu.importlib.util, "find_spec", lambda name: None)
    response = client.get("/api/profile/detect")
    assert response.status_code == 409
