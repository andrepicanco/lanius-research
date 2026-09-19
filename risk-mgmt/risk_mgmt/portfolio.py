"""Scenario 3: what several strategies would have done sharing ONE account balance.

The MT5 Strategy Tester runs a single EA per pass, so a genuinely combined backtest is
not something the terminal can produce. What this module does instead is replay the
already-realized trade lists of two or more strategies against a single balance: trades
are merged chronologically, P/L accrues to one equity curve, and drawdown is measured on
that curve rather than on each strategy separately.

Two things worth being explicit about before trusting the output:

1. **Sizing.** The replay is exact when every strategy was backtested with a FIXED lot,
   because then a trade's P/L in account currency doesn't depend on the balance it was
   opened against, and adding the two streams is legitimate. It is an approximation when
   a strategy sized positions as a % of balance (InpUseFixedLot=false), since in a real
   shared account each EA would have sized off the *combined* balance - which cannot be
   reconstructed from realized P/L alone. `detect_sizing_mode` flags that so the report
   can say it out loud rather than quietly implying a precision it doesn't have.

2. **Margin is not modeled.** A shared account shares margin, and this report has no
   per-symbol margin rates. `ConcurrencyStats` is the proxy: how many positions were
   actually open at the same time, and how often more than one was. Combined with the
   Tester's own margin-level figure per run, that's enough to sanity-check headroom, but
   it is not a margin-call simulation.

Equity here is realized-P/L-only, consistent with how var.py defines "returns"
everywhere else in this package: the curve is marked daily on each trade's exit date,
while drawdown walks the trades in close order (see compute_portfolio). The Tester's own
*equity* drawdown marks open positions tick by tick and will read higher than either;
its *balance* drawdown is the figure that reconciles with this one.
"""

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from .logsource import Trade


@dataclass
class StrategyStats:
    strategy: str
    trades: int
    total_pnl: float
    win_rate: float
    avg_pnl: float
    max_drawdown: float  # drawdown of this strategy standalone, for comparison
    symbols: list[str]
    sizing: str  # "fixed" | "variable"


@dataclass
class ConcurrencyStats:
    max_concurrent: int
    pct_time_multi: float  # share of the covered span with more than one position open
    overlap_by_pair: dict[tuple[str, str], int]


@dataclass
class PortfolioResult:
    equity: pd.Series
    daily_pnl_by_strategy: pd.DataFrame
    initial_balance: float
    total_pnl: float
    max_drawdown: float
    max_drawdown_pct: float
    by_strategy: list[StrategyStats]
    strategy_correlation: pd.DataFrame
    concurrency: ConcurrencyStats
    diversification_benefit: float  # sum of standalone drawdowns - combined drawdown

    @property
    def final_balance(self) -> float:
        return self.initial_balance + self.total_pnl


def daily_pnl_by_strategy(trades: list[Trade]) -> pd.DataFrame:
    """One column per strategy, one row per calendar day across the whole covered span.
    Days a strategy didn't close anything are 0.0, not NaN - it was flat, which is a real
    observation and not missing data."""
    if not trades:
        return pd.DataFrame()

    frame = pd.DataFrame(
        {
            "day": [pd.Timestamp(t.exit_time).normalize() for t in trades],
            "strategy": [t.strategy or "unnamed" for t in trades],
            "pnl": [t.pnl_money for t in trades],
        }
    )
    pivot = frame.pivot_table(index="day", columns="strategy", values="pnl", aggfunc="sum")
    full_range = pd.date_range(pivot.index.min(), pivot.index.max(), freq="D")
    return pivot.reindex(full_range).fillna(0.0)


def equity_curve(daily_pnl: pd.Series, initial_balance: float) -> pd.Series:
    return initial_balance + daily_pnl.cumsum()


def max_drawdown(equity: pd.Series, start: float | None = None) -> tuple[float, float]:
    """Peak-to-trough of the equity curve, as ($, % of the peak it fell from).

    `start` is the balance before the first trade. Without it the running peak would
    begin at the equity value the *first* trade already produced, so an account that
    lost money on its opening trade would report no drawdown at all - the hole it dug
    below its own starting balance would never be counted.
    """
    if equity.empty:
        return 0.0, 0.0
    running_peak = equity.cummax()
    if start is not None:
        running_peak = running_peak.clip(lower=start)
    drawdown = running_peak - equity
    worst = float(drawdown.max())
    peak_at_worst = float(running_peak.loc[drawdown.idxmax()])
    return worst, (worst / peak_at_worst * 100.0) if peak_at_worst else 0.0


