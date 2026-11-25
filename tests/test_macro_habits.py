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


def _game(**kw) -> mh.Game:
    base = {"me": 3, "team": 100, "role": "MID", "kills": [], "pos": lambda _t: None}
    return mh.Game(**{**base, **kw})


def test_dead_when_the_dragon_spawns():
    """C-9: muerte en la que reapareces después de que salga el dragón; el rival lo coge antes de que llegues."""
    from app.coach import clocks

    objectives = [[620, "dragon", 200, 9866, 4414], [950, "dragon", 200, 9866, 4414]]  # el segundo sale a los 920 s
    game = _game(dead=[(400_000, 420_000), (900_000, 935_000)], objectives=objectives, minutes=20, clocks=clocks.SEASON_CLOCKS["16"])
    assert mh.dead_at_spawn(game) == {"rm_dead_spawn": 1, "rm_dead_spawn_lost": 1}
    ours = [[620, "dragon", 200, 9866, 4414], [950, "dragon", 100, 9866, 4414]]  # lo cogió tu equipo
    assert mh.dead_at_spawn(_game(dead=game.dead, objectives=ours, minutes=20, clocks=game.clocks)) == {"rm_dead_spawn": 1, "rm_dead_spawn_lost": 0}
    assert mh.dead_at_spawn(_game(dead=game.dead, objectives=objectives, minutes=20)) == {}  # sin relojes fiables
    early = [[200, "dragon", 200, 9866, 4414]]  # muere antes de las 5:00: relojes que no cuadran
    assert mh.dead_at_spawn(_game(dead=game.dead, objectives=early, minutes=20, clocks=game.clocks)) == {}


def test_late_fights_get_concentrated():
    """C-3: las kills del 0-10 por todo el mapa y las del 20+ en un sitio: la dispersión baja casi del todo."""
    early = [[t, 1, 6, x, y, []] for t, (x, y) in zip((100, 200, 300, 400), ((1000, 1000), (13000, 13000), (1000, 13000), (13000, 1000)))]
    late = [[t, 1, 6, x, y, []] for t, (x, y) in zip((1300, 1310, 1320, 1330), ((7000, 7000), (7100, 7000), (7000, 7100), (7100, 7100)))]
    assert mh.fight_spread(early + late, 30) == {"rm_fight_spread_drop": 99.2}
    assert mh.fight_spread(early + late, 22) == {}  # partida corta
    assert mh.fight_spread(early + late[:3], 30) == {}  # pocas kills tardías


def test_death_right_after_the_rival_reaches_six():
    """C-7: tu rival llega a 6 a los 6:30 (su experiencia de cada minuto); su kill en solitario a los 7:00 cuenta."""
    xp = [0, 300, 700, 1100, 1600, 2000, 2200, 2600, 3000, 3400]
    kills = [[420, 8, 3, 5000, 5000, []], [500, 8, 3, 5000, 5000, []], [400, 8, 3, 5000, 5000, [9, 10]]]
    game = _game(opp=8, kills=kills, opp_frames=[(None, None, v) for v in xp])
    assert mh.spike_deaths(game) == {"rm_spike_deaths": 1}  # la de los 500 s llega tarde y la de los 400 s fue de tres
    assert mh.spike_deaths(_game(kills=kills)) == {}  # sin rival de línea


def test_punishing_the_rival_roam():
    """C-8: tu rival de mid se va a top dos minutos; en uno sigues farmeando en tu línea y en el otro no."""
    opp = [(6000, 6000, 0)] * 15
    opp[5] = opp[6] = (1250, 11800, 0)
    cs = [6 * m for m in range(15)]
    cs[7:] = [v - 4 for v in cs[7:]]  # del 6 al 7 solo 2 de CS
    game = _game(opp_frames=opp, cs=cs, pos=lambda _t: (6000, 6000))
    assert mh.punish_roams(game) == {"rm_punish_roam_pct": 50.0}
    assert mh.punish_roams(_game(opp_frames=[(6000, 6000, 0)] * 15, cs=cs, pos=lambda _t: (6000, 6000))) == {}  # no se fue


def test_behind_in_gold_after_fourteen():
    """C-10: del 14 al 20 vais 4.000 por detrás: de los objetivos de esos minutos cogéis la mitad y mueres una vez."""
    diff = [0] * 14 + [-4000] * 7 + [0] * 5
    objectives = [[900, "dragon", 200, 0, 0], [1000, "herald", 100, 0, 0], [1300, "baron", 200, 0, 0]]
    kills = [[1000, 7, 3, 0, 0, []], [1400, 7, 3, 0, 0, []]]
    assert mh.behind(_game(gold_diff=diff, objectives=objectives, kills=kills)) == {"rm_behind_deaths10": 1.43, "rm_behind_obj_pct": 50.0}
    assert mh.behind(_game(gold_diff=[0] * 26, objectives=objectives, kills=kills)) == {}  # nunca por detrás


