"""Segunda tanda de la auditoría del análisis (falsos positivos verificados con partidas reales de 2026)."""

from __future__ import annotations

from collections import Counter

from app.coach import insights
from app.coach.benchmarks import References
from app.coach.items import Inventory
from tests.test_coach_audit import ITEMS, buy, death, ev, kill, moments, monster, run, tower


def test_gank_only_when_you_died_in_or_next_to_your_lane():
    # El jungla y el mid rivales te matan en su jungla (rotaste allí): no es «te ganquearon».
    a = run([kill(330_000, 7, 1, (10979, 6799), assists=[8])])
    tags = death(a, 330.0)["tags"]
    assert {"jungler", "enemy_jungle"} <= set(tags) and "gank" not in tags
    # En tu línea, sí (el de las factorías: 5:10 en top).
    assert "gank" in death(a, 310.0)["tags"]
    # En el río junto a tu línea, también.
    a = run([kill(330_000, 7, 1, (3600, 11800), assists=[8])])
    assert "gank" in death(a, 330.0)["tags"]


def test_solo_excludes_mutual_kills_and_early_outsiders():
    # Os matáis los dos: no es «pierdes el 1 contra 1».
    a = run([kill(600_000, 6, 1, (1300, 11000)), kill(601_000, 1, 6, (1350, 11050))])
    tags = death(a, 600.0)["tags"]
    assert "solo" not in tags and "fight_even" in tags
    # El jungla rival solo, en la fase de líneas: es un gank, no un duelo de línea.
    a = run([kill(600_000, 7, 1, (1300, 11000))])
    tags = death(a, 600.0)["tags"]
    assert "solo" not in tags and {"gank", "jungler"} <= set(tags)
    assert a["metrics"]["deaths_solo"] == run()["metrics"]["deaths_solo"]  # no suma un 1 contra 1


def test_enemy_ganks_far_ignores_mid_base_and_nearby():
    # Tú (jungla azul) en bot de 5:00 a 6:00 (ya cuenta el gank de las factorías en top a las 5:10).
    frames = {(2, 5): {"position": (11000, 1500)}, (2, 6): {"position": (11000, 1500)}}
    assert run(frames=frames, pid=2)["metrics"]["enemy_ganks_far"] == 1
    # El jungla rival ganquea mid: mid se alcanza desde las dos mitades, no suma.
    mid = kill(330_000, 7, 3, (7400, 7400), assists=[8])
    assert run([mid], frames, pid=2)["metrics"]["enemy_ganks_far"] == 1
    # Otra vez en top mientras tú estabas en bot sin hacer nada allí: sí es la otra mitad. (A las 5:30 no contaría:
    # a las 6:30 ganqueas bot, que es justo el cambio que se recomienda.)
    frames[(2, 4)] = {"position": (11000, 1500)}
    top = kill(250_000, 7, 1, (1300, 11500), assists=[6])
    assert run([top], frames, pid=2)["metrics"]["enemy_ganks_far"] == 2
    assert run([kill(330_000, 7, 1, (1300, 11500), assists=[6])], frames, pid=2)["metrics"]["enemy_ganks_far"] == 1
    # En base (volviendo): no se te puede pedir que respondas.
    in_base = {(2, 4): {"position": (1200, 1200)}, (2, 5): {"position": (1200, 1200)}, (2, 6): {"position": (1200, 1200)}}
    assert run([top], in_base, pid=2)["metrics"]["enemy_ganks_far"] == 0


def test_a_destroyed_pink_is_assumed_to_be_another_one():
    pinks = [
        ev(60_000, "WARD_PLACED", creatorId=3, wardType="CONTROL_WARD"),  # pink de tu mid, al otro lado
        ev(250_000, "WARD_PLACED", creatorId=1, wardType="CONTROL_WARD"),  # tu pink en la línea
        ev(280_000, "WARD_KILL", killerId=7, wardType="CONTROL_WARD"),  # el rival destruye UNA pink
    ]
    a = run(pinks)
    # La destruida pudo ser la de tu mid: tu pink seguía viva, así que no es «sin visión».
    assert "no_ward" not in death(a, 310.0)["tags"]
    # Si tu pink era la única, sí cayó: sin visión.
    a = run(pinks[1:])
    assert "no_ward" in death(a, 310.0)["tags"]


