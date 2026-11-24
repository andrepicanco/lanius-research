"""Nucleo do simulador: dados alinhados (barras do EA + sub-barras), dimensionamento de lote, swap e a
simulacao de UMA posicao aberta (SL, TP e trailing por ATR), identica para os dois EAs.

Modelo de precos (verificavel contra os baselines com `parity`):
  - as barras do MT5 sao de BID; o ask = bid + spread da propria barra;
  - compra: entra no ask, sai no bid. SL/TP de compra disparam pelo bid (minima/maxima);
  - venda: entra no bid, sai no ask. SL/TP de venda disparam pelo ask (minima/maxima + spread);
  - quando SL e TP caem na MESMA sub-barra a ordem e desconhecida: vale o SL ("pessimista");
  - gap: se a sub-barra ABRE alem do SL, a saida e na abertura (pior que o SL); no TP, idem (melhor).
"""

import math
from dataclasses import dataclass

import numpy as np

from .mt5data import SymbolSpec

SL, TP = "sl", "tp"


@dataclass
class Market:
    """Barras do EA (`w*`) e sub-barras (`s*`) alinhadas: a barra j do EA contem as sub-barras lo[j]..hi[j]-1."""
    spec: SymbolSpec
    period_s: int
    wT: np.ndarray
    wO: np.ndarray
    wH: np.ndarray
    wL: np.ndarray
    wC: np.ndarray
    wV: np.ndarray
    sT: np.ndarray
    sO: np.ndarray
    sH: np.ndarray
    sL: np.ndarray
    sC: np.ndarray
    sSp: np.ndarray        # spread em unidades de preco
    lo: np.ndarray
    hi: np.ndarray

    @classmethod
    def build(cls, spec: SymbolSpec, work: np.ndarray, sub: np.ndarray, period_s: int) -> "Market":
        sT = sub["time"]
        return cls(spec, period_s, work["time"], work["open"], work["high"], work["low"], work["close"],
                   work["tick_volume"].astype(float), sT, sub["open"], sub["high"], sub["low"],
                   sub["close"], sub["spread"] * spec.point, np.searchsorted(sT, work["time"], "left"),
                   np.searchsorted(sT, work["time"] + period_s, "left"))

    @property
    def n(self) -> int:
        return len(self.wT)

    def rnd(self, price: float) -> float:
        ts = self.spec.tick_size
        return round(round(price / ts) * ts, self.spec.digits) if ts > 0 else round(price, self.spec.digits)

    @property
    def min_dist(self) -> float:
        return (self.spec.stops_level + 1) * self.spec.point if self.spec.stops_level > 0 else 0.0


def lot_size(spec: SymbolSpec, balance: float, risk_pct: float, sl_dist: float,
             use_fixed: bool, fixed_lot: float) -> float:
    """Mesma conta do RiskManager.mqh (arredonda PARA BAIXO no passo, depois limita a [min, max])."""
    def norm(v: float) -> float:
        if spec.volume_step > 0:
            v = math.floor(v / spec.volume_step + 1e-9) * spec.volume_step
        return round(min(max(v, spec.volume_min), spec.volume_max), 8)
    if use_fixed:
        return norm(fixed_lot)
    if sl_dist <= 0 or spec.tick_value <= 0 or spec.tick_size <= 0:
        return spec.volume_min
    per_lot = sl_dist / spec.tick_size * spec.tick_value
    return norm(balance * risk_pct / 100.0 / per_lot)


def money(spec: SymbolSpec, side: int, entry: float, exit_: float, lots: float) -> float:
    return side * (exit_ - entry) / spec.tick_size * spec.tick_value * lots


