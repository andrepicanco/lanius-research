"""Métricas de um conjunto de trades. Tudo em resultado LÍQUIDO (lucro + swap + comissão).

Breakeven (ponto de equilíbrio) do win rate, pelo payoff realizado:

    BE = perda_média / (ganho_médio + perda_média)

Derivação: com win rate p, ganho médio W e perda média L (ambos positivos), a expectativa por
trade é p*W - (1-p)*L. Ela zera em p = L / (W + L). Para RR fixo (W = RR*L) isto vira o clássico
1/(1+RR), mas os EAs do Lanius usam trailing ATR e saída por tempo, então o RR *realizado* é
quem manda e não o configurado. Por isso o BE é calculado dos próprios trades.

Consequência a ter em mente: o BE muda junto com o win rate quando o trailing corta ganhos ou
perdas, então "win rate > BE" é o mesmo que "expectativa > 0" medida em dinheiro. A folga
(win rate - BE) é a mesma informação de PF > 1, só que na escala do win rate, onde o André
pensa.
"""

import datetime as dt
import math
import statistics
from dataclasses import dataclass

from .models import Trade

# z para os níveis de confiança usados (distribuição normal padrão).
_Z = {0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}


def wilson_interval(wins: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Intervalo de Wilson para uma proporção. Melhor que o normal com n pequeno ou p perto de
    0/1, que é o caso aqui (dezenas de trades)."""
    if n == 0:
        return (0.0, 1.0)
    z = _Z.get(confidence)
    if z is None:
        raise ValueError(f"confiança não suportada: {confidence} (use {sorted(_Z)})")
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def estimate_value_per_point(trades: list[Trade]) -> tuple[float | None, float | None]:
    """Dinheiro por unidade de preço por lote (k), estimado dos próprios trades:
    profit = (preço_saída - preço_entrada) * sinal * volume * k. A mediana entre trades dá k sem
    precisar de tick_value/tick_size do símbolo (que o MT5 devolve 0 quando não consegue
    converter a moeda). Retorna (k, dispersão relativa p10-p90 / mediana) ou (None, None)."""
    ks = []
    for t in trades:
        sign = 1 if t.direction == "buy" else -1
        move = sign * (t.close_price - t.open_price) * t.volume
        if abs(t.profit) >= 1.0 and abs(move) > 0 and (t.profit / move) > 0:
            ks.append(t.profit / move)
    if len(ks) < 5:
        return None, None
    ks.sort()
    med = statistics.median(ks)
    p10, p90 = ks[int(0.1 * (len(ks) - 1))], ks[int(0.9 * (len(ks) - 1))]
    return med, (p90 - p10) / med


def attach_r_multiples(trades: list[Trade]) -> dict:
    """Preenche Trade.r_multiple = líquido / risco inicial em dinheiro, com
    risco = |entrada - SL inicial| * volume * k. Trades sem SL inicial ficam com None.
    Devolve diagnóstico: k, dispersão de k e quantos trades ficaram sem R."""
    k, disp = estimate_value_per_point(trades)
    missing = 0
    for t in trades:
        t.r_multiple = None
        if k is None or t.sl0 is None or t.sl0 == 0:
            missing += 1
            continue
        risk = abs(t.open_price - t.sl0) * t.volume * k
        if risk <= 0:
            missing += 1
            continue
        t.r_multiple = t.net / risk
    return {"value_per_point": k, "k_dispersion": disp, "trades_without_r": missing}


@dataclass
class Metrics:
    n: int
    wins: int
    losses: int
    win_rate: float | None
    avg_win: float | None          # dinheiro, positivo
    avg_loss: float | None         # dinheiro, positivo
    breakeven_wr: float | None
    wr_margin: float | None        # win_rate - breakeven_wr (pontos percentuais / 100)
    wr_ci: tuple[float, float] | None
    net: float
    profit_factor: float | None
    expectancy_money: float | None
    expectancy_r: float | None
    n_with_r: int
    max_dd: float                  # dinheiro, sobre o saldo de trades fechados
    max_dd_pct: float              # % do pico de saldo no ponto do drawdown
    first: dt.datetime | None
    last: dt.datetime | None
    trades_per_month: float | None


def compute_metrics(trades: list[Trade], deposit: float | None, confidence: float = 0.95) -> Metrics:
    ts = sorted(trades, key=lambda t: t.close_time)
    n = len(ts)
    wins = [t.net for t in ts if t.net > 0]
    losses = [-t.net for t in ts if t.net < 0]
    win_rate = len(wins) / n if n else None
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    be = (avg_loss / (avg_win + avg_loss)) if (avg_win is not None and avg_loss is not None) else None
    margin = (win_rate - be) if (win_rate is not None and be is not None) else None
    ci = wilson_interval(len(wins), n, confidence) if n else None
    pf = (sum(wins) / sum(losses)) if losses and wins else None
    net = sum(t.net for t in ts)

    rs = [t.r_multiple for t in ts if t.r_multiple is not None]

    # Drawdown sobre o saldo de trades fechados (equity flutuante não está no relatório de deals).
    bal = deposit if deposit is not None else 0.0
    peak, max_dd, max_dd_pct = bal, 0.0, 0.0
    for t in ts:
        bal += t.net
        if bal > peak:
            peak = bal
        dd = peak - bal
        if dd > max_dd:
            max_dd, max_dd_pct = dd, (dd / peak * 100 if peak > 0 else 0.0)

    first = min((t.open_time for t in ts), default=None)
    last = max((t.close_time for t in ts), default=None)
    tpm = None
    if first and last and (last - first).days >= 30:
        tpm = n / ((last - first).days / 30.4375)

    return Metrics(
        n=n, wins=len(wins), losses=len(losses), win_rate=win_rate, avg_win=avg_win,
        avg_loss=avg_loss, breakeven_wr=be, wr_margin=margin, wr_ci=ci, net=net,
        profit_factor=pf, expectancy_money=(net / n if n else None),
        expectancy_r=(sum(rs) / len(rs) if rs else None), n_with_r=len(rs),
        max_dd=max_dd, max_dd_pct=max_dd_pct, first=first, last=last, trades_per_month=tpm,
    )
