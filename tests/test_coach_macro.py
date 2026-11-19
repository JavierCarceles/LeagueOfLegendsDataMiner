"""Macro con los relojes de 2026: preparación de objetivos y conversión de ventajas.

Cada detección tiene su caso positivo y las excepciones que evitan falsos positivos (vuelta a base
obligada, sin datos de vida, buen cambio del equipo, relojes que no cuadran…). El timeline de las
factorías trae las larvas a las 6:20 (relojes antiguos): los tests que necesitan relojes las quitan.
"""

from __future__ import annotations

from app.coach import clocks as clk
from app.coach import insights
from app.coach.analyzer import analyze_game
from tests.test_coach_audit import BARON, DRAGON, buy, ev, is_event, kill, moments, monster, run, tower

GRUBS_0620 = [is_event("ELITE_MONSTER_KILL", t) for t in (380_000, 385_000, 392_000)]
DRAGON_0900 = is_event("ELITE_MONSTER_KILL", 540_000)
BARON_2610 = is_event("ELITE_MONSTER_KILL", 1_570_000)
HP_FULL = {"championStats": {"health": 900, "healthMax": 1000}}
HP_LOW = {"championStats": {"health": 300, "healthMax": 1000}}


def objective(kind: str, t: int, team: int = 100) -> dict:
    return {"kind": kind, "t": t, "team": team}


# ------------------------------------------------------------------ relojes
def test_clocks_follow_the_2026_timers():
    objs = [
        objective("dragon", 340_000),
        objective("grubs", 500_000, 200),
        objective("dragon", 700_000),
        objective("herald", 930_000),
        objective("dragon", 1_010_000),
        objective("baron", 1_260_000, 200),
        objective("dragon", 1_400_000),  # 4.º de azul: alma
        objective("elder", 1_790_000),  # 6 min después del alma
        objective("baron", 1_650_000, 200),
    ]
    objs.sort(key=lambda o: o["t"])
    assert clk.assign_spawns(objs, clk.SEASON_CLOCKS["16"])
    spawn = {(o["kind"], o["t"]): o["spawn"] for o in objs}
    assert spawn[("dragon", 340_000)] == 300_000
    assert spawn[("dragon", 700_000)] == 640_000  # 5 min después del anterior
    assert spawn[("grubs", 500_000)] == 480_000
    assert spawn[("herald", 930_000)] == 900_000
    assert spawn[("baron", 1_260_000)] == 1_200_000
    assert spawn[("baron", 1_650_000)] == 1_620_000  # 6 min después del anterior
    assert spawn[("elder", 1_790_000)] == 1_760_000
    clocks = clk.SEASON_CLOCKS["16"]
    assert clk.alive_at(objs, clocks, 320_000) == ["dragon"]
    assert "dragon" not in clk.alive_at(objs, clocks, 600_000)  # reapareciendo
    assert clk.alive_at(objs, clocks, 1_500_000)[:1] == []  # tras el alma no hay dragón; el ancestral aún no
    assert "elder" in clk.alive_at(objs, clocks, 1_770_000)


def test_clocks_are_discarded_when_an_objective_dies_before_its_time():
    objs = [objective("grubs", 380_000)]  # larvas a las 6:20: otra temporada
    assert not clk.assign_spawns(objs, clk.SEASON_CLOCKS["16"])
    assert objs[0]["spawn"] is None
    a = run()  # el timeline de las factorías trae las larvas a las 6:20
    assert a["clocks"] is False
    assert all(a["metrics"][k] is None for k in ("obj_late_recall", "obj_late_arrival", "pink_unused", "baron_wasted", "fights_unconverted"))
    assert a["metrics"]["deaths_outnumbered_obj"] is None