def swap_money(spec: SymbolSpec, side: int, t_in: int, t_out: int, lots: float) -> float:
    """Swap em pontos por noite (swap_mode = 1). Cada virada de dia do servidor entre a entrada e a saida
    conta uma noite; a do dia `swap_rollover3days` conta tres. Outros modos: nao modelados (0)."""
    if spec.swap_mode != 1:
        return 0.0
    pts = spec.swap_long if side > 0 else spec.swap_short
    d0, d1 = t_in // 86400, t_out // 86400
    nights = 0
    for d in range(d0, d1):                       # virada do dia d para d+1
        nights += 3 if ((d + 4) % 7) == spec.swap_rollover3days else 1
    return pts * spec.point / spec.tick_size * spec.tick_value * lots * nights


def hour_of(t: int) -> int:
    return (t // 3600) % 24


def weekday_of(t: int) -> int:
    """0 = domingo ... 6 = sabado (como MqlDateTime.day_of_week)."""
    return (t // 86400 + 4) % 7


@dataclass
class Exit:
    j: int                 # barra do EA em que saiu
    k: int                 # sub-barra
    price: float
    reason: str
    sl_final: float


def run_position(m: Market, side: int, j0: int, k0: int, entry: float, sl: float, tp: float,
                 trail_atr: np.ndarray | None, trail_mult: float, stop_fill: bool) -> Exit | None:
    """Acompanha a posicao a partir da sub-barra k0 da barra j0 ate sair. `trail_atr[b]` e o ATR da barra
    fechada b (o EA le o shift 1 na abertura da barra j: trail_atr[j-1]). `stop_fill`: a posicao nasceu de
    uma ordem stop DENTRO da sub-barra k0 (sem gap nessa sub-barra). Devolve None se nao sair ate o fim
    dos dados."""
    sp, md = m.sSp, m.min_dist
    for j in range(j0, m.n):
        if j > j0 and trail_atr is not None and m.lo[j] < m.hi[j]:
            a = trail_atr[j - 1]
            if a == a and a > 0:                  # nao-NaN
                k = m.lo[j]
                if side > 0:
                    price = m.sO[k]               # bid
                    new = m.rnd(price - a * trail_mult)
                    if new > sl and price - new >= md:
                        sl = new
                else:
                    price = m.sO[k] + sp[k]       # ask
                    new = m.rnd(price + a * trail_mult)
                    if new < sl and new - price >= md:
                        sl = new
        ks = k0 if j == j0 else m.lo[j]
        ke = m.hi[j]
        if ks >= ke:
            continue
        if side > 0:
            sl_hit = m.sL[ks:ke] <= sl
            tp_hit = (m.sH[ks:ke] >= tp) if tp > 0 else None
        else:
            sl_hit = m.sH[ks:ke] + sp[ks:ke] >= sl
            tp_hit = (m.sL[ks:ke] + sp[ks:ke] <= tp) if tp > 0 else None
        i_sl = int(sl_hit.argmax()) if sl_hit.any() else None
        i_tp = int(tp_hit.argmax()) if tp_hit is not None and tp_hit.any() else None
        if i_sl is None and i_tp is None:
            continue
        use_sl = i_tp is None or (i_sl is not None and i_sl <= i_tp)
        k = ks + (i_sl if use_sl else i_tp)
        gap_ok = not (stop_fill and j == j0 and k == k0)
        o = m.sO[k] if side > 0 else m.sO[k] + sp[k]
        if use_sl:
            px = (min(sl, o) if side > 0 else max(sl, o)) if gap_ok else sl
            return Exit(j, k, px, SL, sl)
        px = (max(tp, o) if side > 0 else min(tp, o)) if gap_ok else tp
        return Exit(j, k, px, TP, sl)
    # Ainda aberta no fim dos dados: o Tester fecha no ultimo preco do teste (visto nos runs de 23/11, motivo
    # "other" as 23:49:59 de sexta); o motor faz o mesmo, para a janela nova nao perder o trade em aberto.
    k = len(m.sT) - 1
    if k < k0:
        return None
    px = m.sC[k] if side > 0 else m.sC[k] + sp[k]
    return Exit(m.n - 1, k, px, "other", sl)
