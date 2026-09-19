import datetime as dt
from pathlib import Path

import pytest

from risk_mgmt.logsource.mql5_journal import MQL5JournalSource
from risk_mgmt.symbol_spec import SymbolSpec

FIXTURES = Path(__file__).parent / "fixtures"
SPEC = {"ES35": SymbolSpec(symbol="ES35", tick_value=1.0, tick_size=0.5)}
NO_LIVE_FALLBACK = lambda symbol: None  # noqa: E731 - forces the "not found" branch deterministically


def test_parses_the_one_completed_trade_in_the_sample_log():
    trades = MQL5JournalSource.from_directory(FIXTURES, SPEC, pattern="sample_journal.log").load_trades()

    assert len(trades) == 1
    trade = trades[0]
    assert trade.symbol == "ES35"
    assert trade.direction == "sell"
    assert trade.entry_time == dt.datetime(2026, 1, 12, 9, 19, 36)
    assert trade.entry_price == pytest.approx(17567.48)
    assert trade.exit_time == dt.datetime(2026, 1, 12, 10, 41, 37)
    assert trade.exit_price == pytest.approx(17408.42)
    assert trade.exit_reason == "tp"
    assert trade.lots == pytest.approx(0.2)


def test_pnl_uses_the_symbol_spec_tick_value_and_size():
    trade = MQL5JournalSource.from_directory(FIXTURES, SPEC, pattern="sample_journal.log").load_trades()[0]

    profit_points = trade.entry_price - trade.exit_price  # sell trade, profit when price falls
    expected_pnl = (profit_points / SPEC["ES35"].tick_size) * SPEC["ES35"].tick_value * trade.lots
    assert trade.pnl_money == pytest.approx(expected_pnl)


def test_r_multiple_uses_the_original_sl_from_the_placement_line_not_the_trailed_one():
    # The fixture's ticket #4 was placed with SL=17657.00 (original), then trailed to
    # 17644.74 before the TP hit ("position modified" line) - r_multiple must be based
    # on the original 17657.00 risk distance, not the trailed one.
    trade = MQL5JournalSource.from_directory(FIXTURES, SPEC, pattern="sample_journal.log").load_trades()[0]

    original_sl = 17657.00
    risk_distance = abs(trade.entry_price - original_sl)
    profit_points = trade.entry_price - trade.exit_price
    assert trade.r_multiple == pytest.approx(profit_points / risk_distance)


def test_risk_money_uses_the_original_sl_priced_via_the_symbol_spec():
    # Same original SL (17657.00) as the r_multiple test above, but priced to $ via the
    # SymbolSpec instead of expressed as a multiple of the realized profit.
    trade = MQL5JournalSource.from_directory(FIXTURES, SPEC, pattern="sample_journal.log").load_trades()[0]

    original_sl = 17657.00
    risk_distance = abs(trade.entry_price - original_sl)
    expected_risk_money = (risk_distance / SPEC["ES35"].tick_size) * SPEC["ES35"].tick_value * trade.lots
    assert trade.risk_money == pytest.approx(expected_risk_money)


def test_expired_and_still_pending_orders_produce_no_trade():
    # The fixture also has tickets #2, #3, #6 (expired, never filled) and #7 (still
    # pending at end of log) - none of these should show up as trades.
    trades = MQL5JournalSource.from_directory(FIXTURES, SPEC, pattern="sample_journal.log").load_trades()
    assert len(trades) == 1  # only ticket #4 actually filled and closed


def test_raises_on_missing_symbol_spec_with_no_live_fallback_available():
    with pytest.raises(KeyError, match="fetched live from MT5"):
        MQL5JournalSource.from_directory(
            FIXTURES, {}, pattern="sample_journal.log", spec_fetcher=NO_LIVE_FALLBACK
        ).load_trades()


def test_raises_when_no_files_match():
    with pytest.raises(FileNotFoundError):
        MQL5JournalSource.from_directory(FIXTURES, SPEC, pattern="*.does_not_exist")


# --- real .log file format (UTF-16, different column layout) -----------------------

AUS200_SPEC = {"AUS200": SymbolSpec(symbol="AUS200", tick_value=1.0, tick_size=0.01)}


