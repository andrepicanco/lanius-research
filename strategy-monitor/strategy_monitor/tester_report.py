"""Lê o relatório do Strategy Tester em .xlsx (os baselines do André).

O que o arquivo tem (verificado em NAS100_H1.xlsx e DAX40_M12.xlsx, terminal em português):
  - cabeçalho: EA, ativo, "TF (AAAA.MM.DD - AAAA.MM.DD)", uma linha por input, empresa/moeda/
    depósito/alavancagem, qualidade do histórico, barras/ticks;
  - tabela Ordens (11 colunas lógicas) e tabela Negociações/Deals (13 colunas lógicas).

Os rótulos são traduzidos para o idioma do terminal, mas os *valores* ficam em inglês
(buy/sell/in/out/balance/filled, prefixo 'sl '/'tp ' no comentário). Por isso o leitor se guia
por posição e por forma dos valores, nunca pelo texto de um rótulo (mesma lição do parser HTML
do risk-mgmt). Células vazias/mescladas são descartadas antes de interpretar a linha.
"""

import datetime as dt
import re
import warnings
from pathlib import Path

import openpyxl

from .models import Report, Trade

_TIME_FMT = "%Y.%m.%d %H:%M:%S"
_TIME_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}$")
_PERIOD_RE = re.compile(r"^(\w+)\s*\((\d{4}\.\d{2}\.\d{2})\s*-\s*(\d{4}\.\d{2}\.\d{2})\)$")
_INPUT_RE = re.compile(r"^(\w+)=(.*)$")
# Tipos de ordem: a mercado (buy/sell) ou pendente (buy stop, sell limit, buy stop limit...).
_ORDER_TYPE_RE = re.compile(r"^(buy|sell)( stop| limit| stop limit)?$")


class ReportFormatError(ValueError):
    pass


