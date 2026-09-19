import datetime as dt

import pytest

from risk_mgmt.logsource import Trade
from risk_mgmt.var import compute_var, daily_pnl_series, rolling_parametric_var


def _trade(day_offset: int, pnl: float) -> Trade:
    entry = dt.datetime(2026, 1, 1, 9, 0) + dt.timedelta(days=day_offset - 1)
    exit_ = entry + dt.timedelta(hours=6)
    return Trade(
        symbol="TEST", direction="buy", entry_time=entry, entry_price=100.0,
        exit_time=exit_, exit_price=100.0 + pnl, exit_reason="tp",
        lots=1.0, pnl_money=pnl, r_multiple=pnl / 10.0,
    )


def test_daily_pnl_series_sums_same_day_trades_and_fills_gaps_with_zero():
    trades = [_trade(1, 100.0), _trade(1, -20.0), _trade(3, 50.0)]  # day 2 has no trades

    series = daily_pnl_series(trades)

    assert series[pd_ts(1)] == pytest.approx(80.0)
    assert series[pd_ts(2)] == pytest.approx(0.0)
    assert series[pd_ts(3)] == pytest.approx(50.0)


def test_daily_pnl_series_empty_when_no_trades():
    assert daily_pnl_series([]).empty


def test_rolling_var_is_nan_before_the_window_is_full():
    trades = [_trade(d, 10.0) for d in range(1, 6)]  # only 5 days of history
    series = daily_pnl_series(trades)

    var = rolling_parametric_var(series, window_days=10, confidence=0.95)

    assert var.isna().all()


def test_rolling_var_matches_hand_computed_value_on_constant_pnl():
    # constant daily P/L -> std=0 -> VaR = -mean, clipped at 0
    trades = [_trade(d, 50.0) for d in range(1, 11)]
    series = daily_pnl_series(trades)

    var = rolling_parametric_var(series, window_days=10, confidence=0.95)

    assert var.dropna().iloc[-1] == pytest.approx(0.0)  # mean=50 > 0 -> VaR clipped to 0


def test_rolling_var_positive_when_losses_dominate_the_window():
    trades = [_trade(d, -80.0) for d in range(1, 11)]
    series = daily_pnl_series(trades)

    var = rolling_parametric_var(series, window_days=10, confidence=0.95)

    assert var.dropna().iloc[-1] == pytest.approx(80.0)  # std=0 -> VaR = -mean = 80


def test_compute_var_returns_both_windows():
    trades = [_trade(d, 10.0 if d % 2 == 0 else -10.0) for d in range(1, 61)]

    result = compute_var(trades, window_days=10, baseline_days=60, confidence=0.95)

    assert result.latest_short is not None
    assert result.latest_baseline is not None


def pd_ts(day: int):
    import pandas as pd
    return pd.Timestamp(2026, 1, day)
