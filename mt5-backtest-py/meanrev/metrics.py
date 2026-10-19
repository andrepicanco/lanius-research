"""Turning a trade ledger into money, and then into the numbers worth arguing about.

Two things here are deliberate departures from notebook 01:

*Money, not summed percentages.* The notebook added percentage returns together, which
implicitly assumes you re-bet the same notional every time and never compounds. Here a
ledger is priced with the symbol's value per point and a sizing rule, so the equity
curve means something and can be compared with what risk-mgmt reports.

*Significance separated from signal-to-noise.* The notebook's `n_std` (mean/std) answers
"how big is the edge relative to a single trade's variability" - a per-trade quality
measure. It is not a test of whether the mean differs from zero; that needs the sqrt(n).
Both are reported, under different names, because AUDCHF M30 scores 0.04 on one and 3.6
on the other and the gap is the whole lesson.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

# Bars per year, used to annualize Sharpe. Forex trades ~24h x 5 days.
BARS_PER_YEAR = {"M15": 24 * 4 * 5 * 52, "M30": 24 * 2 * 5 * 52, "H1": 24 * 5 * 52, "D1": 252}


@dataclass(frozen=True)
class Sizing:
    """How many lots a trade gets.

    `fixed` is what makes a shared-balance replay in risk-mgmt exact (see the note at the
    top of risk_mgmt/portfolio.py). `risk_pct` mirrors the EA's InpRiskPercent and needs
    the ledger's `risk_price` column, which only exists when an ATR stop was configured.
    """

    mode: str = "fixed"  # "fixed" | "risk_pct"
    lots: float = 0.10
    risk_pct: float = 1.0
    initial_balance: float = 10_000.0

    def __post_init__(self):
        if self.mode not in ("fixed", "risk_pct"):
            raise ValueError(f"mode must be fixed or risk_pct, got {self.mode!r}")


def to_money(
    trades: pd.DataFrame,
    value_per_point: float,
    point: float,
    sizing: Sizing = Sizing(),
    commission_per_lot: float = 0.0,
    swap_long_per_lot: float = 0.0,
    swap_short_per_lot: float = 0.0,
) -> pd.DataFrame:
    """Adds lots, commission, swap and P/L in account currency to a ledger.

    `value_per_point` is tick_value / tick_size for the symbol, the same quantity
    risk-mgmt's SymbolSpec carries. MT5 returns 0 for it when it cannot convert to the
    account currency (the ASXAUD case from 2026-09-19), and a zero silently zeroes every
    P/L, so it is rejected here rather than multiplied through.
    """
    if value_per_point <= 0:
        raise ValueError(
            f"value_per_point must be > 0, got {value_per_point}. MT5 reports 0 when it "
            f"cannot convert the symbol to the account currency - take the value from "
            f"risk-mgmt/config/symbols.yaml or add the quote-currency pair to Market Watch."
        )

    out = trades.copy()
    if out.empty:
        return out.assign(lots=[], commission=[], swap=[], pnl_gross=[], pnl_money=[], r_multiple=[])

    if sizing.mode == "fixed":
        out["lots"] = sizing.lots
    else:
        if out["risk_price"].isna().any():
            raise ValueError(
                "risk_pct sizing needs a stop distance on every trade, but risk_price is "
                "missing - run with an ExitRules that sets atr_sl_mult, or use fixed lots."
            )
        # Sized off the starting balance, not a running one: a ledger priced against its
        # own growing equity is path-dependent and can no longer be merged with another
        # strategy's on a shared balance. The notebook says so where it matters.
        risk_money = sizing.initial_balance * sizing.risk_pct / 100.0
        out["lots"] = risk_money / (out["risk_price"] * value_per_point)

    out["nights"] = _nights_held(out)
    per_lot_swap = np.where(out["direction"] == "buy", swap_long_per_lot, swap_short_per_lot)

    out["commission"] = out["lots"] * commission_per_lot
    out["swap"] = out["lots"] * out["nights"] * per_lot_swap
    out["pnl_gross"] = out["gross_price"] * value_per_point * out["lots"]
    out["pnl_spread"] = -out["spread_cost_price"] * value_per_point * out["lots"]
    out["pnl_money"] = out["pnl_gross"] + out["pnl_spread"] - out["commission"] + out["swap"]

    risk_money_per_trade = out["risk_price"] * value_per_point * out["lots"]
    out["r_multiple"] = np.where(risk_money_per_trade > 0, out["pnl_money"] / risk_money_per_trade, 0.0)
    return out


def _nights_held(trades: pd.DataFrame) -> pd.Series:
    """Rollover count: how many day boundaries the position was held across."""
    entry = pd.to_datetime(trades["entry_time"], utc=True).dt.normalize()
    exit_ = pd.to_datetime(trades["exit_time"], utc=True).dt.normalize()
    return (exit_ - entry).dt.days.clip(lower=0)


def equity_curve(pnl: pd.Series, initial_balance: float) -> pd.Series:
    return initial_balance + pnl.cumsum()


def max_drawdown(equity: pd.Series, start: float | None = None) -> tuple[float, float]:
    """Peak-to-trough as ($, % of the peak it fell from), walking trades in close order.

    Same convention as risk_mgmt.portfolio.max_drawdown, including the `start` baseline,
    so a ledger exported from here reconciles with the risk report instead of differing
    by a methodology nobody remembers choosing.
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


