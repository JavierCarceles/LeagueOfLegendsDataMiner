"""app/analytics/stats.py: Wilson, shrink, log5, logit/sigmoid y varianza posterior."""

from __future__ import annotations

import math

import pytest

from app.analytics import stats

PS = [0.0001, 0.01, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 0.99, 0.9999]


# -------------------------------------------------------------------- Wilson
def test_wilson_without_games_is_the_whole_interval():
    assert stats.wilson(0, 0) == (0.0, 1.0)


@pytest.mark.parametrize("wins, games", [(0, 1), (1, 1), (3, 10), (50, 100), (99, 100), (0, 500), (500, 500), (523, 1000)])
def test_wilson_bounds_contain_the_observed_rate(wins, games):
    lo, hi = stats.wilson(wins, games)
    eps = 1e-12  # con 0 victorias centre - radius da ~1e-17 por redondeo
    assert 0.0 <= lo <= wins / games + eps
    assert wins / games - eps <= hi <= 1.0


def test_wilson_known_value():
    lo, hi = stats.wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-4)
    assert hi == pytest.approx(0.5962, abs=1e-4)


def test_wilson_is_symmetric():
    lo, hi = stats.wilson(30, 100)
    lo2, hi2 = stats.wilson(70, 100)
    assert lo == pytest.approx(1 - hi2)
    assert hi == pytest.approx(1 - lo2)