def detect_sizing_mode(trades: list[Trade]) -> str:
    """"fixed" when every trade on a given symbol used the same volume, "variable"
    otherwise. This is what decides whether merging two P/L streams is exact or merely
    indicative, so it's inferred from the data rather than taken on trust from a flag."""
    by_symbol: dict[str, set[float]] = {}
    for trade in trades:
        by_symbol.setdefault(trade.symbol, set()).add(round(trade.lots, 4))
    return "fixed" if all(len(v) == 1 for v in by_symbol.values()) else "variable"


def concurrency(trades: list[Trade]) -> ConcurrencyStats:
    """Sweeps the entry/exit intervals to find how crowded the account actually got.

    `overlap_by_pair` counts, for each pair of strategies, how many times a trade from
    one opened while the other already had something open - the quantity that decides
    whether "both strategies on one balance" is a real interaction or just two streams
    that happen to share a report.
    """
    if not trades:
        return ConcurrencyStats(0, 0.0, {})

    events: list[tuple[dt.datetime, int, str]] = []
    for trade in trades:
        name = trade.strategy or "unnamed"
        events.append((trade.entry_time, 1, name))
        events.append((trade.exit_time, -1, name))
    events.sort(key=lambda e: (e[0], e[1]))  # close before open at an identical timestamp

    open_by_strategy: dict[str, int] = {}
    overlap: dict[tuple[str, str], int] = {}
    max_concurrent = 0
    multi_seconds = 0.0
    previous_time = events[0][0]
    running = 0

    for when, delta, name in events:
        span = (when - previous_time).total_seconds()
        if running > 1:
            multi_seconds += span
        previous_time = when

        if delta == 1:
            for other, count in open_by_strategy.items():
                if count > 0 and other != name:
                    key = tuple(sorted((name, other)))
                    overlap[key] = overlap.get(key, 0) + 1
            open_by_strategy[name] = open_by_strategy.get(name, 0) + 1
        else:
            open_by_strategy[name] = max(0, open_by_strategy.get(name, 0) - 1)

        running += delta
        max_concurrent = max(max_concurrent, running)

    total_seconds = (events[-1][0] - events[0][0]).total_seconds()
    return ConcurrencyStats(
        max_concurrent=max_concurrent,
        pct_time_multi=(multi_seconds / total_seconds * 100.0) if total_seconds else 0.0,
        overlap_by_pair=overlap,
    )


def _strategy_stats(trades: list[Trade]) -> list[StrategyStats]:
    by_strategy: dict[str, list[Trade]] = {}
    for trade in trades:
        by_strategy.setdefault(trade.strategy or "unnamed", []).append(trade)

    stats = []
    for name in sorted(by_strategy):
        group = sorted(by_strategy[name], key=lambda t: t.exit_time)
        pnls = [t.pnl_money for t in group]
        standalone = equity_curve(pd.Series(pnls), 0.0)  # trade-ordered, not calendar
        stats.append(
            StrategyStats(
                strategy=name,
                trades=len(group),
                total_pnl=sum(pnls),
                win_rate=len([p for p in pnls if p > 0]) / len(group),
                avg_pnl=sum(pnls) / len(group),
                max_drawdown=max_drawdown(standalone, 0.0)[0],
                symbols=sorted({t.symbol for t in group}),
                sizing=detect_sizing_mode(group),
            )
        )
    return stats


def compute_portfolio(trades: list[Trade], initial_balance: float) -> PortfolioResult | None:
    if not trades:
        return None

    by_strategy_daily = daily_pnl_by_strategy(trades)
    total_daily = by_strategy_daily.sum(axis=1)
    equity = equity_curve(total_daily, initial_balance)

    # Drawdown walks the merged trades in close order rather than the daily curve: two
    # trades closing on the same day net out before a daily series ever sees them, which
    # understates the dip. Trade order is also what the Tester's own balance-drawdown
    # figure uses, so a single-strategy run here reconciles with its report exactly.
    ordered = sorted(trades, key=lambda t: t.exit_time)
    trade_equity = equity_curve(pd.Series([t.pnl_money for t in ordered]), initial_balance)
    worst, worst_pct = max_drawdown(trade_equity, initial_balance)

    stats = _strategy_stats(trades)
    standalone_sum = sum(s.max_drawdown for s in stats)

    correlation = (
        by_strategy_daily.corr() if by_strategy_daily.shape[1] > 1 else pd.DataFrame()
    )

    return PortfolioResult(
        equity=equity,
        daily_pnl_by_strategy=by_strategy_daily,
        initial_balance=initial_balance,
        total_pnl=float(total_daily.sum()),
        max_drawdown=worst,
        max_drawdown_pct=worst_pct,
        by_strategy=stats,
        strategy_correlation=correlation,
        concurrency=concurrency(trades),
        diversification_benefit=standalone_sum - worst,
    )
