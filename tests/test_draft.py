"""app/analytics/draft.py con una BD poblada de partidas sintéticas.

360 partidas deterministas con picks aleatorios por rol; el equipo con Darius
gana ~82 % de las veces y el resto es una moneda al aire. OP.GG desactivado:
el modelo solo usa la base de datos propia.
"""

from __future__ import annotations

import pytest

from app.analytics import draft
from app.analytics.filters import Filters
from app.riot.routing import ROLES
from tests.factories import (
    AATROX,
    AHRI,
    DARIUS,
    GAREN,
    JINX,
    KAISA,
    LEE_SIN,
    NAUTILUS,
    ORIANNA,
    SYNDRA,
    THRESH,
    VIEGO,
    WUKONG,
    ZED,
    EZREAL,
    LEONA,
    build_services,
    generate_draft_matches,
    make_match,
    store_payloads,
)

N_MATCHES = 360
F = Filters()
ALLY = {"TOP": DARIUS, "JUNGLE": LEE_SIN, "MID": AHRI, "ADC": JINX, "SUPPORT": THRESH}
ENEMY = {"TOP": AATROX, "JUNGLE": VIEGO, "MID": ZED, "ADC": KAISA, "SUPPORT": NAUTILUS}


@pytest.fixture(scope="module")
def draft_svc(tmp_path_factory):
    base = tmp_path_factory.mktemp("draft")
    with pytest.MonkeyPatch.context() as mp:
        svc = build_services(base, mp)
        results = store_payloads(svc, generate_draft_matches(N_MATCHES))
        assert results == ["stored"] * N_MATCHES
        yield svc
        svc.shutdown()
        svc.db.close()


def wp(svc, ally, enemy) -> float:
    return draft.analyze(svc, F, ally, enemy)["win_probability"]


# ------------------------------------------------------------------ analyze
def test_full_draft_analysis(draft_svc):
    res = draft.analyze(draft_svc, F, ALLY, ENEMY)
    p = res["win_probability"]
    assert 0 <= p <= 100
    lo, hi = res["interval"]
    assert 0 <= lo <= p <= hi <= 100
    assert res["lanes_filled"] == 5
    assert res["picks"] == {"ally": 5, "enemy": 5}
    assert [lane["role"] for lane in res["lanes"]] == list(ROLES)
    assert all(lane["kind"] == "matchup" and lane["games"] > 0 for lane in res["lanes"])
    assert len(res["synergies"]["ally"]) == 10 and len(res["synergies"]["enemy"]) == 10
    assert res["teams"]["ally"]["picks"] == 5
    assert res["confidence"]["level"] in (1, 2, 3)
    assert res["evidence"]["opgg"] is None  # sin fuentes externas en los tests
    assert res["composition"]["ally"]["source"] == "data"
    curve = res["duration_curve"]
    assert curve is not None and len(curve) == 4
    assert all(0 <= point["p"] <= 100 for point in curve)


def test_lane_counts_come_from_own_matchup_table(draft_svc):
    res = draft.analyze(draft_svc, F, {"TOP": DARIUS}, {"TOP": AATROX})
    lane = res["lanes"][0]
    row = draft_svc.agg.matchup(F, "TOP", DARIUS, AATROX)
    assert (lane["games"], lane["wins"]) == (row["games"], row["wins"])
    assert lane["sources"]["own"]["games"] == row["games"]
    assert lane["sources"]["opgg"] is None


@pytest.mark.parametrize(
    "ally, enemy",
    [
        (ALLY, ENEMY),
        ({"TOP": DARIUS}, {"TOP": AATROX}),
        ({"TOP": GAREN, "MID": SYNDRA}, {"JUNGLE": WUKONG, "ADC": EZREAL, "SUPPORT": LEONA}),
        ({"MID": ORIANNA}, {}),
        ({**ALLY, "TOP": GAREN}, {**ENEMY, "MID": SYNDRA}),
    ],
)
def test_swapping_sides_gives_the_complementary_probability(draft_svc, ally, enemy):
    p = wp(draft_svc, ally, enemy)
    q = wp(draft_svc, enemy, ally)
    assert p + q == pytest.approx(100.0, abs=0.11)


