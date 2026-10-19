import numpy as np
import pandas as pd
import pytest
from conftest import make_bars

from meanrev.costs import FREE
from meanrev.exits import ExitRules
from meanrev.signals import SignalParams
from meanrev.validation import (
    SearchBudget,
    neighbour_count,
    parameter_surface,
    permutation_test,
    plateau_score,
    shuffle_bars,
    walk_forward,
)


def walk(bars=3000, seed=5, freq="30min"):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(scale=0.002, size=bars)))
    return make_bars(
        [(c, c * 1.002, c * 0.998, c) for c in close],
        freq=freq,
        spread=2,
    )


def test_parameter_surface_has_a_cell_per_combination():
    surface = parameter_surface(walk(), "T", streaks=[1, 2], exit_bars=[3, 5, 7])
    assert surface.shape == (2, 3)
    assert list(surface.index) == [1, 2]
    assert list(surface.columns) == [3, 5, 7]


def test_search_budget_counts_every_evaluation():
    budget = SearchBudget()
    parameter_surface(walk(), "T", streaks=[1, 2], exit_bars=[3, 5, 7], budget=budget)

    assert budget.evaluations == 6
    assert "streak=2 exit=7" in budget.labels[-1]
    assert str(budget) == "6 combinations evaluated"


def test_plateau_score_penalises_an_isolated_spike():
    """The whole point: a lone peak surrounded by bad neighbours should not win."""
    surface = pd.DataFrame(
        [[0.0, 0.0, 0.0], [0.0, 90.0, 0.0], [0.0, 0.0, 0.0]],
        index=[1, 2, 3], columns=[3, 5, 7],
    )
    smoothed = plateau_score(surface)
    assert smoothed.loc[2, 5] == pytest.approx(10.0)  # 90 spread over 9 cells
    assert smoothed.loc[2, 5] < surface.loc[2, 5]


def test_plateau_score_keeps_a_broad_shoulder():
    surface = pd.DataFrame(np.full((3, 3), 10.0), index=[1, 2, 3], columns=[3, 5, 7])
    assert plateau_score(surface).loc[2, 5] == pytest.approx(10.0)


def test_plateau_score_does_not_wrap_the_grid_around():
    """np.roll would average the last row onto the first; a corner must only see the
    three cells actually adjacent to it."""
    surface = pd.DataFrame(np.arange(9.0).reshape(3, 3), index=[1, 2, 3], columns=[3, 5, 7])
    smoothed = plateau_score(surface)

    assert smoothed.iloc[0, 0] == pytest.approx(np.mean([0, 1, 3, 4]))
    assert smoothed.iloc[1, 1] == pytest.approx(np.mean(np.arange(9.0)))
    assert smoothed.notna().all().all()


def test_neighbour_count_flags_cells_judged_on_less_evidence():
    surface = pd.DataFrame(np.arange(9.0).reshape(3, 3), index=[1, 2, 3], columns=[3, 5, 7])
    counts = neighbour_count(surface)

    assert counts.iloc[1, 1] == 9   # interior
    assert counts.iloc[0, 0] == 4   # corner
    assert counts.iloc[0, 1] == 6   # edge


def test_walk_forward_never_tests_on_a_month_it_trained_on():
    folds, oos = walk_forward(
        walk(bars=8000), "T", streaks=[1, 2], exit_bars=[3, 5],
        train_months=2, test_months=1,
    )
    assert not folds.empty
    for row in folds.itertuples():
        assert row.train_to < row.test_from
    assert set(oos["fold"]) == set(folds["fold"])


def test_walk_forward_records_what_each_fold_chose():
    folds, _ = walk_forward(
        walk(bars=8000), "T", streaks=[1, 2, 3], exit_bars=[3, 5],
        train_months=2, test_months=1,
    )
    assert folds["chosen_streak"].isin([1, 2, 3]).all()
    assert folds["chosen_exit_bars"].isin([3, 5]).all()
    # If every fold picks the same cell the surface is stable; if not, that instability
    # is itself the finding. Either way the column has to be populated.
    assert folds["chosen_streak"].notna().all()


def test_shuffle_bars_preserves_the_calendar_and_the_bar_distribution():
    df = walk()
    surrogate = shuffle_bars(df, np.random.default_rng(0))

    assert surrogate["time"].equals(df["time"])
    assert surrogate["spread"].equals(df["spread"])
    assert len(surrogate) == len(df)
    # Same overall drift: the returns were permuted, not replaced.
    assert np.log(surrogate["close"].iloc[-1] / surrogate["close"].iloc[0]) == pytest.approx(
        np.log(df["close"].iloc[-1] / df["close"].iloc[0])
    )


