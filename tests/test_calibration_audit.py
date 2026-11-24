"""Auditoría de la calibración: qué separa elos, qué no y si es por el ruido de la reconstrucción."""

import random

from app.coach import calibration_audit as ca
from app.coach import rule_metrics


def _rows(n=400):
    """Las partidas con la posición segura cambian con el elo (10 → 10,6); las inseguras son ruido puro (5 o 15)."""
    rows = []
    for group, shift in (("PLATINUM", 0.0), ("CHALLENGER", 0.6)):
        for i in range(n):
            sure = i % 2 == 0
            v = (10 + shift + (1 if i % 4 == 0 else -1)) if sure else (5.0 if i % 4 == 1 else 15.0)
            rows.append({"role": "SUPPORT", "group": group, "certainty": 0.05 if sure else 0.5, "values": {"rm_roam_lane": v},
                         "mistakes": set(), "official": {}, "with_ev": False, "sure_mistakes": set(), "unsure_mistakes": set()})
    return rows


def test_lo_que_separa_y_lo_que_es_igual():
    assert ca.compare({"PLATINUM": [1.0] * 150 + [2.0] * 150, "CHALLENGER": [3.0] * 300})["veredicto"] == "separa"
    same = ca.compare({"PLATINUM": [1.0, 2.0] * 150, "CHALLENGER": [2.0, 1.0] * 150})
    assert same["veredicto"] == "igual"
    assert ca.compare({"PLATINUM": [1.0] * 10, "CHALLENGER": [2.0] * 10})["veredicto"] == "pocos datos"


def test_el_ruido_de_la_reconstruccion_tapa_la_diferencia():
    report = ca.audit(_rows())
    item = report["SUPPORT"]["metricas"]["rm_roam_lane"]
    assert item["veredicto"] != "separa" and item["posicion_segura"]["veredicto"] == "separa"
    assert item["diagnostico"] == "lo iguala el ruido de la reconstrucción"


def test_la_vision_exacta_de_riot_va_a_mi_progreso():
    raw = {"ch": {"wardTakedownsBefore20M": 3, "controlWardsPlaced": 6, "visionScorePerMinute": 2.4, "wardsGuarded": 1,
                  "twoWardsOneSweeperCount": 2, "killAfterHiddenWithAlly": 1, "enemyJungleMonsterKills": 12, "scuttleCrabKills": 2},
           "riot": {}}
    sup = rule_metrics.metrics(raw, [], 30, "SUPPORT")
    assert sup["rl_ward_takedowns20"] == 3 and sup["rl_pinks10"] == 2.0 and sup["rl_vision_min"] == 2.4
    assert sup["rl_two_wards_sweep"] == 2 and "rl_enemy_camps10" not in sup
    jg = rule_metrics.metrics(raw, [], 30, "JUNGLE")
    assert jg["rl_enemy_camps10"] == 4.0 and jg["rl_scuttles"] == 2
    assert "rl_ward_takedowns20" not in rule_metrics.metrics(raw, [], 18, "MID")  # no llegó al 20