def test_stronger_champion_raises_win_probability(draft_svc):
    with_darius = wp(draft_svc, ALLY, ENEMY)
    with_garen = wp(draft_svc, {**ALLY, "TOP": GAREN}, ENEMY)
    assert with_darius > with_garen
    assert with_darius > 50
    # Y en el lado enemigo la baja.
    vs_darius = wp(draft_svc, {**ALLY, "TOP": AATROX}, {**ENEMY, "TOP": DARIUS})
    vs_garen = wp(draft_svc, {**ALLY, "TOP": AATROX}, {**ENEMY, "TOP": GAREN})
    assert vs_darius < vs_garen
    assert wp(draft_svc, {"TOP": DARIUS}, {"TOP": AATROX}) > 50 > wp(draft_svc, {"TOP": AATROX}, {"TOP": DARIUS})
    assert wp(draft_svc, {"TOP": DARIUS}, {}) > wp(draft_svc, {"TOP": GAREN}, {})


def test_lane_impact_has_the_sign_of_the_lane(draft_svc):
    res = draft.analyze(draft_svc, F, ALLY, ENEMY)
    top = res["lanes"][0]
    assert top["logit"] > 0 and top["impact_pp"] > 0
    res = draft.analyze(draft_svc, F, ENEMY, ALLY)
    assert res["lanes"][0]["impact_pp"] < 0


def test_partial_draft(draft_svc):
    res = draft.analyze(draft_svc, F, {"TOP": DARIUS, "MID": AHRI}, {"JUNGLE": VIEGO})
    kinds = {lane["role"]: lane["kind"] for lane in res["lanes"]}
    assert kinds == {"TOP": "solo", "JUNGLE": "solo", "MID": "solo", "ADC": "empty", "SUPPORT": "empty"}
    assert 0 <= res["win_probability"] <= 100
    assert res["lanes_filled"] == 0
    assert res["picks"] == {"ally": 2, "enemy": 1}
    assert len(res["synergies"]["ally"]) == 1 and res["synergies"]["enemy"] == []
    assert res["teams"]["enemy"]["picks"] == 1
    assert res["duration_curve"] is None  # con <4 campeones no hay curva


def test_partial_draft_with_one_matchup(draft_svc):
    res = draft.analyze(draft_svc, F, {"TOP": DARIUS, "ADC": JINX}, {"TOP": AATROX})
    assert res["lanes_filled"] == 1
    assert [lane["kind"] for lane in res["lanes"]] == ["matchup", "empty", "empty", "solo", "empty"]
    assert res["win_probability"] > 50


def test_empty_draft_is_a_coin_flip(draft_svc):
    res = draft.analyze(draft_svc, F, {}, {})
    assert res["win_probability"] == 50.0
    assert all(lane["kind"] == "empty" for lane in res["lanes"])
    assert res["teams"] == {"ally": None, "enemy": None}
    assert res["composition"] == {"ally": None, "enemy": None}


def test_unknown_roles_and_empty_slots_are_ignored(draft_svc):
    res = draft.analyze(draft_svc, F, {"TOP": DARIUS, "XYZ": AHRI, "MID": None, "ADC": 0}, {})
    assert res["picks"] == {"ally": 1, "enemy": 0}
    assert res["win_probability"] == wp(draft_svc, {"TOP": DARIUS}, {})


# ------------------------------------------------------------------ suggest
def test_suggest_excludes_picked_champions_and_is_sorted(draft_svc):
    ally = {"JUNGLE": LEE_SIN, "MID": AHRI}
    enemy = {"TOP": AATROX, "ADC": JINX}
    res = draft.suggest(draft_svc, F, ally, enemy, "TOP", min_games=0, min_lane_games=0)
    champs = [r["champ"] for r in res["best"]]
    assert champs == [DARIUS, GAREN]  # Aatrox ya está elegido; Darius es el fuerte
    assert set(champs).isdisjoint(set(ally.values()) | set(enemy.values()))
    scores = [r["score_pp"] for r in res["best"]]
    assert scores == sorted(scores, reverse=True)
    assert res["opponent"] == AATROX
    assert all(r["lane_games"] > 0 for r in res["best"])


