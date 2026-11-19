"""Seguimiento de tus partidas: geometría, análisis, conclusiones, recolector y API."""

from __future__ import annotations

import copy
from collections import Counter

import pytest
from fastapi.testclient import TestClient

import app.services
from app.coach import geometry as geo
from app.coach import insights, store
from app.coach.analyzer import ANALYSIS_VERSION, NotInMatch, analyze_game, find_me
from app.coach.benchmarks import References, static_refs
from app.coach.items import Inventory
from app.coach.tracker import GameTracker, build_digest, digest
from app.main import create_app
from app.riot.client import RiotAPIError
from tests.factories import (
    AATROX,
    DARIUS,
    GAME_START_MS,
    LEE_SIN,
    TEST_KEY,
    VIEGO,
    FakeOpgg,
    install_opgg,
    item_catalog,
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
    # 1 contra 1 frente a su rival de línea, a la vista: no es un problema de wards.
    assert "solo" in deaths[690.0]["tags"] and "gank" not in deaths[690.0]["tags"] and "no_ward" not in deaths[690.0]["tags"]
    # Sin catálogo de objetos no se juzga el oro (no se sabe si tenía hueco para comprar).
    assert {"enemy_jungle", "isolated"} <= set(deaths[1440.0]["tags"]) and "unspent_gold" not in deaths[1440.0]["tags"]
    assert "before_objective" in deaths[1545.0]["tags"]
    moment = next(m for m in a["moments"] if m["t"] == 310.0)
    assert moment["title"] == f"Gank de {{champ:{VIEGO}}} (jungla rival)"
    assert "adelantado" in moment["detail"] and "larvas" in moment["detail"]


def test_laner_metrics(scenario):
    _, _, a = scenario
    m = a["metrics"]
    expected = {
        # Sin visión solo la del gank de las 5:10 (el 1 contra 1 de las 11:30 no depende de wards).
        "deaths": 4, "deaths_pre14": 2, "deaths_to_jungler_pre14": 1, "deaths_ganked": 1, "deaths_overextended": 1,
        "deaths_no_vision_pre14": 1, "deaths_solo": 2, "deaths_enemy_jungle": 1, "deaths_unspent_gold": None,
        "deaths_before_objective": 2, "deaths_isolated_late": 1, "obj_lost_dead": 1, "obj_lost_far": 0, "objectives_team": 1,
        "obj_participation": 0.0, "idle_min": 2, "cs10": 60, "cs_diff10": -15,
        "gold_diff15": -1200, "wards_pre14": 3, "control_wards_placed": 1, "recalls": 2, "first_recall_min": 18.5,
        "control_wards_bought": 1, "afk_min": 0.0,
    }
    assert {k: m[k] for k in expected} == expected
    # Sin catálogo de objetos el oro no se juzga (ni para bien ni para mal): ver test_gold_with_the_item_catalog.
    assert m["gold_hoard_min"] is None
    assert m["time_dead_pct"] == pytest.approx(140 / 1800 * 100, abs=0.1)
    kinds = {mo["kind"] for mo in a["moments"]}
    assert {"death", "idle", "lane"} <= kinds and "gold" not in kinds
    # El barón perdido estando muerto ya lo cuenta la muerte de justo antes: no se repite.
    assert not any(mo["kind"] == "objective" for mo in a["moments"])
    assert "barón" in next(mo for mo in a["moments"] if mo["t"] == 1545.0)["detail"]
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


# ------------------------------------------------- contexto: oro, objetivos, build
ITEMS = item_catalog()
BLUE_JUNGLE_TOP = (3821, 8101)  # azul de tu jungla
RED_JUNGLE_TOP = (7035, 10880)  # rojo de la jungla rival (lado del barón)


def _event(t: int, kind: str, **fields) -> dict:
    return {"timestamp": t, "type": kind, **fields}


def _buy(t: int, pid: int, item: int) -> dict:
    return _event(t, "ITEM_PURCHASED", participantId=pid, itemId=item)


def _kill(t: int, killer: int, victim: int, pos: tuple[int, int], assists=()) -> dict:
    return _event(t, "CHAMPION_KILL", killerId=killer, victimId=victim, assistingParticipantIds=list(assists),
                  position={"x": pos[0], "y": pos[1]}, bounty=300, shutdownBounty=0)


def _with(timeline: dict, events=(), frames: dict | None = None) -> dict:
    """Copia del timeline con eventos extra y valores de frame cambiados: ``{(pid, minuto): {...}}``."""
    tl = copy.deepcopy(timeline)
    frames_list = tl["info"]["frames"]
    for e in events:
        frames_list[min(e["timestamp"] // 60_000, len(frames_list) - 1)]["events"].append(e)
    for (pid, minute), values in (frames or {}).items():
        frame = frames_list[minute]["participantFrames"][str(pid)]
        for key, value in values.items():
            frame[key] = {"x": value[0], "y": value[1]} if key == "position" else value
    return tl


def test_item_catalog_and_inventory():
    assert ITEMS.is_legendary(3071) and ITEMS.is_completed(3047) and not ITEMS.is_completed(1001)
    assert ITEMS.is_starter(1055) and ITEMS.is_starter(3865) and not ITEMS.is_starter(1036)
    assert not ITEMS.uses_slot(3340) and not ITEMS.uses_slot(2138) and ITEMS.uses_slot(2055)
    assert ITEMS.anti_heal(3123) and not ITEMS.anti_heal(3071)
    # Con hueco puedes comprar cualquier pieza de la Cuchilla negra; con el inventario lleno, solo lo que combina tu Pico.
    held = Counter({1037: 1})
    assert ITEMS.options([3071], held, 2) == [(2022, 250), (1036, 350), (1028, 400), (3067, 800), (3044, 1100), (3071, 2125)]
    assert ITEMS.options([3071], held, 0) == [(3071, 2125)]
    assert ITEMS.options([3071], Counter({2055: 1}), 0) == []  # una pink no combina con nada

    events = [
        _buy(1000, 1, 1036), _buy(2000, 1, 1036),
        _event(3000, "ITEM_DESTROYED", participantId=1, itemId=1036), _event(3000, "ITEM_DESTROYED", participantId=1, itemId=1036),
        _buy(3000, 1, 3035), _event(4000, "ITEM_UNDO", participantId=1, beforeId=3035, afterId=0, goldGain=750),
        _event(5000, "ITEM_DESTROYED", participantId=1, itemId=3865),  # el objeto de support viene dado y evoluciona solo
    ]
    inv = Inventory(ITEMS, events)
    assert inv.at(3500) == Counter({3035: 1})
    assert inv.at(4500) == Counter({1036: 2})  # deshacer devuelve los componentes
    assert inv.at(6000) == Counter({1036: 2, 3866: 1}) and inv.free_slots(inv.at(6000)) == 3
    assert [p["item"] for p in inv.bought()] == [1036, 1036]


def _analyze(timeline_events=(), frames=None, *, pid: int = 1, overrides=None, items=ITEMS) -> dict:
    over = copy.deepcopy(ME_OVERRIDES)
    for k, v in (overrides or {}).items():
        over.setdefault(k, {}).update(v)
    match = make_match("EUW1_9400000001", duration=1800, puuids={1: ME}, overrides=over, game_start=GAME_START_MS)
    return analyze_game(match, _with(make_timeline(match), timeline_events, frames), pid=pid, items=items)


def test_gold_with_the_item_catalog():
    a = _analyze()
    m, deaths = a["metrics"], {d["t"]: d for d in a["deaths"]}
    gold = next(mo for mo in a["moments"] if mo["kind"] == "gold")
    assert "Bacteriófago (1.100)" in gold["detail"] and "hueco" in gold["detail"]  # pieza de su siguiente objeto
    assert "Botas blindadas (1.200)" in next(mo for mo in a["moments"] if mo["t"] == 1440.0)["detail"]
    # Desde que le llegaba el oro (interpolado entre frames: 15:34 y 22:37) hasta que compra o muere, menos 45 s de margen
    # y la pelea de las 24:00: 131 s + 13 s.
    assert m["gold_hoard_min"] == pytest.approx(2.4, abs=0.05) and "unspent_gold" in deaths[1440.0]["tags"]
    assert a["series"]["hoard"][16:19] == [1, 1, 1] and a["series"]["useful_gold"][16] == 1100


def test_saving_with_a_full_inventory_is_not_hoarding():
    # 15:10: 5 objetos terminados + Doran = inventario lleno. Ahorrar para la Cuchilla negra (18:30) no es un error.
    full = [_buy(910_000 + i, 1, item) for i, item in enumerate((3161, 3053, 3047, 6694, 3111))]
    a = _analyze(full)
    m = a["metrics"]
    assert m["gold_hoard_min"] == 0 and m["deaths_unspent_gold"] == 0 and m["gold_saving_min"] >= 3
    assert not any(mo["kind"] == "gold" for mo in a["moments"])
    assert not any("clogged" in mo["tags"] for mo in a["moments"])
    # Lleno, pero con dos dagas que no son de tu siguiente objeto: el problema es el orden de compra.
    clogged = [_buy(910_000 + i, 1, item) for i, item in enumerate((3161, 3053, 3047, 1042, 1042))]
    a = _analyze(clogged)
    assert a["metrics"]["gold_hoard_min"] == 0 and a["metrics"]["inv_clogged_min"] >= 3
    moment = next(mo for mo in a["moments"] if "clogged" in mo["tags"])
    assert "Daga" in moment["detail"] and "Cuchilla negra" in moment["detail"]


def test_basic_boots_never_clog_the_inventory():
    """Partida real: «Botas ocupaban hueco sin ser parte de ese objeto». Las botas no se venden para avanzar otro objeto."""
    boots = [_buy(910_000 + i, 1, item) for i, item in enumerate((3161, 3053, 6694, 1001, 1042))]
    a = _analyze(boots)
    assert not any("Botas" in mo["detail"] for mo in a["moments"] if "clogged" in mo["tags"])
    # Con una daga suelta además de las botas, el aviso sigue (y no nombra las botas).
    stray = [_buy(910_000 + i, 1, item) for i, item in enumerate((3161, 3053, 1042, 1001, 1042))]
    a = _analyze(stray)
    moment = next(mo for mo in a["moments"] if "clogged" in mo["tags"])
    assert "Daga" in moment["detail"] and "Botas" not in moment["detail"]


def test_final_push_and_afk_are_not_errors():
    # Oro en el empujón final (gana el azul): no cuenta.
    a = _analyze(frames={(1, 29): {"currentGold": 2600}, (1, 30): {"currentGold": 2900}})
    assert a["metrics"]["gold_hoard_min"] == pytest.approx(2.4, abs=0.05)
    # Desconectado en la fuente desde el 27: ni minutos vacíos, ni oro parado, pero se avisa.
    still = {(1, minute): {"position": (400, 420), "xp": 17_000, "currentGold": 3000} for minute in range(27, 31)}
    a = _analyze(frames=still)
    m = a["metrics"]
    assert m["afk_min"] == pytest.approx(3.0, abs=0.05) and a["afk"] == [{"start": 1620.0, "end": 1800.0}]
    assert m["idle_min"] == 2 and m["gold_hoard_min"] == pytest.approx(2.4, abs=0.05)
    assert any(mo["kind"] == "afk" for mo in a["moments"])


def test_objective_traded_on_the_other_side_is_a_good_decision():
    # El rival coge el dragón a las 20:30 mientras el jungla azul (pid 2) invade su jungla, mata y tira una torre.
    dragon = _event(1_230_000, "ELITE_MONSTER_KILL", killerId=7, killerTeamId=200, monsterType="DRAGON", monsterSubType="FIRE_DRAGON",
                    position={"x": 9866, "y": 4414}, assistingParticipantIds=[9, 10], bounty=0)
    far = {(2, 20): {"position": RED_JUNGLE_TOP}, (2, 21): {"position": RED_JUNGLE_TOP, "jungleMinionsKilled": 136}}
    plays = [
        _kill(1_210_000, 2, 7, RED_JUNGLE_TOP),
        _kill(1_215_000, 3, 8, (7500, 11500), assists=[2]),
        _event(1_240_000, "BUILDING_KILL", killerId=2, teamId=200, buildingType="TOWER_BUILDING", laneType="TOP_LANE",
               towerType="OUTER_TURRET", position={"x": 4318, "y": 13875}, assistingParticipantIds=[3], bounty=0),
    ]
    a = _analyze([dragon, *plays], far, pid=2)
    trade = next(mo for mo in a["moments"] if mo["kind"] == "trade")
    assert trade["severity"] == "good" and trade["title"].startswith("Buen cambio: el dragón por")
    assert "1 torre" in trade["title"] and "su jungla" in trade["title"]
    # Las larvas de las 6:20 (gank abajo: 1 kill por 3 larvas) siguen siendo un mal cambio; el dragón no cuenta.
    assert a["metrics"]["obj_lost_far"] == 1 and a["metrics"]["obj_trades"] == 1
    assert not any("dragón" in mo["title"] for mo in a["moments"] if "far" in mo["tags"])
    # Lejos y sin sacar nada: eso sí es regalarlo.
    a = _analyze([dragon], {(2, 20): {"position": BLUE_JUNGLE_TOP}, (2, 21): {"position": BLUE_JUNGLE_TOP}}, pid=2)
    lost = next(mo for mo in a["moments"] if "far" in mo["tags"] and "dragón" in mo["title"])
    assert lost["title"] == "El rival cogió el dragón gratis y estabas lejos" and lost["severity"] == "medium"
    assert a["metrics"]["obj_lost_far"] == 2 and a["metrics"]["obj_trades"] == 0


def test_deaths_in_won_fights_or_with_a_trade_weigh_less():
    # 11:30: pid 1 cae, pero su jungla mata a dos rivales en la misma pelea.
    won = [_kill(695_000, 2, 6, (1300, 9100)), _kill(700_000, 2, 7, (1250, 9050))]
    # 24:00: muere aislado en la jungla roja mientras su equipo hace el barón.
    baron = _event(1_450_000, "ELITE_MONSTER_KILL", killerId=2, killerTeamId=100, monsterType="BARON_NASHOR",
                   position={"x": 5007, "y": 10471}, assistingParticipantIds=[3, 4, 5], bounty=0)
    a = _analyze([*won, baron])
    deaths = {d["t"]: d for d in a["deaths"]}
    assert "fight_won" in deaths[690.0]["tags"] and deaths[690.0]["severity"] == "low"
    assert next(mo for mo in a["moments"] if mo["t"] == 690.0)["title"] == "Caíste, pero ganasteis la pelea (2 kills y 1 caído)"
    assert "traded" in deaths[1440.0]["tags"] and a["metrics"]["deaths_isolated_late"] == 0
    assert "tu equipo cogió el barón a cambio" in next(mo for mo in a["moments"] if mo["t"] == 1440.0)["detail"]


def test_build_order_skills_and_anti_heal():
    skills = [_event(t, "SKILL_LEVEL_UP", participantId=1, skillSlot=slot, levelUpType="NORMAL")
              for t, slot in ((1_000, 1), (90_100, 2), (180_100, 3), (270_100, 1), (360_100, 1), (500_000, 4), (905_000, 4), (1_352_000, 4))]
    extra = [_buy(1_200_000, 1, 3053), _event(1_500_000, "ITEM_SOLD", participantId=1, itemId=3053), *skills]
    a = _analyze(extra, overrides={6: {"totalHeal": 40_000}})
    b, m = a["build"], a["metrics"]
    assert [(o["item"], o["kind"]) for o in b["order"]] == [(3071, "legendary"), (3053, "legendary"), (3047, "boots")]
    assert m["first_item_min"] == 18.5 and m["second_item_min"] == 20.0 and m["boots_min"] == pytest.approx(24.3, abs=0.05)
    assert b["skills"].startswith("QWEQQR") and b["skill_max"][0] == "Q"
    assert b["r_delays"] == [{"level": 6, "t": 450.0, "delay": 50}] and m["r_late"] == 1
    assert b["sold"][0]["item"] == 3053 and m["sold_legendaries"] == 1
    assert b["anti_heal"]["healers"][0]["champ"] == DARIUS and b["anti_heal"]["suggest"] == 3123
    titles = [mo["title"] for mo in a["moments"] if mo["kind"] == "build"]
    assert "Subiste la R 50 s tarde (nivel 6)" in titles and "Vendiste Calibrador de Sterak" in titles
    assert f"Sin Heridas graves contra {{champ:{DARIUS}}}" in titles
    assert build_digest(b) == {"items": [3071, 3053], "boots": 3047, "skill_max": b["skill_max"]}


def test_compare_build_with_the_most_played():
    ref = {"core": [3071, 3053, 6694], "boots": [3047], "skill_max": ["Q", "W", "E"], "popular": [{"id": 3071, "pick_rate": 0.6}]}
    same = insights.compare_build({"items": [3071, 3053, 6694], "boots": 3047, "skill_max": ["Q", "W", "E"]}, ref)
    assert same["overlap"] == 3 and all(n["ok"] for n in same["notes"])
    other = insights.compare_build({"items": [3161, 3053, 3071], "boots": 3111, "skill_max": ["W", "Q", "E"]}, ref)
    notes = {n["text"]: n["ok"] for n in other["notes"]}
    assert notes["Tu primer objeto no está entre los habituales de tu campeón en este rol."] is False
    assert notes["Coincides en 2 de 3 objetos clave con la build más jugada."] is True
    assert any("maximizas W > Q > E; lo habitual es Q > W > E" in t for t in notes)
    assert insights.compare_build(None, ref) is None and insights.compare_build({"items": []}, None) is None


# ---------------------------------------------------------------- conclusiones
def _rows(n: int) -> list[dict]:
    rows = []
    for i in range(n):
        match = my_match(f"EUW1_92000000{i:02d}", start_offset_h=-i)
        a = analyze_game(match, make_timeline(match), pid=1, items=ITEMS)
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
    games = _rows(8)  # con menos de 5 partidas no se declara ningún punto débil (ver test_coach_audit)
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
    assert g["games"] == 8 and g["rate"] == 0 and len(g["history"]) == 8  # 2 muertes antes del 14 en cada partida
    assert out["readiness"]["ready"] is False and out["readiness"]["missing_games"] == 2
    assert out["deaths"]["total"] == 32 and out["deaths"]["tags"]["gank"]["count"] == 8
    assert out["champions"][0]["champ"] == AATROX and out["champions"][0]["games"] == 8


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

    def league_entries_by_puuid(self, platform, puuid, **kw):
        self.calls.append(("league", puuid))
        return [{"queueType": "RANKED_SOLO_5x5", "tier": "PLATINUM", "rank": "IV", "leaguePoints": 45, "wins": 32, "losses": 31}]

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


def test_peers_of_your_games_are_sampled_without_requests(svc):
    fake = _fake_world()
    svc.riot = fake
    store.save_profile(svc.db, game_name="Tempu", tag_line="EUW", platform="euw1", puuid=ME)
    tracker = GameTracker(svc)
    tracker.sync()
    calls = len(fake.calls)
    assert tracker.sample_peers() == 4  # las 4 partidas con timeline
    assert len(fake.calls) == calls  # ni una petición: el match y el timeline ya estaban guardados
    assert svc.db.scalar("SELECT COUNT(*) FROM peer_samples WHERE pid > 0") == 36
    assert svc.db.scalar("SELECT COUNT(*) FROM peer_samples WHERE pid = 1") == 0  # tú no cuentas
    assert tracker.sample_peers() == 0  # ya están


def _peer_db(svc, n: int, role: str = "JUNGLE", metrics=None, prefix: str = "EUW1_"):
    for i in range(n):
        store.save_peer_samples(svc.db, f"{prefix}{i}", 1_700_000_000_000 + i, ANALYSIS_VERSION,
                                [{"pid": 7, "role": role, "reliable": True, "metrics": metrics or {"cs10": 60, "deaths": 6, "deaths_before_objective": 1.6}}])


def test_references_calibrated_with_the_players_of_your_games(svc):
    _peer_db(svc, 25)
    refs = References(svc, "EMERALD", current="PLATINUM")
    jungle = refs.for_role("JUNGLE")
    assert jungle["cs10"] == pytest.approx(60 * 1.0 / 0.96, abs=0.01)  # escalado de Platino a Esmeralda
    assert jungle["deaths"] == pytest.approx(6 * 1.0 / 1.07, abs=0.01)
    assert jungle["deaths_before_objective"] == pytest.approx(1.6 / 1.07, abs=0.01)
    assert refs.source("JUNGLE") == "peers"
    assert refs.info("JUNGLE")["peer_samples"] == 25 and refs.info("JUNGLE")["from_tier"] == "PLATINUM"
    assert jungle["vision_min"] == static_refs("JUNGLE", "EMERALD")["vision_min"]  # sin muestras: la orientativa
    # Las métricas espejo frente a tu rival no se calibran (su media sería tu propia diferencia con el signo cambiado).
    _peer_db(svc, 25, metrics={"cs_diff10": -15, "gold_diff15": -450, "enemy_ganks_far": 0.4}, prefix="EUW1_9")
    mirror = References(svc, "EMERALD", current="PLATINUM").for_role("JUNGLE")
    assert mirror["cs_diff10"] == 0 and mirror["gold_diff15"] == 0 and mirror["enemy_ganks_far"] == static_refs("JUNGLE", "EMERALD")["enemy_ganks_far"]
    # Sin tu liga no se puede escalar: orientativas. Con pocas muestras, también.
    assert References(svc, "EMERALD").source("JUNGLE") == "static"
    assert References(svc, "EMERALD", current="CHALLENGER").info("JUNGLE")["from_tier"] == "MASTER"
    other = References(svc, "EMERALD", current="PLATINUM")
    assert other.source("TOP") == "static"


def test_peer_references_need_enough_reliable_samples(svc):
    _peer_db(svc, 19)
    assert References(svc, "EMERALD", current="PLATINUM").source("JUNGLE") == "static"
    store.save_peer_samples(svc.db, "EUW1_X", 1, ANALYSIS_VERSION, [{"pid": 7, "role": "JUNGLE", "reliable": False, "metrics": {"cs10": 10}}])
    assert References(svc, "EMERALD", current="PLATINUM").source("JUNGLE") == "static"  # la poco fiable no suma
    store.save_peer_samples(svc.db, "EUW1_Y", 2, ANALYSIS_VERSION - 1, [{"pid": 7, "role": "JUNGLE", "reliable": True, "metrics": {"cs10": 10}}])
    assert References(svc, "EMERALD", current="PLATINUM").source("JUNGLE") == "static"  # ni la de otra versión


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
    assert summary["builds"][0]["champ"] == AATROX and summary["builds"][0]["first_item"]["id"] == 3071
    assert summary["builds"][0]["compare"] is None  # sin OP.GG
    assert [r["has_timeline"] for r in summary["recent"]].count(False) == 1
    assert summary["refs_source"] == {"TOP": "static"}
    assert ok(client.get("/api/coach/summary", params={"queue": "ranked"}))["games"] == 3
    assert ok(client.get("/api/coach/summary", params={"queue": "normal"}))["games"] == 2
    assert ok(client.get("/api/coach/summary", params={"role": "JUNGLE"}))["games"] == 0

    detail = ok(client.get("/api/coach/games/euw1_9300000001"))
    assert detail["replay"]["tracks"] and detail["refs"]["cs_min"] > 0 and detail["score"] is not None
    assert detail["items_known"] and detail["build"]["order"][0]["item"] == 3071 and detail["build_compare"] is None
    fake_opgg = install_opgg(svc, FakeOpgg(builds={(AATROX, "TOP"): {"core": [3071, 3053, 6694], "boots": [3047], "skill_max": ["Q", "W", "E"], "popular": []}}))
    detail = ok(client.get("/api/coach/games/EUW1_9300000001"))
    assert detail["build_compare"]["core"] == [3071, 3053, 6694] and detail["build_compare"]["first"] == 3071
    assert ok(client.get("/api/coach/summary"))["builds"][0]["compare"]["core"] == [3071, 3053, 6694]
    assert ("champion_build", AATROX, "TOP", False) in fake_opgg.calls  # el resumen solo mira la caché
    assert client.get("/api/coach/games/EUW1_1").status_code == 404
    # La vista «Partida» reutiliza el timeline guardado: no gasta otra llamada.
    timelines_before = fake.count("timeline")
    assert ok(client.get("/api/matches/EUW1_9300000001/timeline"))["series"]
    assert fake.count("timeline") == timelines_before

    # Hábitos (tilt, sesiones, pool, ventajas) y revisión guiada con las 3 preguntas.
    habits = summary["habits"]
    assert habits["tilt"]["games"] == 5 and habits["reviews"] == {"games": 0, "week": 0}
    review = ok(client.get("/api/coach/games/EUW1_9300000001/review"))
    assert [q["id"] for q in review["questions"]] == ["knew", "decided", "better"]
    assert 1 <= len(review["moments"]) <= 3 and review["answers"] == {}
    t = review["moments"][0]["t"]
    assert ok(client.put("/api/coach/games/euw1_9300000001/review", json={"t": t, "question": "knew", "answer": " Su jungla estaba arriba "}))["ok"]
    assert ok(client.get("/api/coach/games/EUW1_9300000001/review"))["answers"] == {f"{t:.1f}|knew": "Su jungla estaba arriba"}
    assert ok(client.get("/api/coach/summary"))["habits"]["reviews"] == {"games": 1, "week": 1}
    ok(client.put("/api/coach/games/EUW1_9300000001/review", json={"t": t, "question": "knew", "answer": ""}))  # vaciar = borrar
    assert ok(client.get("/api/coach/games/EUW1_9300000001/review"))["answers"] == {}
    assert client.put("/api/coach/games/EUW1_1/review", json={"t": 1, "question": "knew", "answer": "x"}).status_code == 404
    assert client.put("/api/coach/games/EUW1_9300000001/review", json={"t": 1, "question": "otra", "answer": "x"}).status_code == 422
    learn = ok(client.get("/api/coach/learn"))
    assert learn["topics"]["farm"]["videos"] and learn["by_tag"]

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