def test_one_tower_does_not_compensate_two_objectives():
    far = {(2, 15): {"position": (2000, 2000)}, (2, 16): {"position": (2000, 2000)}}
    events = [
        monster(930_000, 7, 200, "RIFTHERALD", (5007, 10471), assists=[8]),
        tower(950_000, 3, 200, "MID_LANE", (8955, 8510), assists=[4]),  # vuestra torre: compensa el heraldo…
        monster(970_000, 7, 200, "DRAGON", (9866, 4414), sub="FIRE_DRAGON", assists=[9, 10]),  # …pero no también el dragón
    ]
    a = run(events, far, pid=2)
    lost = [m for m in moments(a, "objective", "far") if m["t"] == 970.0]
    assert len(lost) == 1 and "dragón" in lost[0]["title"]
    assert not [m for m in moments(a, "objective", "far") if m["t"] == 930.0]


def test_games_with_a_riot_flagged_afk_are_not_reliable():
    a = run(overrides={8: {"wasAfk": True}})
    assert a["metrics"]["riot_afk"] == 1
    assert not insights.reliable({"queue": 420, "metrics": a["metrics"]})
    assert insights.reliable({"queue": 420, "metrics": run()["metrics"]})


def test_ready_without_goals_does_not_claim_you_meet_them():
    games = [{"queue": 420, "role": "TOP", "metrics": {"deaths_pre14": 0, "cs_min": 9, "vision_min": 1, "kp": 60, "time_dead_pct": 3,
              "idle_pct": 5, "gold_hoard_min": 0, "obj_participation": 70, "obj_lost_far": 0, "first_item_min": 9, "deaths_to_jungler_pre14": 0}}] * 12
    out = insights.readiness(games, References(None, "GOLD"), [])
    assert out["ready"] and "Cumples tus propósitos" not in out["message"]


def test_one_bad_game_is_not_a_weakness_for_averages():
    def row(values, ref):
        import math
        mean = sum(values) / len(values)
        sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
        return {"key": "cs_min", "label": "CS", "category": "farm", "unit": "", "decimals": 2, "better": "higher", "value": round(mean, 3),
                "ref": ref, "dev": round((ref - mean) / ref, 3), "status": "bad", "games": len(values), "sd": round(sd, 4), "trend": None, "series": []}

    one_bad = row([7.0] * 7 + [0.5], 6.8)  # media 6,19: un 9 % por debajo… por una sola partida
    assert insights.weaknesses([one_bad], {}) == []
    always_low = row([5.0, 5.2, 4.9, 5.1, 5.0, 5.3, 4.8, 5.1], 6.8)
    assert [w["metric"] for w in insights.weaknesses([always_low], {})] == ["cs_min"]


def test_items_received_without_a_purchase_take_a_slot():
    # Calzado de la runa: no hay compra, solo su ITEM_DESTROYED al mejorarlo a las 13:00.
    events = [buy(5_000, 1, 1055), ev(780_000, "ITEM_DESTROYED", participantId=1, itemId=2422), buy(780_000, 1, 3047)]
    inv = Inventory(ITEMS, events)
    assert inv.at(400_000)[2422] == 1  # ya lo llevaba (desde lo antes que puede llegar)
    assert inv.at(780_000)[2422] == 0 and inv.at(780_000)[3047] == 1
    assert inv.purchases[-1]["consumed"] == [2422]
    assert inv.at(300_000) == Counter({1055: 1})  # antes de los 6 min, no
    # Galletas: 2 gastadas, sin compra.
    events = [ev(200_000, "ITEM_DESTROYED", participantId=1, itemId=2010), ev(260_000, "ITEM_DESTROYED", participantId=1, itemId=2010)]
    inv = Inventory(ITEMS, events)
    assert inv.at(150_000)[2010] == 1 and inv.at(230_000)[2010] == 1 and inv.at(300_000)[2010] == 0
