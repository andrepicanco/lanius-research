"""Monthly and overall trade-result aggregation - the depth layer on top of the
portfolio-level VaR curves: per-asset monthly P/L, and a risk-oriented monthly summary
(win rate, best/worst trade, intra-month drawdown, VaR for that month).
"""

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from .logsource import Trade
from .var import parametric_var_of_series


def _month_key(when: dt.datetime) -> str:
    return when.strftime("%Y-%m")


@dataclass
class AssetMonthStats:
    month: str
    symbol: str
    trades: int
    pnl: float
    avg_pnl: float


@dataclass
class MonthSummary:
    month: str
    trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    pnl_stdev: float | None
    best_trade: float
    worst_trade: float
    max_drawdown: float
    avg_risk_money: float | None
    var_month: float | None


@dataclass
class OverallSummary:
    date_from: dt.date
    date_to: dt.date
    trades: int
    total_pnl: float
    win_rate: float
    avg_pnl: float


def asset_month_stats(trades: list[Trade]) -> list[AssetMonthStats]:
    if not trades:
        return []

    df = pd.DataFrame(
        {
            "month": [_month_key(t.exit_time) for t in trades],
            "symbol": [t.symbol for t in trades],
            "pnl": [t.pnl_money for t in trades],
        }
    )
    grouped = df.groupby(["month", "symbol"])["pnl"].agg(["count", "sum"]).reset_index()
    grouped = grouped.sort_values(["month", "symbol"])

    result = []
    for _, row in grouped.iterrows():
        count = int(row["count"])
        total = float(row["sum"])
        result.append(AssetMonthStats(month=row["month"], symbol=row["symbol"], trades=count, pnl=total, avg_pnl=total / count))
    return result


def _max_drawdown(pnls_in_order: list[float]) -> float:
    """Peak-to-trough of the cumulative sum of the given P/L values, in the order given -
    realized-P/L-only (no intraday equity marking), consistent with how this module
    treats "returns" everywhere else."""
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls_in_order:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd


def month_summaries(trades: list[Trade], daily_pnl: pd.Series, confidence: float) -> list[MonthSummary]:
    if not trades:
        return []

    by_month: dict[str, list[Trade]] = {}
    for t in trades:
        by_month.setdefault(_month_key(t.exit_time), []).append(t)

    summaries = []
    for month in sorted(by_month):
        month_trades = sorted(by_month[month], key=lambda t: t.exit_time)
        pnls = [t.pnl_money for t in month_trades]
        wins = [p for p in pnls if p > 0]

        # Sample stdev is undefined for n<2, same convention as parametric_var_of_series.
        pnl_stdev = float(pd.Series(pnls).std(ddof=1)) if len(pnls) >= 2 else None

        risks = [t.risk_money for t in month_trades if t.risk_money is not None]
        avg_risk_money = sum(risks) / len(risks) if risks else None

        var_month = None
        if not daily_pnl.empty:
            month_period = pd.Period(month, freq="M")
            month_slice = daily_pnl[daily_pnl.index.to_period("M") == month_period]
            var_month = parametric_var_of_series(month_slice, confidence)

        summaries.append(
            MonthSummary(
                month=month,
                trades=len(month_trades),
                win_rate=len(wins) / len(month_trades),
                total_pnl=sum(pnls),
                avg_pnl=sum(pnls) / len(month_trades),
                pnl_stdev=pnl_stdev,
                best_trade=max(pnls),
                worst_trade=min(pnls),
                max_drawdown=_max_drawdown(pnls),
                avg_risk_money=avg_risk_money,
                var_month=var_month,
            )
        )
    return summaries


def cumulative_pnl_by_asset(asset_stats: list[AssetMonthStats], months: list[str]) -> dict[str, dict[str, float]]:
    """Running total of P/L per symbol across `months`, in order. A month with no trades
    for a symbol contributes 0.0, which naturally carries the prior running total forward
    rather than needing separate "was this month blank" bookkeeping.
    """
    symbols = sorted({a.symbol for a in asset_stats})
    pnl_by_key = {(a.month, a.symbol): a.pnl for a in asset_stats}

    result: dict[str, dict[str, float]] = {}
    for symbol in symbols:
        running = 0.0
        per_month: dict[str, float] = {}
        for month in months:
            running += pnl_by_key.get((month, symbol), 0.0)
            per_month[month] = running
        result[symbol] = per_month
    return result


def overall_summary(trades: list[Trade]) -> OverallSummary | None:
    if not trades:
        return None

    pnls = [t.pnl_money for t in trades]
    wins = [p for p in pnls if p > 0]
    exit_dates = [t.exit_time.date() for t in trades]

    return OverallSummary(
        date_from=min(exit_dates),
        date_to=max(exit_dates),
        trades=len(trades),
        total_pnl=sum(pnls),
        win_rate=len(wins) / len(trades),
        avg_pnl=sum(pnls) / len(trades),
    )
