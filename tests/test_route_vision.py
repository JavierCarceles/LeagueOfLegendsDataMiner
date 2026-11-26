"""Destinos de los roams, visión útil (``route_macro``) y errores que no lo son (cambios igualados, lo que los mejores hacen más)."""

from app.coach import insights, route_macro, tracker
from app.coach import geometry as geo


def _anchors(points):
    """[(ms, x, y, 0)] cada 5 s a partir del 1:30."""
    return [(90_000 + i * 5_000, x, y, 0) for i, (x, y) in enumerate(points)]


def test_el_support_que_sale_al_rio_y_a_mid_cuenta_cada_destino():
    bot = (12500, 2500)
    path = [bot] * 6 + [(9800, 4400)] * 6 + [bot] * 6 + [(7500, 7500)] * 6 + [bot] * 150
    out = route_macro.features(_anchors(path), [], [], me=5, role="SUPPORT", team=100, wards=[], objectives=[])
    assert out["rm_roams"] == 2
    assert out["rm_roam_river"] == 1 and out["rm_roam_lane"] == 1
    assert out["rm_roam_own_jg"] == 0 and out["rm_roam_enemy_jg"] == 0


def test_vision_ward_del_objetivo_pinks_y_jungla_rival():
    # Quieto junto al dragón de 1:30 a 6:00, luego en la jungla rival de abajo (equipo azul).
    path = [geo.PITS["dragon"]] * 54 + [(11000, 7000)] * 100
    wards = [
        [200.0, "YELLOW_TRINKET"],  # junto al dragón, 100 s antes de que caiga
        [250.0, "CONTROL_WARD"],  # pink junto al dragón
        [400.0, "YELLOW_TRINKET"],  # jungla rival antes del 10
        [500.0, "SIGHT_WARD"],
        [900.0, "TEEMO_MUSHROOM"],  # no cuenta
    ]
    objectives = [[300.0, "dragon", 100, 9866, 4414], [1500.0, "baron", 200, 5007, 10471]]
    out = route_macro.features(_anchors(path), [], [], me=5, role="SUPPORT", team=100, wards=wards, objectives=objectives)
    assert out["rm_wards_pre14"] == 4
    assert out["rm_pink_obj"] == 1
    assert out["rm_ward_enemy_jg10"] == 2
    assert out["rm_obj_ward_pct"] == 50.0  # el dragón con ward; el barón no
    assert out["rm_ward_enemy_jg_pct"] == 50.0


def test_sin_wards_no_hay_metricas_de_vision():
    path = [(12500, 2500)] * 160
    out = route_macro.features(_anchors(path), [], [], me=5, role="ADC", team=100)
    assert "rm_wards_pre14" not in out and "rm_obj_ward_pct" not in out


def test_tus_wards_salen_de_la_linea_de_tiempo():
    tl = {"info": {"frames": [{"events": [
        {"type": "WARD_PLACED", "creatorId": 5, "wardType": "YELLOW_TRINKET", "timestamp": 61_000},
        {"type": "WARD_PLACED", "creatorId": 5, "wardType": "UNDEFINED", "timestamp": 62_000},
        {"type": "WARD_PLACED", "creatorId": 4, "wardType": "CONTROL_WARD", "timestamp": 63_000},
    ]}]}}
    assert tracker.my_wards(tl, 5) == [[61.0, "YELLOW_TRINKET"]]


def test_un_cambio_igualado_no_es_morir_adelantado_ni_que_te_ganqueen():
    assert insights.mistake_type("death", {"overextended"}, "ADC")["id"] == "death_overextended"
    assert insights.mistake_type("death", {"overextended", "fight_even"}, "ADC") is None
    assert insights.mistake_type("death", {"gank", "fight_even"}, "TOP") is None
    assert insights.excused({"kind": "death", "tags": ["gank", "fight_even"]}, "TOP")
    assert not insights.excused({"kind": "death", "tags": ["gank"]}, "TOP")
    # Morir antes de un objetivo rival sigue contando aunque el cambio fuera igualado.
    assert insights.mistake_type("death", {"before_objective", "overextended", "fight_even"}, "MID")["id"] == "death_before_objective"


def test_lo_que_los_mejores_hacen_mas_pesa_menos_que_lo_que_no_separa():
    tiers = {
        "PLATINUM": {"types": {"death_gank": {"per_game": 0.23, "pct": 20}, "death_solo": {"per_game": 0.6, "pct": 40}, "death_isolated": {"per_game": 0.40, "pct": 30}}},
        "CHALLENGER": {"types": {"death_gank": {"per_game": 0.39, "pct": 31}, "death_solo": {"per_game": 0.55, "pct": 38}, "death_isolated": {"per_game": 0.21, "pct": 17}}},
    }
    assert insights.separation(tiers, "death_gank")["rises"]
    solo = insights.separation(tiers, "death_solo")
    assert solo["flat"] and not solo["rises"]
    iso = insights.separation(tiers, "death_isolated")
    assert not iso["flat"] and not iso["rises"]


def test_el_support_que_roamea_al_volver_de_base_y_al_lado_de_arriba():
    bot, base = (12500, 2500), (1000, 1000)
    # Sale de la línea (al río de abajo), vuelve, va a base y al salir pasa por el río de arriba antes de volver a bot.
    path = [bot] * 12 + [(9800, 4400)] * 6 + [bot] * 12 + [base] * 4 + [(5000, 9500)] * 6 + [bot] * 120
    out = route_macro.features(_anchors(path), [], [], me=5, role="SUPPORT", team=100, wards=[], objectives=[])
    assert out["rm_roams"] == 2
    assert out["rm_sup_base_roams"] == 1
    assert out["rm_sup_top_side"] == 1
    assert out["rm_sup_first_roam_min"] == 2.5


def test_el_support_que_no_sale_no_tiene_primer_roam():
    out = route_macro.features(_anchors([(12500, 2500)] * 160), [], [], me=5, role="SUPPORT", team=100, wards=[], objectives=[])
    assert out["rm_sup_base_roams"] == 0 and out["rm_sup_top_side"] == 0
    assert "rm_sup_first_roam_min" not in out
