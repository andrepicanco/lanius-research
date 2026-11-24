"""BreakoutDonchian1: rompimento do canal Donchian de N barras (fechamento alem do nivel + buffer em ATR),
a mercado na abertura da barra seguinte. Transcricao do BreakoutDonchian1.mq5 / BreakoutSignal.mqh:

  - barra de sinal = shift 1 (fechada); o canal sao as N barras ANTES dela (ela nao entra no proprio nivel);
  - compra: fechamento > maxima do canal + buffer*ATR; venda: o espelho. Filtros opcionais: direcao,
    SMA no TF do EA, SMA em TF maior (ultima barra FECHADA dele), volume de ticks, horario, dia, spread, cooldown;
  - entra a mercado (compra no ask, venda no bid) com SL = ATR x mult e TP opcional = R x SL;
  - trailing por ATR (so aperta), uma vez por barra nova.

Limite conhecido: o filtro de volume de ticks usa o tick_volume REAL das barras do MT5; no Tester "Cada tick"
os ticks sao gerados, entao esse filtro nao e reproduzivel. Fica desligado por padrao no EA.
"""

import numpy as np

from . import indicators as ind
from .core import Market, hour_of, lot_size, money, run_position, swap_money, weekday_of
from .inputs import SIZING, TRADE_FILTERS, allowed_day, allowed_hour, parse
from .types import SimTrade

SCHEMA = {
    "InpTimeframe": (int, 0), "InpDonchianPeriod": (int, 20), "InpBufferATRMultiplier": (float, 0.1),
    "InpAllowLong": (bool, True), "InpAllowShort": (bool, True),
    "InpUseSMAFilter": (bool, False), "InpSMAFilterPeriod": (int, 200),
    "InpUseHTFFilter": (bool, False), "InpHTFTimeframe": (int, 16392), "InpHTFSMAPeriod": (int, 50),
    "InpUseTickVolFilter": (bool, False), "InpTickVolMAPeriod": (int, 20), "InpTickVolMinRatio": (float, 1.2),
    "InpCooldownBars": (int, 0),
    "InpSLATRPeriod": (int, 14), "InpSLATRMultiplier": (float, 2.0), "InpTP_RMultiple": (float, 0.0),
    "InpUseTrailing": (bool, True), "InpTrailATRPeriod": (int, 14), "InpTrailATRMultiplier": (float, 3.0),
    **SIZING, **TRADE_FILTERS,
}


def warmup_bars(p: dict) -> int:
    n = max(p["InpDonchianPeriod"], p["InpSLATRPeriod"], p["InpTrailATRPeriod"],
            p["InpSMAFilterPeriod"] if p["InpUseSMAFilter"] else 0, p["InpTickVolMAPeriod"])
    return 3 * n + 50


def htf_sma_at(htf_T: np.ndarray, htf_C: np.ndarray, htf_period_s: int, period: int, T: np.ndarray) -> np.ndarray:
    """SMA da ultima barra FECHADA do TF maior, vista em cada abertura de barra T (NaN se ainda nao ha)."""
    sma = ind.sma(htf_C, period)
    idx = np.searchsorted(htf_T + htf_period_s, T, side="right") - 1      # barra cujo fechamento <= T
    out = np.full(len(T), np.nan)
    ok = idx >= 0
    out[ok] = sma[idx[ok]]
    return out