def test_alone_after_losing_the_first_tower():
    """C-12: tras perder vuestra primera torre (800 s), mueres solo a los 900 s; a los 950 s muere también un aliado."""
    kills = [[900, 7, 3, 5000, 5000, []], [950, 7, 3, 6000, 6000, []], [952, 8, 4, 6100, 6000, []], [1100, 7, 3, 5000, 5000, []]]
    buildings = [[800, 100, "tower", "mid", 5846, 6396], [600, 200, "tower", "top", 4318, 13875]]
    assert mh.alone_after_tower(_game(kills=kills, buildings=buildings)) == {"rm_alone_after_tower": 1}
    assert mh.alone_after_tower(_game(kills=kills, buildings=[])) == {}  # no perdisteis ninguna torre


def test_immobile_adc_dying_in_a_side_lane():
    """C-13: Jinx muere en bot pasado el río tras caer la primera torre; antes de la torre o en mid no cuenta."""
    kills = [[650, 7, 3, 13866, 5000, []], [800, 7, 3, 13866, 5000, []], [900, 7, 3, 7500, 7500, []]]
    buildings = [[700, 200, "tower", "top", 4318, 13875]]
    game = _game(role="ADC", champ=222, kills=kills, buildings=buildings)
    assert mh.adc_side_deaths(game) == {"rm_adc_side_deaths": 1}
    assert mh.habits(game)["rm_adc_side_deaths"] == 1
    assert "rm_adc_side_deaths" not in mh.habits(_game(role="ADC", champ=81, kills=kills, buildings=buildings))  # Ezreal salta


def test_consistency_between_games():
    """C-11: cuánto cambian tus muertes de una partida a otra y la media de tu 20 % de partidas peores (las últimas 20)."""
    from app.coach import consistency

    deaths = [2, 4, 6, 8, 10, 2, 4, 6, 8, 10]
    assert consistency.summary(deaths) == {"deaths_sd": 2.83, "worst_deaths": 10.0}
    assert consistency.summary(deaths[:9]) is None  # menos de 10 partidas
    assert consistency.summary([20] * 5 + deaths + deaths)["worst_deaths"] == 10.0  # solo cuentan las últimas 20


def test_consistency_report_against_each_elo():
    """Tus muertes frente a la mediana de cada elo (``elo_profile``): más constante que Aspirante sale en verde."""
    from app.coach import insights

    games = [{"role": "MID", "has_timeline": True, "metrics": {"deaths": d}} for d in (3, 4, 3, 4, 3, 4, 3, 4, 3, 4, 3)]
    profile = {"roles": {"MID": {
        "BRONZE": {"consistency": {"deaths_sd": 3.1, "worst_deaths": 12.0}},
        "CHALLENGER": {"consistency": {"deaths_sd": 2.4, "worst_deaths": 9.0}},
    }}}
    out = insights.consistency_report(games, profile)
    assert out["role"] == "MID" and out["games"] == 11
    sd, worst = out["rows"]
    assert sd["key"] == "deaths_sd" and sd["mine"] == 0.5 and sd["trend"] == "down" and sd["standing"] == "top"
    assert worst["mine"] == 4.0 and worst["tiers"] == {"BRONZE": 12.0, "CHALLENGER": 9.0}
    assert insights.consistency_report(games[:9], profile) is None


def test_elo_profile_consistency_needs_enough_players():
    """La constancia de un elo es la mediana de sus jugadores con 10 partidas o más (con 15 jugadores como mínimo)."""
    from app.coach import elo_profile

    agg = elo_profile._Agg()
    for p in range(15):
        for i in range(10):
            agg.add([], f"p{p}", None, (), (i, 2 + (i % 2) * 2))  # 2, 4, 2, 4…: desviación 1 y peores 4
    assert agg.public()["consistency"] == {"deaths_sd": 1.0, "worst_deaths": 4.0, "players": 15}
    few = elo_profile._Agg()
    for i in range(10):
        few.add([], "solo", None, (), (i, 3))
    assert few.public()["consistency"] is None


def test_early_jungle_route():
    """Primeros 4 min del jungla azul: empieza por los raptors, 3 campamentos propios, kill a los 3:10 y un campamento
    de la jungla rival: «3 campamentos y gank», sin full clear."""
    from app.coach import geometry as geo
    from app.coach.route_macro import early_route

    camps = {f"{t}_{n}": p for (t, n), p in geo.CAMPS.items()}
    anchors = [
        (95_000, *camps["100_raptors"], 6), (130_000, *camps["100_red"], 6), (160_000, *camps["100_krugs"], 6),
        (200_000, 7000, 7000, 0), (225_000, *camps["200_raptors"], 6),
    ]
    out = early_route(anchors, [[190, 2, 8, 7000, 7000, []]], 2, 100)
    assert out == {"jr_full": 0, "jr_three_gank": 100, "jr_kill3": 0, "jr_invade": 100, "jr_start_small": 100}
    assert early_route(anchors[:1], [], 2, 100) == {}  # sin ruta reconstruida
