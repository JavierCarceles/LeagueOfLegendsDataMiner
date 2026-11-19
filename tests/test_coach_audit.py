"""Regresiones de la auditoría del análisis por partida (falsos positivos y casos raros de Riot).

Regla de producto: nunca decir que algo es un error (ni elogiarlo) si los datos no lo respaldan con
claridad. Cuando no se puede comprobar (rival de línea desconocido, AFK, objetos desconocidos,
Swiftplay…) se omite el veredicto.
"""

from __future__ import annotations

import copy
import time

import pytest

from app.coach import geometry as geo
from app.coach import insights, store
from app.coach.analyzer import ANALYSIS_VERSION, analyze_game
from app.coach.benchmarks import References
from app.coach.items import Inventory, ItemCatalog
from app.coach.tracker import GameTracker, digest
from tests.factories import DARIUS, GAME_START_MS, _tl_frame, item_catalog, item_json, make_match, make_timeline

ME = "puuid-yo"
ME_OVERRIDES = {1: {"deaths": 4, "kills": 1, "totalTimeSpentDead": 140, "riotIdGameName": "Tempu", "riotIdTagline": "EUW"}}
ITEMS = item_catalog()
DRAGON = (9866, 4414)
BARON = (5007, 10471)
RED_JUNGLE_TOP = (7035, 10880)


# ------------------------------------------------------------------ utilidades
def ev(t: int, kind: str, **fields) -> dict:
    return {"timestamp": t, "type": kind, **fields}


def buy(t: int, pid: int, item: int) -> dict:
    return ev(t, "ITEM_PURCHASED", participantId=pid, itemId=item)


def kill(t: int, killer: int, victim: int, pos, assists=()) -> dict:
    return ev(t, "CHAMPION_KILL", killerId=killer, victimId=victim, assistingParticipantIds=list(assists),
              position={"x": pos[0], "y": pos[1]}, bounty=300, shutdownBounty=0)


def monster(t: int, killer: int, team: int, kind: str, pos, sub=None, assists=()) -> dict:
    e = ev(t, "ELITE_MONSTER_KILL", killerId=killer, killerTeamId=team, monsterType=kind,
           position={"x": pos[0], "y": pos[1]}, assistingParticipantIds=list(assists), bounty=0)
    if sub:
        e["monsterSubType"] = sub
    return e


def tower(t: int, killer: int, owner: int, lane: str, pos, assists=(), tower_type="OUTER_TURRET") -> dict:
    return ev(t, "BUILDING_KILL", killerId=killer, teamId=owner, buildingType="TOWER_BUILDING", laneType=lane,
              towerType=tower_type, position={"x": pos[0], "y": pos[1]}, assistingParticipantIds=list(assists), bounty=0)


def plate(t: int, killer: int, owner: int, lane: str, pos, assists=()) -> dict:
    return ev(t, "TURRET_PLATE_DESTROYED", killerId=killer, teamId=owner, laneType=lane,
              position={"x": pos[0], "y": pos[1]}, assistingParticipantIds=list(assists))


def is_kill(t: int, victim: int | None = None):
    return lambda e: e.get("type") == "CHAMPION_KILL" and e.get("timestamp") == t and (victim is None or e.get("victimId") == victim)


def is_event(kind: str, t: int):
    return lambda e: e.get("type") == kind and e.get("timestamp") == t


