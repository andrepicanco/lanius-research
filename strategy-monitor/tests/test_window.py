"""Janela julgada: o corte é o fim do período do cabeçalho do baseline (decisão de 2026-11-23)."""

import datetime as dt

from conftest import make_xlsx, trade_spec
from strategy_monitor import compare as cmp
from strategy_monitor.config import Config
from strategy_monitor.metrics import compute_metrics
from strategy_monitor.tester_report import read_report
from strategy_monitor.window import baseline_end, describe, judged_window


def _base(tmp_path, header_end="2026.02.28"):
    # 3 trades em fevereiro (o último fecha em 03/02); cabeçalho até `header_end` (forward sem trades)
    f = tmp_path / "base.xlsx"
    make_xlsx(f, [trade_spec(1, 100, 110, 95), trade_spec(2, 100, 90, 95), trade_spec(3, 100, 110, 95)],
              expert="EA", symbol="TST", tf="H1", period=("2026.01.01", header_end))
    return read_report(f)


def _cur(tmp_path):
    # corrente: 2 de fevereiro (dentro do baseline) + 1 em 15/02 (forward) + 2 de março
    f = tmp_path / "cur.xlsx"
    make_xlsx(f, [trade_spec(1, 100, 110, 95), trade_spec(2, 100, 90, 95), trade_spec(15, 100, 110, 95),
                  trade_spec(1, 100, 112, 95, month=3), trade_spec(2, 100, 90, 95, month=3)],
              expert="EA", symbol="TST", tf="H1")
    return read_report(f)


def test_cutoff_is_the_header_end_not_the_last_trade(tmp_path):
    base = _base(tmp_path)
    assert baseline_end(base) == dt.datetime(2026, 2, 28)           # e não 03/02 (último trade)
    judged, w = judged_window(base, _cur(tmp_path).trades)
    assert len(judged) == 2                                          # só março; o forward de 15/02 fica de fora
    assert w.n_report == 5 and w.n_excluded == 3


def test_a_trade_after_the_header_end_is_the_cutoff_if_it_is_later(tmp_path):
    # cabeçalho antes do último trade (arquivo estranho): nunca misturar trades do baseline
    base = _base(tmp_path, header_end="2026.02.01")
    last = max(t.close_time for t in base.trades)
    assert last > dt.datetime(2026, 2, 1) and baseline_end(base) == last


def test_description_names_the_baseline_period_and_the_cutoff(tmp_path):
    _, w = judged_window(_base(tmp_path), _cur(tmp_path).trades)
    text = "\n".join(describe(w))
    assert "Baseline: 2026-01-01 → 2026-02-28 (último trade: 2026-02-03)" in text
    assert "a partir de 2026-02-28" in text and "2 trades" in text


def test_compare_verdict_is_na_when_there_are_no_trades():
    empty = compute_metrics([], 10000.0)
    v = cmp.compare(empty, empty, Config())
    assert v.overall == cmp.NA and len(v.checks) == 1