def test_suggest_excludes_champions_picked_in_another_role(draft_svc):
    res = draft.suggest(draft_svc, F, {"MID": DARIUS}, {"TOP": AATROX}, "TOP", min_games=0, min_lane_games=0)
    assert [r["champ"] for r in res["best"]] == [GAREN]


def test_suggest_without_opponent_ranks_by_strength(draft_svc):
    res = draft.suggest(draft_svc, F, {}, {}, "TOP", min_games=0)
    assert res["opponent"] is None
    assert [r["champ"] for r in res["best"]][0] == DARIUS
    scores = [r["score_pp"] for r in res["best"]]
    assert scores == sorted(scores, reverse=True)


def test_suggest_for_the_enemy_side(draft_svc):
    res = draft.suggest(draft_svc, F, {"TOP": DARIUS}, {}, "TOP", side="enemy", min_games=0, min_lane_games=0)
    assert res["side"] == "enemy" and res["opponent"] == DARIUS
    assert {r["champ"] for r in res["best"]} == {AATROX, GAREN}
    assert all(r["lane_wr"] < 50 for r in res["best"])  # nadie gana a Darius


def test_suggest_min_lane_games_filters_rare_matchups(draft_svc):
    games_vs_aatrox = {
        champ: draft_svc.agg.matchup(F, "TOP", champ, AATROX)["games"] for champ in (DARIUS, GAREN)
    }
    threshold = max(games_vs_aatrox.values()) + 1
    res = draft.suggest(draft_svc, F, {}, {"TOP": AATROX}, "TOP", min_games=0, min_lane_games=threshold)
    assert res["best"] == [] and res["evaluated"] == 0
    res = draft.suggest(draft_svc, F, {}, {"TOP": AATROX}, "TOP", min_games=0, min_lane_games=min(games_vs_aatrox.values()))
    assert {r["champ"] for r in res["best"]} == {DARIUS, GAREN}


def test_suggest_limit_min_games_and_worst(draft_svc):
    res = draft.suggest(draft_svc, F, {}, {}, "MID", limit=2, min_games=0)
    assert res["evaluated"] == 4 and len(res["best"]) == 2
    assert res["worst"] and res["worst"][0]["score_pp"] <= res["best"][-1]["score_pp"]
    assert draft.suggest(draft_svc, F, {}, {}, "MID", min_games=10**6)["evaluated"] == 0


# ----------------------------------------------------------------- backtest
def test_backtest_calibrates_alpha(draft_svc):
    try:
        report = draft.backtest(draft_svc, sample=3000, save=True)
        assert report["status"] == "ok"
        assert report["matches"] == N_MATCHES  # todas tienen los 5 roles
        assert 0.2 <= report["alpha"] <= 2.0
        assert draft.get_alpha(draft_svc) == pytest.approx(report["alpha"])
        assert draft.last_backtest(draft_svc)["alpha"] == report["alpha"]
        # α es el máximo de verosimilitud: no puede empeorar el log-loss de α = 1.
        assert report["calibrated"]["log_loss"] <= report["raw"]["log_loss"] + 1e-4
        # Hay señal real (Darius): el modelo supera a predecir siempre el mismo lado.
        assert report["calibrated"]["accuracy"] > report["baseline"]["accuracy"]
        assert sum(b["n"] for b in report["calibration"]) == report["matches"]
    finally:
        draft_svc.db.meta_set("draft_alpha", "1.0")  # no contaminar otros tests


def test_backtest_needs_200_full_role_aggregated_matches(svc):
    full = generate_draft_matches(150, first_id=7_200_000_000)
    partial = [make_match(f"EUW1_{7_300_000_000 + i}", positions={2: "TOP"}) for i in range(60)]
    normals = [make_match(f"EUW1_{7_400_000_000 + i}", queue=400) for i in range(60)]
    store_payloads(svc, full + partial + normals)
    report = draft.backtest(svc, save=False)
    assert report == {"status": "insufficient", "matches": 150, "needed": 200}