@dataclass
class Stats:
    trades: int
    win_rate: float
    total_pct: float
    avg_pct: float
    signal_to_noise: float   # mean/std per trade - notebook 01's `n_std`
    t_stat: float            # mean/std * sqrt(n) - the actual significance test
    profit_factor: float
    payoff: float
    avg_bars_held: float
    total_money: float | None = None
    max_drawdown_money: float | None = None
    max_drawdown_pct: float | None = None

    def as_row(self) -> dict:
        return asdict(self)


def compute_stats(trades: pd.DataFrame, column: str = "net_pct", initial_balance: float = 10_000.0) -> Stats:
    """Aggregate performance. `column` picks which P/L layer to score, which is how the
    notebook's gross-to-net waterfall reuses one function for every step."""
    if trades.empty:
        return Stats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    r = trades[column]
    wins, losses = r[r > 0], r[r < 0]
    std = r.std(ddof=1)

    stats = Stats(
        trades=len(r),
        win_rate=float((r > 0).mean()),
        total_pct=float(r.sum()),
        avg_pct=float(r.mean()),
        signal_to_noise=float(r.mean() / std) if std else 0.0,
        t_stat=float(r.mean() / std * np.sqrt(len(r))) if std else 0.0,
        profit_factor=float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() else float("inf"),
        payoff=float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else float("nan"),
        avg_bars_held=float(trades["bars_held"].mean()),
    )

    if "pnl_money" in trades.columns:
        ordered = trades.sort_values("exit_time")
        equity = equity_curve(ordered["pnl_money"].reset_index(drop=True), initial_balance)
        worst, worst_pct = max_drawdown(equity, initial_balance)
        stats.total_money = float(ordered["pnl_money"].sum())
        stats.max_drawdown_money = worst
        stats.max_drawdown_pct = worst_pct

    return stats


def excursion_table(trades: pd.DataFrame, point: float, quantiles=(0.5, 0.75, 0.9, 0.95)) -> pd.DataFrame:
    """MAE/MFE distribution in points, split by whether the trade ended up positive.

    This is the empirical way to place a stop and a target: if the adverse excursion of
    winning trades rarely exceeds X, a stop at X costs almost nothing and caps the
    losers; if the favourable excursion of losing trades routinely reaches Y, those
    trades were in profit at some point and a target at Y would have banked them.
    Guessing 2xATR and 2R, as the EA does today, is the alternative this replaces.
    """
    if trades.empty:
        return pd.DataFrame()

    work = trades.assign(
        mae_points=trades["mae_price"].abs() / point,
        mfe_points=trades["mfe_price"] / point,
        outcome=np.where(trades["net_price"] > 0, "winner", "loser"),
    )
    table = work.groupby("outcome")[["mae_points", "mfe_points"]].quantile(list(quantiles)).unstack()
    table.columns = [f"{metric}_q{int(q * 100)}" for metric, q in table.columns]
    return table.join(work.groupby("outcome").size().rename("trades"))