def _rows(path: Path) -> list[tuple]:
    """Linhas cruas (com células vazias, para preservar a posição das colunas)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "Workbook contains no default style" - export do MT5
        wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    return [tuple(row) for row in ws.iter_rows(values_only=True)]


def _compact(row: tuple) -> list:
    return [c for c in row if c is not None]


def _num(x) -> float | None:
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = x.replace(" ", "").replace("\xa0", "")
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _parse_header(rows: list[list]) -> dict:  # rows já compactadas
    # A linha do período ("H1 (AAAA.MM.DD - AAAA.MM.DD)") é a âncora: o ativo vem logo antes e o
    # EA antes dele. Cada uma é [rótulo, valor].
    pi = next((i for i, r in enumerate(rows[:20]) if r and isinstance(r[-1], str) and _PERIOD_RE.match(r[-1])), None)
    if pi is None or pi < 2:
        raise ReportFormatError("cabeçalho do relatório fora do formato esperado")
    expert, symbol, period = (str(rows[i][-1]) for i in (pi - 2, pi - 1, pi))
    m = _PERIOD_RE.match(period)
    tf, d0, d1 = m.groups()

    inputs: dict[str, str] = {}
    i = pi + 1
    # A primeira linha de inputs tem rótulo + valor; as seguintes só o valor. Termina na
    # próxima linha com rótulo (Empresa), que não casa com NOME=valor.
    while i < len(rows) and rows[i] and isinstance(rows[i][-1], str) and _INPUT_RE.match(rows[i][-1]):
        k, v = _INPUT_RE.match(rows[i][-1]).groups()
        inputs[k] = v
        i += 1

    # Depois dos inputs: empresa, moeda, depósito, alavancagem, [título Resultados],
    # qualidade do histórico, barras/ticks. Cada um é lido pelo formato, tolerando ausência.
    deposit = history_quality = ticks = None
    for r in rows[i : i + 10]:
        if len(r) >= 2 and deposit is None and isinstance(r[-1], (int, float)) and len(r) == 2 and r[-1] >= 100:
            deposit = float(r[-1])
        if len(r) == 2 and isinstance(r[1], str) and r[1].endswith("%") and history_quality is None:
            history_quality = r[1]
        if len(r) >= 4 and isinstance(r[3], (int, float)) and ticks is None and isinstance(r[1], (int, float)):
            ticks = float(r[3])
    return dict(
        expert=expert, symbol=symbol, timeframe=tf,
        date_from=dt.datetime.strptime(d0, "%Y.%m.%d").date(),
        date_to=dt.datetime.strptime(d1, "%Y.%m.%d").date(),
        inputs=inputs, deposit=deposit, history_quality=history_quality, ticks=ticks,
    )


def _is_order_row(r: list) -> bool:
    # [abertura, nº, ativo, tipo, 'vol / vol', (preço), (S/L), (T/P), horário, estado, comentário]
    return (
        len(r) >= 9 and isinstance(r[0], str) and _TIME_RE.match(r[0])
        and isinstance(r[1], (int, float)) and isinstance(r[3], str) and _ORDER_TYPE_RE.match(r[3])
        and isinstance(r[4], str) and "/" in r[4]
    )


def _is_deal_row(r: list) -> bool:
    # [hora, nº, ativo, tipo, direção, volume, preço, ordem, comissão, swap, lucro, saldo, comentário]
    return (
        len(r) >= 12 and isinstance(r[0], str) and _TIME_RE.match(r[0])
        and r[3] in ("buy", "sell") and r[4] in ("in", "out", "in/out")
        and isinstance(r[5], (int, float, str))
    )


def _order_sl_tp(raw: tuple | list) -> tuple[float | None, float | None]:
    """S/L e T/P de uma linha da tabela Ordens, lidos por posição RELATIVA: são as duas últimas
    células antes do 2º horário da linha (o "Horário" de execução). Em branco => None. Funciona
    igual com colunas mescladas. Um T/P vazio não desloca o S/L."""
    vol = next(i for i, c in enumerate(raw) if isinstance(c, str) and "/" in c)
    end = next(i for i in range(vol + 1, len(raw)) if isinstance(raw[i], str) and _TIME_RE.match(raw[i]))
    seg = raw[vol + 1 : end]
    if len(seg) < 3:
        return None, None
    return _num(seg[-2]), _num(seg[-1])


def read_report(path: str | Path) -> Report:
    path = Path(path)
    raw_rows = _rows(path)
    rows = [_compact(r) for r in raw_rows]
    head = _parse_header(rows)

    orders: dict[int, tuple[float | None, float | None]] = {}
    deals = []
    for raw, r in zip(raw_rows, rows):
        if _is_order_row(r):
            # S/L e T/P só existem na ordem de entrada.
            orders[int(r[1])] = _order_sl_tp(raw)
        elif _is_deal_row(r):
            deals.append(r)

    warns: list[str] = []
    trades = _pair_deals(deals, orders, warns)
    if not trades:
        warns.append("nenhuma negociação fechada encontrada")
    return Report(source=str(path), trades=trades, warnings=warns, **head)


def _pair_deals(deals, orders, warns) -> list[Trade]:
    """Pareia 'in' com 'out' por ativo, FIFO (exato para EA com uma posição por vez e a ordem
    padrão do MT5 de fechamento nos demais casos). As colunas numéricas após o volume são:
    preço, ordem, comissão, swap, lucro, saldo."""
    open_by_symbol: dict[str, list[dict]] = {}
    trades: list[Trade] = []
    for r in deals:
        t = dt.datetime.strptime(r[0], _TIME_FMT)
        symbol, kind, direction = r[2], r[3], r[4]
        vol = _num(r[5])
        price, order_no, comm, swap, profit = (_num(x) for x in r[6:11])
        if direction == "in":
            sl, tp = orders.get(int(order_no), (None, None)) if order_no is not None else (None, None)
            open_by_symbol.setdefault(symbol, []).append(
                dict(t=t, kind=kind, vol=vol, price=price, comm=comm or 0.0, sl=sl, tp=tp)
            )
        else:  # out
            queue = open_by_symbol.get(symbol) or []
            if not queue:
                warns.append(f"saída sem entrada correspondente em {symbol} às {r[0]}")
                continue
            o = queue.pop(0)
            if abs(o["vol"] - vol) > 1e-9:
                warns.append(f"volume de entrada {o['vol']} != saída {vol} em {symbol} às {r[0]} (fechamento parcial não tratado)")
            comment = r[-1] if isinstance(r[-1], str) else ""
            reason = "sl" if comment.startswith("sl ") else "tp" if comment.startswith("tp ") else "other"
            trades.append(Trade(
                symbol=symbol, direction=o["kind"], volume=vol,
                open_time=o["t"], close_time=t, open_price=o["price"], close_price=price,
                profit=profit or 0.0, swap=swap or 0.0, commission=(comm or 0.0) + o["comm"],
                exit_reason=reason, sl0=o["sl"], tp0=o["tp"],
            ))
    for sym, q in open_by_symbol.items():
        if q:
            warns.append(f"{len(q)} posição(ões) ainda aberta(s) em {sym} no fim do relatório (ignorada(s))")
    return trades