def test_parses_the_real_utf16_log_format_and_a_genuine_stop_loss_exit():
    trades = MQL5JournalSource.from_directory(
        FIXTURES, AUS200_SPEC, pattern="sample_journal_real_format.log"
    ).load_trades()

    assert len(trades) == 1
    trade = trades[0]
    assert trade.symbol == "AUS200"
    assert trade.direction == "buy"
    assert trade.entry_time == dt.datetime(2026, 1, 2, 3, 44, 59)
    assert trade.entry_price == pytest.approx(8730.00)
    assert trade.exit_time == dt.datetime(2026, 1, 2, 8, 13, 5)
    assert trade.exit_price == pytest.approx(8715.30)
    assert trade.exit_reason == "sl"

    original_sl = 8691.60
    risk_distance = abs(trade.entry_price - original_sl)
    profit_points = trade.exit_price - trade.entry_price  # buy trade
    assert trade.r_multiple == pytest.approx(profit_points / risk_distance)


def test_missing_spec_falls_back_to_the_injected_fetcher():
    # spec_fetcher is injected here instead of hitting a real MT5 connection - this is
    # what the CLI wires to a real live lookup by default (see MQL5JournalSource docstring).
    fetcher_calls = []

    def fake_fetcher(symbol):
        fetcher_calls.append(symbol)
        return SymbolSpec(symbol=symbol, tick_value=1.0, tick_size=0.01)

    trades = MQL5JournalSource.from_directory(
        FIXTURES, {}, pattern="sample_journal_real_format.log", spec_fetcher=fake_fetcher
    ).load_trades()

    assert fetcher_calls == ["AUS200"]
    assert len(trades) == 1


def test_missing_spec_raises_a_clear_error_when_the_fetcher_also_fails():
    with pytest.raises(KeyError, match="fetched live from MT5"):
        MQL5JournalSource.from_directory(
            FIXTURES, {}, pattern="sample_journal_real_format.log", spec_fetcher=NO_LIVE_FALLBACK
        ).load_trades()


# --- EA-initiated closes (MeanRev1's time-based exit) ---------------------------------
#
# Regression: the parser used to discover exits ONLY from the terminal's "take profit /
# stop loss triggered" lines, so a position the EA closed itself was never paired and
# vanished. On a real MeanRev1 run that dropped 569 of 657 trades and left a residue of
# 70 stop losses against 18 take profits, flipping a profitable backtest into a loss.

TIME_EXIT_LOG = """\
2026.01.05 19:00:00   [MeanRev1][F40EUR][INFO] HandleIdleState: opened SELL @ 8412.05000 SL=8500.00000 TP=8200.00000 lots=0.20 ticket=2
2026.01.05 19:00:00   deal #2 sell 0.2 F40EUR at 8412.05 done (based on order #2)
2026.01.06 11:00:00   deal #3 buy 0.2 F40EUR at 8380.00 done (based on order #3)
2026.01.07 09:00:00   [MeanRev1][F40EUR][INFO] HandleIdleState: opened BUY @ 8300.00000 SL=8250.00000 TP=8400.00000 lots=0.20 ticket=4
2026.01.07 09:00:00   deal #4 buy 0.2 F40EUR at 8300.00 done (based on order #4)
2026.01.07 15:58:28   stop loss triggered #4 buy 0.2 F40EUR 8300.00 sl: 8250.00 tp: 8400.00 [#5 sell 0.2 F40EUR at 8250.00]
2026.01.07 15:58:28   deal #5 sell 0.2 F40EUR at 8250.00 done (based on order #5)
"""

F40_SPEC = {"F40EUR": SymbolSpec(symbol="F40EUR", tick_value=1.0, tick_size=1.0)}


@pytest.fixture
def time_exit_log(tmp_path):
    (tmp_path / "journal.log").write_text(TIME_EXIT_LOG, encoding="utf-8")
    return tmp_path


def test_close_without_a_triggered_line_is_still_paired(time_exit_log):
    trades = MQL5JournalSource.from_directory(time_exit_log, F40_SPEC, spec_fetcher=NO_LIVE_FALLBACK).load_trades()

    assert len(trades) == 2  # not 1: the EA-closed trade must not be dropped
    assert [t.exit_reason for t in trades] == ["time", "sl"]


