"""Orquestra um backtest do motor Python e devolve um `Report` (o mesmo tipo que o leitor do Tester produz),
de modo que metricas, comparacao e descoberta funcionam sem mudanca.

    rep = run_engine(baseline, cache, inputs=None, from_date=..., until=...)
"""

import datetime as dt

import numpy as np

from ..models import Report, Trade
from . import breakout_donchian1, idxswing91
from .core import Market
from .inputs import parse
from .mt5data import TF_MIN, BarCache, DataError, epoch, from_epoch, tf_from_mt5_enum
from .types import SimTrade

STRATEGIES = {"IdxSwing91": idxswing91, "BreakoutDonchian1": breakout_donchian1}


def supported(expert: str) -> bool:
    return expert in STRATEGIES


def _to_trade(t: SimTrade, symbol: str) -> Trade:
    return Trade(symbol=symbol, direction="buy" if t.side > 0 else "sell", volume=t.lots,
                 open_time=from_epoch(t.t_in), close_time=from_epoch(t.t_out), open_price=t.price_in,
                 close_price=t.price_out, profit=t.profit, swap=t.swap, commission=0.0, exit_reason=t.reason,
                 sl0=t.sl0, tp0=t.tp0 or None)


def run_engine(base: Report, cache: BarCache, inputs: dict | None, from_date: dt.date, until: dt.date,
               deposit: float | None = None) -> Report:
    """Backtest de `base.expert` em `base.symbol` com os inputs do baseline (ou sobrepostos por `inputs`),
    de `from_date` ate `until` (exclusivo, como o Tester). Os indicadores aquecem com barras ANTERIORES a
    `from_date` (sao buscadas alem da janela), como o Tester faz."""
    mod = STRATEGIES.get(base.expert)
    if mod is None:
        raise DataError(f"o motor Python ainda nao implementa {base.expert} (so: {', '.join(STRATEGIES)})")
    use = {**base.inputs, **(inputs or {})}
    p = parse(use, mod.SCHEMA)
    work_tf = tf_from_mt5_enum(p["InpTimeframe"], base.timeframe)
    period_s = TF_MIN[work_tf] * 60
    start = dt.datetime.combine(from_date, dt.time.min)
    end = dt.datetime.combine(until, dt.time.min)
    warm_start = start - dt.timedelta(seconds=period_s * mod.warmup_bars(p) * 7 // 5 + 3 * 86400)

    sub_tf = cache.sub_tf(work_tf, start, end, base.symbol)
    spec = cache.spec(base.symbol)
    work = cache.get(base.symbol, work_tf, warm_start, end)
    sub = cache.get(base.symbol, sub_tf, start - dt.timedelta(days=2), end)
    if len(work) < 100 or len(sub) < 10:
        raise DataError(f"historico insuficiente de {base.symbol} {work_tf}/{sub_tf} ({len(work)}/{len(sub)} barras)")
    m = Market.build(spec, work, sub, period_s)
    i0 = int(np.searchsorted(m.wT, epoch(start), "left"))

    kw = {}
    if base.expert == "BreakoutDonchian1" and p["InpUseHTFFilter"]:
        htf_tf = tf_from_mt5_enum(p["InpHTFTimeframe"], work_tf)
        h = cache.get(base.symbol, htf_tf, warm_start - dt.timedelta(days=30), end)
        kw["htf"] = (h["time"], h["close"], TF_MIN[htf_tf] * 60)
    sims = mod.simulate(use, m, deposit or base.deposit or 10000.0, i0, **kw)
    trades = [_to_trade(t, base.symbol) for t in sims]
    return Report(source=f"engine:{work_tf}/{sub_tf}", expert=base.expert, symbol=base.symbol, timeframe=base.timeframe,
                  date_from=from_date, date_to=until, inputs=use, deposit=deposit or base.deposit,
                  history_quality=None, ticks=None, trades=trades,
                  warnings=[f"motor Python: barras {work_tf}, sub-barras {sub_tf} (aproximacao do Tester)"])
