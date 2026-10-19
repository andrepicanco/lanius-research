import numpy as np
import pandas as pd
import pytest

from meanrev.indicators import (
    atr,
    hurst_exponent,
    ou_half_life,
    rsi_wilder,
    sma,
    true_range,
    variance_ratio,
    zone_streak,
)


def test_sma_is_trailing_and_leaves_warmup_nan():
    result = sma(pd.Series([1.0, 2.0, 3.0, 4.0]), 3)
    assert result.isna().tolist() == [True, True, False, False]
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[3] == pytest.approx(3.0)


def test_rsi_wilder_matches_hand_computation():
    """Wilder recursion at period 2: avg = avg_prev + (x - avg_prev)/2.

    Seeded at the FIRST available delta, not at zero - that is what
    `ewm(adjust=False)` does when the leading value is NaN, and notebook 01 relied on
    it. See the seeding note in indicators.rsi_wilder.
    """
    close = pd.Series([10.0, 11.0, 12.0, 11.0])
    deltas = close.diff().tolist()[1:]  # +1, +1, -1

    gains, losses = [max(deltas[0], 0)], [max(-deltas[0], 0)]
    for delta in deltas[1:]:
        gains.append(gains[-1] + (max(delta, 0) - gains[-1]) / 2)
        losses.append(losses[-1] + (max(-delta, 0) - losses[-1]) / 2)

    expected = 100 - 100 / (1 + gains[-1] / losses[-1])
    assert rsi_wilder(close, 2).iloc[-1] == pytest.approx(expected)


def test_rsi_all_gains_saturates_at_100():
    assert rsi_wilder(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 2).iloc[-1] == pytest.approx(100.0)


def test_rsi_wilder_differs_from_simple_average_at_period_2():
    """The distinction notebook 01 called out - and the reason the EA's iRSI matches."""
    close = pd.Series([10.0, 11.0, 10.5, 12.0, 11.0, 13.0])
    delta = close.diff()
    simple_rs = delta.clip(lower=0).rolling(2).mean() / (-delta.clip(upper=0)).rolling(2).mean()
    simple_rsi = 100 - 100 / (1 + simple_rs)
    assert rsi_wilder(close, 2).iloc[-1] != pytest.approx(simple_rsi.iloc[-1])


def test_true_range_uses_the_widest_of_the_three_spans():
    high = pd.Series([10.0, 12.0])
    low = pd.Series([9.0, 11.5])
    close = pd.Series([9.5, 12.0])
    # Second bar gapped up: high - prev_close (12 - 9.5) beats high - low (0.5).
    assert true_range(high, low, close).iloc[1] == pytest.approx(2.5)


def test_atr_converges_to_the_constant_true_range():
    """On a steady ramp every bar has the same true range, so ATR settles on it.

    Here the ramp is gentle enough (step ~0.05) that the bar's own high-low span of 1.0
    is the widest of the three candidates, so ATR converges to 1.0. The gap-dominated
    case is covered by test_true_range_uses_the_widest_of_the_three_spans.
    """
    bars = 200
    close = pd.Series(np.linspace(10, 20, bars))
    high, low = close + 0.5, close - 0.5

    result = atr(high, low, close, 14)
    assert (result.dropna() > 0).all()
    assert result.iloc[-1] == pytest.approx(1.0, abs=1e-6)


def test_zone_streak_counts_runs_and_resets():
    flag = pd.Series([0, 1, 1, 1, 0, 1, 0]).astype(bool)
    assert zone_streak(flag).tolist() == [0, 1, 2, 3, 0, 1, 0]


def test_zone_streak_counts_leading_run():
    assert zone_streak(pd.Series([True, True, False])).tolist() == [1, 2, 0]


def test_ou_half_life_short_for_mean_reverting_series():
    rng = np.random.default_rng(0)
    level, series = 0.0, []
    for _ in range(2000):
        level = 0.5 * level + rng.normal(scale=0.01)  # strong pull back to 0
        series.append(np.exp(level))
    assert ou_half_life(pd.Series(series)) < 3


def test_ou_half_life_infinite_for_trending_series():
    assert ou_half_life(pd.Series(np.exp(np.linspace(0, 1, 500)))) == float("inf")


def test_hurst_below_half_for_mean_reversion_above_for_momentum():
    """Persistence, not drift, is what lifts H above 0.5.

    A random walk with a constant drift still has H = 0.5: the drift adds no variance
    to the lagged differences. Trending here therefore means positively autocorrelated
    increments.
    """
    rng = np.random.default_rng(1)

    level, reverting = 0.0, []
    for _ in range(3000):
        level = 0.2 * level + rng.normal(scale=0.01)
        reverting.append(np.exp(level))

    increment, momentum = 0.0, []
    for _ in range(3000):
        increment = 0.8 * increment + rng.normal(scale=0.01)
        momentum.append(increment)
    momentum = np.exp(np.cumsum(momentum))

    assert hurst_exponent(pd.Series(reverting)) < 0.5
    assert hurst_exponent(pd.Series(momentum)) > 0.5


def test_variance_ratio_near_one_for_random_walk():
    rng = np.random.default_rng(2)
    walk = np.exp(np.cumsum(rng.normal(scale=0.01, size=20000)))
    assert variance_ratio(pd.Series(walk), lag=5) == pytest.approx(1.0, abs=0.1)


def test_point_size_reads_the_instruments_digits():
    from meanrev.data import point_size

    assert point_size(pd.Series([1.23456, 1.23457])) == pytest.approx(1e-5)
    assert point_size(pd.Series([15234.25, 15234.50])) == pytest.approx(1e-2)


def test_point_size_caps_float_noise_instead_of_returning_1e15():
    """A computed series carries 15 decimals; uncapped this silently zeroed every
    spread charge in the permutation test."""
    from meanrev.data import MAX_DIGITS, point_size

    noisy = pd.Series(np.exp(np.cumsum(np.full(100, 0.001))))
    assert point_size(noisy) == pytest.approx(10.0 ** -MAX_DIGITS)