def test_clocks_unknown_season_swiftplay_and_other_maps():
    assert clk.clocks_for({"gameVersion": "16.22.1", "queueId": 420, "mapId": 11})
    assert clk.clocks_for({"gameVersion": "15.10.1", "queueId": 420, "mapId": 11}) is None
    assert clk.clocks_for({"gameVersion": "16.22.1", "queueId": 480, "mapId": 11}) is None
    assert clk.clocks_for({"gameVersion": "16.22.1", "queueId": 450, "mapId": 12}) is None


def test_with_valid_clocks_the_game_is_judged():
    a = run(drop=GRUBS_0620)
    assert a["clocks"] is True
    assert a["metrics"]["obj_late_recall"] == 0 and a["metrics"]["deaths_outnumbered_obj"] == 0


# ------------------------------------------------------- vuelta a base a destiempo
def _late_recall_game(hp=HP_FULL, recall_t=330_000, extra=(), with_loss=True):
    events = [
        monster(340_000, 4, 100, "DRAGON", DRAGON, sub="FIRE_DRAGON", assists=[5]),  # vuestro dragón a las 5:40…
        *([kill(335_000, 9, 3, (9700, 4300))] if with_loss else []),  # …con vuestro mid caído en el foso
        buy(recall_t, 2, 1036),  # el jungla vuelve a base con el dragón ya fuera
        *extra,
    ]
    frames = {(2, 5): hp, (2, 4): hp} if hp else None
    return run(events, frames, pid=2, drop=[*GRUBS_0620, DRAGON_0900])


def test_late_recall_with_the_objective_up_and_your_team_fighting_it():
    a = _late_recall_game()
    found = moments(a, "objective", "late_recall")
    assert len(found) == 1
    m = found[0]
    assert m["title"] == "Volviste a base a destiempo antes del dragón"
    assert "con el dragón ya en el mapa" in m["detail"] and "lo cogisteis, pero con 1 caído" in m["detail"]
    assert m["t"] == 330.0
    assert a["metrics"]["obj_late_recall"] == 1


def test_late_recall_exceptions():
    # Vuelta obligada (poca vida) o sin datos de vida en el frame: no se acusa.
    assert not moments(_late_recall_game(hp=HP_LOW), tag="late_recall")
    assert not moments(_late_recall_game(hp=None), tag="late_recall")
    # Tu equipo lo cogió sin bajas: tu vuelta a base no costó nada.
    assert not moments(_late_recall_game(with_loss=False), tag="late_recall")
    # Volver 50 s antes de que aparezca es lo correcto (60-90 s es lo ideal): no es a destiempo.
    assert not moments(_late_recall_game(recall_t=250_000), tag="late_recall")
    # Acababas de pelear: volver es lo normal.
    assert not moments(_late_recall_game(extra=[kill(310_000, 2, 8, (6000, 6000))]), tag="late_recall")


# ------------------------------------------------------------------ llegar tarde
LATE_FRAMES = {(2, 4): {"position": (3000, 11000)}, (2, 5): {"position": (4500, 8500)}, (2, 6): {"position": (7500, 6500)}}


def _late_arrival_game(extra=(), frames=None):
    events = [
        monster(345_000, 7, 200, "DRAGON", DRAGON, sub="FIRE_DRAGON", assists=[9, 10]),
        kill(340_000, 10, 4, (9700, 4300)),  # tu ADC cae peleándolo
        *extra,
    ]
    return run(events, {**LATE_FRAMES, **(frames or {})}, pid=2, drop=[*GRUBS_0620, DRAGON_0900])


def test_late_arrival_to_a_contested_objective():
    a = _late_arrival_game()
    found = moments(a, "objective", "late")
    assert len(found) == 1
    assert found[0]["title"] == "Llegaste tarde al dragón"
    assert "un minuto antes de que apareciera estabas en" in found[0]["detail"]
    assert a["metrics"]["obj_late_arrival"] == 1
    # No se cuenta además como objetivo regalado (no estabas lejos: llegabas).
    assert not moments(a, "objective", "far")


