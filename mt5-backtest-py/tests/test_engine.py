"""Engine behaviour against hand-built bars.

Every test here pins a decision that would otherwise be invisible in a 58,000-bar run:
where the fill lands, which of stop and target wins a bar that spans both, when the
holding clock starts, and whether the spread is charged once or twice.

The fixtures produce exactly one signal, on bar SIGNAL_BAR, so a test can name the
entry bar and the bars the position then runs into.
"""

import numpy as np
import pytest
from conftest import ENTRY_BAR, FAST, SIGNAL_BAR, down_then_spike, make_bars, steady, up_then_dip

from meanrev.costs import FREE, CostModel
from meanrev.engine import NOTEBOOK_01, ExecParams, run_backtest
from meanrev.exits import ExitRules


def test_no_exit_rule_is_rejected_at_construction():
    with pytest.raises(ValueError, match="no exit rule is active"):
        ExitRules()


def test_take_profit_without_stop_is_rejected():
    with pytest.raises(ValueError, match="atr_sl_mult is required"):
        ExitRules(atr_tp_r=2.0, time_bars=5)


def test_trailing_without_initial_stop_is_rejected():
    with pytest.raises(ValueError, match="needs an initial stop"):
        ExitRules(atr_trailing_mult=1.0, time_bars=5)


def test_the_fixture_produces_exactly_one_tradeable_buy():
    """Guards the fixtures themselves - a silently signal-free fixture would make every
    other test in this file pass vacuously.

    The tail can also fire a late counter-trend sell on the final bar, which has no bar
    left to fill on; what matters is that exactly one position is ever opened.
    """
    result = run_backtest(up_then_dip(steady(124.0, 6)), "T", FAST, ExitRules(time_bars=2))
    assert len(result.trades) == 1
    assert result.trades.iloc[0]["direction"] == "buy"
    assert result.trades.iloc[0]["entry_bar"] == ENTRY_BAR


def test_entry_fills_on_the_open_after_the_signal():
    df = up_then_dip(steady(124.0, 6))
    trade = run_backtest(df, "T", FAST, ExitRules(time_bars=2)).trades.iloc[0]

    assert trade["signal_time"] == df.loc[SIGNAL_BAR, "time"]
    assert trade["entry_price"] == df.loc[ENTRY_BAR, "open"]


def test_notebook_mode_fills_at_the_signal_bar_close():
    """Notebook 01 measured from the close of the bar that produced the signal, which
    assumes you can transact at a price you only know once the bar is over."""
    df = up_then_dip(steady(124.0, 6))
    trade = run_backtest(df, "T", FAST, ExitRules(time_bars=2), exec_params=NOTEBOOK_01).trades.iloc[0]

    assert trade["entry_bar"] == SIGNAL_BAR
    assert trade["entry_price"] == df.loc[SIGNAL_BAR, "close"]


def test_stop_wins_when_one_bar_spans_both_stop_and_target():
    """The conservative reading, and the engine must flag that it was needed."""
    wide = [(124.0, 124.1, 123.9, 124.0), (124.0, 200.0, 50.0, 180.0)] + steady(180.0, 3)
    result = run_backtest(up_then_dip(wide), "T", FAST, ExitRules(atr_sl_mult=1.0, atr_tp_r=1.0, time_bars=10))

    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "sl"
    assert trade["exit_price"] == pytest.approx(trade["sl_price"])
    assert result.ambiguous_bars == 1
    assert result.ambiguous_fraction == pytest.approx(1.0)


def test_target_alone_is_taken_when_the_stop_is_untouched():
    reaches_target = [(124.0, 124.1, 123.9, 124.0), (124.0, 200.0, 123.95, 180.0)] + steady(180.0, 3)
    result = run_backtest(
        up_then_dip(reaches_target), "T", FAST, ExitRules(atr_sl_mult=1.0, atr_tp_r=1.0, time_bars=10)
    )

    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "tp"
    assert trade["exit_price"] == pytest.approx(trade["tp_price"])
    assert result.ambiguous_bars == 0


