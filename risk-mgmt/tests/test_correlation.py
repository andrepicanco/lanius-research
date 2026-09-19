import numpy as np
import pandas as pd
import pytest

from risk_mgmt.correlation import (
    compute_correlation,
    daily_returns,
    pc1_explained_variance,
    quarterly_pc1_series,
    top_correlated_pairs,
)


def _closes(n_days: int = 30) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n_days, freq="D")
    a = pd.Series(range(100, 100 + n_days), index=dates, dtype=float)
    b = a * 2  # proportional scaling -> pct_change identical to A -> corr exactly +1
    c = pd.Series([100.0 - (i % 2) for i in range(n_days)], index=dates)  # alternating, ~uncorrelated

    # D's own daily returns are exactly the negative of A's - an affine price
    # transform (e.g. 200 - A) does NOT give exact -1 correlation on returns (percent
    # change isn't invariant under an affine shift, only under proportional scaling),
    # so build D directly from -A's returns to get an exact -1 in return space.
    a_returns = a.pct_change().fillna(0.0)
    d = pd.Series(index=dates, dtype=float)
    d.iloc[0] = 100.0
    for i in range(1, len(dates)):
        d.iloc[i] = d.iloc[i - 1] * (1 - a_returns.iloc[i])

    return pd.DataFrame({"A": a, "B": b, "C": c, "D": d})


def test_perfectly_correlated_series_score_close_to_one():
    result = compute_correlation(_closes(), window_days=10, top_n=3)
    assert result.rolling.loc["A", "B"] == pytest.approx(1.0, abs=1e-6)


def test_top_pairs_lists_each_pair_once_excluding_self_pairs():
    result = compute_correlation(_closes(), window_days=10, top_n=10)
    pair_keys = {frozenset((p.symbol_a, p.symbol_b)) for p in result.top_pairs}

    assert len(pair_keys) == len(result.top_pairs)  # no duplicates
    assert all(p.symbol_a != p.symbol_b for p in result.top_pairs)
    assert frozenset(("A", "B")) in pair_keys


def test_top_n_is_respected():
    result = compute_correlation(_closes(), window_days=10, top_n=1)
    assert len(result.top_pairs) == 1
    assert result.top_pairs[0].rolling_corr == pytest.approx(1.0, abs=1e-6)  # A/B is the strongest pair


def test_daily_returns_drops_the_first_row():
    closes = _closes(n_days=5)
    returns = daily_returns(closes)
    assert len(returns) == len(closes) - 1


def test_bottom_pairs_ranks_most_negative_first():
    result = compute_correlation(_closes(), window_days=10, top_n=2)

    assert len(result.bottom_pairs) == 2
    worst = result.bottom_pairs[0]
    assert {worst.symbol_a, worst.symbol_b} == {"A", "D"}
    assert worst.rolling_corr == pytest.approx(-1.0, abs=1e-6)
    # ascending order: first entry must be <= second
    assert result.bottom_pairs[0].rolling_corr <= result.bottom_pairs[1].rolling_corr


def test_bottom_pairs_are_disjoint_ranking_from_top_pairs():
    result = compute_correlation(_closes(), window_days=10, top_n=1)
    assert result.top_pairs[0].rolling_corr > result.bottom_pairs[0].rolling_corr


# --- PC1 / PCA -----------------------------------------------------------------------


def test_pc1_is_one_when_two_assets_are_perfectly_correlated():
    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    returns = pd.DataFrame({"A": np.linspace(0.001, 0.02, 20), "B": np.linspace(0.001, 0.02, 20)}, index=dates)

    ratio = pc1_explained_variance(returns)
    assert ratio == pytest.approx(1.0, abs=1e-6)


def test_pc1_is_close_to_one_over_n_when_assets_are_independent():
    rng = np.random.default_rng(42)
    dates = pd.date_range("2026-01-01", periods=500, freq="D")
    returns = pd.DataFrame(rng.normal(0, 1, size=(500, 4)), columns=["A", "B", "C", "D"], index=dates)

    ratio = pc1_explained_variance(returns)
    assert ratio == pytest.approx(0.25, abs=0.05)  # 1/n floor for 4 independent assets


def test_pc1_is_none_with_fewer_than_two_assets():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    returns = pd.DataFrame({"A": np.linspace(0.001, 0.01, 10)}, index=dates)
    assert pc1_explained_variance(returns) is None


def test_pc1_is_none_with_fewer_rows_than_columns():
    dates = pd.date_range("2026-01-01", periods=2, freq="D")
    returns = pd.DataFrame({"A": [0.01, 0.02], "B": [0.02, 0.01], "C": [0.01, 0.03]}, index=dates)
    assert pc1_explained_variance(returns) is None


def test_quarterly_pc1_series_groups_by_calendar_quarter():
    dates = pd.date_range("2026-01-01", periods=200, freq="D")  # spans Q1, Q2, Q3
    a = pd.Series(np.linspace(100, 150, 200), index=dates)
    b = a * 1.5
    closes = pd.DataFrame({"A": a, "B": b})

    series = quarterly_pc1_series(closes)
    quarters = [q for q, _ in series]

    assert quarters == sorted(quarters)  # chronological
    assert all(q.startswith("2026-Q") for q in quarters)
    assert len(set(quarters)) == len(quarters)  # each quarter appears once
    # A and B are perfectly correlated in every quarter
    for _, ratio in series:
        assert ratio == pytest.approx(1.0, abs=1e-6)


def test_quarterly_pc1_series_empty_when_no_price_data():
    assert quarterly_pc1_series(pd.DataFrame()) == []