def test_late_arrival_exceptions():
    # Cerca del foso un minuto antes: no es llegar tarde.
    assert not moments(_late_arrival_game(frames={(2, 4): {"position": (9000, 5000)}}), tag="late")
    # Volviste a base antes (se juzga aparte, con su propia regla).
    assert not moments(_late_arrival_game(extra=[buy(250_000, 2, 1036)]), tag="late")
    # Moriste justo antes: lo cuenta la muerte.
    assert not moments(_late_arrival_game(extra=[kill(280_000, 7, 2, (6000, 8000))]), tag="late")


# ------------------------------------------------------- pelear con uno menos
def _outnumbered_game(ally_death_t=1_300_000, extra=()):
    events = [
        kill(ally_death_t, 8, 3, (7400, 7400)),  # tu mid muere en el centro…
        kill(1_330_000, 7, 2, (9500, 4600), assists=[9]),  # …y tú, junto al dragón, 30 s después
        monster(1_350_000, 7, 200, "DRAGON", DRAGON, sub="FIRE_DRAGON", assists=[9]),
        *extra,
    ]
    return run(events, pid=2, drop=[*GRUBS_0620, DRAGON_0900])


def test_death_at_an_objective_with_one_ally_down():
    a = _outnumbered_game()
    d = next(d for d in a["deaths"] if d["t"] == 1330.0)
    assert "fight_outnumbered" in d["tags"]
    m = next(m for m in a["moments"] if m["t"] == 1330.0 and m["kind"] == "death")
    assert m["title"] == "Muerte junto al dragón con uno menos (4 contra 5)"
    assert "muerto desde antes de la pelea" in m["detail"]
    assert m["severity"] == "high"
    assert a["metrics"]["deaths_outnumbered_obj"] == 1


def test_outnumbered_exceptions():
    # El aliado acababa de caer (la propia pelea): la inferioridad no se sabía.
    a = _outnumbered_game(ally_death_t=1_325_000)
    assert "fight_outnumbered" not in next(d for d in a["deaths"] if d["t"] == 1330.0)["tags"]
    # Su alma en juego (3 dragones suyos): hay que disputarlo aunque sea con uno menos.
    theirs = [monster(t, 7, 200, "DRAGON", DRAGON, sub="FIRE_DRAGON") for t in (340_000, 700_000, 1_010_000)]
    a = _outnumbered_game(extra=theirs)
    assert "fight_outnumbered" not in next(d for d in a["deaths"] if d["t"] == 1330.0)["tags"]


# ------------------------------------------------------------------ pinks
def _pink_game(extra=()):
    events = [
        buy(200_000, 2, 2055),  # pink a las 3:20
        monster(400_000, 2, 100, "DRAGON", DRAGON, sub="FIRE_DRAGON", assists=[4, 5]),
        *extra,
    ]
    return run(events, pid=2, drop=[*GRUBS_0620, DRAGON_0900])


def test_pink_carried_through_an_objective_without_placing_it():
    a = _pink_game()
    found = moments(a, "vision", "pink")
    assert len(found) == 1
    assert found[0]["title"] == "Pink sin usar antes del dragón"
    assert a["metrics"]["pink_unused"] == 1


def test_pink_placed_is_fine():
    placed = [ev(280_000, "WARD_PLACED", creatorId=2, wardType="CONTROL_WARD"), ev(280_000, "ITEM_DESTROYED", participantId=2, itemId=2055)]
    a = _pink_game(placed)
    assert not moments(a, tag="pink")
    assert a["metrics"]["pink_unused"] == 0


# ------------------------------------------------------------------ barón
def _baron_game(extra=()):
    events = [monster(1_500_000, 2, 100, "BARON_NASHOR", BARON, assists=[1, 3]), *extra]
    return run(events, pid=2, drop=[*GRUBS_0620, BARON_2610])