def test_ea_closed_trade_keeps_its_real_fill_prices(time_exit_log):
    time_exit = MQL5JournalSource.from_directory(
        time_exit_log, F40_SPEC, spec_fetcher=NO_LIVE_FALLBACK
    ).load_trades()[0]

    assert time_exit.direction == "sell"
    assert time_exit.entry_price == pytest.approx(8412.05)
    assert time_exit.exit_price == pytest.approx(8380.00)
    assert time_exit.exit_time == dt.datetime(2026, 1, 6, 11, 0)
    assert time_exit.pnl_money > 0  # sold at 8412.05, bought back at 8380.00


def test_meanrev1_entry_wording_still_yields_the_original_sl(time_exit_log):
    time_exit = MQL5JournalSource.from_directory(
        time_exit_log, F40_SPEC, spec_fetcher=NO_LIVE_FALLBACK
    ).load_trades()[0]

    assert time_exit.risk_money == pytest.approx(abs(8412.05 - 8500.00) * 0.2)
    assert time_exit.r_multiple == pytest.approx((8412.05 - 8380.00) / abs(8412.05 - 8500.00))


# --- several backtests concatenated into one log -------------------------------------
#
# Regression: each Strategy Tester run restarts order/ticket numbering at 2, so a log
# holding a whole basket has ticket 2 once per symbol. Keying the SL map by bare ticket
# handed a EURUSD trade at 1.17 the stop loss of an ASXAUD trade at 8778, producing an
# "avg risk at entry" in the tens of millions. Compounding it, MeanRev1 enters at market
# and the terminal records the deal BEFORE the EA logs its SL, so resolving the SL while
# reading the deal found nothing at all for the first run in the file.

MULTI_RUN_LOG = """\
2026.01.05 19:00:00   deal #2 sell 1.5 ASXAUD at 8739.6 done (based on order #2)
2026.01.05 19:00:00   [MeanRev1][ASXAUD][INFO] HandleIdleState: opened SELL @ 8739.60000 SL=8778.20000 TP=8623.80000 lots=1.50 ticket=2
2026.01.06 03:00:00   deal #3 buy 1.5 ASXAUD at 8718.6 done (based on order #3)
2026.02.05 19:00:00   deal #2 buy 0.2 EURUSD at 1.17148 done (based on order #2)
2026.02.05 19:00:00   [MeanRev1][EURUSD][INFO] HandleIdleState: opened BUY @ 1.17148000 SL=1.16648000 TP=1.18148000 lots=0.20 ticket=2
2026.02.06 03:00:00   deal #3 sell 0.2 EURUSD at 1.17500 done (based on order #3)
"""

MULTI_SPEC = {
    "ASXAUD": SymbolSpec(symbol="ASXAUD", tick_value=0.1, tick_size=0.1),
    "EURUSD": SymbolSpec(symbol="EURUSD", tick_value=1.0, tick_size=0.00001),
}


@pytest.fixture
def multi_run_log(tmp_path):
    (tmp_path / "20260919.log").write_text(MULTI_RUN_LOG, encoding="utf-8")
    return tmp_path


def _multi_run_trades(directory):
    loaded = MQL5JournalSource.from_directory(
        directory, MULTI_SPEC, spec_fetcher=NO_LIVE_FALLBACK
    ).load_trades()
    return {t.symbol: t for t in loaded}


def test_each_run_keeps_its_own_stop_loss(multi_run_log):
    trades = _multi_run_trades(multi_run_log)

    assert trades["ASXAUD"].risk_money is not None
    # the bug: EURUSD's ticket 2 picked up ASXAUD's 8778.20 stop
    assert abs(trades["EURUSD"].entry_price - 1.16648) < 0.01


def test_stop_loss_is_found_even_though_the_deal_line_comes_first(multi_run_log):
    """The first run in the file is the one that used to come back with no SL at all."""
    trades = _multi_run_trades(multi_run_log)

    risk_points = abs(8739.60 - 8778.20)
    expected = (risk_points / 0.1) * 0.1 * 1.5
    assert trades["ASXAUD"].risk_money == pytest.approx(expected)


def test_risk_money_stays_within_a_plausible_order_of_magnitude(multi_run_log):
    """The symptom that surfaced this: 'Avg risk at entry' in the tens of millions on a
    10,000 account. Both trades risk a fraction of a percent of price."""
    trades = _multi_run_trades(multi_run_log)

    eurusd = trades["EURUSD"]
    expected = (abs(1.17148 - 1.16648) / 0.00001) * 1.0 * 0.2
    assert eurusd.risk_money == pytest.approx(expected)
    assert eurusd.risk_money < 1_000
