"""Ajustes del análisis de «Mi progreso» calibrados con 704 partidas de junglas Aspirante (OTPs, data/otps)."""

from __future__ import annotations

from app.coach.analyzer import ANALYSIS_VERSION
from app.coach.benchmarks import BASE
from tests.test_coach import RED_JUNGLE_TOP, _analyze, _event, _kill


def test_analysis_version_bumped_so_your_games_are_reanalysed():
    assert ANALYSIS_VERSION >= 10


def test_r_is_only_required_at_level_6():
    skills = [_event(t, "SKILL_LEVEL_UP", participantId=1, skillSlot=slot, levelUpType="NORMAL")
              for t, slot in ((1_000, 1), (90_100, 2), (180_100, 3), (270_100, 1), (360_100, 1), (500_000, 4))]
    a = _analyze(skills)  # una sola R (a las 8:20, nivel 6 a las 7:30): nunca la sube a 11 ni a 16
    assert a["build"]["r_delays"] == [{"level": 6, "t": 450.0, "delay": 50}] and a["metrics"]["r_late"] == 1
    assert not any("nivel 11" in mo["title"] or "nivel 16" in mo["title"] for mo in a["moments"])


def test_no_boots_warning_for_the_jungler():
    a = _analyze(pid=2)
    assert not any(mo["title"].startswith("Sin botas") for mo in a["moments"])


def test_jungler_dying_in_a_lost_fight_in_their_jungle_is_a_fight_not_a_lone_mistake():
    # 20:00: el jungla (pid 2) y su mid (pid 3) caen juntos en la jungla roja; el rival hace el barón al minuto.
    fight = [_kill(1_200_000, 7, 2, RED_JUNGLE_TOP, assists=[8]), _kill(1_204_000, 8, 3, (7100, 10950), assists=[7])]
    baron = _event(1_260_000, "ELITE_MONSTER_KILL", killerId=7, killerTeamId=200, monsterType="BARON_NASHOR",
                   position={"x": 5007, "y": 10471}, assistingParticipantIds=[8, 9], bounty=0)
    a = _analyze([*fight, baron], pid=2)
    death = next(d for d in a["deaths"] if d["t"] == 1200.0)
    assert {"enemy_jungle", "before_objective"} <= set(death["tags"]) and death["fight"]["allies"] == 2
    moment = next(mo for mo in a["moments"] if mo["t"] == 1200.0)
    assert moment["title"].startswith("Muerte en pelea")  # antes: «Muerte en la jungla rival», grave
    # 1 + 0,3 (objetivo tras una pelea perdida por varios: pasa casi igual en Esmeralda y en Aspirante); antes 2,5 = grave.
    assert death["severity"] == "low"


def test_jungle_references_are_never_better_than_challenger():
    """Lo que hacen los junglas Aspirante (medias de data/otps): una referencia de Esmeralda no puede exigir más."""
    challenger_lower_better = {"deaths_pre14": 1.9, "deaths_before_objective": 0.85, "time_dead_pct": 8.2, "obj_lost_dead": 0.35}
    for key, value in challenger_lower_better.items():
        assert BASE["JUNGLE"][key] >= value, key
    challenger_higher_better = {"cs_min": 7.1, "gold_min": 480, "impact_pre10": 7.0, "jungle_cs10": 71}
    for key, value in challenger_higher_better.items():
        assert BASE["JUNGLE"][key] < value, key


def test_jungle_references_match_what_emerald_otps_do():
    """240 partidas de OTPs Esmeralda (Hecarim): la base de jungla (≈ Esmeralda) queda cerca de su valor real."""
    emerald = {"deaths": 5.83, "deaths_pre14": 2.03, "time_dead_pct": 10.4, "kp": 51.9, "cs10": 68.8, "gold_min": 458, "impact_pre10": 6.6}
    for key, value in emerald.items():
        assert abs(BASE["JUNGLE"][key] - value) <= 0.15 * value, (key, BASE["JUNGLE"][key], value)