def test_baron_without_any_structure_afterwards():
    a = _baron_game()
    found = moments(a, "objective", "baron_wasted")
    assert len(found) == 1 and "team" in found[0]["tags"]
    assert a["metrics"]["baron_wasted"] == 1


def test_baron_converted_into_a_tower():
    a = _baron_game([tower(1_600_000, 3, 200, "MID_LANE", (8955, 8510))])
    assert not moments(a, tag="baron_wasted")


# ------------------------------------------------------- cazado y recompensa
def test_collapsed_on_alone_in_their_half_and_bounty_given_away():
    hunted = ev(1_200_000, "CHAMPION_KILL", killerId=6, victimId=1, assistingParticipantIds=[7, 8],
                position={"x": 3000, "y": 13700}, bounty=300, shutdownBounty=600)
    a = run([hunted])
    d = next(d for d in a["deaths"] if d["t"] == 1200.0)
    assert {"isolated", "collapsed", "bounty"} <= set(d["tags"])
    m = next(m for m in a["moments"] if m["t"] == 1200.0 and m["kind"] == "death")
    assert m["title"] == "Te cazaron solo en su mitad del mapa (3 rivales)"
    assert "regalaste 600 de oro de recompensa" in m["detail"]
    assert a["metrics"]["deaths_collapsed"] == 1
    # En tu mitad del mapa no es «cazado en su mitad».
    own_half = ev(1_200_000, "CHAMPION_KILL", killerId=6, victimId=1, assistingParticipantIds=[7, 8],
                  position={"x": 1300, "y": 9000}, bounty=300, shutdownBounty=0)
    d = next(d for d in run([own_half])["deaths"] if d["t"] == 1200.0)
    assert "collapsed" not in d["tags"] and "bounty" not in d["tags"]


# ------------------------------------------------- objetivos del otro lado del mapa
def test_adc_is_not_blamed_for_the_15_00_herald_while_still_in_lane():
    far_bot = {(4, 15): {"position": (12000, 2000)}, (4, 16): {"position": (12100, 2100)}}
    herald = monster(930_000, 7, 200, "RIFTHERALD", BARON, assists=[8])
    a = run([herald], far_bot, pid=4)
    assert a["metrics"]["obj_lost_far"] == 0
    # Si ya cayó su torre exterior, la fase de líneas acabó y sí se espera que rote.
    a = run([herald, tower(800_000, 9, 100, "BOT_LANE", (10504, 1029))], far_bot, pid=4)
    assert a["metrics"]["obj_lost_far"] == 1


def test_ornn_purchases_are_not_recalls():
    a = run(overrides={1: {"championId": 516}})
    anchors = a["replay"]["tracks"]["1"]["a"]
    assert not any(kind == 2 and abs(t - 1110.0) < 0.5 for t, _x, _y, kind in anchors)
    b = run()
    assert any(kind == 2 and abs(t - 1110.0) < 0.5 for t, _x, _y, kind in b["replay"]["tracks"]["1"]["a"])


# ------------------------------------------------------- errores que repites
def test_new_mistakes_group_in_recurring_mistakes():
    def game(i, tags, kind="objective"):
        return {"match_id": f"EUW1_{i}", "queue": 420, "role": "JUNGLE", "champ": 64, "win": False, "has_timeline": True,
                "metrics": {}, "mistakes": [[kind, "medium", 300.0, tags, "x"]]}

    games = [game(i, ["objective", "late_recall"]) for i in range(3)] + [game(9, ["vision", "pink"], "vision")]
    out = insights.recurring_mistakes(games)
    ids = [x["id"] for x in out["items"]]
    assert ids == ["objective_late_recall"]
    assert out["items"][0]["tip"].startswith("Mira el reloj del objetivo")


def test_analysis_without_timeline_keeps_working():
    from tests.test_coach_audit import base_match

    a = analyze_game(base_match(), None, pid=1)
    assert a["clocks"] is False and a["has_timeline"] is False
