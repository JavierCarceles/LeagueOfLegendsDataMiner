"""Reglas medibles de las guías (``rule_metrics``) y la partida sin quitar ningún ward (``champ_checks``)."""

from app.coach import champ_checks, rule_metrics
from app.coach.insights import _move_trend


def _participant(**kw):
    p = {
        "summoner1Id": 4, "summoner2Id": 12, "summoner1Casts": 3, "summoner2Casts": 5, "wardsKilled": 6,
        "visionWardsBoughtInGame": 3,
        "challenges": {"visionScoreAdvantageLaneOpponent": 0.25, "killsOnOtherLanesEarlyJungleAsLaner": 1, "kTurretsDestroyedBeforePlatesFall": 1},
    }
    p.update(kw)
    return p


def test_las_metricas_salen_del_participante_y_de_las_vueltas_a_base():
    raw = rule_metrics.raw_fields(_participant())
    assert raw["tp_casts"] == 5  # el Teleport es el segundo hechizo
    backs = [
        {"t": 3, "items": [1055], "gold": 450, "dead": False},  # compra inicial: no cuenta
        {"t": 400, "items": [1036, 2055], "gold": 425, "dead": False},
        {"t": 700, "items": [1001], "gold": 300, "dead": False},  # vuelta sin oro para nada útil
        {"t": 900, "items": [3006], "gold": 800, "dead": True},  # al reaparecer: no cuenta
        {"t": 1500, "items": [6672], "gold": 3000, "dead": False},
    ]
    m = rule_metrics.metrics(raw, backs, 30, "TOP")
    assert m["rl_wards_killed10"] == 2.0
    assert m["rl_vision_adv"] == 25.0
    assert m["rl_roam_kills"] == 1 and m["rl_towers_pre14"] == 1 and m["rl_tp_casts"] == 5
    assert m["rl_pink_back_pct"] == round(100 / 3, 1)
    assert m["rl_cheap_recall_pct"] == 50.0  # 1 de las 2 vueltas sin morir antes del 20


def test_jungla_sin_roams_de_linea_ni_teleport_y_partidas_cortas_sin_datos():
    raw = rule_metrics.raw_fields(_participant())
    m = rule_metrics.metrics(raw, [], 30, "JUNGLE")
    assert "rl_roam_kills" not in m and "rl_tp_casts" not in m
    assert rule_metrics.metrics(raw, [], 12, "TOP") == {}


def test_el_dossier_de_un_otp_da_lo_mismo_que_tu_partida():
    d = {
        "role": "TOP", "duration": 1800, "spells": [4, 12],
        "riot_stats": {"wardsKilled": 6, "visionWardsBoughtInGame": 3, "summoner1Casts": 3, "summoner2Casts": 5},
        "challenges": _participant()["challenges"],
        "analysis": {"build": {"backs": []}},
    }
    assert rule_metrics.from_dossier(d) == rule_metrics.metrics(rule_metrics.raw_fields(_participant()), [], 30, "TOP")


def test_recuentos_pequenos_tambien_marcan_hacia_donde_van_los_elos():
    row = {"unit": "", "mine": 0.03, "tiers": {"EMERALD": 0.04, "CHALLENGER": 0.14}}
    assert _move_trend(row) == {"trend": "up", "standing": "low"}
    flat = {"unit": "%", "mine": 8.0, "tiers": {"EMERALD": 7.8, "CHALLENGER": 8.4}}
    assert _move_trend(flat)["trend"] is None


def test_partida_larga_sin_quitar_ningun_ward():
    rec = {"role": "JUNGLE", "minutes": 31, "wards_killed": 0, "control_wards": 2, "wards_pre14": 6}
    tags = [m["tags"][-1] for m in champ_checks.check(rec, None, None, "Lee Sin")]
    assert tags == ["no_sweep"]
    assert champ_checks.check({**rec, "wards_killed": 1}, None, None, "Lee Sin") == []
    assert champ_checks.check({**rec, "minutes": 22}, None, None, "Lee Sin") == []
    assert champ_checks.check({**rec, "role": "SUPPORT"}, None, None, "Nami") == []
