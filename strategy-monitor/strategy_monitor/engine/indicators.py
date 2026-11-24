"""Indicadores com a mesma definicao dos builtins do MT5 (iMA, iRSI, iATR), em numpy.

Convencao: arrays do tamanho da serie; posicoes sem valor definido ficam NaN.
  - EMA: alfa = 2/(n+1), semente = primeiro preco (MovingAverage.mq5).
  - SMA: media simples dos ultimos n.
  - RSI: Wilder (suavizacao RMA); primeira media = media simples das n primeiras variacoes.
  - ATR: media SIMPLES do true range (ATR.mq5 do MT5), nao Wilder. Ver `atr(..., wilder=True)` para o outro.
"""

import numpy as np


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    cs = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    a = 2.0 / (n + 1)
    out = np.empty(len(x))
    prev = out[0] = x[0]
    for i in range(1, len(x)):
        prev = a * x[i] + (1.0 - a) * prev
        out[i] = prev
    return out


def rsi(close: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return out
    d = np.diff(close)
    gain, loss = np.maximum(d, 0.0), np.maximum(-d, 0.0)
    ag, al = gain[:n].mean(), loss[:n].mean()
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + gain[i]) / n
        al = (al * (n - 1) + loss[i]) / n
        out[i + 1] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    tr = np.empty(len(close))
    tr[0] = high[0] - low[0]
    pc = close[:-1]
    tr[1:] = np.maximum(high[1:], pc) - np.minimum(low[1:], pc)
    return tr


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int, wilder: bool = False) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return out
    tr = true_range(high, low, close)
    if not wilder:
        out[n:] = sma(tr[1:], n)[n - 1:]        # media de TR[i-n+1..i], i >= n
        return out
    prev = out[n] = tr[1:n + 1].mean()
    for i in range(n + 1, len(close)):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def rolling_max_before(x: np.ndarray, n: int) -> np.ndarray:
    """out[b] = max(x[b-n .. b-1]): as n barras ANTES de b (a barra b nao entra)."""
    out = np.full(len(x), np.nan)
    if len(x) > n:
        w = np.lib.stride_tricks.sliding_window_view(x, n).max(axis=1)   # w[s] = max(x[s..s+n-1])
        out[n:] = w[:len(x) - n]
    return out


def rolling_min_before(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) > n:
        w = np.lib.stride_tricks.sliding_window_view(x, n).min(axis=1)
        out[n:] = w[:len(x) - n]
    return out
