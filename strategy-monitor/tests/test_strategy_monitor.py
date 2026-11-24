import datetime as dt
import math
from pathlib import Path

import pytest

from conftest import trade_spec
from strategy_monitor import compare as cmp
from strategy_monitor.config import Config, load_config
from strategy_monitor.metrics import attach_r_multiples, compute_metrics, wilson_interval
from strategy_monitor.telegram import split_chunks
from strategy_monitor.tester_report import ReportFormatError, read_report


# ---------- leitor ----------

def test_reads_header_inputs_and_trades(xlsx_factory):
    trades = [trade_spec(1, 100, 110, 95, tp=120, reason="tp"), trade_spec(2, 100, 97, 95, reason="sl")]
    r = read_report(xlsx_factory(trades=trades, expert="EA_X", symbol="ABC", tf="M30"))
    assert (r.expert, r.symbol, r.timeframe) == ("EA_X", "ABC", "M30")
    assert r.date_from == dt.date(2026, 1, 1) and r.date_to == dt.date(2026, 6, 30)
    assert r.inputs == {"InpA": "1", "InpB": "2"}
    assert r.deposit == 10000.0 and r.history_quality == "100%" and r.ticks == 5000.0
    assert len(r.trades) == 2 and not r.warnings
    assert r.trades[0].exit_reason == "tp" and r.trades[1].exit_reason == "sl"


def test_sl_tp_read_by_column_position_even_when_tp_blank(xlsx_factory):
    # TP desligado (célula vazia) não pode deslocar o S/L para a coluna do TP.
    r = read_report(xlsx_factory(trades=[trade_spec(1, 100, 110, 95, tp=None)]))
    assert r.trades[0].sl0 == 95 and r.trades[0].tp0 is None


def test_pending_order_types_are_joined(xlsx_factory):
    r = read_report(xlsx_factory(trades=[trade_spec(1, 100, 110, 95, tp=120, pending=True)]))
    assert r.trades[0].sl0 == 95 and r.trades[0].tp0 == 120


