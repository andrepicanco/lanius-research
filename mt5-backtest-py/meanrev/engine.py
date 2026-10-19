"""Event-driven backtest for the mean_rev_1 setup.

What it fixes relative to notebook 01's column arithmetic:
  * fills on the bar *after* the signal, which is when the EA can actually trade;
  * holds one position per symbol at a time, so overlapping windows stop being counted
    as simultaneous trades the account could never have taken;
  * charges the spread the terminal recorded, from the rates themselves;
  * simulates SL/TP against the bar's range instead of ignoring them.

Everything here works in price units. Converting to money needs the symbol's value per
point and a sizing rule, which live in metrics.py - keeping them out means the engine
can be tested without a symbol spec, and makes the money layer a visible step in the
notebook's gross-to-net waterfall rather than a hidden default.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .costs import FREE, CostModel
from .exits import ExitRules
from .indicators import atr
from .signals import SignalParams, add_indicators, generate_signals

_LEDGER_COLUMNS = [
    "symbol", "direction", "signal_time", "entry_time", "entry_price", "exit_time",
    "exit_price", "exit_reason", "bars_held", "sl_price", "tp_price", "gross_price",
    "spread_cost_price", "net_price", "gross_pct", "net_pct", "mae_price", "mfe_price",
    "entry_bar", "exit_bar", "risk_price",
]


@dataclass(frozen=True)
class ExecParams:
    """How a signal becomes a fill.

    The defaults describe the EA. The `signal_close` / `bar_close` pair reproduces
    notebook 01's assumption that you can transact at the close of the bar that
    generated the signal, which the parity check needs - and which is not tradeable.
    """

    entry_fill: str = "next_open"        # "next_open" | "signal_close"
    close_exit_fill: str = "next_open"   # "next_open" | "bar_close"
    allow_overlap: bool = False
    atr_period: int = 14

    def __post_init__(self):
        if self.entry_fill not in ("next_open", "signal_close"):
            raise ValueError(f"entry_fill must be next_open or signal_close, got {self.entry_fill!r}")
        if self.close_exit_fill not in ("next_open", "bar_close"):
            raise ValueError(f"close_exit_fill must be next_open or bar_close, got {self.close_exit_fill!r}")


NOTEBOOK_01 = ExecParams(entry_fill="signal_close", close_exit_fill="bar_close", allow_overlap=True)


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    signals_total: int
    signals_skipped_overlap: int
    ambiguous_bars: int = 0
    exit_rules: ExitRules | None = None
    exec_params: ExecParams | None = None
    meta: dict = field(default_factory=dict)

    @property
    def ambiguous_fraction(self) -> float:
        """Share of trades whose exit bar could have hit stop and target in either order.

        A high value means the stop-first assumption is carrying the result, and the
        comparison should not be trusted without intrabar data.
        """
        return self.ambiguous_bars / len(self.trades) if len(self.trades) else 0.0

    def summary(self) -> str:
        return (
            f"{len(self.trades)} trades from {self.signals_total} signals "
            f"({self.signals_skipped_overlap} skipped for overlap), "
            f"net {self.trades['net_pct'].sum():.2f}% summed, "
            f"{self.ambiguous_fraction:.1%} of exits ambiguous"
        )


def run_backtest(
    df: pd.DataFrame,
    symbol: str,
    signal_params: SignalParams,
    exit_rules: ExitRules,
    costs: CostModel = FREE,
    exec_params: ExecParams = ExecParams(),
    point: float | None = None,
) -> BacktestResult:
    from .data import point_size

    enriched = add_indicators(df, signal_params)
    enriched["atr"] = atr(enriched["high"], enriched["low"], enriched["close"], exec_params.atr_period)
    signals = generate_signals(enriched, signal_params)
    point = point if point is not None else point_size(df["close"])

    arrays = {
        name: enriched[name].to_numpy()
        for name in ("open", "high", "low", "close", "atr", "rsi", "sma_short")
    }
    spread = enriched["spread"].to_numpy(dtype=float)
    times = enriched["time"].to_numpy()
    n_bars = len(enriched)

    rows: list[dict] = []
    ambiguous = 0
    skipped = 0
    last_exit_bar = -1

    for signal in signals.itertuples(index=False):
        entered_at_open = exec_params.entry_fill == "next_open"
        entry_bar = signal.bar + 1 if entered_at_open else signal.bar
        if entry_bar >= n_bars:
            break
        if not exec_params.allow_overlap and entry_bar <= last_exit_bar:
            skipped += 1
            continue

        trade = _simulate_one(
            arrays=arrays, spread=spread, times=times, n_bars=n_bars,
            signal_bar=signal.bar, entry_bar=entry_bar, entered_at_open=entered_at_open,
            direction=signal.direction, rules=exit_rules, costs=costs,
            exec_params=exec_params, point=point, symbol=symbol,
        )
        if trade is None:
            continue

        ambiguous += trade.pop("_ambiguous")
        rows.append(trade)
        last_exit_bar = trade["exit_bar"]

    trades = pd.DataFrame(rows, columns=_LEDGER_COLUMNS)
    return BacktestResult(
        trades=trades,
        signals_total=len(signals),
        signals_skipped_overlap=skipped,
        ambiguous_bars=ambiguous,
        exit_rules=exit_rules,
        exec_params=exec_params,
        meta={"symbol": symbol, "point": point, "bars": n_bars},
    )


def _simulate_one(
    *, arrays, spread, times, n_bars, signal_bar, entry_bar, entered_at_open,
    direction, rules: ExitRules, costs: CostModel, exec_params: ExecParams, point, symbol,
) -> dict | None:
    op, high, low, close = arrays["open"], arrays["high"], arrays["low"], arrays["close"]
    rsi, sma_short, atr_series = arrays["rsi"], arrays["sma_short"], arrays["atr"]

    sign = 1.0 if direction == "buy" else -1.0
    entry_price = op[entry_bar] if entered_at_open else close[entry_bar]

    atr_at_signal = atr_series[signal_bar]
    sl_price = tp_price = np.nan
    risk_price = np.nan
    if rules.atr_sl_mult is not None:
        if not np.isfinite(atr_at_signal) or atr_at_signal <= 0:
            return None  # ATR warmup - no defensible stop distance, so no trade
        risk_price = rules.atr_sl_mult * atr_at_signal
        sl_price = entry_price - sign * risk_price
        if rules.atr_tp_r is not None:
            tp_price = entry_price + sign * risk_price * rules.atr_tp_r

    # The entry bar's own range only counts when we were filled at its open; when the
    # fill is at its close, that bar has already happened.
    first_live_bar = entry_bar if entered_at_open else entry_bar + 1
    mfe = mae = 0.0
    ambiguous = 0

    held_offset = 1 if entered_at_open else 0
    exit_bar = n_bars - 1
    exit_price = close[exit_bar]
    exit_reason = "end_of_data"
    # Held bars are counted at the bar that DECIDED the exit, not at the bar that filled
    # it. A close-evaluated rule decides on bar b and fills at b+1, so deriving the count
    # from exit_bar would report InpExitBars + 1 and make the time exit look longer than
    # the EA's.
    held_at_exit = exit_bar - entry_bar + held_offset

    for bar in range(first_live_bar, n_bars):
        bars_held = bar - entry_bar + held_offset

        hit_sl = np.isfinite(sl_price) and (low[bar] <= sl_price if sign > 0 else high[bar] >= sl_price)
        hit_tp = np.isfinite(tp_price) and (high[bar] >= tp_price if sign > 0 else low[bar] <= tp_price)

        # Excursions must stop where the position did. On the bar that stops us out we
        # cannot claim the rest of its range: a favourable spike after the stop was
        # never ours, and counting it would inflate exactly the MFE figure the exit
        # study uses to argue a target would have banked these trades. The adverse side
        # is the stop distance itself, by definition.
        favourable = (high[bar] - entry_price) if sign > 0 else (entry_price - low[bar])
        adverse = (low[bar] - entry_price) if sign > 0 else (entry_price - high[bar])
        if hit_sl:
            adverse = -abs(entry_price - sl_price)
        elif hit_tp:
            favourable = abs(tp_price - entry_price)
        if not hit_sl:
            mfe = max(mfe, favourable)
        mae = min(mae, adverse)

        if hit_sl and hit_tp:
            ambiguous = 1
        if hit_sl:
            exit_bar, exit_price, exit_reason, held_at_exit = bar, sl_price, "sl", bars_held
            break
        if hit_tp:
            exit_bar, exit_price, exit_reason, held_at_exit = bar, tp_price, "tp", bars_held
            break

        reason = _close_rule_hit(
            rules=rules, bars_held=bars_held, sign=sign, bar=bar,
            close=close, rsi=rsi, sma_short=sma_short, entry_price=entry_price,
        )
        if reason is not None:
            exit_bar, exit_price = _close_fill(bar, n_bars, op, close, exec_params)
            exit_reason, held_at_exit = reason, bars_held
            break

        if rules.atr_trailing_mult is not None and np.isfinite(atr_series[bar]):
            candidate = close[bar] - sign * rules.atr_trailing_mult * atr_series[bar]
            # Only ever tighten, matching CTradeManager::ApplyTrailing.
            sl_price = max(sl_price, candidate) if sign > 0 else min(sl_price, candidate)

    gross_price = sign * (exit_price - entry_price)
    spread_cost = float(costs.spread_cost_price(spread[entry_bar], point))
    net_price = gross_price - spread_cost

    return {
        "symbol": symbol,
        "direction": direction,
        "signal_time": times[signal_bar],
        "entry_time": times[entry_bar],
        "entry_price": entry_price,
        "exit_time": times[exit_bar],
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "bars_held": held_at_exit,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "gross_price": gross_price,
        "spread_cost_price": spread_cost,
        "net_price": net_price,
        "gross_pct": gross_price / entry_price * 100,
        "net_pct": net_price / entry_price * 100,
        "mae_price": mae,
        "mfe_price": mfe,
        "entry_bar": entry_bar,
        "exit_bar": exit_bar,
        "risk_price": risk_price,
        "_ambiguous": ambiguous,
    }


def _close_rule_hit(*, rules, bars_held, sign, bar, close, rsi, sma_short, entry_price) -> str | None:
    """Rules evaluated on a closed bar, in the order the engine resolves them."""
    if rules.rsi_revert_level is not None:
        reverted = rsi[bar] >= rules.rsi_revert_level if sign > 0 else rsi[bar] <= rules.rsi_revert_level
        if reverted:
            return "rsi_revert"

    if rules.sma_touch and np.isfinite(sma_short[bar]):
        touched = close[bar] >= sma_short[bar] if sign > 0 else close[bar] <= sma_short[bar]
        if touched:
            return "sma_touch"

    if rules.first_profitable_close and sign * (close[bar] - entry_price) > 0:
        return "first_profitable_close"

    if rules.time_bars is not None and bars_held >= rules.time_bars:
        return "time"

    return None


def _close_fill(bar, n_bars, op, close, exec_params) -> tuple[int, float]:
    """A close-evaluated exit is decided at a bar's close but filled at market.

    For the EA that is the first tick of the next bar, so `next_open` is the faithful
    choice; `bar_close` exists to reproduce notebook 01.
    """
    if exec_params.close_exit_fill == "next_open" and bar + 1 < n_bars:
        return bar + 1, op[bar + 1]
    return bar, close[bar]
