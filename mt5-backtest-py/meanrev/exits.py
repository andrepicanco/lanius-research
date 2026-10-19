"""Exit rules, as a single declarative config the engine evaluates per bar.

Notebook 01 only ever tested "close after N bars", which is also the one thing the EA
does by default. The point of collecting the alternatives here is to answer whether
that fixed holding period is worth keeping, by running every rule against the *same*
signals so the exit is the only thing that varies.

Rules marked "not in the EA" below would need MQL5 changes before a favourable result
could be traded - the notebook flags that on every comparison.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitRules:
    """Any combination may be active; the first one to trigger on a bar wins.

    Within a bar the order is: stop loss, take profit, then the close-evaluated rules.
    Stop before target is the conservative reading when a single bar's range spans both
    and we cannot see the path inside it - the engine counts those bars so you know how
    much of the result rests on the assumption.
    """

    # In the EA today
    time_bars: int | None = None          # InpExitBars
    atr_sl_mult: float | None = None      # InpSLATRMultiplier
    atr_tp_r: float | None = None         # InpTP_RMultiple, in units of the SL distance
    atr_trailing_mult: float | None = None  # InpUseTrailing / InpTrailATRMultiplier

    # Not in the EA - research only
    rsi_revert_level: float | None = None  # exit when RSI crosses back through this
    first_profitable_close: bool = False   # Connors-style
    sma_touch: bool = False                # exit when price reaches the short SMA

    def __post_init__(self):
        if self.atr_tp_r is not None and self.atr_sl_mult is None:
            raise ValueError("atr_tp_r is a multiple of the SL distance, so atr_sl_mult is required")
        if self.atr_trailing_mult is not None and self.atr_sl_mult is None:
            raise ValueError("atr_trailing_mult needs an initial stop to tighten; set atr_sl_mult")
        if not self.any_active():
            raise ValueError(
                "no exit rule is active - a position would never be closed. Set at least "
                "one of time_bars, atr_sl_mult, rsi_revert_level, first_profitable_close, "
                "sma_touch."
            )

    def any_active(self) -> bool:
        return any(
            [
                self.time_bars is not None,
                self.atr_sl_mult is not None,
                self.rsi_revert_level is not None,
                self.first_profitable_close,
                self.sma_touch,
            ]
        )

    @property
    def needs_ea_change(self) -> bool:
        """True when the combination cannot be run by MeanRev1.mq5 as it stands today."""
        return self.rsi_revert_level is not None or self.first_profitable_close or self.sma_touch

    def describe(self) -> str:
        parts = []
        if self.time_bars is not None:
            parts.append(f"time({self.time_bars})")
        if self.atr_sl_mult is not None:
            parts.append(f"sl({self.atr_sl_mult}xATR)")
        if self.atr_tp_r is not None:
            parts.append(f"tp({self.atr_tp_r}R)")
        if self.atr_trailing_mult is not None:
            parts.append(f"trail({self.atr_trailing_mult}xATR)")
        if self.rsi_revert_level is not None:
            parts.append(f"rsi_revert({self.rsi_revert_level})")
        if self.first_profitable_close:
            parts.append("first_profitable_close")
        if self.sma_touch:
            parts.append("sma_touch")
        return " + ".join(parts) + (" [needs EA change]" if self.needs_ea_change else "")


TIME_ONLY_6 = ExitRules(time_bars=6)
EA_DEFAULT = ExitRules(time_bars=6, atr_sl_mult=2.0, atr_tp_r=2.0)