def test_bad_header_raises(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active["A1"] = "isto não é um relatório"
    p = tmp_path / "x.xlsx"
    wb.save(p)
    with pytest.raises(ReportFormatError):
        read_report(p)


# ---------- métricas ----------

def _metrics(xlsx_factory, specs, k=1.0, **kw):
    r = read_report(xlsx_factory(trades=specs, k=k, **kw))
    diag = attach_r_multiples(r.trades)
    return r, compute_metrics(r.trades, r.deposit), diag


def test_breakeven_formula(xlsx_factory):
    # 1 ganho de +30 e 2 perdas de -10: W=30, L=10 -> BE = 10/(30+10) = 25%; win rate = 1/3.
    specs = [trade_spec(1, 100, 130, 90), trade_spec(2, 100, 90, 90), trade_spec(3, 100, 90, 90)]
    _, m, _ = _metrics(xlsx_factory, specs)
    assert m.breakeven_wr == pytest.approx(0.25)
    assert m.win_rate == pytest.approx(1 / 3)
    assert m.wr_margin == pytest.approx(1 / 3 - 0.25)
    # RR fixo de 3:1 recai no clássico 1/(1+RR) = 25%.
    assert m.breakeven_wr == pytest.approx(1 / (1 + 3))


def test_expectancy_in_r_and_k_estimation(xlsx_factory):
    # k = 2 dinheiro por ponto por lote. Risco = |100-90|*1*2 = 20. Ganho +30 pts = +60 -> 3R; perda -10 pts -> -1R.
    specs = [trade_spec(d, 100, 130 if d % 2 else 90, 90) for d in range(1, 11)]
    r, m, diag = _metrics(xlsx_factory, specs, k=2.0)
    assert diag["value_per_point"] == pytest.approx(2.0, rel=1e-6)
    assert [round(t.r_multiple, 6) for t in r.trades[:2]] == [3.0, -1.0]
    assert m.expectancy_r == pytest.approx((5 * 3 - 5 * 1) / 10)


def test_sell_side_profit_sign(xlsx_factory):
    specs = [trade_spec(d, 100, 90, 110, side="sell") for d in range(1, 8)]  # vendas que ganham
    r, m, _ = _metrics(xlsx_factory, specs)
    assert m.wins == 7 and all(t.r_multiple == pytest.approx(1.0) for t in r.trades)


def test_drawdown_uses_closed_balance_and_pct_of_peak(xlsx_factory):
    # +50, depois -30 e -20: pico 10050, vale 10000 -> DD 50 = 0.4975% do pico.
    specs = [trade_spec(1, 100, 150, 90), trade_spec(2, 100, 70, 90), trade_spec(3, 100, 80, 90)]
    _, m, _ = _metrics(xlsx_factory, specs)
    assert m.max_dd == pytest.approx(50.0)
    assert m.max_dd_pct == pytest.approx(50 / 10050 * 100)


def test_wilson_interval_known_value():
    lo, hi = wilson_interval(8, 10, 0.95)  # valor de referência da fórmula de Wilson
    assert (lo, hi) == (pytest.approx(0.4902, abs=1e-3), pytest.approx(0.9433, abs=1e-3))
    assert wilson_interval(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError):
        wilson_interval(1, 2, 0.5)


# ---------- veredito ----------

def _m(xlsx_factory, wins, losses, w=30, l=10):
    specs, d = [], 1
    for _ in range(wins):
        specs.append(trade_spec(1 + d % 27, 100, 100 + w, 100 - l, month=2 + d // 28)); d += 1
    for _ in range(losses):
        specs.append(trade_spec(1 + d % 27, 100, 100 - l, 100 - l, month=2 + d // 28)); d += 1
    return _metrics(xlsx_factory, specs)[1]


def test_verdict_ok_when_win_rate_above_breakeven(xlsx_factory):
    base = _m(xlsx_factory, 20, 40)           # win 33,3% > BE 25%
    v = cmp.compare(base, base, Config(min_trades=30))
    assert v.overall == cmp.OK


def test_verdict_falha_when_significantly_below_breakeven(xlsx_factory):
    base = _m(xlsx_factory, 20, 40)
    cur = _m(xlsx_factory, 4, 56)             # win 6,7%, IC95 superior ~16% < BE 25%
    v = cmp.compare(base, cur, Config(min_trades=30))
    wr = next(c for c in v.checks if c.name.startswith("Win rate"))
    assert wr.status == cmp.FALHA and v.overall == cmp.FALHA


def test_verdict_small_sample_caps_win_rate_failure_at_atencao(xlsx_factory):
    base = _m(xlsx_factory, 20, 40)
    cur = _m(xlsx_factory, 4, 56)             # mesmo caso que dá FALHA com min_trades=30...
    v = cmp.compare(base, cur, Config(min_trades=1000))   # ...mas aqui n=60 < 1000
    wr = next(c for c in v.checks if c.name.startswith("Win rate"))
    assert v.small_sample and wr.status == cmp.ATENCAO and "amostra pequena" in wr.detail


def test_drawdown_cap_from_config_forces_failure(xlsx_factory):
    base = _m(xlsx_factory, 20, 40)
    cfg = Config(min_trades=30)
    cfg.thresholds.max_drawdown_pct = 0.01
    v = cmp.compare(base, base, cfg)
    assert next(c for c in v.checks if c.name.startswith("Drawdown")).status == cmp.FALHA


# ---------- config / telegram ----------

def test_baseline_dir_precedence(tmp_path, monkeypatch):
    f = tmp_path / "m.yaml"
    f.write_text("baseline_dir: C:/do/arquivo\nmin_trades: 12\n", encoding="utf-8")
    monkeypatch.delenv("STRATEGY_MONITOR_BASELINE_DIR", raising=False)
    assert load_config(f).baseline_dir == Path("C:/do/arquivo") and load_config(f).min_trades == 12
    monkeypatch.setenv("STRATEGY_MONITOR_BASELINE_DIR", "C:/do/env")
    assert load_config(f).baseline_dir == Path("C:/do/env")
    assert load_config(f, baseline_dir="C:/da/cli").baseline_dir == Path("C:/da/cli")


def test_split_chunks_respects_limit_and_keeps_lines():
    text = "\n".join(f"linha {i}" for i in range(2000))
    chunks = split_chunks(text, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    assert "\n".join(chunks) == text


def test_telegram_requires_env(monkeypatch):
    from strategy_monitor import telegram
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(telegram.TelegramConfigError):
        telegram.send_message("x")


# ---------- contra os baselines reais (pulado se a pasta não existir) ----------

_REAL = Path(r"C:\Users\15339\AppData\Roaming\MetaQuotes\Terminal\98832BEDFCD443328B914728FFAC3D44"
             r"\MQL5\Profiles\strategy_baseline")
# Números impressos pelo próprio Strategy Tester nesses relatórios (rebaixamento máximo do saldo,
# fator de lucro, média de lucro/perda, lucro líquido, nº de trades).
_VENDOR = {
    "NAS100_H1.xlsx": dict(n=161, wins=67, net=418.77, pf=1.212004, avg_win=35.732239, avg_loss=21.013723, dd=319.47),
    "DAX40_M12.xlsx": dict(n=93, wins=38, net=402.92, pf=1.308834, avg_win=44.936053, avg_loss=23.720909, dd=252.78),
}


@pytest.mark.skipif(not _REAL.is_dir(), reason="pasta de baselines do André não existe nesta máquina")
@pytest.mark.parametrize("name", sorted(_VENDOR))
def test_matches_tester_summary_on_real_baselines(name):
    f = _REAL / name
    if not f.exists():
        pytest.skip(f"{name} ausente")
    v = _VENDOR[name]
    r = read_report(f)
    m = compute_metrics(r.trades, r.deposit)
    assert (m.n, m.wins) == (v["n"], v["wins"])
    assert m.net == pytest.approx(v["net"], abs=0.01)
    assert m.profit_factor == pytest.approx(v["pf"], abs=1e-5)
    assert m.avg_win == pytest.approx(v["avg_win"], abs=1e-5)
    assert m.avg_loss == pytest.approx(v["avg_loss"], abs=1e-5)
    assert m.max_dd == pytest.approx(v["dd"], abs=0.01)
    assert not r.warnings


# ---------- .ini do Tester ----------

def test_build_ini_matches_tester_saved_format(xlsx_factory, tmp_path):
    import datetime as dt
    from strategy_monitor.tester_ini import build_ini, write_ini
    r = read_report(xlsx_factory(trades=[trade_spec(1, 100, 110, 95)], expert="EA_X", symbol="ABC", tf="M12",
                                 inputs={"InpTimeframe": "12", "InpUseTrailing": "true"}))
    text = build_ini(r, "rep_abc", dt.date(2026, 11, 17))
    assert text.startswith("[Tester]\r\nExpert=EA_X\EA_X.ex5\r\nSymbol=ABC\r\nPeriod=M12\r\n")
    for line in ("Model=0", "ExecutionMode=20", "FromDate=2026.01.01", "ToDate=2026.11.17", "Deposit=10000",
                 "ForwardMode=0", "Report=rep_abc", "ShutdownTerminal=1", "[TesterInputs]",
                 "InpTimeframe=12", "InpUseTrailing=true"):
        assert line in text.split("\r\n")
    p = tmp_path / "x.ini"
    write_ini(p, text)
    raw = p.read_bytes()
    assert raw[:2] == b"\xff\xfe" and raw[2:].decode("utf-16-le") == text


def test_build_ini_input_overrides_do_not_touch_the_baseline(xlsx_factory):
    import datetime as dt
    from strategy_monitor.tester_ini import build_ini
    r = read_report(xlsx_factory(trades=[trade_spec(1, 100, 110, 95)], inputs={"InpA": "1", "InpB": "2"}))
    text = build_ini(r, "x", dt.date(2026, 7, 1), inputs={"InpA": "9"})
    assert "InpA=9" in text and "InpA=1" not in text and "InpB=2" in text and r.inputs["InpA"] == "1"


# ---------- runner (subprocesso simulado) ----------

def _runner_setup(tmp_path, monkeypatch, outcomes):
    """outcomes: lista, uma entrada por tentativa; True => o 'terminal' escreve o relatorio."""
    import datetime as dt
    from strategy_monitor import runner
    term = tmp_path / "term"
    term.mkdir()
    (term / "terminal64.exe").write_bytes(b"")
    calls = []

    class FakeProc:
        def __init__(self, args, cwd):
            calls.append(args)
            ok = outcomes[len(calls) - 1]
            name = next(a for a in args if a.startswith("/config:")).split("/config:")[1]
            if ok:
                (term / (Path(name).stem + ".htm")).write_text("rel")
                (term / (Path(name).stem + "-hst.png")).write_text("png")
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(runner.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    monkeypatch.setattr(runner, "_is_running", lambda exe: False)
    monkeypatch.setattr(runner, "_wait_for_relaunch", lambda exe, report, timeout_s, appear_s=25.0: report.is_file())
    stopped = []
    monkeypatch.setattr(runner, "_stop_instances", lambda exe, wait_s=20.0: stopped.append(1) or 0)
    return runner, term, calls, stopped


def _baseline(xlsx_factory):
    return read_report(xlsx_factory(trades=[trade_spec(1, 100, 110, 95)], expert="EA_X"))


def test_runner_success_moves_report_and_cleans_temp_files(xlsx_factory, tmp_path, monkeypatch):
    import datetime as dt
    runner, term, calls, _ = _runner_setup(tmp_path, monkeypatch, [True])
    out = runner.run_backtest(_baseline(xlsx_factory), runner.RunnerConfig(term), dt.date(2026, 7, 1), "sm_t", tmp_path / "out")
    assert out == tmp_path / "out" / "sm_t.htm" and out.read_text() == "rel"
    assert not list(term.glob("sm_t*")) and not list((term / "Config").glob("*.ini"))
    assert calls[0][1:] == ["/portable", f"/config:{term / 'Config' / 'sm_t.ini'}"]


def test_runner_retries_once_after_a_silent_exit(xlsx_factory, tmp_path, monkeypatch):
    import datetime as dt
    runner, term, calls, stopped = _runner_setup(tmp_path, monkeypatch, [False, True])
    out = runner.run_backtest(_baseline(xlsx_factory), runner.RunnerConfig(term), dt.date(2026, 7, 1), "sm_t", tmp_path / "out")
    assert out.exists() and len(calls) == 2 and stopped == [1]


def test_runner_gives_up_after_two_silent_exits(xlsx_factory, tmp_path, monkeypatch):
    import datetime as dt
    runner, term, calls, _ = _runner_setup(tmp_path, monkeypatch, [False, False])
    with pytest.raises(runner.RunnerError, match="2 tentativas"):
        runner.run_backtest(_baseline(xlsx_factory), runner.RunnerConfig(term), dt.date(2026, 7, 1), "sm_t", tmp_path / "out")
    assert len(calls) == 2


def test_runner_refuses_when_test_terminal_is_open(xlsx_factory, tmp_path, monkeypatch):
    import datetime as dt
    runner, term, calls, _ = _runner_setup(tmp_path, monkeypatch, [True])
    monkeypatch.setattr(runner, "_is_running", lambda exe: True)
    with pytest.raises(runner.RunnerError, match="está aberto"):
        runner.run_backtest(_baseline(xlsx_factory), runner.RunnerConfig(term), dt.date(2026, 7, 1), "sm_t", tmp_path / "out")
    assert calls == []


# ---------- limpeza de disco ----------

def _fake_terminal(tmp_path, hist_mb=3):
    t = tmp_path / "t"
    for rel in ("Tester/cache", "Tester/logs", "Tester/bases", "Tester/Agent-x-3000/logs", "logs",
                "Bases/Srv/history/DAX40", "Bases/Default/History/EURUSD", "Config", "MQL5/Experts"):
        (t / rel).mkdir(parents=True)
    for rel, mb in (("Tester/cache/a", 1), ("Tester/logs/a.log", 1), ("Tester/bases/b", 1),
                    ("Tester/Agent-x-3000/logs/c.log", 1), ("logs/x.log", 1),
                    ("Bases/Srv/history/DAX40/2026.hcc", hist_mb), ("Bases/Default/History/EURUSD/2023.hcc", 1),
                    ("Config/accounts.dat", 1), ("MQL5/Experts/ea.ex5", 1)):
        (t / rel).write_bytes(b"x" * mb * 1024 * 1024)
    return t


def test_purge_keeps_login_and_experts_and_clears_regenerable_files(tmp_path):
    from strategy_monitor.cleanup import purge_after_run
    t = _fake_terminal(tmp_path)
    rep = purge_after_run(t, max_terminal_mb=10_000)          # abaixo do teto: historico fica
    assert not rep.purged_history and (t / "Bases/Srv/history/DAX40/2026.hcc").exists()
    assert not list((t / "Tester/cache").iterdir()) and not list((t / "logs").iterdir())
    assert (t / "Config/accounts.dat").exists() and (t / "MQL5/Experts/ea.ex5").exists()
    assert rep.freed_mb == pytest.approx(5, abs=0.1)


def test_purge_drops_downloaded_history_above_cap_but_never_config(tmp_path):
    from strategy_monitor.cleanup import purge_after_run
    t = _fake_terminal(tmp_path, hist_mb=5)
    rep = purge_after_run(t, max_terminal_mb=4)
    assert rep.purged_history and not (t / "Bases/Srv/history/DAX40/2026.hcc").exists()
    assert not (t / "Bases/Default/History/EURUSD/2023.hcc").exists()
    assert (t / "Config/accounts.dat").exists() and (t / "MQL5/Experts/ea.ex5").exists()


def test_prune_reports_keeps_newest_per_baseline(tmp_path):
    import os, time
    from strategy_monitor.cleanup import prune_reports
    for i in range(4):
        for key in ("EA_DAX40_M12", "EA_NAS100_H1"):
            f = tmp_path / f"sm_{key}_20261117_10000{i}.htm"
            f.write_text("x"); os.utime(f, (time.time() + i, time.time() + i))
    removed = prune_reports(tmp_path, keep=2)
    assert len(removed) == 4 and len(list(tmp_path.glob("sm_*.htm"))) == 4
    assert (tmp_path / "sm_EA_DAX40_M12_20261117_100003.htm").exists()


def test_runner_refuses_with_low_disk(xlsx_factory, tmp_path, monkeypatch):
    import datetime as dt
    runner, term, calls, _ = _runner_setup(tmp_path, monkeypatch, [True])
    monkeypatch.setattr(runner, "free_gb", lambda p: 0.2)
    with pytest.raises(runner.RunnerError, match="pouco espaco"):
        runner.run_backtest(_baseline(xlsx_factory), runner.RunnerConfig(term, min_free_gb=1.0), dt.date(2026, 7, 1), "sm_t", tmp_path / "out")
    assert calls == []


def test_purge_removes_metaeditor_that_liveupdate_reinstalls(tmp_path):
    from strategy_monitor.cleanup import purge_after_run
    t = _fake_terminal(tmp_path)
    (t / "MetaEditor64.exe").write_bytes(b"x" * 2 * 1024 * 1024)
    (t / "terminal64.exe").write_bytes(b"x")
    purge_after_run(t, max_terminal_mb=10_000)
    assert not (t / "MetaEditor64.exe").exists() and (t / "terminal64.exe").exists()


def _fake_clock(monkeypatch, runner):
    now = [0.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runner.time, "sleep", lambda sec: now.__setitem__(0, now[0] + sec))
    return now


def test_wait_for_relaunch_waits_for_the_relaunched_instance_to_finish(tmp_path, monkeypatch):
    """LiveUpdate relanca o terminal com o mesmo /config: o runner deve esperar essa instancia, nao mata-la."""
    from strategy_monitor import runner
    now = _fake_clock(monkeypatch, runner)
    report = tmp_path / "rep.htm"

    def running(exe):             # nada ate t=10; instancia relancada roda de t=10 a t=40, e grava o relatorio ao fim
        if 10 <= now[0] < 40:
            return True
        if now[0] >= 40 and not report.exists():
            report.write_text("ok")
        return False

    monkeypatch.setattr(runner, "_is_running", running)
    assert runner._wait_for_relaunch(tmp_path / "t.exe", report, timeout_s=600, appear_s=25) is True


def test_wait_for_relaunch_gives_up_quickly_when_nothing_relaunches(tmp_path, monkeypatch):
    from strategy_monitor import runner
    now = _fake_clock(monkeypatch, runner)
    monkeypatch.setattr(runner, "_is_running", lambda exe: False)
    assert runner._wait_for_relaunch(tmp_path / "t.exe", tmp_path / "rep.htm", timeout_s=600, appear_s=25) is False
    assert now[0] <= 26            # esperou so a janela de aparecimento, nao o timeout inteiro


def test_wait_for_relaunch_respects_the_timeout(tmp_path, monkeypatch):
    from strategy_monitor import runner
    now = _fake_clock(monkeypatch, runner)
    monkeypatch.setattr(runner, "_is_running", lambda exe: True)      # instancia que nunca termina
    assert runner._wait_for_relaunch(tmp_path / "t.exe", tmp_path / "rep.htm", timeout_s=100, appear_s=25) is False
    assert 100 <= now[0] < 110