def test_jungle_metrics_that_do_not_change_with_elo_are_not_scaled():
    """Hecarim jungla, Esmeralda frente a Aspirante: muertes antes del 14, en la jungla rival, ganks antes del 10 y
    ganks rivales en la otra mitad no cambian con el elo; las muertes y el farm sí."""
    from app.coach.benchmarks import BASE, ELO_FLAT, static_refs

    diamond, silver = static_refs("JUNGLE", "DIAMOND"), static_refs("JUNGLE", "SILVER")
    for key in ELO_FLAT["JUNGLE"]:
        assert diamond[key] == silver[key] == BASE["JUNGLE"][key], key
    assert diamond["deaths"] < BASE["JUNGLE"]["deaths"] < silver["deaths"]  # estas sí se ajustan
    assert diamond["cs10"] > BASE["JUNGLE"]["cs10"] > silver["cs10"]
    assert static_refs("TOP", "DIAMOND")["deaths_pre14"] != BASE["TOP"]["deaths_pre14"]  # solo en jungla


def _champ_summary(svc, champ_name, tier, games, players, metrics):
    import json

    folder = svc.settings.data_dir / "otps" / champ_name.replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    block = {"games": games, "players": players, "metrics": {k: {"mean": v, "median": v, "n": games} for k, v in metrics.items()}}
    (folder / "resumen.json").write_text(json.dumps({"roles": {"JUNGLE": {"all": block, "by_tier": {tier: block}}}}), encoding="utf-8")


def test_farm_reference_is_your_champions_when_its_otps_have_enough_games(svc):
    """El farm esperado depende del campeón (Hecarim 8,8 CS/min frente a Lee Sin 7,1 en Aspirante): con OTPs
    suficientes de tu campeón en tu elo objetivo, su media manda en farm y primer objeto; el resto, la del rol."""
    from app.coach.benchmarks import References
    from app.coach.insights import game_refs
    from tests.factories import LEE_SIN, VIEGO

    _champ_summary(svc, "Lee Sin", "EMERALD", 150, 8, {"cs_min": 6.1, "cs10": 60.0, "deaths": 9.9, "first_item_min": 9.0})
    _champ_summary(svc, "Viego", "EMERALD", 60, 3, {"cs_min": 9.9})  # pocas partidas: no cuenta
    refs = References(svc, "EMERALD")
    role = refs.for_role("JUNGLE")
    lee = game_refs(refs, {"champ": LEE_SIN, "role": "JUNGLE"})
    assert lee["cs_min"] == 6.1 and lee["cs10"] == 60.0 and lee["first_item_min"] == 9.0
    assert lee["deaths"] == role["deaths"]  # solo farm y primer objeto
    assert game_refs(refs, {"champ": VIEGO, "role": "JUNGLE"}) == role
    assert refs.champion(LEE_SIN, "JUNGLE")["games"] == 150 and References(svc, "DIAMOND").champion(LEE_SIN, "JUNGLE") is None


def test_invading_is_only_judged_against_otps_of_your_champion(svc):
    """CS de la jungla rival antes del 10: Hecarim Esmeralda 8,5 frente a Aspirante 14,3; pero depende tanto del
    campeón que con una media general un jungla tanque saldría «débil» sin serlo: sin OTPs de tu campeón, no se juzga."""
    from app.coach.benchmarks import References
    from app.coach.insights import game_refs
    from tests.factories import LEE_SIN, VIEGO

    _champ_summary(svc, "Lee Sin", "EMERALD", 150, 8, {"invade_cs_pre10": 9.0})
    refs = References(svc, "EMERALD")
    assert "invade_cs_pre10" not in refs.for_role("JUNGLE")
    assert "invade_cs_pre10" not in game_refs(refs, {"champ": VIEGO, "role": "JUNGLE"})
    assert game_refs(refs, {"champ": LEE_SIN, "role": "JUNGLE"})["invade_cs_pre10"] == 9.0