def test_wilson_narrows_with_more_games():
    widths = [hi - lo for lo, hi in (stats.wilson(n // 2, n) for n in (10, 100, 1_000, 10_000))]
    assert widths == sorted(widths, reverse=True)
    assert widths[-1] < 0.02


def test_wilson_does_not_treat_3_0_as_certain():
    lo, hi = stats.wilson(3, 3)
    assert hi == pytest.approx(1.0)
    assert 0.3 < lo < 0.5
    lo, hi = stats.wilson(0, 3)
    assert lo == pytest.approx(0.0, abs=1e-12) and hi < 0.7


# -------------------------------------------------------------------- shrink
def test_shrink_without_games_returns_prior():
    assert stats.shrink(0, 0, 0.53, 100) == 0.53
    assert stats.shrink(0, 0, 0.4, 0) == 0.4


def test_shrink_without_prior_weight_is_raw_rate():
    assert stats.shrink(3, 4, 0.5, 0) == 0.75


def test_shrink_formula():
    assert stats.shrink(3, 3, 0.5, 3) == pytest.approx(0.75)  # (3 + 3·0.5) / (3 + 3)
    assert stats.shrink(10, 30, 0.6, 70) == pytest.approx((10 + 42) / 100)


@pytest.mark.parametrize("wins, games", [(0, 5), (5, 5), (12, 20), (70, 100), (1, 1000)])
def test_shrink_lies_between_raw_rate_and_prior(wins, games):
    raw = wins / games
    value = stats.shrink(wins, games, 0.5, 50)
    assert min(raw, 0.5) <= value <= max(raw, 0.5)


def test_shrink_converges_to_raw_rate_with_lots_of_games():
    assert stats.shrink(60_000, 100_000, 0.5, 100) == pytest.approx(0.6, abs=1e-3)


# ---------------------------------------------------------------------- log5
@pytest.mark.parametrize("p", PS)
@pytest.mark.parametrize("q", PS)
def test_log5_is_complementary(p, q):
    assert stats.log5(p, q) + stats.log5(q, p) == pytest.approx(1.0)


@pytest.mark.parametrize("p", PS + [0.0, 1.0])
def test_log5_against_an_average_opponent_is_own_rate(p):
    assert stats.log5(p, 0.5) == pytest.approx(p)


@pytest.mark.parametrize("p", PS)
def test_log5_between_equals_is_a_coin_flip(p):
    assert stats.log5(p, p) == pytest.approx(0.5)


def test_log5_known_value():
    # Ejemplo del docstring: 52 % contra 48 % -> ~54 %.
    assert stats.log5(0.52, 0.48) == pytest.approx(0.54, abs=0.001)


def test_log5_is_monotonic_in_own_strength():
    values = [stats.log5(p, 0.6) for p in PS]
    assert values == sorted(values)


def test_log5_degenerate_inputs_do_not_divide_by_zero():
    assert stats.log5(0.0, 0.0) == 0.5
    assert stats.log5(1.0, 1.0) == 0.5
    assert stats.log5(1.0, 0.0) == 1.0


# ------------------------------------------------------------ logit / sigmoid
@pytest.mark.parametrize("p", PS)
def test_sigmoid_inverts_logit(p):
    assert stats.sigmoid(stats.logit(p)) == pytest.approx(p, rel=1e-9)


@pytest.mark.parametrize("x", [-12.0, -3.5, -0.2, 0.0, 0.7, 4.0, 12.0])
def test_logit_inverts_sigmoid(x):
    assert stats.logit(stats.sigmoid(x)) == pytest.approx(x, abs=1e-8)


def test_sigmoid_properties():
    assert stats.sigmoid(0.0) == 0.5
    for x in (0.3, 2.0, 7.5):
        assert stats.sigmoid(-x) == pytest.approx(1 - stats.sigmoid(x))
    # Estable numéricamente: no hay OverflowError en los extremos.
    assert stats.sigmoid(800) == 1.0
    assert stats.sigmoid(-800) == 0.0


def test_logit_is_clamped_at_the_extremes():
    assert stats.logit(0.5) == 0.0
    assert math.isfinite(stats.logit(0.0)) and stats.logit(0.0) < -10
    assert math.isfinite(stats.logit(1.0)) and stats.logit(1.0) > 10
    assert stats.logit(0.0) == pytest.approx(-stats.logit(1.0))


# ------------------------------------------------------ posterior_logit_var
@pytest.mark.parametrize(
    "wins, games, prior, k",
    [(0, 0, 0.5, 400), (3, 3, 0.5, 10), (0, 10, 0.4, 50), (55, 100, 0.5, 250), (5_000, 9_000, 0.6, 100), (10, 10, 0.99, 1)],
)
def test_posterior_logit_var_is_positive(wins, games, prior, k):
    value = stats.posterior_logit_var(wins, games, prior, k)
    assert value > 0 and math.isfinite(value)


def test_posterior_logit_var_shrinks_with_more_data():
    values = [stats.posterior_logit_var(n // 2, n, 0.5, 100) for n in (0, 10, 100, 1_000, 10_000)]
    assert all(a > b for a, b in zip(values, values[1:]))


def test_posterior_logit_var_matches_beta_delta_method():
    wins, games, prior, k = 30, 50, 0.5, 20
    a, b = wins + k * prior, games - wins + k * (1 - prior)
    s = a + b
    var_p = a * b / (s * s * (s + 1))
    mean = a / s
    assert stats.posterior_logit_var(wins, games, prior, k) == pytest.approx(var_p / (mean * (1 - mean)) ** 2)


def test_posterior_logit_var_degenerate_returns_one():
    assert stats.posterior_logit_var(0, 0, 0.5, 0) == 1.0


# ----------------------------------------------------------------- helpers
@pytest.mark.parametrize(
    "games, level", [(0, 0), (1, 1), (19, 1), (20, 2), (59, 2), (60, 3), (199, 3), (200, 4), (599, 4), (600, 5), (10**6, 5)]
)
def test_sample_confidence_levels(games, level):
    assert stats.sample_confidence(games)["level"] == level


def test_small_helpers():
    assert stats.safe_div(1, 0) is None
    assert stats.safe_div(1, 0, 7) == 7
    assert stats.safe_div(3, 4) == 0.75
    assert stats.clamp(5, 0, 1) == 1 and stats.clamp(-5, 0, 1) == 0
    assert stats.pct(0.12345) == 12.3 and stats.pct(None) is None
    assert stats.round_or_none(None) is None and stats.round_or_none(1.26, 1) == 1.3