def simulate(inputs: dict, m: Market, deposit: float, i0: int, htf: tuple | None = None) -> list[SimTrade]:
    """`htf` = (tempos, fechamentos, periodo em segundos) do TF maior, so quando o filtro HTF esta ligado."""
    p = parse(inputs, SCHEMA)
    c, h, l = m.wC, m.wH, m.wL
    n = p["InpDonchianPeriod"]
    atr_sig = ind.atr(h, l, c, p["InpSLATRPeriod"])
    trail = ind.atr(h, l, c, p["InpTrailATRPeriod"]) if p["InpUseTrailing"] else None
    hh, ll = ind.rolling_max_before(h, n), ind.rolling_min_before(l, n)
    with np.errstate(invalid="ignore"):
        buf = p["InpBufferATRMultiplier"] * atr_sig
        long_ = (c > hh + buf) & p["InpAllowLong"]
        short = (c < ll - buf) & p["InpAllowShort"] & ~long_
        if p["InpUseSMAFilter"]:
            s = ind.sma(c, p["InpSMAFilterPeriod"])
            long_ &= c > s
            short &= c < s
        if p["InpUseHTFFilter"]:
            if htf is None:
                raise ValueError("filtro HTF ligado mas sem barras do timeframe maior")
            # O EA le o shift 1 do TF maior NA ABERTURA da barra i, para o sinal da barra i-1: o valor
            # visto na abertura de b+1 vale para a barra de sinal b.
            hs_open = htf_sma_at(htf[0], htf[1], htf[2], p["InpHTFSMAPeriod"], m.wT)
            hs_sig = np.append(hs_open[1:], np.nan)
            long_ &= c > hs_sig
            short &= c < hs_sig
        if p["InpUseTickVolFilter"]:
            mv = p["InpTickVolMAPeriod"]
            avg = np.full(m.n, np.nan)
            if m.n > mv:
                cs = np.cumsum(np.insert(m.wV, 0, 0.0))
                avg[mv:] = (cs[mv:m.n] - cs[:m.n - mv]) / mv      # media das mv barras ANTES de b
            ok = m.wV >= p["InpTickVolMinRatio"] * avg
            long_ &= ok
            short &= ok
        long_ &= ~np.isnan(atr_sig) & ~np.isnan(hh)
        short &= ~np.isnan(atr_sig) & ~np.isnan(ll)

    spec, cool = m.spec, p["InpCooldownBars"]
    out: list[SimTrade] = []
    bal, i, last_exit = deposit, max(i0, n + 2), None
    while i < m.n:
        b = i - 1
        if not (long_[b] or short[b]) or m.lo[i] >= m.hi[i]:
            i += 1
            continue
        t = int(m.wT[i])
        if p["InpUseTradingHoursFilter"] and not allowed_hour(p, hour_of(t)):
            i += 1
            continue
        if p["InpUseTradingDaysFilter"] and not allowed_day(p, weekday_of(t)):
            i += 1
            continue
        if cool > 0 and last_exit is not None and i - cool >= 0 and last_exit >= m.wT[i - cool]:
            i += 1
            continue
        side = 1 if long_[b] else -1
        k0 = m.lo[i]
        if p["InpMaxSpreadPoints"] > 0 and m.sSp[k0] / spec.point > p["InpMaxSpreadPoints"]:
            i += 1
            continue
        sl_dist = atr_sig[b] * p["InpSLATRMultiplier"]
        tp_dist = p["InpTP_RMultiple"] * sl_dist if p["InpTP_RMultiple"] > 0 else 0.0
        if m.min_dist and (sl_dist < m.min_dist or (tp_dist and tp_dist < m.min_dist)):
            i += 1
            continue
        lots = lot_size(spec, bal, p["InpRiskPercent"], sl_dist, p["InpUseFixedLot"], p["InpFixedLot"])
        ref = m.sO[k0] + m.sSp[k0] if side > 0 else m.sO[k0]      # compra no ask, venda no bid
        sl = m.rnd(ref - side * sl_dist)
        tp = m.rnd(ref + side * tp_dist) if tp_dist else 0.0
        ex = run_position(m, side, i, k0, ref, sl, tp, trail, p["InpTrailATRMultiplier"], stop_fill=False)
        if ex is None:
            break
        t_in, t_out = int(m.sT[k0]), int(m.sT[ex.k])
        pnl = money(spec, side, ref, ex.price, lots)
        swp = swap_money(spec, side, t_in, t_out, lots)
        bal += pnl + swp
        out.append(SimTrade(side, t_in, t_out, ref, ex.price, lots, sl, tp, ex.reason, pnl, swp))
        last_exit = t_out
        i = ex.j + 1
    return out
