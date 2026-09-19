import datetime as dt

import pandas as pd
import pytest

from risk_mgmt.logsource import Trade
from risk_mgmt.monthly import asset_month_stats, cumulative_pnl_by_asset, month_summaries, overall_summary
from risk_mgmt.var import daily_pnl_series


def _trade(symbol: str, exit_day: dt.datetime, pnl: float, risk: float | None = None) -> Trade:
    entry = exit_day - dt.timedelta(hours=4)
    return Trade(
        symbol=symbol, direction="buy", entry_time=entry, entry_price=100.0,
        exit_time=exit_day, exit_price=100.0 + pnl, exit_reason="tp",
        lots=1.0, pnl_money=pnl, r_multiple=pnl / 10.0, risk_money=risk,
    )


JAN = lambda d, h=12: dt.datetime(2026, 1, d, h)  # noqa: E731
FEB = lambda d, h=12: dt.datetime(2026, 2, d, h)  # noqa: E731


def _sample_trades() -> list[Trade]:
    return [
        _trade("US500", JAN(2), 100.0),
        _trade("US500", JAN(5), -40.0),
        _trade("DE40", JAN(10), 20.0),
        _trade("US500", FEB(3), 30.0),
    ]


def test_asset_month_stats_groups_by_month_and_symbol():
    stats = asset_month_stats(_sample_trades())
    by_key = {(s.month, s.symbol): s for s in stats}

    us500_jan = by_key[("2026-01", "US500")]
    assert us500_jan.trades == 2
    assert us500_jan.pnl == pytest.approx(60.0)
    assert us500_jan.avg_pnl == pytest.approx(30.0)

    de40_jan = by_key[("2026-01", "DE40")]
    assert de40_jan.trades == 1
    assert de40_jan.pnl == pytest.approx(20.0)

    us500_feb = by_key[("2026-02", "US500")]
    assert us500_feb.trades == 1
    assert us500_feb.pnl == pytest.approx(30.0)


def test_asset_month_stats_empty_when_no_trades():
    assert asset_month_stats([]) == []


def test_month_summaries_basic_fields():
    trades = _sample_trades()
    daily_pnl = daily_pnl_series(trades)

    summaries = month_summaries(trades, daily_pnl, confidence=0.95)
    by_month = {s.month: s for s in summaries}

    jan = by_month["2026-01"]
    assert jan.trades == 3
    assert jan.win_rate == pytest.approx(2 / 3)  # 2 wins (100, 20) out of 3
    assert jan.total_pnl == pytest.approx(80.0)
    assert jan.avg_pnl == pytest.approx(80.0 / 3)
    assert jan.best_trade == pytest.approx(100.0)
    assert jan.worst_trade == pytest.approx(-40.0)

    feb = by_month["2026-02"]
    assert feb.trades == 1
    assert feb.total_pnl == pytest.approx(30.0)


def test_month_summaries_max_drawdown_from_exit_order():
    # Same day pnl doesn't matter here - exit order within the month does.
    # Sequence: +100, -40, +20 -> cumulative: 100, 60, 80 -> peak 100, trough 60 -> DD 40
    trades = [_trade("US500", JAN(2), 100.0), _trade("US500", JAN(5), -40.0), _trade("US500", JAN(10), 20.0)]
    daily_pnl = daily_pnl_series(trades)

    summary = month_summaries(trades, daily_pnl, confidence=0.95)[0]
    assert summary.max_drawdown == pytest.approx(40.0)


def test_month_summaries_var_is_none_with_fewer_than_two_days_of_history():
    # A single trade -> daily_pnl for that month has only 1 non-trivial day (plus
    # possibly reindexed zero days, but if the whole series is one day, var_month is None)
    trades = [_trade("US500", JAN(2), 50.0)]
    daily_pnl = daily_pnl_series(trades)

    summary = month_summaries(trades, daily_pnl, confidence=0.95)[0]
    assert summary.var_month is None


def test_month_summaries_var_is_populated_with_enough_daily_history():
    trades = [_trade("US500", JAN(d), -10.0 if d % 2 == 0 else 10.0) for d in range(1, 15)]
    daily_pnl = daily_pnl_series(trades)

    summary = month_summaries(trades, daily_pnl, confidence=0.95)[0]
    assert summary.var_month is not None
    assert summary.var_month >= 0.0


def test_month_summaries_empty_when_no_trades():
    assert month_summaries([], pd.Series(dtype=float), confidence=0.95) == []


def test_month_summaries_pnl_stdev_matches_sample_stdev():
    trades = [_trade("US500", JAN(2), 100.0), _trade("US500", JAN(5), -40.0), _trade("DE40", JAN(10), 20.0)]
    daily_pnl = daily_pnl_series(trades)

    summary = month_summaries(trades, daily_pnl, confidence=0.95)[0]
    assert summary.pnl_stdev == pytest.approx(pd.Series([100.0, -40.0, 20.0]).std(ddof=1))


def test_month_summaries_pnl_stdev_is_none_with_a_single_trade():
    trades = [_trade("US500", JAN(2), 50.0)]
    daily_pnl = daily_pnl_series(trades)

    summary = month_summaries(trades, daily_pnl, confidence=0.95)[0]
    assert summary.pnl_stdev is None


def test_month_summaries_avg_risk_money_averages_only_the_known_values():
    trades = [
        _trade("US500", JAN(2), 100.0, risk=200.0),
        _trade("US500", JAN(5), -40.0, risk=None),  # e.g. a live-mode trade, unknown risk
        _trade("DE40", JAN(10), 20.0, risk=100.0),
    ]
    daily_pnl = daily_pnl_series(trades)

    summary = month_summaries(trades, daily_pnl, confidence=0.95)[0]
    assert summary.avg_risk_money == pytest.approx(150.0)  # (200 + 100) / 2, the None excluded


def test_month_summaries_avg_risk_money_is_none_when_all_unknown():
    trades = [_trade("US500", JAN(2), 100.0, risk=None), _trade("US500", JAN(5), -40.0, risk=None)]
    daily_pnl = daily_pnl_series(trades)

    summary = month_summaries(trades, daily_pnl, confidence=0.95)[0]
    assert summary.avg_risk_money is None


def test_cumulative_pnl_by_asset_carries_forward_through_untraded_months():
    asset_stats = asset_month_stats(_sample_trades())
    cumulative = cumulative_pnl_by_asset(asset_stats, months=["2026-01", "2026-02"])

    # DE40 only traded in January (pnl 20.0) - February should carry that total forward.
    assert cumulative["DE40"]["2026-01"] == pytest.approx(20.0)
    assert cumulative["DE40"]["2026-02"] == pytest.approx(20.0)

    # US500: Jan 100 - 40 = 60, then +30 in Feb -> running total 90.
    assert cumulative["US500"]["2026-01"] == pytest.approx(60.0)
    assert cumulative["US500"]["2026-02"] == pytest.approx(90.0)


def test_overall_summary():
    summary = overall_summary(_sample_trades())

    assert summary.trades == 4
    assert summary.total_pnl == pytest.approx(110.0)  # 100 - 40 + 20 + 30
    assert summary.win_rate == pytest.approx(3 / 4)
    assert summary.date_from == dt.date(2026, 1, 2)
    assert summary.date_to == dt.date(2026, 2, 3)


def test_overall_summary_none_when_no_trades():
    assert overall_summary([]) is None