def test_shuffle_bars_keeps_high_and_low_bracketing_open_and_close():
    surrogate = shuffle_bars(walk(), np.random.default_rng(3))
    assert (surrogate["high"] >= surrogate[["open", "close"]].max(axis=1)).all()
    assert (surrogate["low"] <= surrogate[["open", "close"]].min(axis=1)).all()


def test_shuffle_bars_destroys_serial_structure():
    """A surrogate must break the autocorrelation the strategy trades on, or the null is
    not a null."""
    rng = np.random.default_rng(4)
    level, prices = 0.0, []
    for _ in range(4000):
        level = 0.3 * level + rng.normal(scale=0.01)
        prices.append(100 * np.exp(level))
    df = make_bars([(p, p * 1.001, p * 0.999, p) for p in prices], spread=2)

    real = np.log(df["close"]).diff().dropna()
    surrogate = np.log(shuffle_bars(df, rng)["close"]).diff().dropna()

    assert abs(real.autocorr(lag=1)) > 0.3
    assert abs(surrogate.autocorr(lag=1)) < 0.1


def test_permutation_test_reports_a_plausible_p_value_on_a_random_walk():
    """On data with no exploitable structure the real result should sit inside the null
    distribution, not out in its tail."""
    result = permutation_test(
        walk(bars=2000), "T", SignalParams(streak=1), ExitRules(time_bars=5),
        costs=FREE, n_permutations=25, seed=1,
    )
    assert 0.0 < result["p_value"] <= 1.0
    assert result["n_permutations"] == 25
    assert len(result["null"]) == 25
    assert result["p_value"] > 0.02  # a random walk should not look significant


def test_shuffle_bars_keeps_the_instruments_price_precision():
    """Regression: the surrogate used to carry 15 decimals of float noise.

    Anything inferring precision from the prices - data.point_size above all - then read
    a point of ~1e-15, charged no spread, and turned the null into a distribution of
    GROSS returns being compared against a NET result.
    """
    from meanrev.data import point_size

    df = walk()
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].round(5)
    surrogate = shuffle_bars(df, np.random.default_rng(0))

    assert point_size(surrogate["close"]) == point_size(df["close"])


def test_surrogate_trades_pay_the_same_spread_as_real_ones():
    """The property the p-value depends on: like-for-like costs on both sides."""
    from meanrev.costs import CostModel
    from meanrev.engine import ExecParams, run_backtest

    df = walk()
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].round(5)
    point = 1e-5
    args = (SignalParams(streak=1), ExitRules(time_bars=5), CostModel(), ExecParams())

    real = run_backtest(df, "T", *args, point=point)
    surrogate = run_backtest(shuffle_bars(df, np.random.default_rng(0)), "T", *args, point=point)

    assert (real.trades["spread_cost_price"] > 0).all()
    assert (surrogate.trades["spread_cost_price"] > 0).all()
    assert surrogate.trades["spread_cost_price"].mean() == pytest.approx(
        real.trades["spread_cost_price"].mean(), rel=0.2
    )


def test_permutation_test_charges_costs_on_the_null_too():
    """End to end: with costs on, the null must not look like a gross distribution."""
    from meanrev.costs import CostModel
    from meanrev.engine import ExecParams

    df = walk(bars=2000)
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].round(5)

    gross = permutation_test(
        df, "T", SignalParams(streak=1), ExitRules(time_bars=5),
        costs=FREE, exec_params=ExecParams(), n_permutations=12, seed=1,
    )
    net = permutation_test(
        df, "T", SignalParams(streak=1), ExitRules(time_bars=5),
        costs=CostModel(), exec_params=ExecParams(), n_permutations=12, seed=1,
    )
    assert net["null_mean"] < gross["null_mean"]
    assert net["actual"] < gross["actual"]


def test_permutation_test_scores_gross_and_net_from_the_same_surrogates():
    """The two columns must come from one sweep, or they are not comparable."""
    from meanrev.costs import CostModel
    from meanrev.engine import ExecParams
    from meanrev.validation import permutation_summary

    df = walk(bars=2000)
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].round(5)

    result = permutation_test(
        df, "T", SignalParams(streak=1), ExitRules(time_bars=5),
        costs=CostModel(), exec_params=ExecParams(), n_permutations=10, seed=2,
    )

    assert set(result["by_column"]) == {"gross_pct", "net_pct"}
    gross, net = result["by_column"]["gross_pct"], result["by_column"]["net_pct"]
    assert net["actual"] < gross["actual"]          # costs were charged on the real run
    assert net["null_mean"] < gross["null_mean"]    # ...and on the null

    table = permutation_summary(result)
    assert list(table.index) == ["gross_pct", "net_pct"]
    assert "z" in table.columns
