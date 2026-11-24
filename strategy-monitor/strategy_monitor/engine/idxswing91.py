"""IdxSwing91: cruzamento do EMA sobre o fechamento + RSI + filtro SMA, confirmado por rompimento (ordem stop)
do extremo da barra de gatilho. Transcricao do IdxSwing91.mq5 / SwingSignal.mqh / TradeManager.mqh; os
numeros de linha abaixo referem-se a esses arquivos. O que o EA faz, na ordem:

  1. na ABERTURA de cada barra, se nao ha posicao nem ordem, avalia a barra fechada (shift 1) contra a anterior;
  2. compra: fechou acima do EMA (cruzou de baixo), RSI > nivel de compra e fechamento > SMA; venda, o espelho;
  3. coloca uma ordem stop no extremo da barra de gatilho (maxima na compra), SL no extremo oposto +/- buffer,
     TP = R x distancia; anula se o preco ja passou do nivel (gap) ou se violar a distancia minima do corretor;
  4. a ordem vale `InpTriggerValidBars` barras (a barra de colocacao inclusa); cancela na abertura da seguinte
     e SO reavalia sinal na barra depois dessa (o EA nao testa gatilho na barra do cancelamento);
  5. com posicao, o trailing por ATR so aperta o SL, uma vez por barra nova.
"""

import numpy as np

from . import indicators as ind
from .core import Market, hour_of, lot_size, money, run_position, swap_money, weekday_of
from .inputs import SIZING, TRADE_FILTERS, allowed_day, allowed_hour, parse
from .types import SimTrade

SCHEMA = {
    "InpTimeframe": (int, 0), "InpEMAPeriod": (int, 9), "InpSMAPeriod": (int, 21), "InpTriggerValidBars": (int, 3),
    "InpRSIPeriod": (int, 3), "InpRSIBuyLevel": (float, 70.0), "InpRSISellLevel": (float, 30.0),
    "InpSLBufferPoints": (int, 30), "InpTP_RMultiple": (float, 2.0),
    "InpUseTrailing": (bool, False), "InpATRPeriod": (int, 14), "InpATRMultiplier": (float, 2.0),
    **SIZING, **TRADE_FILTERS,
}


def warmup_bars(p: dict) -> int:
    return 3 * max(p["InpEMAPeriod"], p["InpSMAPeriod"], p["InpRSIPeriod"], p["InpATRPeriod"]) + 50


def simulate(inputs: dict, m: Market, deposit: float, i0: int) -> list[SimTrade]:
    p = parse(inputs, SCHEMA)
    c, h, l = m.wC, m.wH, m.wL
    e, s, r = ind.ema(c, p["InpEMAPeriod"]), ind.sma(c, p["InpSMAPeriod"]), ind.rsi(c, p["InpRSIPeriod"])
    trail = ind.atr(h, l, c, p["InpATRPeriod"]) if p["InpUseTrailing"] else None
    with np.errstate(invalid="ignore"):
        up = np.zeros(m.n, bool)
        dn = np.zeros(m.n, bool)
        up[1:] = (c[:-1] <= e[:-1]) & (c[1:] > e[1:])
        dn[1:] = (c[:-1] >= e[:-1]) & (c[1:] < e[1:])
        buy = up & (r > p["InpRSIBuyLevel"]) & (s < c)
        sell = dn & (r < p["InpRSISellLevel"]) & (s > c)

    spec, valid = m.spec, p["InpTriggerValidBars"]
    out: list[SimTrade] = []
    bal, i = deposit, max(i0, 2)
    while i < m.n:
        b = i - 1
        if not (buy[b] or sell[b]) or m.lo[i] >= m.hi[i]:
            i += 1
            continue
        t = int(m.wT[i])
        if p["InpUseTradingHoursFilter"] and not allowed_hour(p, hour_of(t)):
            i += 1
            continue
        if p["InpUseTradingDaysFilter"] and not allowed_day(p, weekday_of(t)):
            i += 1
            continue
        side = 1 if buy[b] else -1
        level = h[b] if side > 0 else l[b]
        sl_raw = (l[b] if side > 0 else h[b]) - side * p["InpSLBufferPoints"] * spec.point
        k_now = m.lo[i]
        ask_now, bid_now = m.sO[k_now] + m.sSp[k_now], m.sO[k_now]
        if (side > 0 and ask_now >= level) or (side < 0 and bid_now <= level):   # gap: nivel ja ultrapassado
            i += 1
            continue
        if p["InpMaxSpreadPoints"] > 0 and m.sSp[k_now] / spec.point > p["InpMaxSpreadPoints"]:
            i += 1
            continue
        risk = abs(level - sl_raw)
        tp_raw = level + side * p["InpTP_RMultiple"] * risk
        entry, sl, tp = m.rnd(level), m.rnd(sl_raw), m.rnd(tp_raw)
        if m.min_dist and (abs(entry - sl) < m.min_dist or abs(tp - entry) < m.min_dist):
            i += 1
            continue
        lots = lot_size(spec, bal, p["InpRiskPercent"], risk, p["InpUseFixedLot"], p["InpFixedLot"])

        fill = None
        for j in range(i, min(i + valid, m.n)):          # a ordem vive nas barras i .. i+valid-1
            ks, ke = m.lo[j], m.hi[j]
            if ks >= ke:
                continue
            hit = (m.sH[ks:ke] + m.sSp[ks:ke] >= entry) if side > 0 else (m.sL[ks:ke] <= entry)
            if hit.any():
                fill = (j, ks + int(hit.argmax()))
                break
        if fill is None:
            i += valid + 1                                # cancela na barra i+valid; reavalia em i+valid+1
            continue
        j, k = fill
        open_px = m.sO[k] + m.sSp[k] if side > 0 else m.sO[k]
        gap = open_px >= entry if side > 0 else open_px <= entry
        price_in = open_px if gap else entry
        ex = run_position(m, side, j, k, price_in, sl, tp, trail, p["InpATRMultiplier"], stop_fill=not gap)
        if ex is None:
            break                                         # posicao ainda aberta no fim dos dados
        t_in, t_out = int(m.sT[k]), int(m.sT[ex.k])
        pnl = money(spec, side, price_in, ex.price, lots)
        swp = swap_money(spec, side, t_in, t_out, lots)
        bal += pnl + swp
        out.append(SimTrade(side, t_in, t_out, price_in, ex.price, lots, sl, tp, ex.reason, pnl, swp))
        i = ex.j + 1
    return out