def timeline_with(timeline: dict, events=(), frames: dict | None = None, drop=()) -> dict:
    """Copia del timeline con eventos extra, valores de frame cambiados ``{(pid, minuto): {...}}`` y eventos quitados."""
    tl = copy.deepcopy(timeline)
    frames_list = tl["info"]["frames"]
    for f in frames_list:
        f["events"] = [e for e in f["events"] if not any(d(e) for d in drop)]
    for e in events:
        frames_list[min(e["timestamp"] // 60_000, len(frames_list) - 1)]["events"].append(e)
    for (pid, minute), values in (frames or {}).items():
        frame = frames_list[minute]["participantFrames"][str(pid)]
        for key, value in values.items():
            frame[key] = {"x": value[0], "y": value[1]} if key == "position" else value
    return tl


def base_match(match_id: str = "EUW1_9600000001", overrides=None, **kw) -> dict:
    over = copy.deepcopy(ME_OVERRIDES)
    for k, v in (overrides or {}).items():
        over.setdefault(k, {}).update(v)
    return make_match(match_id, duration=1800, puuids={1: ME}, overrides=over, game_start=GAME_START_MS, **kw)


def run(events=(), frames=None, *, pid: int = 1, drop=(), items=ITEMS, overrides=None, **kw) -> dict:
    match = base_match(overrides=overrides, **kw)
    return analyze_game(match, timeline_with(make_timeline(match), events, frames, drop), pid=pid, items=items)


def death(a: dict, t: float) -> dict:
    return next(d for d in a["deaths"] if d["t"] == t)


def death_moment(a: dict, t: float) -> dict:
    return next(m for m in a["moments"] if m["t"] == t and m["kind"] == "death")


def moments(a: dict, kind: str | None = None, tag: str | None = None) -> list[dict]:
    return [m for m in a["moments"] if (kind is None or m["kind"] == kind) and (tag is None or tag in m["tags"])]


# ---------------------------------------------------------- 1. visión
def test_no_ward_is_only_judged_when_vision_mattered_and_counts_team_wards():
    a = run()
    # 1 contra 1 contra su rival de línea a la vista: no es un problema de wards.
    assert "no_ward" not in death(a, 690.0)["tags"]
    # Gank del jungla rival sin visión propia ni de aliados cerca: sí.
    assert "no_ward" in death(a, 310.0)["tags"]
    assert "sin ward tuyo ni de un aliado cerca" in death_moment(a, 310.0)["detail"]
    assert a["metrics"]["deaths_no_vision_pre14"] == 1

    # Un ward de su jungla, que estaba cerca de esa línea al ponerlo, cuenta como visión.
    near_ally = run([ev(300_000, "WARD_PLACED", creatorId=2, wardType="YELLOW_TRINKET")], {(2, 5): {"position": (2000, 12000)}})
    assert "no_ward" not in death(near_ally, 310.0)["tags"]
    # Una pink propia de hace 2:30 sigue viva (las pinks no caducan)…
    pink = ev(160_000, "WARD_PLACED", creatorId=1, wardType="CONTROL_WARD")
    assert "no_ward" not in death(run([pink]), 310.0)["tags"]
    # …salvo que el rival la destruya.
    cleared = run([pink, ev(200_000, "WARD_KILL", killerId=7, wardType="CONTROL_WARD")])
    assert "no_ward" in death(cleared, 310.0)["tags"]


def test_tower_execution_and_bot_lane_2v2_are_not_vision_or_gank_deaths():
    executed = ev(500_000, "CHAMPION_KILL", killerId=0, victimId=4, assistingParticipantIds=[], position={"x": 13700, "y": 4300}, bounty=0)
    tags = death(run([executed], pid=4), 500.0)["tags"]
    assert "executed" in tags and not {"gank", "solo", "no_ward"} & set(tags)
    a = run([kill(500_000, 9, 4, (12400, 1600), assists=[10]), kill(560_000, 7, 4, (12400, 1600), assists=[9])], pid=4)
    assert not {"gank", "no_ward"} & set(death(a, 500.0)["tags"])  # el ADC y el support rivales son su línea
    assert {"jungler", "gank"} <= set(death(a, 560.0)["tags"])  # con el jungla rival, sí es gank


# ------------------------------------------------------- 14. rival desconocido
def test_unknown_lane_opponent_does_not_turn_a_1v1_into_a_gank():
    a = run(positions={6: ""})  # el top rival sin teamPosition (individualPosition «Invalid»)
    assert a["opponent"] is None
    tags = death(a, 690.0)["tags"]
    assert "gank" not in tags and "solo" in tags
    assert death_moment(a, 690.0)["title"] == f"Muerte 1 contra 1 contra {{champ:{DARIUS}}}"
    # El gank del jungla rival (identificado por su rol o su Smite) sigue siendo gank.
    assert "gank" in death(a, 310.0)["tags"]
    assert a["metrics"]["deaths_ganked"] == 1


# ------------------------------------------------- 6. peleas ganadas y cambios
def test_won_fights_are_not_counted_as_mistakes_in_the_metrics():
    won = [kill(312_000, 2, 7, (2300, 13200)), kill(318_000, 2, 6, (2400, 13250))]
    a = run(won)
    d = death(a, 310.0)
    assert {"gank", "jungler", "overextended", "fight_won"} <= set(d["tags"])  # el detalle se conserva
    assert death_moment(a, 310.0)["title"] == "Caíste, pero ganasteis la pelea (2 kills y 1 caído)"
    m = a["metrics"]
    assert m["deaths_ganked"] == m["deaths_to_jungler_pre14"] == m["deaths_overextended"] == m["deaths_no_vision_pre14"] == 0
    assert m["deaths_fight_won"] == 1 and m["deaths_pre14"] == 2  # el recuento bruto no cambia


def test_split_push_collapse_is_traded_when_the_team_scores_elsewhere():
    extra = [
        kill(1_440_000, 6, 1, (10979, 6799), assists=[7, 10]),  # 3 rivales van a por pid 1
        kill(1_450_000, 3, 8, (6600, 6900), assists=[2, 5]),  # 4 contra 2 en mid a la vez
        kill(1_455_000, 4, 9, (6700, 7000), assists=[2, 3]),
    ]
    a = run(extra, drop=[is_kill(1_440_000)])
    d = death(a, 1440.0)
    assert "traded" in d["tags"] and a["metrics"]["deaths_isolated_late"] == 0
    assert "tu equipo cogió 2 kills a cambio" in death_moment(a, 1440.0)["detail"]
    # Una muerte 1 contra 1 no atrae a nadie: las kills de otra pelea no la compensan.
    solo = run([kill(1_440_000, 8, 1, (10979, 6799)), kill(1_450_000, 3, 9, (6600, 6900))], drop=[is_kill(1_440_000)])
    assert "traded" not in death(solo, 1440.0)["tags"]


def test_objectives_taken_before_the_death_are_not_a_trade():
    # Dragón azul a las 9:00; el ADC muere 20 s después persiguiendo: no lo «compró» su muerte.
    after = run([kill(560_000, 7, 4, (12400, 1600), assists=[9])], pid=4)
    assert "traded" not in death(after, 560.0)["tags"]
    # Muere a las 8:20 y su equipo coge el dragón 40 s después: eso sí cuenta como cambio.
    before = run([kill(500_000, 9, 4, (12400, 1600), assists=[10])], pid=4)
    assert "traded" in death(before, 500.0)["tags"]


# ----------------------------------------------------- 2. antes de un objetivo
def test_before_objective_needs_a_death_that_could_matter():
    # ADC muerto 2 contra 2 en bot a las 5:20; el rival coge las larvas (arriba) a las 6:20.
    a = run([kill(320_000, 9, 4, (12400, 1600), assists=[10])], pid=4)
    assert "before_objective" not in death(a, 320.0)["tags"] and a["metrics"]["deaths_before_objective"] == 0
    # El mismo ADC muerto en bot a las 8:20 y dragón rival a las 9:00: sí.
    swap_dragon = [kill(500_000, 9, 4, (12400, 1600), assists=[10]), monster(540_000, 7, 200, "DRAGON", DRAGON, sub="FIRE_DRAGON", assists=[9])]
    b = run(swap_dragon, pid=4, drop=[is_event("ELITE_MONSTER_KILL", 540_000)])
    assert "before_objective" in death(b, 500.0)["tags"]
    # El top que muere arriba antes de las larvas (su lado del mapa) también.
    assert "before_objective" in death(run(), 310.0)["tags"]


# ---------------------------------------------------- 21. adelantado
def test_overextended_ignores_a_lane_whose_outer_turret_already_fell():
    death_spot = kill(750_000, 6, 1, (5200, 13650))
    a = run([tower(600_000, 1, 200, "TOP_LANE", (4318, 13875)), death_spot])
    assert "overextended" not in death(a, 750.0)["tags"]
    assert "overextended" in death(run([death_spot]), 750.0)["tags"]
    # Justo en el centro de la línea no es estar adelantado (margen del 15 %).
    middle = (1872, 12948)
    assert not geo.overextended(100, "top", middle) and not geo.overextended(200, "top", middle)
    assert geo.overextended(100, "top", (2200, 13300))


# ------------------------------------------------- 3 y 4. objetivos y cambios
def _dragon_at_20(*extra, frames=None) -> dict:
    """Dragón rojo a las 20:30 con pid 1 farmeando en top (lejos) y lo que se añada."""
    top = {(1, 20): {"position": (1300, 11500)}, (1, 21): {"position": (1340, 11460)}, **(frames or {})}
    return run([monster(1_230_000, 7, 200, "DRAGON", DRAGON, sub="FIRE_DRAGON", assists=[8]), *extra], top)


def test_good_trade_praise_requires_your_participation():
    bot_plays = [kill(1_200_000, 4, 9, (12500, 2200), assists=[5]), kill(1_205_000, 4, 10, (12600, 2300), assists=[5])]
    # El dúo de abajo mata a dos y tira una torre: buen cambio del equipo, pero el top no participó.
    team_only = _dragon_at_20(*bot_plays, tower(1_220_000, 4, 200, "BOT_LANE", (13866, 4505)))
    assert not moments(team_only, kind="trade") and team_only["metrics"]["obj_trades"] == 0
    assert team_only["metrics"]["obj_lost_far"] == 0  # tampoco se le culpa: el equipo lo compensó
    # Si el top tira su torre mientras tanto, el mérito sí es suyo.
    mine = _dragon_at_20(*bot_plays, tower(1_220_000, 1, 200, "TOP_LANE", (4318, 13875)))
    trade = next(m for m in moments(mine, kind="trade"))
    assert trade["severity"] == "good" and trade["title"].startswith("Buen cambio: el dragón por")
    assert mine["metrics"]["obj_trades"] == 1


def test_fair_trade_is_not_counted_as_a_good_trade():
    a = _dragon_at_20(tower(1_220_000, 1, 200, "TOP_LANE", (4318, 13875)))
    trade = next(m for m in moments(a, kind="trade"))
    assert trade["severity"] == "low" and trade["title"].startswith("Cambio justo") and "fair_trade" in trade["tags"]
    assert a["metrics"]["obj_trades"] == 0 and a["metrics"]["obj_fair_trades"] == 1


def test_conceded_objective_explains_what_the_enemy_also_took():
    """Partida real (16.22): «solo sacasteis 1 kill, 1 torre y 3 placas» por un heraldo parecía un falso
    positivo porque no decía que el rival, a la vez, mató a dos y tiró dos torres. Se explican ambos lados."""
    bot = {(1, 20): {"position": (12000, 2000)}, (1, 21): {"position": (12050, 2050)}}
    events = [
        monster(1_230_000, 7, 200, "BARON_NASHOR", BARON, assists=[6, 8, 9, 10]),
        plate(1_220_000, 1, 200, "BOT_LANE", (13866, 4505)),  # lo tuyo: una placa abajo
        kill(1_210_000, 8, 3, (7400, 7400), assists=[9]),  # lo del rival además del barón
        tower(1_225_000, 8, 100, "MID_LANE", (5846, 6396), assists=[9]),
    ]
    a = run(events, bot)
    far = next(m for m in moments(a, tag="far") if "barón" in m["title"])
    assert "sacasteis 1 placa, pero el rival, además del barón, sacó 1 kill y 1 torre" in far["detail"]
    # Sin pérdidas extra se mantiene el texto corto.
    plain = run(events[:2], bot)
    far = next(m for m in moments(plain, tag="far") if "barón" in m["title"])
    assert "solo sacasteis 1 placa" in far["detail"] and "además" not in far["detail"]


def test_plates_elsewhere_do_not_pay_for_a_death():
    """Temporada 2026: hay placas toda la partida (120 de oro). Que tu equipo rompa placas en otra línea no
    compensa tu muerte (antes 4 placas «valían» 640 y la muerte salía como cambio y no contaba como error)."""
    my_death = kill(1_500_000, 8, 1, (7400, 7400))
    plates = [plate(1_510_000 + i * 8_000, 4, 200, "BOT_LANE", (13866, 4505), assists=[5]) for i in range(5)]
    a = run([my_death, *plates])
    assert "traded" not in death(a, 1500.0)["tags"]
    # Si además cae la torre, sí hay algo a cambio (y sus placas no se cuentan dos veces).
    a = run([my_death, *plates, tower(1_555_000, 4, 200, "BOT_LANE", (13866, 4505), assists=[5])])
    assert "traded" in death(a, 1500.0)["tags"]


def test_a_turret_and_its_plates_count_once():
    from app.coach.analyzer import BUILDING_VALUE, _structures_value

    turret = {"kind": "tower", "owner": 200, "lane": "bot", "pos": (13866, 4505)}
    own_plates = [{"kind": "plate", "owner": 200, "lane": "bot", "pos": (13866, 4505)} for _ in range(3)]
    other_plate = {"kind": "plate", "owner": 200, "lane": "mid", "pos": (8955, 8510)}
    assert _structures_value([turret, *own_plates]) == BUILDING_VALUE["tower"]
    assert _structures_value([turret, *own_plates, other_plate]) == BUILDING_VALUE["tower"] + BUILDING_VALUE["plate"]
    assert _structures_value(own_plates) == 3 * BUILDING_VALUE["plate"] == 360


def test_laners_are_not_blamed_for_early_objectives_on_the_other_side():
    support = run(pid=5)  # en bot (2 contra 2) mientras el rival coge las larvas arriba a las 6:20
    assert not moments(support, tag="far") and support["metrics"]["obj_lost_far"] == 0
    top = run([monster(420_000, 7, 200, "DRAGON", DRAGON, sub="FIRE_DRAGON", assists=[9, 10])])
    assert top["metrics"]["obj_lost_far"] == 0  # el top en su línea no disputa el primer dragón
    jungle = run(pid=2)  # al jungla sí le toca: las larvas por una kill abajo es un mal cambio
    assert jungle["metrics"]["obj_lost_far"] == 1 and any("larvas" in m["title"] for m in moments(jungle, tag="far"))


def test_respawn_purchase_is_not_a_recall_and_the_objective_is_not_counted_twice():
    events = [kill(1_200_000, 8, 1, (7400, 7400), assists=[9]), monster(1_285_000, 7, 200, "BARON_NASHOR", BARON, assists=[6, 8, 9, 10])]
    frames = {(1, 21): {"position": (9000, 2500)}, (1, 22): {"position": (12000, 2000)}}
    drop = [is_kill(1_440_000, 1), is_kill(1_545_000, 1)]
    match = base_match(overrides={1: {"deaths": 3, "totalTimeSpentDead": 90}})
    tl = timeline_with(make_timeline(match), events, frames, drop)
    first = analyze_game(match, tl, pid=1, items=ITEMS)
    respawn = next(d["t"] for d in first["deaths"] if d["t"] == 1200.0)  # existe la muerte
    assert respawn == 1200.0
    a = analyze_game(match, timeline_with(tl, [buy(1_251_000, 1, 1036)]), pid=1, items=ITEMS)  # compra al reaparecer
    assert "before_objective" in death(a, 1200.0)["tags"]
    assert not moments(a, tag="far") and a["metrics"]["obj_lost_far"] == 0
    assert a["metrics"]["recalls"] == first["metrics"]["recalls"]  # comprar al reaparecer no es volver a base


# ------------------------------------------------------- 5. minutos vacíos
def test_setting_up_an_objective_is_not_idle_time():
    frames = {(4, m): {"position": (9300, 4900), "minionsKilled": 168, "damageStats": {"totalDamageDoneToChampions": 12_000}} for m in (24, 25, 26)}
    a = run([monster(1_590_000, 2, 100, "DRAGON", DRAGON, sub="CHEMTECH_DRAGON", assists=[4, 5, 3])], frames, pid=4)
    assert not moments(a, kind="idle") and a["metrics"]["idle_min"] == 0


def test_sweeping_wards_counts_as_activity():
    frames = {(5, m): {"position": (9300, 4900), "minionsKilled": 24, "damageStats": {"totalDamageDoneToChampions": 12_000}} for m in (24, 25, 26)}
    sweeps = [ev(t, "WARD_KILL", killerId=5, wardType="YELLOW_TRINKET") for t in (1_455_000, 1_490_000, 1_520_000, 1_545_000)]
    a = run(sweeps, frames, pid=5)
    assert not [m for m in moments(a, kind="idle") if m["t"] >= 1440]


def _support_frames(position) -> dict:
    return {
        (5, m): {"position": position, "minionsKilled": m, "damageStats": {"totalDamageDoneToChampions": 7_500 + (m - 15) * 300}}
        for m in range(15, 31)
    }


def test_support_next_to_an_ally_is_not_idle_but_alone_and_inactive_is():
    with_carry = run(frames=_support_frames((6600, 6900)), pid=5)  # junto al ADC (6480, 6860)
    assert with_carry["metrics"]["idle_min"] == 0
    alone = run(frames=_support_frames((12000, 2000)), pid=5)
    assert alone["metrics"]["idle_pct"] == 100.0 and moments(alone, kind="idle")


# ---------------------------------------------------- 7. desconexiones (AFK)
def _disconnected(first: int, last: int) -> dict:
    """pid 1 quieto en la fuente (sin experiencia, CS ni daño) de ``first`` a ``last`` y luego sigue jugando."""
    frames = {}
    for minute in range(first, last + 1):
        frames[(1, minute)] = {
            "position": (400, 420), "xp": first * 650, "minionsKilled": first * 6, "level": 2,
            "totalGold": 500 + first * 300 + (minute - first) * 120, "currentGold": 300 + (minute - first) * 120,
            "damageStats": {"totalDamageDoneToChampions": 1000},
        }
    lost = last - first
    for minute in range(last + 1, 31):
        base = _tl_frame(1, minute)
        frames[(1, minute)] = {"xp": base["xp"] - lost * 650, "minionsKilled": base["minionsKilled"] - lost * 6,
                               "totalGold": base["totalGold"] - lost * 180}
    return frames


def test_disconnect_skips_lane_verdicts_and_counts_per_played_minute():
    drop = [is_kill(310_000), is_kill(480_000), lambda e: e.get("type") == "WARD_PLACED" and e.get("creatorId") == 1 and 120_000 <= e["timestamp"] <= 600_000]
    a = run(frames=_disconnected(2, 9), drop=drop)
    assert a["afk"] == [{"start": 120.0, "end": 540.0}] and any(m["kind"] == "afk" for m in a["moments"])
    assert not moments(a, kind="lane")
    m = a["metrics"]
    assert m["cs10"] is None and m["cs_diff10"] is None and m["gold_diff10"] is None and m["gold_diff15"] is None
    assert m["cs_min"] == round(199 / (30 - m["afk_min"]), 2)  # por minuto jugado


def test_death_right_before_a_disconnect_is_not_judged():
    drop = [is_kill(310_000), is_kill(480_000), lambda e: e.get("type") == "WARD_PLACED" and e.get("creatorId") == 1 and 360_000 <= e["timestamp"] <= 660_000]
    a = run([kill(400_000, 6, 1, (1300, 11000))], _disconnected(7, 10), drop=drop)
    d = death(a, 400.0)
    assert "afk" in d["tags"] and d["severity"] == "low"
    assert death_moment(a, 400.0)["title"] == "Muerte antes de quedarte AFK o desconectado"
    assert a["metrics"]["deaths_solo"] == 2  # las de las 11:30 y las 25:45; la de antes de desconectarse no cuenta


# ------------------------------------------------------------- 12 y 13. jungla
def test_cross_map_objective_is_not_failing_to_track_the_enemy_jungler():
    a = run([kill(540_000, 7, 1, (1300, 11000), assists=[6])], pid=2)  # gank arriba mientras pid 2 hace el dragón
    assert a["metrics"]["enemy_ganks_far"] == 1  # solo el de las 5:10
    assert not [m for m in moments(a, kind="jungle") if "tracking" in m["tags"]]


def _idle_jungle(*extra, overrides=None) -> dict:
    drop = [is_kill(390_000), is_event("ELITE_MONSTER_KILL", 540_000)]
    frames = {(2, 4): {"position": (8000, 3000)}, (2, 9): {"position": (8394, 2641)}}  # lejos de los cangrejos
    return run(list(extra), frames, pid=2, drop=drop, overrides=overrides)


def test_early_impact_counts_plates_and_crabs():
    title = "Early sin impacto: ni kills, ni objetivos, ni invasiones antes del 10"
    assert any(m["title"] == title for m in moments(_idle_jungle(), kind="jungle"))  # de verdad sin nada
    plates = [plate(t, 4, 200, "BOT_LANE", (13866, 4505), assists=[2, 5]) for t in (400_000, 410_000)]
    with_plates = _idle_jungle(*plates)
    assert with_plates["metrics"]["impact_pre10"] == 1 and not any(m["title"] == title for m in with_plates["moments"])
    crabs = _idle_jungle(overrides={2: {"challenges": {"initialCrabCount": 2}}})
    assert crabs["metrics"]["crabs_pre10"] == 2 and not any(m["title"] == title for m in crabs["moments"])


# ------------------------------------------------------- 9, 10 y 11. resumen
REFS = References(None, "PLATINUM")


def row(i: int, role: str, queue: int = 420, **metrics) -> dict:
    return {"match_id": f"EUW1_{i}", "queue": queue, "role": role, "champ": DARIUS, "win": True, "metrics": metrics, "deaths": [], "team": 100}


def table_row(games: list[dict], key: str) -> dict | None:
    return next((r for r in insights.metric_table(games, REFS) if r["key"] == key), None)


def test_trends_need_enough_games_of_one_role_and_a_clear_change():
    noise = [row(i, "MID", cs_min=v) for i, v in enumerate([5.0, 5.0, 5.0, 5.01, 5.0, 5.0])]
    assert table_row(noise, "cs_min")["trend"] is None  # 3 contra 3
    tiny = [row(i, "MID", cs_min=v) for i, v in enumerate([5.0] * 4 + [5.01] * 4)]
    assert table_row(tiny, "cs_min")["trend"]["improving"] is None  # cambio despreciable
    mixed = [row(i, "SUPPORT", vision_min=1.6) for i in range(4)] + [row(i + 4, "TOP", vision_min=0.75) for i in range(4)]
    assert table_row(mixed, "vision_min")["trend"] is None  # roles distintos: no se comparan
    clear = [row(i, "MID", cs_min=v) for i, v in enumerate([7.5, 7.6, 7.4, 7.5, 5.0, 5.2, 4.9, 5.1])]
    assert table_row(clear, "cs_min")["trend"]["improving"] is True


def test_weaknesses_need_enough_games_and_evidence():
    three = [row(i, "TOP", deaths_before_objective=v) for i, v in enumerate([1, 1, 0])]
    assert not insights.weaknesses(insights.metric_table(three, REFS), {})
    noisy = [row(i, "TOP", deaths_before_objective=v) for i, v in enumerate([1, 1, 0, 0, 0])]
    assert not insights.weaknesses(insights.metric_table(noisy, REFS), {})
    clear = [row(i, "TOP", deaths_before_objective=1) for i in range(5)]
    assert [w["id"] for w in insights.weaknesses(insights.metric_table(clear, REFS), {})] == ["deaths_before_objective"]
    one_bad_game = [row(i, "TOP", gold_diff15=v) for i, v in enumerate([-3000, 100, 0, 50, 20])]
    assert not insights.weaknesses(insights.metric_table(one_bad_game, REFS), {})
    behind = [row(i, "TOP", gold_diff15=v) for i, v in enumerate([-600, -550, -700, -650, -500])]
    assert [w["id"] for w in insights.weaknesses(insights.metric_table(behind, REFS), {})] == ["gold_diff15"]


def test_goals_only_use_games_where_the_metric_applies():
    games = [row(i, "MID", cs_min=8.0) for i in range(3)] + [row(i + 3, "SUPPORT", cs_min=1.1) for i in range(3)]
    goal = {"id": 1, "metric": "cs_min", "op": ">=", "target": 7, "label": "x", "role": None, "active": True}
    evaluated = insights.evaluate_goals([goal], games)[0]
    assert evaluated["games"] == 3 and evaluated["rate"] == 100
    jungle = [row(i, "JUNGLE", deaths_to_jungler_pre14=0) for i in range(5)]
    goal = {"id": 2, "metric": "deaths_to_jungler_pre14", "op": "<=", "target": 0, "label": "x", "role": None, "active": True}
    assert insights.evaluate_goals([goal], jungle)[0]["games"] == 0


def test_swiftplay_and_afk_games_are_left_out_of_the_conclusions():
    ranked = [row(i, "TOP", cs_min=7.0, deaths_pre14=1) for i in range(5)]
    excluded = [
        row(10, "TOP", queue=480, cs_min=2.0, deaths_pre14=5),
        row(11, "TOP", cs_min=2.0, deaths_pre14=5, afk_min=7.0),
        row(12, "TOP", cs_min=2.0, deaths_pre14=5, ally_afk_min=4.0),
        row(13, "TOP", cs_min=2.0, deaths_pre14=5, enemy_afk_min=3.5),
    ]
    games = excluded + ranked
    assert table_row(games, "cs_min")["games"] == 5 and table_row(games, "cs_min")["value"] == 7.0
    goal = {"id": 1, "metric": "deaths_pre14", "op": "<=", "target": 1, "label": "x", "role": None, "active": True}
    out = insights.summarize(games, REFS, [goal])
    assert out["games"] == 9  # se siguen viendo una a una
    assert out["goals"][0]["games"] == 5 and out["goals"][0]["rate"] == 100
    assert out["readiness"]["missing_games"] == 5
    assert all(g["score"] is None for g in excluded) and all(g["score"] is not None for g in ranked)


# ------------------------------------------------------- 16, 17 y 20. objetos y oro
def catalog_with_stacks() -> ItemCatalog:
    data = item_json()
    data["data"]["2003"]["stacks"] = 5
    data["data"]["2055"]["stacks"] = 2
    return ItemCatalog.from_ddragon(data)


def test_stackable_items_share_one_slot():
    cat = catalog_with_stacks()
    assert cat.stacks(2003) == 5 and cat.stacks(1036) == 1 and cat.version == "16.22.1"
    events = [buy(1000 + i, 1, item) for i, item in enumerate((3161, 3053, 6694, 1042, 2003, 2003))]
    inv = Inventory(cat, events)
    assert inv.free_slots(inv.at(10_000)) == 1  # 3 objetos + Daga + 1 hueco de pociones
    extra = [ev(910_000, "ITEM_SOLD", participantId=1, itemId=1055)] + [
        buy(910_000 + i, 1, item) for i, item in enumerate((3161, 3053, 6694, 1042, 2003, 2003), start=1)
    ]
    a = run(extra, items=cat)
    # Hasta comprar la Cuchilla negra (18:30) no estaba lleno: tenía hueco para el Bacteriófago. Después sí lo está
    # (con la Daga suelta), y eso sí es un inventario atascado.
    assert not [m for m in moments(a, tag="clogged") if m["t"] < 1110]
    gold = next(m for m in moments(a, kind="gold") if m["t"] >= 900)
    assert "Bacteriófago (1.100)" in gold["detail"] and "hueco" in gold["detail"]


def test_unknown_items_take_a_slot_and_switch_off_item_verdicts():
    inv = Inventory(ITEMS, [buy(1000 + i, 1, item) for i, item in enumerate((3161, 3053, 6694, 3071, 3047, 999_999))])
    assert inv.free_slots(inv.at(10_000)) == 0 and inv.unknown == {999_999}
    a = run([buy(400_000, 1, 999_999)])
    assert a["items_reliable"] is False and a["unknown_items"] == [999_999]
    m = a["metrics"]
    assert m["gold_hoard_min"] is None and m["deaths_unspent_gold"] is None and m["first_item_min"] is None
    assert not moments(a, kind="gold") and not [x for x in moments(a, kind="build") if "skills" not in x["tags"]]
    assert all("unspent_gold" not in d["tags"] for d in a["deaths"])


def test_item_catalog_from_another_season_switches_off_item_verdicts():
    data = item_json(version="15.24.1")
    a = run(items=ItemCatalog.from_ddragon(data))
    assert a["items_known"] is True and a["items_reliable"] is False and a["metrics"]["gold_hoard_min"] is None


def test_hoarding_starts_when_the_gold_was_actually_reached():
    frames = {(1, 6): {"currentGold": 800}, (1, 7): {"currentGold": 1150}}
    a = run([buy(478_000, 1, 3044)], frames, drop=[is_kill(310_000), is_kill(480_000)])
    # 800 a las 6:00 y 1.150 a las 7:00: los 1.100 llegan hacia las 6:51 y compra a las 7:58 (67 s, 22 s de más).
    assert not [m for m in moments(a, kind="gold") if m["t"] < 600]


# ------------------------------------------------------------- 23. datos raros
def test_odd_payloads_do_not_crash():
    match = base_match()
    tl = make_timeline(match)
    odd_team = copy.deepcopy(match)
    odd_team["info"]["participants"][9]["teamId"] = 0
    assert analyze_game(odd_team, make_timeline(odd_team), pid=1, items=ITEMS)["has_timeline"]
    null_pos = copy.deepcopy(tl)
    null_pos["info"]["frames"][5]["participantFrames"]["1"]["position"] = {"x": None, "y": None}
    assert analyze_game(match, null_pos, pid=1, items=ITEMS)["has_timeline"]
    no_duration = copy.deepcopy(match)
    no_duration["info"].pop("gameDuration")
    a = analyze_game(no_duration, tl, pid=1, items=ITEMS)
    assert a["duration"] == 1800 and a["metrics"]["cs_min"] == pytest.approx(199 / 30, abs=0.01)
    my_team_odd = copy.deepcopy(match)
    my_team_odd["info"]["participants"][0]["teamId"] = 0
    assert analyze_game(my_team_odd, tl, pid=1, items=ITEMS)["has_timeline"] is False  # sin equipo no se sitúa nada


def test_evolve_skill_ups_are_not_r_points():
    # R a tiempo en 6, 11 y 16 y una evolución (Kai'Sa, Kha'Zix…) que no es un punto de habilidad.
    skills = [ev(t, "SKILL_LEVEL_UP", participantId=1, skillSlot=4, levelUpType="NORMAL") for t in (450_100, 900_100, 1_350_100)]
    skills.append(ev(455_000, "SKILL_LEVEL_UP", participantId=1, skillSlot=4, levelUpType="EVOLVE"))
    a = run(skills)
    assert a["metrics"]["r_late"] == 0 and a["build"]["skills"] == "RRR"


# ------------------------------------------------------------ tracker y digest
def test_digest_counts_every_severity():
    a = _dragon_at_20(kill(1_200_000, 4, 9, (12500, 2200), assists=[5]), kill(1_205_000, 4, 10, (12600, 2300), assists=[5]),
                      tower(1_220_000, 1, 200, "TOP_LANE", (4318, 13875)))
    issues = digest(a)["issues"]
    assert set(issues) == {"high", "medium", "low", "good"} and issues["good"] == 1
    assert sum(issues.values()) == len(a["moments"])
    assert ANALYSIS_VERSION >= 3


class _FakeRiot:
    """Riot falso mínimo para ``GameTracker.sync``: una partida y su timeline."""

    def __init__(self, match: dict, timeline: dict) -> None:
        self.match_id = match["metadata"]["matchId"]
        self._match, self._timeline = match, timeline

    def account_by_riot_id(self, game_name, tag_line, platform, **kw):
        return {"puuid": ME, "gameName": "Tempu", "tagLine": "EUW"}

    def active_platform(self, puuid, platform_hint="euw1", **kw):
        return "euw1"

    def summoner_by_puuid(self, platform, puuid, **kw):
        return {}

    def match_ids(self, platform, puuid, *, start=0, count=20, **kw):
        return [self.match_id][start: start + count]

    def match(self, match_id, **kw):
        return copy.deepcopy(self._match)

    def timeline(self, match_id, **kw):
        return copy.deepcopy(self._timeline)


def test_tracker_stores_the_role_used_by_the_analysis(svc):
    # Sin teamPosition para el dúo de abajo y los dos marcados como UTILITY: el análisis descarta el rol.
    overrides = {4: {"teamPosition": "", "individualPosition": "UTILITY"}, 5: {"teamPosition": "", "individualPosition": "UTILITY"}}
    match = make_match("EUW1_9600000099", puuids={4: ME}, overrides=overrides)
    svc.riot = _FakeRiot(match, make_timeline(match))
    store.save_profile(svc.db, game_name="Tempu", tag_line="EUW", platform="euw1", puuid=ME)
    tracker = GameTracker(svc)
    tracker.sync()
    assert svc.db.scalar("SELECT role FROM my_games WHERE match_id = 'EUW1_9600000099'") is None
    with svc.db.write() as conn:
        conn.execute("UPDATE my_games SET role = 'SUPPORT'")
    tracker.reanalyze_outdated(force=True)
    assert svc.db.scalar("SELECT role FROM my_games WHERE match_id = 'EUW1_9600000099'") is None


def test_tracker_watches_the_client_without_a_profile(svc, monkeypatch):
    from app import lcu

    monkeypatch.setattr(lcu, "available", lambda: True)
    monkeypatch.setattr(lcu, "gameflow_phase", lambda: "InProgress")
    assert store.get_profile(svc.db) is None
    tracker = GameTracker(svc)
    tracker.start()
    try:
        deadline = time.time() + 5
        while not tracker.state["in_game"] and time.time() < deadline:
            time.sleep(0.05)
        assert tracker.state["in_game"] is True and tracker.state["phase"] == "InProgress"
        assert tracker.state["status"] in ("no_profile", "starting")
    finally:
        tracker.shutdown()


def test_client_watch_errors_never_stop_the_tracker(svc, monkeypatch):
    from app import lcu

    def boom():
        raise RuntimeError("cliente roto")

    monkeypatch.setattr(lcu, "available", lambda: True)
    monkeypatch.setattr(lcu, "gameflow_phase", boom)
    tracker = GameTracker(svc)
    tracker._watch_client_safe()  # no lanza
    assert tracker.state["in_game"] is False


def test_summary_reports_sample_so_the_ui_never_claims_no_weaknesses_with_few_games():
    from app.coach import insights
    from app.coach.benchmarks import References
    from tests.test_coach import _rows

    few = insights.summarize(_rows(3), References(None, "PLATINUM"), [])
    assert few["weaknesses"] == []
    assert few["weakness_sample"] < few["min_games_weakness"]  # la web dice «falta muestra», no «lo haces bien»
    enough = insights.summarize(_rows(8), References(None, "PLATINUM"), [])
    assert enough["weakness_sample"] >= enough["min_games_weakness"] and enough["weaknesses"]
    assert enough["reliable_games"] == 8
