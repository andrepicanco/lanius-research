import numpy as np
import pandas as pd
import pytest

from meanrev.export import RISK_MGMT_COLUMNS, write_trades_csv
from meanrev.metrics import (
    Sizing,
    compute_stats,
    equity_curve,
    excursion_table,
    max_drawdown,
    to_money,
)


def ledger(net_prices, risk_price=1.0, direction="buy", bars_held=3):
    """A minimal priced-in-points ledger, enough to exercise the money layer."""
    n = len(net_prices)
    return pd.DataFrame(
        {
            "symbol": ["T"] * n,
            "direction": [direction] * n,
            "entry_time": pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"),
            "exit_time": pd.date_range("2024-01-02", periods=n, freq="D", tz="UTC"),
            "entry_price": [100.0] * n,
            "exit_price": [100.0 + p for p in net_prices],
            "exit_reason": ["time"] * n,
            "bars_held": [bars_held] * n,
            "gross_price": list(net_prices),
            "spread_cost_price": [0.0] * n,
            "net_price": list(net_prices),
            "gross_pct": [p for p in net_prices],
            "net_pct": [p for p in net_prices],
            "mae_price": [min(p, 0.0) - 0.5 for p in net_prices],
            "mfe_price": [max(p, 0.0) + 0.5 for p in net_prices],
            "risk_price": [risk_price] * n,
        }
    )


def test_zero_value_per_point_is_rejected_by_name():
    """The ASXAUD failure mode from 2026-09-19: MT5 returns 0 when it cannot convert to
    the account currency, and multiplying it through reports $0.00 for every trade while
    the trade count stays right - which reads as a break-even strategy, not a bug."""
    with pytest.raises(ValueError, match="value_per_point must be > 0"):
        to_money(ledger([1.0]), value_per_point=0.0, point=0.01)


def test_fixed_sizing_prices_every_trade_at_the_same_lots():
    priced = to_money(ledger([2.0, -1.0]), value_per_point=10.0, point=0.01, sizing=Sizing(lots=0.5))

    assert (priced["lots"] == 0.5).all()
    assert priced["pnl_money"].tolist() == pytest.approx([2.0 * 10 * 0.5, -1.0 * 10 * 0.5])


def test_risk_pct_sizing_risks_the_same_money_on_every_trade():
    trades = ledger([2.0, -1.0], risk_price=2.0)
    priced = to_money(
        trades, value_per_point=10.0, point=0.01,
        sizing=Sizing(mode="risk_pct", risk_pct=1.0, initial_balance=10_000.0),
    )
    risked = priced["risk_price"] * 10.0 * priced["lots"]
    assert risked.tolist() == pytest.approx([100.0, 100.0])  # 1% of 10k


def test_risk_pct_sizing_refuses_a_ledger_without_stop_distances():
    trades = ledger([1.0])
    trades["risk_price"] = np.nan
    with pytest.raises(ValueError, match="risk_pct sizing needs a stop distance"):
        to_money(trades, value_per_point=10.0, point=0.01, sizing=Sizing(mode="risk_pct"))


def test_commission_and_swap_reduce_net_but_not_gross():
    priced = to_money(
        ledger([2.0]), value_per_point=10.0, point=0.01, sizing=Sizing(lots=1.0),
        commission_per_lot=3.0, swap_long_per_lot=-1.0,
    )
    row = priced.iloc[0]
    assert row["pnl_gross"] == pytest.approx(20.0)
    assert row["commission"] == pytest.approx(3.0)
    assert row["swap"] == pytest.approx(-1.0)  # one night held
    assert row["pnl_money"] == pytest.approx(20.0 - 3.0 - 1.0)


def test_swap_uses_the_short_rate_for_sells():
    priced = to_money(
        ledger([1.0], direction="sell"), value_per_point=10.0, point=0.01,
        sizing=Sizing(lots=1.0), swap_long_per_lot=-5.0, swap_short_per_lot=+2.0,
    )
    assert priced.iloc[0]["swap"] == pytest.approx(2.0)


def test_r_multiple_is_pnl_over_the_money_actually_risked():
    priced = to_money(ledger([4.0], risk_price=2.0), value_per_point=10.0, point=0.01, sizing=Sizing(lots=1.0))
    assert priced.iloc[0]["r_multiple"] == pytest.approx(2.0)


def test_max_drawdown_counts_a_loss_on_the_very_first_trade():
    """Without the starting balance as a baseline the running peak begins after trade
    one, so an account that loses immediately reports no drawdown at all."""
    equity = equity_curve(pd.Series([-100.0, 50.0]), 1000.0)
    assert max_drawdown(equity, start=1000.0)[0] == pytest.approx(100.0)
    assert max_drawdown(equity, start=None)[0] == pytest.approx(0.0)


def test_max_drawdown_measures_peak_to_trough():
    equity = equity_curve(pd.Series([100.0, -300.0, 50.0]), 1000.0)
    worst, worst_pct = max_drawdown(equity, start=1000.0)
    assert worst == pytest.approx(300.0)
    assert worst_pct == pytest.approx(300.0 / 1100.0 * 100)


def test_t_stat_and_signal_to_noise_differ_by_sqrt_n():
    stats = compute_stats(ledger([1.0, -0.5, 0.8, -0.3, 1.2, -0.1] * 20))
    assert stats.t_stat == pytest.approx(stats.signal_to_noise * np.sqrt(stats.trades))


def test_stats_on_an_empty_ledger_do_not_raise():
    stats = compute_stats(ledger([]))
    assert stats.trades == 0
    assert stats.total_pct == 0.0


def test_stats_pick_up_money_columns_when_present():
    priced = to_money(ledger([2.0, -3.0, 1.0]), value_per_point=10.0, point=0.01, sizing=Sizing(lots=1.0))
    stats = compute_stats(priced, initial_balance=1000.0)

    assert stats.total_money == pytest.approx(0.0)
    assert stats.max_drawdown_money == pytest.approx(30.0)


def test_excursion_table_splits_winners_from_losers():
    table = excursion_table(ledger([2.0, -1.0, 3.0, -0.5]), point=0.01)
    assert set(table.index) == {"winner", "loser"}
    assert table.loc["winner", "trades"] == 2
    assert "mae_points_q50" in table.columns


def test_export_refuses_an_unpriced_ledger():
    with pytest.raises(ValueError, match="not priced yet"):
        write_trades_csv(ledger([1.0]), "ignored")


def test_export_writes_the_columns_risk_mgmt_reads(tmp_path):
    priced = to_money(ledger([2.0, -1.0]), value_per_point=10.0, point=0.01, sizing=Sizing(lots=1.0))
    path = write_trades_csv(priced, tmp_path, symbol="AUDCHF")

    assert path.name == "AUDCHF_trades.csv"
    written = pd.read_csv(path, parse_dates=["entry_time", "exit_time"])
    assert list(written.columns) == RISK_MGMT_COLUMNS
    # risk-mgmt compares these against naive datetimes from other sources.
    assert written["entry_time"].dt.tz is None


def test_export_maps_research_only_exits_onto_the_risk_mgmt_vocabulary():
    """risk-mgmt types exit_reason as tp | sl | time | unknown; rules it has never heard
    of collapse to `time`, since they are all the strategy closing its own position."""
    priced = to_money(ledger([1.0] * 3), value_per_point=10.0, point=0.01, sizing=Sizing(lots=1.0))
    priced["exit_reason"] = ["rsi_revert", "sma_touch", "end_of_data"]

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        written = pd.read_csv(write_trades_csv(priced, tmp, symbol="X"))
    assert written["exit_reason"].tolist() == ["time", "time", "unknown"]
