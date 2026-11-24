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
