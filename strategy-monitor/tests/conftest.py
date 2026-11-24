"""Fábrica de relatórios .xlsx sintéticos no mesmo layout dos baselines reais (colunas A..M)."""

import datetime as dt
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FMT = "%Y.%m.%d %H:%M:%S"


def make_xlsx(path: Path, trades: list[dict], expert="TestEA", symbol="TST", tf="H1",
              period=("2026.01.01", "2026.06.30"), deposit=10000.0, inputs=None, k=1.0):
    """Cada trade: dict(open=datetime, close=datetime, side='buy'|'sell', vol, entry, exit, sl, tp,
    reason='sl'|'tp'|'' , pending=False). O lucro é (exit-entry)*sinal*vol*k."""
    inputs = inputs or {"InpA": "1", "InpB": "2"}
    ws = openpyxl.Workbook().active
    r = 1

    def put(cells: dict[str, object]):
        nonlocal r
        for col, val in cells.items():
            ws[f"{col}{r}"] = val
        r += 1

    put({"A": "Relatório do Testador de Estratégia"})
    put({"A": "Servidor (Build 1)"})
    put({"A": "Configuração"})
    put({"A": "Expert Advisor (Robô):", "D": expert})
    put({"A": "Ativo:", "D": symbol})
    put({"A": "Período:", "D": f"{tf} ({period[0]} - {period[1]})"})
    for i, (name, val) in enumerate(inputs.items()):
        put({"A": "Parâmetros de entrada:", "D": f"{name}={val}"} if i == 0 else {"D": f"{name}={val}"})
    put({"A": "Empresa:", "D": "Corretora"})
    put({"A": "Moeda:", "D": "USD"})
    put({"A": "Depósito Inicial:", "D": deposit})
    put({"A": "Alavancagem:", "D": "1:100"})
    put({"A": "Resultados"})
    put({"A": "Qualidade do histórico:", "D": "100%"})
    put({"A": "Barras:", "D": 100.0, "E": "Ticks:", "H": 5000.0, "I": "Ativos:", "L": 1.0})
    r += 3
    put({"A": "Ordens"})
    put({"A": "Horário da Abertura", "B": "Ordem"})

    # Ordens: uma de entrada e uma de saída por trade.
    n = 0
    for t in trades:
        n += 1
        typ = t["side"] + (" stop" if t.get("pending") else "")
        put({"A": t["open"].strftime(FMT), "B": 2 * n, "C": symbol, "D": typ, "E": f"{t['vol']} / {t['vol']}",
             "G": t["entry"] if t.get("pending") else 0.0, "H": t["sl"], "I": t.get("tp"),
             "J": t["open"].strftime(FMT), "L": "filled", "M": expert})
        out_side = "sell" if t["side"] == "buy" else "buy"
        put({"A": t["close"].strftime(FMT), "B": 2 * n + 1, "C": symbol, "D": out_side,
             "E": f"{t['vol']} / {t['vol']}", "G": 0.0, "J": t["close"].strftime(FMT), "L": "filled",
             "M": f"{t['reason']} {t['exit']}" if t.get("reason") else ""})
    r += 2
    put({"A": "Negociações"})
    put({"A": "Horário", "B": "Negociação"})
    bal = deposit
    put({"A": dt.datetime(2026, 1, 1).strftime(FMT), "B": 1, "D": "balance", "K": deposit, "L": bal})
    n = 0
    for t in trades:
        n += 1
        sign = 1 if t["side"] == "buy" else -1
        profit = round((t["exit"] - t["entry"]) * sign * t["vol"] * k, 2)
        bal += profit
        out_side = "sell" if t["side"] == "buy" else "buy"
        put({"A": t["open"].strftime(FMT), "B": 2 * n, "C": symbol, "D": t["side"], "E": "in", "F": t["vol"],
             "G": t["entry"], "H": 2 * n, "I": 0.0, "J": 0.0, "K": 0.0, "L": bal - profit, "M": expert})
        put({"A": t["close"].strftime(FMT), "B": 2 * n + 1, "C": symbol, "D": out_side, "E": "out", "F": t["vol"],
             "G": t["exit"], "H": 2 * n + 1, "I": 0.0, "J": 0.0, "K": profit, "L": bal,
             "M": f"{t['reason']} {t['exit']}" if t.get("reason") else ""})
    ws.parent.save(path)


def trade_spec(day: int, entry: float, exit_: float, sl: float, side="buy", vol=1.0, tp=None, month=2, **kw):
    o = dt.datetime(2026, month, day, 10, 0, 0)
    return dict(open=o, close=o + dt.timedelta(hours=3), side=side, vol=vol, entry=entry, exit=exit_, sl=sl, tp=tp, **kw)


@pytest.fixture
def xlsx_factory(tmp_path):
    def _make(name="rep.xlsx", **kw):
        p = tmp_path / name
        make_xlsx(p, **kw)
        return p
    return _make
