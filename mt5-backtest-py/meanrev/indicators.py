"""Indicator primitives, extracted as pure functions from notebook 01.

They are kept deliberately literal to the notebook's formulas: the parity test in
tests/test_parity_nb01.py only means something if these reproduce the notebook exactly.
Where the notebook and MQL5 agree (RSI with Wilder smoothing, ATR), the docstring says
so, because a silent divergence between research and the EA is the expensive kind.
"""

import numpy as np
import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def rsi_wilder(close: pd.Series, period: int) -> pd.Series:
    """RSI with Wilder smoothing - `ewm(alpha=1/period, adjust=False)`, not a simple
    rolling mean. At period=2 the two differ enough to change which bars are flagged,
    and MQL5's native iRSI() uses Wilder, so this is the one that matches the EA.

    Seeding caveat: `ewm` starts from the first available delta, while MQL5's iRSI
    seeds from a simple average of the first `period` deltas. The two differ only over
    the warmup and the gap decays geometrically - at period 2 it is gone within a
    handful of bars - but it is why the first few RSI values here are not identical to
    the terminal's.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # min_periods: `ewm` alone happily returns a value from the very first observation,
    # so an RSI configured for 14 periods would be "ready" after one bar. MQL5's iRSI
    # publishes nothing until it has `period` bars, and a stop or a signal derived from
    # a half-formed indicator is worse than no signal.
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ATR with Wilder smoothing, matching MQL5's iATR().

    Like rsi_wilder, it stays NaN until `period` bars exist rather than reporting a
    value derived from fewer - which is what lets the engine decline a trade whose stop
    distance would otherwise come from an indicator that has not warmed up.
    """
    return true_range(high, low, close).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def zone_streak(in_zone: pd.Series) -> pd.Series:
    """Counts consecutive bars the flag stays true, resetting to 0 the moment it drops.

    This is notebook 01's `groupby(run_id).cumsum()`, which is also what the EA's
    backward walk from shift 1 computes. The value is the *length so far*, so a signal
    that fires on `streak == N` fires exactly once per run - see signals.py.
    """
    flag = in_zone.astype(int)
    run_id = (flag != flag.shift()).cumsum()
    return flag.groupby(run_id).cumsum()


def ou_half_life(close: pd.Series) -> float:
    """Ornstein-Uhlenbeck half-life of mean reversion, in bars.

    Regresses the bar-to-bar change on the prior level; a negative slope means the
    series pulls back toward its mean, and the half-life says how fast. Returns inf
    when the slope is non-negative (no reversion to measure). This is a property of the
    instrument, independent of any parameter we might fit, which is why section 2 of
    the notebook uses it to pick assets before optimizing anything.
    """
    y = np.log(close.dropna())
    lagged = y.shift(1).dropna()
    delta = (y - y.shift(1)).dropna()
    lagged, delta = lagged.align(delta, join="inner")
    if len(lagged) < 3:
        return float("inf")

    slope = np.polyfit(lagged.values, delta.values, 1)[0]
    if slope >= 0:
        return float("inf")
    return float(-np.log(2) / slope)


def hurst_exponent(close: pd.Series, max_lag: int = 100) -> float:
    """Hurst exponent via the variance of lagged differences.

    H < 0.5 mean-reverting, H = 0.5 random walk, H > 0.5 trending. Estimated on log
    prices so the result does not depend on the instrument's price level.
    """
    y = np.log(close.dropna()).values
    max_lag = min(max_lag, len(y) // 2)
    if max_lag < 3:
        return float("nan")

    lags = np.arange(2, max_lag)
    tau = [np.std(y[lag:] - y[:-lag]) for lag in lags]
    tau = np.asarray(tau)
    valid = tau > 0
    if valid.sum() < 3:
        return float("nan")

    return float(np.polyfit(np.log(lags[valid]), np.log(tau[valid]), 1)[0])


def variance_ratio(close: pd.Series, lag: int = 5) -> float:
    """Lo-MacKinlay variance ratio: Var(q-bar return)/(q * Var(1-bar return)).

    1.0 is a random walk, below 1.0 is mean reversion, above is trending. Complements
    the Hurst estimate at one specific horizon - useful because a strategy holding for
    `exit_bars` cares about reversion at that horizon, not in general.
    """
    r = np.log(close.dropna()).diff().dropna()
    if len(r) <= lag or r.var() == 0:
        return float("nan")

    q_returns = r.rolling(lag).sum().dropna()
    return float(q_returns.var() / (lag * r.var()))
