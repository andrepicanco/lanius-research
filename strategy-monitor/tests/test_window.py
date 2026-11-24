"""Janela julgada: o corte padrão é o fim do período do cabeçalho do baseline (decisão de 2026-11-23)."""

import datetime as dt

import pytest

from conftest import make_xlsx, trade_spec
from strategy_monitor import cli
from strategy_monitor.tester_report import read_report
from strategy_monitor.window import baseline_end, judged_window


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


def test_default_cutoff_is_the_header_end_not_the_last_trade(tmp_path):
    base = _base(tmp_path)
    assert baseline_end(base) == dt.datetime(2026, 2, 28)           # e não 03/02 (último trade)
    judged, w = judged_window(base, _cur(tmp_path).trades, None, False)
    assert len(judged) == 2 and w.n_overlap == 0                    # só março; o forward de 15/02 fica de fora
    assert w.n_report == 5 and w.n_excluded == 3


def test_a_trade_after_the_header_end_is_the_cutoff_if_it_is_later(tmp_path):
    # cabeçalho antes do último trade (arquivo estranho): nunca misturar trades do baseline
    base = _base(tmp_path, header_end="2026.02.01")
    last = max(t.close_time for t in base.trades)
    assert last > dt.datetime(2026, 2, 1)                  # o último trade fecha depois do cabeçalho
    assert baseline_end(base) == last


def test_full_reports_overlap_and_since_before_the_end_too(tmp_path):
    base, cur = _base(tmp_path), _cur(tmp_path)
    _, w_full = judged_window(base, cur.trades, None, True)
    assert w_full.cutoff is None and w_full.n_judged == 5 and w_full.n_overlap == 3
    _, w_since = judged_window(base, cur.trades, "2026-02-10", False)
    assert w_since.n_overlap == 1                                    # o de 15/02 (forward)


def _cli(tmp_path, capsys, *extra):
    env = tmp_path / "vazio.env"
    env.write_text("", encoding="utf-8")
    d = tmp_path / "bl"
    d.mkdir(exist_ok=True)
    (d / "b.xlsx").write_bytes((tmp_path / "base.xlsx").read_bytes())
    cli.main(["--env-file", str(env), "--baseline-dir", str(d), "compare",
              "--current", str(tmp_path / "cur.xlsx"), *extra])
    return capsys.readouterr().out


def test_output_names_the_baseline_period_and_what_was_left_out(tmp_path, capsys):
    _base(tmp_path), _cur(tmp_path)
    out = _cli(tmp_path, capsys)
    assert "Baseline: 2026-01-01 → 2026-02-28 (último trade: 2026-02-03)" in out
    assert "a partir de 2026-02-28" in out and "3 de 5 do relatório ficaram de fora" in out
    assert "Sobreposição" not in out


def test_full_gets_an_overlap_warning_and_can_never_be_ok(tmp_path, capsys):
    _base(tmp_path), _cur(tmp_path)
    out = _cli(tmp_path, capsys, "--full")
    first = out.splitlines()[0]
    assert "[aviso] 3 dos 5 trades julgados são do período do baseline" in out
    assert "[ATENCAO] Sobreposição com o baseline" in out
    assert "[OK]" not in first


@pytest.mark.parametrize("args,only,until", [([], None, None), (["DAX40"], "DAX40", None),
                                              (["--until", "2026-11-20", "--telegram"], None, "2026-11-20")])
def test_monitor_is_run_with_the_default_window(monkeypatch, args, only, until):
    seen = {}
    monkeypatch.setattr(cli, "_cmd_run", lambda cfg, a: seen.update(vars(a)) or 0)
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: object())
    monkeypatch.setattr(cli, "load_env_file", lambda *a, **k: None)
    assert cli.main(["monitor", *args]) == 0
    assert seen["only"] == only and seen["until"] == until and seen["since"] is None and seen["full"] is False
    assert seen["telegram"] == ("--telegram" in args)
