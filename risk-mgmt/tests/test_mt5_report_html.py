"""The fixture below mirrors a real MetaTrader 5 Strategy Tester report byte for byte in
the ways that matter: UTF-16 with a BOM, Portuguese column headers, `10 000.00` number
formatting, and the Deals/Orders two-table layout. Captured from a
FivePercentOnline-Real (Build 6200) terminal running MeanRev1 on ASXAUD M30.
"""

import datetime as dt

import pytest

from risk_mgmt.logsource.mt5_report_html import Mt5ReportHtmlSource, _num


def _row(*cells: str) -> str:
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


HEADER = (
    "<html><body><table>"
    + _row("Relatório do Testador de Estratégia")
    + _row("Expert Advisor (Robô):", "MeanRev1")
    + _row("Ativo:", "ASXAUD")
    + _row("Parâmetros de entrada:", "InpSMAShortPeriod=21")
    + _row("", "InpTradeComment=MeanRev1")
    + _row("", "InpMagicNumber=369369")
)

ORDERS = (
    _row("Horário da Abertura", "Ordem", "Ativo", "Tipo", "Volume", "Preço",
         "S / L", "T / P", "Horário", "Estado", "Comentário")
    + _row("2026.01.05 19:00:00", "2", "ASXAUD", "sell", "1.5 / 1.5", "0.0",
           "8778.2", "8623.8", "2026.01.05 19:00:00", "filled", "MeanRev1")
    + _row("2026.01.06 03:00:00", "3", "ASXAUD", "buy", "1.5 / 1.5", "0.0",
           "", "", "2026.01.06 03:00:00", "filled", "")
    + _row("2026.01.06 04:00:00", "4", "ASXAUD", "buy", "1.5 / 1.5", "0.0",
           "8634.9", "8847.0", "2026.01.06 04:00:00", "filled", "MeanRev1")
    + _row("2026.01.06 10:30:00", "5", "ASXAUD", "sell", "1.5 / 1.5", "0.0",
           "", "", "2026.01.06 10:30:00", "filled", "")
)

DEALS = (
    _row("Horário", "Oferta", "Ativo", "Tipo", "Direção", "Volume", "Preço",
         "Ordem", "Comissão", "Swap", "Lucro", "Saldo", "Comentário")
    + _row("2026.01.01 00:00:00", "1", "", "balance", "", "", "", "",
           "0.00", "0.00", "10 000.00", "10 000.00", "")
    + _row("2026.01.05 19:00:00", "2", "ASXAUD", "sell", "in", "1.5", "8739.6", "2",
           "0.00", "0.00", "0.00", "10 000.00", "MeanRev1")
    + _row("2026.01.06 03:00:00", "3", "ASXAUD", "buy", "out", "1.5", "8718.6", "3",
           "0.00", "-0.32", "21.14", "10 020.82", "")
    + _row("2026.01.06 04:00:00", "4", "ASXAUD", "buy", "in", "1.5", "8687.9", "4",
           "0.00", "0.00", "0.00", "10 020.82", "MeanRev1")
    + _row("2026.01.06 10:30:00", "5", "ASXAUD", "sell", "out", "1.5", "8634.9", "5",
           "-1.50", "0.00", "-53.35", "9 965.97", "sl 8634.9")
    + "</table></body></html>"
)

REPORT = HEADER + ORDERS + DEALS


@pytest.fixture
def report_dir(tmp_path):
    (tmp_path / "ReportTester-1.html").write_text(REPORT, encoding="utf-16")
    return tmp_path


def test_parses_both_trades_with_fifo_pairing(report_dir):
    trades = Mt5ReportHtmlSource.from_directory(report_dir).load_trades()

    assert len(trades) == 2
    first, second = trades

    assert first.direction == "sell"
    assert first.entry_time == dt.datetime(2026, 1, 5, 19, 0)
    assert first.exit_time == dt.datetime(2026, 1, 6, 3, 0)
    assert first.entry_price == 8739.6
    assert first.exit_price == 8718.6
    assert first.lots == 1.5

    assert second.direction == "buy"
    assert second.entry_price == 8687.9


def test_pnl_is_net_of_commission_and_swap(report_dir):
    first, second = Mt5ReportHtmlSource.from_directory(report_dir).load_trades()

    # balance moved 10 000.00 -> 10 020.82, i.e. profit 21.14 plus a -0.32 swap
    assert first.pnl_money == pytest.approx(20.82)
    assert first.swap == pytest.approx(-0.32)

    # and 10 020.82 -> 9 965.97, i.e. -53.35 profit plus a -1.50 commission
    assert second.pnl_money == pytest.approx(-54.85)
    assert second.commission == pytest.approx(-1.50)


def test_exit_reason_comes_from_the_deal_comment(report_dir):
    first, second = Mt5ReportHtmlSource.from_directory(report_dir).load_trades()

    assert first.exit_reason == "time"  # blank comment: the EA closed it itself
    assert second.exit_reason == "sl"


def test_risk_recovered_by_joining_the_orders_table(report_dir):
    first, second = Mt5ReportHtmlSource.from_directory(report_dir).load_trades()

    # short from 8739.6 with SL 8778.2 -> 38.6 points risked, 21.0 points made
    assert first.r_multiple == pytest.approx(21.0 / 38.6, rel=1e-6)
    # long from 8687.9 stopped out exactly at its SL 8634.9 -> a clean -1R
    assert second.r_multiple == pytest.approx(-1.0, rel=1e-6)

    # ~1.0067 $/point at 1.5 lots, inferred from the deals' own profit figures
    assert first.risk_money == pytest.approx(38.86, abs=0.05)
    assert second.risk_money == pytest.approx(53.35, abs=0.05)


def test_metadata_is_read_without_touching_localized_labels(report_dir):
    report = Mt5ReportHtmlSource.from_directory(report_dir).load_reports()[0]

    assert report.initial_balance == pytest.approx(10_000.0)
    assert report.symbol == "ASXAUD"
    assert report.strategy == "MeanRev1"  # from the entry deals' comment column


def test_explicit_strategy_name_overrides_detection(report_dir):
    trades = Mt5ReportHtmlSource.from_directory(report_dir, strategy="mean_rev_1").load_trades()

    assert {t.strategy for t in trades} == {"mean_rev_1"}


def test_utf8_encoded_copy_parses_identically(tmp_path):
    (tmp_path / "utf8.html").write_text(REPORT, encoding="utf-8")

    trades = Mt5ReportHtmlSource.from_directory(tmp_path).load_trades()

    assert len(trades) == 2
    assert trades[0].pnl_money == pytest.approx(20.82)


def test_report_without_a_deals_table_is_rejected(tmp_path):
    (tmp_path / "graph_only.html").write_text(HEADER + "</table></body></html>", encoding="utf-16")

    with pytest.raises(ValueError, match="No closed trades"):
        Mt5ReportHtmlSource.from_directory(tmp_path).load_trades()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("10 000.00", 10_000.0),      # space thousands separator, as MT5 writes it
        ("1 537.38", 1537.38),
        ("-0.32", -0.32),
        ("10.000,50", 10_000.50),     # comma-decimal locales
        ("1234,5", 1234.5),
        ("", None),
        ("   ", None),
    ],
)
def test_localized_number_parsing(text, expected):
    assert _num(text) == expected