def test_time_exit_counts_the_entry_bar_and_fills_next_open():
    """PARAMETROS.md: the position closes once exactly InpExitBars candles have closed.

    Entering at a bar's open makes that bar a full holding bar, so time_bars=2 decides
    on the bar after the entry bar and fills at the open following it - the EA closes at
    market on the new bar, not at the previous bar's close.
    """
    df = up_then_dip(steady(124.0, 8))
    trade = run_backtest(df, "T", FAST, ExitRules(time_bars=2)).trades.iloc[0]

    assert trade["bars_held"] == 2
    assert trade["exit_bar"] == ENTRY_BAR + 2
    assert trade["exit_price"] == df.loc[ENTRY_BAR + 2, "open"]


def test_notebook_mode_time_exit_is_close_to_close_over_n_bars():
    df = up_then_dip(steady(124.0, 8))
    trade = run_backtest(df, "T", FAST, ExitRules(time_bars=3), exec_params=NOTEBOOK_01).trades.iloc[0]

    assert trade["exit_bar"] == SIGNAL_BAR + 3
    assert trade["exit_price"] == df.loc[SIGNAL_BAR + 3, "close"]
    assert trade["gross_price"] == pytest.approx(
        df.loc[SIGNAL_BAR + 3, "close"] - df.loc[SIGNAL_BAR, "close"]
    )


def test_only_one_position_at_a_time_unless_overlap_is_allowed():
    rng = np.random.default_rng(7)
    noise = np.cumsum(rng.normal(scale=0.5, size=600)) + 100
    df = make_bars([(c, c + 0.5, c - 0.5, c) for c in noise])

    solo = run_backtest(df, "T", FAST, ExitRules(time_bars=8))
    overlapping = run_backtest(df, "T", FAST, ExitRules(time_bars=8), exec_params=ExecParams(allow_overlap=True))

    assert solo.signals_skipped_overlap > 0
    assert len(solo.trades) < len(overlapping.trades)
    # The defining property: no trade starts before the previous one has finished.
    entries = solo.trades["entry_bar"].to_numpy()
    exits = solo.trades["exit_bar"].to_numpy()
    assert (entries[1:] > exits[:-1]).all()


def test_spread_is_charged_once_per_round_turn():
    df = up_then_dip(steady(124.0, 6))
    df["spread"] = 20  # points

    free = run_backtest(df, "T", FAST, ExitRules(time_bars=2), costs=FREE)
    charged = run_backtest(df, "T", FAST, ExitRules(time_bars=2), costs=CostModel(), point=0.01)

    assert free.trades.iloc[0]["spread_cost_price"] == 0.0
    assert charged.trades.iloc[0]["spread_cost_price"] == pytest.approx(20 * 0.01)
    # The cost is a separate line, not baked into the fill - gross must be untouched.
    assert charged.trades.iloc[0]["gross_price"] == pytest.approx(free.trades.iloc[0]["gross_price"])
    assert charged.trades.iloc[0]["net_price"] == pytest.approx(
        free.trades.iloc[0]["gross_price"] - 20 * 0.01
    )


def test_slippage_is_charged_on_both_sides():
    df = up_then_dip(steady(124.0, 6))
    result = run_backtest(
        df, "T", FAST, ExitRules(time_bars=2), costs=CostModel(slippage_points=3), point=0.01
    )
    assert result.trades.iloc[0]["spread_cost_price"] == pytest.approx(2 * 3 * 0.01)


def test_fixed_spread_ignores_the_bar_column():
    df = up_then_dip(steady(124.0, 6))
    df["spread"] = 999
    result = run_backtest(
        df, "T", FAST, ExitRules(time_bars=2),
        costs=CostModel(use_bar_spread=False, fixed_spread_points=10), point=0.01,
    )
    assert result.trades.iloc[0]["spread_cost_price"] == pytest.approx(10 * 0.01)


def test_trailing_stop_only_tightens():
    """A stop that loosened would deepen a loss, so CTradeManager::ApplyTrailing forbids
    it - and the simulation has to forbid it too, or trailing looks free."""
    rally = [(124.0, 124.5, 123.5, 124.0)] + [(124.0 + i * 5, 126.0 + i * 5, 123.5 + i * 5, 125.5 + i * 5) for i in range(6)]
    rally += [(200.0, 200.0, 100.0, 100.0)]  # sharp reversal that takes out any stop

    trailed = run_backtest(up_then_dip(rally), "T", FAST, ExitRules(atr_sl_mult=3.0, atr_trailing_mult=1.0, time_bars=20))
    plain = run_backtest(up_then_dip(rally), "T", FAST, ExitRules(atr_sl_mult=3.0, time_bars=20))

    # Both stop out on the reversal, but the trailed stop had climbed with the rally and
    # therefore exits higher - never lower, which is the property under test.
    assert trailed.trades.iloc[0]["exit_reason"] == "sl"
    assert plain.trades.iloc[0]["exit_reason"] == "sl"
    assert trailed.trades.iloc[0]["exit_price"] > plain.trades.iloc[0]["exit_price"]


