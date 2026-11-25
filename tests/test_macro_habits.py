"""Hábitos de macro de ``macro/macro_internet_por_elo.md`` medibles con el análisis (``app/coach/macro_habits.py``)."""

from __future__ import annotations

from app.coach import macro_habits as mh


def test_objectives_turned_into_structures():
    """Tras un objetivo de tu equipo, ¿cae una torre o un inhibidor rival en 90 s (3 min tras el barón)? Las larvas
    seguidas son una sola jugada; si tu equipo pierde a dos o más en ese rato, no cuenta."""
    objectives = [
        [480, "grubs", 100, 4800, 10100], [490, "grubs", 100, 4800, 10100],  # una jugada (convertida a los 560)
        [600, "dragon", 200, 9800, 4400],  # del rival: no cuenta
        [900, "herald", 100, 4800, 10100],  # sin estructura después
        [1200, "dragon", 100, 9800, 4400],  # perdéis a dos: no cuenta
        [1500, "baron", 100, 5000, 10400],  # inhibidor a los 2:30: sí (3 min tras el barón)
    ]
    buildings = [[560, 200, "tower", "top", 4318, 13875], [1640, 200, "inhibitor", "mid", 11593, 11669], [700, 100, "tower", "bot", 10504, 1029]]
    kills = [[1210, 7, 2, 9000, 4000, []], [1215, 8, 3, 9000, 4000, []]]
    assert mh.objective_conversion(objectives, buildings, kills, 100) == {"rm_obj_convert_pct": 66.7}
    assert mh.objective_conversion(objectives, None, kills, 100) == {}
    assert mh.objective_conversion([], buildings, kills, 100) == {}


def test_jungle_pace_full_clear_and_first_back():
    cs = [0, 0, 8, 16, 26, 30, 40, 52, 60, 70]
    out = mh.jungle_pace(cs, [{"t": 10, "dead": False}, {"t": 250, "dead": False}])
    assert out == {"rm_jg_clear_min": 3.8, "rm_first_back_min": 4.17}  # 24 de CS entre el 3 (16) y el 4 (26)
    assert mh.jungle_pace([0, 4, 8, 12, 16, 18, 20, 22, 23], [{"t": 10}, {"t": 200, "dead": True}]) == {}  # sin full clear y murió


def test_jungle_idle_minutes_after_the_first_clear():
    cs = [0, 0, 8, 16, 24, 30, 36, 36, 44, 50, 56, 60, 66, 70, 76]  # del 6 al 7 sin CS
    in_jungle = (3800, 7000)

    def pos(_t):
        return in_jungle

    assert mh.jungle_idle(cs, [], 2, 100, [], pos, []) == {"rm_jg_idle_min": 1}
    assert mh.jungle_idle(cs, [[390, 2, 8, 0, 0, []]], 2, 100, [], pos, []) == {"rm_jg_idle_min": 0}  # kill tuya ese minuto
    assert mh.jungle_idle(cs, [], 2, 100, [], pos, [(355_000, 380_000)]) == {"rm_jg_idle_min": 0}  # muerto
    assert mh.jungle_idle(cs, [], 2, 100, [], lambda _t: (400, 400), []) == {"rm_jg_idle_min": 0}  # en base


def test_farsight_per_ten_minutes():
    wards = [[100, "YELLOW_TRINKET"], [900, "BLUE_TRINKET"], [1200, "BLUE_TRINKET"], [1500, "BLUE_TRINKET"]]
    assert mh.farsight(wards, 30) == {"rm_farsight10": 1.0}
    assert mh.farsight(wards, 12) == {}  # partida demasiado corta


def test_the_extra_tower_shot_in_a_dive():
    """Tu muerte bajo una torre rival en pie en los 10 s siguientes a una kill de tu equipo allí (pid 2, azul)."""
    under_mid = (9100, 8600)  # junto a la exterior roja de mid (8955, 8510)
    kills = [[595, 1, 8, 9000, 8550, [2]], [600, 9, 2, *under_mid, []]]
    assert mh.dive_extra(kills, [], 2, 100) == {"rm_dive_extra": 1}
    fallen = [[500, 200, "tower", "mid", 8955, 8510]]  # la torre ya había caído: no es un dive
    assert mh.dive_extra(kills, fallen, 2, 100) == {"rm_dive_extra": 0}
    alone = [[600, 9, 2, *under_mid, []]]  # sin kill de tu equipo antes
    assert mh.dive_extra(alone, [], 2, 100) == {"rm_dive_extra": 0}
    assert mh.team_of(3) == 100 and mh.team_of(7) == 200 and mh.team_of(0) is None and mh.team_of("x") is None
