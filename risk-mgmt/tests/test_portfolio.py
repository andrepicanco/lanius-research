import datetime as dt

import pytest

from risk_mgmt.logsource import Trade
from risk_mgmt.portfolio import (
    compute_portfolio,
    concurrency,
    daily_pnl_by_strategy,
    detect_sizing_mode,
    equity_curve,
    max_drawdown,
)


def make_trade(strategy, day, pnl, *, symbol="EURUSD", lots=0.1, hours=4):
    entry = dt.datetime(2026, 1, day, 9, 0)
    return Trade(
        symbol=symbol,
        direction="buy",
        entry_time=entry,
        entry_price=1.0,
        exit_time=entry + dt.timedelta(hours=hours),
        exit_price=1.0,
        exit_reason="time",
        lots=lots,
        pnl_money=pnl,
        r_multiple=0.0,
        strategy=strategy,
    )


def test_daily_pnl_fills_flat_days_with_zero_not_nan():
    trades = [make_trade("A", 1, 100.0), make_trade("A", 5, -40.0)]

    frame = daily_pnl_by_strategy(trades)

    assert list(frame.columns) == ["A"]
    assert len(frame) == 5  # Jan 1 through Jan 5, gap days included
    assert frame["A"].iloc[1] == 0.0
    assert frame["A"].sum() == pytest.approx(60.0)


def test_each_strategy_gets_its_own_column():
    trades = [make_trade("A", 1, 100.0), make_trade("B", 1, -25.0)]

    frame = daily_pnl_by_strategy(trades)

    assert sorted(frame.columns) == ["A", "B"]
    assert frame.sum(axis=1).iloc[0] == pytest.approx(75.0)


def test_max_drawdown_measures_peak_to_trough():
    equity = equity_curve(
        daily_pnl_by_strategy([make_trade("A", d, p) for d, p in [(1, 100.0), (2, -300.0), (3, 50.0)]]).sum(axis=1),
        1000.0,
    )

    worst, worst_pct = max_drawdown(equity)

    assert worst == pytest.approx(300.0)          # 1100 peak down to 800
    assert worst_pct == pytest.approx(300 / 1100 * 100)


def test_drawdown_counts_a_loss_taken_on_the_very_first_trade():
    """Without a starting-balance baseline the running peak would begin below water and
    this would report 0.0 - the case that made the standalone figures wrong."""
    equity = equity_curve(
        daily_pnl_by_strategy([make_trade("A", 1, -100.0), make_trade("A", 2, 100.0)]).sum(axis=1),
        1000.0,
    )

    assert max_drawdown(equity, 1000.0)[0] == pytest.approx(100.0)


def test_sizing_mode_is_inferred_from_the_volumes_actually_used():
    constant = [make_trade("A", 1, 10.0, lots=0.5), make_trade("A", 2, 10.0, lots=0.5)]
    varying = [make_trade("A", 1, 10.0, lots=0.5), make_trade("A", 2, 10.0, lots=0.8)]

    assert detect_sizing_mode(constant) == "fixed"
    assert detect_sizing_mode(varying) == "variable"


def test_sizing_mode_compares_within_a_symbol_not_across_them():
    # two symbols, each traded at its own constant size, is still fixed-lot sizing
    trades = [make_trade("A", 1, 10.0, symbol="EURUSD", lots=0.5),
              make_trade("A", 2, 10.0, symbol="ASXAUD", lots=1.5)]

    assert detect_sizing_mode(trades) == "fixed"


def test_concurrency_counts_cross_strategy_overlap():
    # A holds Jan 1 09:00-17:00; B opens at 13:00, inside that window
    a = make_trade("A", 1, 10.0, hours=8)
    b = make_trade("B", 1, 10.0, hours=2)
    b.entry_time = dt.datetime(2026, 1, 1, 13, 0)
    b.exit_time = dt.datetime(2026, 1, 1, 15, 0)

    stats = concurrency([a, b])

    assert stats.max_concurrent == 2
    assert stats.overlap_by_pair == {("A", "B"): 1}
    assert stats.pct_time_multi == pytest.approx(2 / 8 * 100)


def test_non_overlapping_strategies_report_no_interaction():
    a = make_trade("A", 1, 10.0, hours=2)
    b = make_trade("B", 3, 10.0, hours=2)

    stats = concurrency([a, b])

    assert stats.max_concurrent == 1
    assert stats.overlap_by_pair == {}
    assert stats.pct_time_multi == 0.0


def test_portfolio_merges_strategies_onto_one_balance():
    trades = [
        make_trade("A", 1, 100.0),
        make_trade("B", 1, -50.0),
        make_trade("A", 2, 25.0),
    ]

    result = compute_portfolio(trades, initial_balance=10_000.0)

    assert result.total_pnl == pytest.approx(75.0)
    assert result.final_balance == pytest.approx(10_075.0)
    assert [s.strategy for s in result.by_strategy] == ["A", "B"]
    assert next(s for s in result.by_strategy if s.strategy == "A").trades == 2


def test_diversification_benefit_is_positive_when_losses_offset():
    """A and B lose on opposite days, so each one's own 100 drawdown is partly absorbed
    by the other being up at the time. What survives on the combined curve is only the
    dip between the two same-day closes, not the full 200 the parts suffered apart."""
    trades = [
        make_trade("A", 1, -100.0), make_trade("B", 1, 100.0),
        make_trade("A", 2, 100.0), make_trade("B", 2, -100.0),
    ]

    result = compute_portfolio(trades, initial_balance=10_000.0)

    assert sum(s.max_drawdown for s in result.by_strategy) == pytest.approx(200.0)
    assert result.max_drawdown == pytest.approx(100.0)
    assert result.diversification_benefit == pytest.approx(100.0)
    assert result.strategy_correlation.loc["A", "B"] == pytest.approx(-1.0)


def test_single_strategy_drawdown_matches_its_standalone_figure():
    """With only one strategy there is nothing to diversify, so the combined drawdown
    must equal the standalone one - the property that lets a single-strategy run be
    reconciled against the Tester's own balance-drawdown field."""
    trades = [make_trade("A", d, p) for d, p in [(1, 50.0), (2, -120.0), (3, -30.0), (4, 200.0)]]

    result = compute_portfolio(trades, initial_balance=10_000.0)

    assert result.max_drawdown == pytest.approx(result.by_strategy[0].max_drawdown)
    assert result.diversification_benefit == pytest.approx(0.0)


def test_same_day_closes_are_not_netted_away_in_drawdown():
    """Two trades closing the same day: a daily-netted curve would show a 20 dip, but
    the account really went 100 down before recovering."""
    losing = make_trade("A", 1, -100.0, hours=2)
    winning = make_trade("A", 1, 80.0, hours=6)

    result = compute_portfolio([losing, winning], initial_balance=1_000.0)

    assert result.max_drawdown == pytest.approx(100.0)


def test_empty_input_yields_no_portfolio():
    assert compute_portfolio([], initial_balance=10_000.0) is None