def test_no_trade_when_atr_is_still_warming_up():
    """An ATR stop needs an ATR; without one the engine declines rather than guessing."""
    result = run_backtest(
        up_then_dip(steady(124.0, 6)), "T", FAST, ExitRules(atr_sl_mult=2.0, time_bars=5),
        exec_params=ExecParams(atr_period=500),
    )
    assert result.signals_total >= 1
    assert result.trades.empty


def test_flat_market_produces_no_signals(flat_bars):
    result = run_backtest(flat_bars, "T", FAST, ExitRules(time_bars=3))
    assert result.signals_total == 0
    assert result.trades.empty
    assert result.summary().startswith("0 trades")


def test_mae_and_mfe_bracket_the_realized_move():
    swing = [(124.0, 140.0, 110.0, 130.0)] + steady(130.0, 4)
    trade = run_backtest(up_then_dip(swing), "T", FAST, ExitRules(time_bars=3)).trades.iloc[0]

    assert trade["mfe_price"] >= max(trade["gross_price"], 0)
    assert trade["mae_price"] <= min(trade["gross_price"], 0)


def test_short_trades_are_priced_in_the_right_direction():
    df = down_then_spike([(76.0, 76.1, 75.9, 76.0), (70.0, 70.5, 69.5, 70.0)] + steady(70.0, 4))
    trade = run_backtest(df, "T", FAST, ExitRules(time_bars=2)).trades.iloc[0]

    assert trade["direction"] == "sell"
    assert trade["exit_price"] < trade["entry_price"]
    assert trade["gross_price"] > 0  # price fell, so the short made money


def test_exit_rules_flag_whether_the_ea_can_run_them():
    assert not ExitRules(time_bars=6, atr_sl_mult=2.0, atr_tp_r=2.0).needs_ea_change
    assert ExitRules(rsi_revert_level=50.0).needs_ea_change
    assert "needs EA change" in ExitRules(sma_touch=True).describe()


def test_stop_is_checked_before_close_rules_within_the_same_bar():
    """Order matters: a bar that both breaks the stop and satisfies a close rule is a
    stop-out, because the broker acts intrabar and the EA only acts at the close."""
    stop_then_recover = [(124.0, 124.5, 50.0, 124.0)] + steady(124.0, 4)
    trade = run_backtest(
        up_then_dip(stop_then_recover), "T", FAST,
        ExitRules(atr_sl_mult=1.0, time_bars=1, first_profitable_close=True),
    ).trades.iloc[0]

    assert trade["exit_reason"] == "sl"


def test_excursions_stop_where_the_position_stopped_out():
    """A spike after the stop was never ours, and MFE must not claim it.

    Without this the exit study would see losers that "reached" a target they were
    stopped out before touching, and conclude a wider target banks them.
    """
    spike_after_stop = [(124.0, 400.0, 50.0, 390.0)] + steady(390.0, 3)
    result = run_backtest(
        up_then_dip(spike_after_stop), "T", FAST, ExitRules(atr_sl_mult=1.0, time_bars=10)
    )

    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "sl"
    assert trade["mfe_price"] == pytest.approx(0.0)  # not the bar's 400 high
    assert trade["mae_price"] == pytest.approx(-abs(trade["entry_price"] - trade["sl_price"]))


def test_target_exit_caps_the_favourable_excursion_at_the_target():
    overshoot = [(124.0, 400.0, 123.95, 390.0)] + steady(390.0, 3)
    result = run_backtest(
        up_then_dip(overshoot), "T", FAST, ExitRules(atr_sl_mult=1.0, atr_tp_r=1.0, time_bars=10)
    )

    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "tp"
    assert trade["mfe_price"] == pytest.approx(trade["tp_price"] - trade["entry_price"])
