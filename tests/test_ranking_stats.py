"""Medias por elo con las partidas del ranking sin analizar (``ranking_stats``) y su mezcla en el perfil por elo."""

from app.coach import elo_profile, ranking_stats


def _match(controls=(2, 6), minutes=30):
    parts = []
    for i, (pos, cw) in enumerate((("UTILITY", controls[0]), ("JUNGLE", controls[1]))):
        parts.append({
            "participantId": i + 1, "teamPosition": pos, "summoner1Id": 4, "summoner2Id": 11, "wardsKilled": 4,
            "challenges": {"controlWardsPlaced": cw, "visionScorePerMinute": 1.5, "wardTakedownsBefore20M": 3,
                           "enemyJungleMonsterKills": 9, "scuttleCrabKills": 2},
        })
    parts.append({"participantId": 3, "teamPosition": "", "challenges": {}})  # sin rol: no cuenta
    return {"info": {"gameDuration": minutes * 60, "participants": parts}}


def test_suma_los_10_jugadores_por_rol_y_elo_y_lo_vuelca(tmp_path):
    stats = ranking_stats.RankingStats(tmp_path)
    stats.add(_match(), "GOLD")
    stats.add(_match(controls=(4, 6)), "SILVER")  # Hierro → Platino van juntos
    stats.add(_match(), "UNRANKED")  # sin elo conocido: no cuenta
    stats.flush()
    data = ranking_stats.load(tmp_path)
    assert data["games"] == 2
    sup = data["roles"]["SUPPORT"]["PLATINUM"]
    assert sup["rl_pinks10"][0] == 2 and abs(sup["rl_pinks10"][1] - (2 * 10 / 30 + 4 * 10 / 30)) < 1e-6
    jg = data["roles"]["JUNGLE"]["PLATINUM"]
    assert jg["rl_enemy_camps10"][0] == 2 and "rl_scuttles" in jg
    # Partidas cortas (menos de 15 min): sin métricas.
    assert ranking_stats.participant_metrics(_match(minutes=12)) == []
    # El siguiente volcado suma a lo que había.
    stats.add(_match(), "GOLD")
    stats.flush()
    assert ranking_stats.load(tmp_path)["games"] == 3


def test_el_perfil_por_elo_mezcla_las_medias_del_ranking(tmp_path):
    stats = ranking_stats.RankingStats(tmp_path)
    for _ in range(3):
        stats.add(_match(controls=(6, 6)), "GOLD")  # pinks10 del support = 2,0
    stats.flush()
    out = {"roles": {"SUPPORT": {"PLATINUM": {"games": 1, "move": {"rl_pinks10": 1.0}}}}}
    elo_profile._merge_ranking(out, tmp_path)
    block = out["roles"]["SUPPORT"]["PLATINUM"]
    assert block["move"]["rl_pinks10"] == 1.75 and block["move_n"]["rl_pinks10"] == 4  # (1·1 + 3·2) / 4
