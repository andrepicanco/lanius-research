"""Entry-signal generation for the mean_rev_1 setup.

Mirrors the rule documented in strategies/mean_rev_1/PARAMETROS.md: the streak trigger
fires on the bar where the RSI's run inside the zone reaches *exactly* `streak`, and
only counts when it agrees with the SMA trend. Everything here is evaluated on closed
bars; the engine is what turns a signal into a fill on the following bar.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .indicators import rsi_wilder, sma, zone_streak


@dataclass(frozen=True)
class SignalParams:
    sma_short: int = 21
    sma_long: int = 88
    rsi_period: int = 2
    overbought: float = 80.0
    oversold: float = 20.0
    streak: int = 2

    def __post_init__(self):
        if self.sma_short >= self.sma_long:
            raise ValueError(f"sma_short ({self.sma_short}) must be < sma_long ({self.sma_long})")
        if not 0 <= self.oversold < self.overbought <= 100:
            raise ValueError(f"need 0 <= oversold < overbought <= 100, got {self.oversold}/{self.overbought}")
        if self.streak < 1:
            raise ValueError(f"streak must be >= 1, got {self.streak}")


def add_indicators(df: pd.DataFrame, params: SignalParams) -> pd.DataFrame:
    """Returns a copy with the indicator and streak columns the signal rule needs."""
    out = df.copy()
    out["sma_short"] = sma(out["close"], params.sma_short)
    out["sma_long"] = sma(out["close"], params.sma_long)
    out["rsi"] = rsi_wilder(out["close"], params.rsi_period)

    out["uptrend"] = out["sma_short"] > out["sma_long"]
    out["overbought"] = out["rsi"] > params.overbought
    out["oversold"] = out["rsi"] < params.oversold
    out["overbought_streak"] = zone_streak(out["overbought"])
    out["oversold_streak"] = zone_streak(out["oversold"])
    return out


def generate_signals(df: pd.DataFrame, params: SignalParams) -> pd.DataFrame:
    """One row per entry signal, indexed by the bar that produced it.

    A signal on bar i means "the EA saw this at the close of bar i"; the fill happens
    on bar i+1. Buying needs an oversold run in an uptrend, selling an overbought run
    in a downtrend - counter-trend combinations never fire, per the EA.
    """
    enriched = df if "oversold_streak" in df.columns else add_indicators(df, params)

    warmup = enriched[["sma_short", "sma_long", "rsi"]].notna().all(axis=1)
    buy = warmup & enriched["uptrend"] & (enriched["oversold_streak"] == params.streak)
    sell = warmup & ~enriched["uptrend"] & (enriched["overbought_streak"] == params.streak)

    signals = enriched.loc[buy | sell, ["time"]].copy()
    signals["direction"] = pd.Series("buy", index=signals.index).where(buy, "sell")
    signals["bar"] = signals.index
    return signals.reset_index(drop=True)[["bar", "time", "direction"]]
