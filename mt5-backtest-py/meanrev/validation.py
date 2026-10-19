"""Guards against mistaking a lucky parameter for an edge.

Notebook 01's cell 34 is a list of the best (streak, exit_bars) pair found per asset,
chosen by looking at the whole sample. Nothing in that procedure can tell a real effect
from the best of several hundred draws, and the list is the natural output of the
procedure whether or not an edge exists. The three tools here address that:

  * `parameter_surface` - judge a plateau, not a peak. An optimum surrounded by bad
    neighbours is noise; one sitting on a broad shoulder might not be.
  * `walk_forward` - choose parameters on data, measure on data you did not choose on.
  * `permutation_test` - compare the result against the same strategy run on series
    with the same bar-size distribution but no serial structure to exploit.

`SearchBudget` exists so the count of combinations tried is written down. The best of
300 looks good by construction, and a p-value that ignores that is not a p-value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .costs import FREE, CostModel
from .engine import ExecParams, run_backtest
from .exits import ExitRules
from .metrics import compute_stats
from .signals import SignalParams


@dataclass
class SearchBudget:
    """Counts every parameter combination evaluated, so the search can be declared."""

    evaluations: int = 0
    labels: list[str] = field(default_factory=list)

    def record(self, label: str) -> None:
        self.evaluations += 1
        self.labels.append(label)

    def __str__(self) -> str:
        return f"{self.evaluations} combinations evaluated"


def parameter_surface(
    df: pd.DataFrame,
    symbol: str,
    streaks: list[int],
    exit_bars: list[int],
    costs: CostModel = FREE,
    exec_params: ExecParams = ExecParams(),
    base_params: SignalParams | None = None,
    metric: str = "total_pct",
    budget: SearchBudget | None = None,
    point: float | None = None,
) -> pd.DataFrame:
    """Grid of `metric` over streak x exit_bars, as a matrix ready to heatmap."""
    base = base_params or SignalParams()
    records = []

    for streak in streaks:
        params = SignalParams(
            sma_short=base.sma_short, sma_long=base.sma_long, rsi_period=base.rsi_period,
            overbought=base.overbought, oversold=base.oversold, streak=streak,
        )
        for bars in exit_bars:
            result = run_backtest(df, symbol, params, ExitRules(time_bars=bars), costs, exec_params, point=point)
            stats = compute_stats(result.trades)
            if budget is not None:
                budget.record(f"{symbol} streak={streak} exit={bars}")
            records.append({"streak": streak, "exit_bars": bars, **stats.as_row()})

    return pd.DataFrame(records).pivot(index="streak", columns="exit_bars", values=metric)


def plateau_score(surface: pd.DataFrame) -> pd.DataFrame:
    """Each cell replaced by the mean of itself and its immediate neighbours.

    A parameter that only works in isolation loses most of its value here; one that sits
    on a shoulder keeps it. Pick the best cell of *this* matrix, not of the raw surface.
    """
    values = surface.to_numpy(dtype=float)
    rows, cols = values.shape

    # Shifted copies padded with NaN rather than np.roll: rolling would wrap the last
    # row onto the first and average unrelated corners of the grid together. Cells on
    # the border therefore average over fewer neighbours - their plateau score is
    # weaker evidence, not invalid, and masking them would throw away half a small grid.
    stacked = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            shifted = np.full_like(values, np.nan)
            src_rows = slice(max(0, -dr), rows - max(0, dr))
            dst_rows = slice(max(0, dr), rows - max(0, -dr))
            src_cols = slice(max(0, -dc), cols - max(0, dc))
            dst_cols = slice(max(0, dc), cols - max(0, -dc))
            shifted[dst_rows, dst_cols] = values[src_rows, src_cols]
            stacked.append(shifted)

    smoothed = np.nanmean(np.stack(stacked), axis=0)
    return pd.DataFrame(smoothed, index=surface.index, columns=surface.columns)


def neighbour_count(surface: pd.DataFrame) -> pd.DataFrame:
    """How many cells each plateau score averaged over - 9 in the interior, fewer at the
    border. Read alongside plateau_score when the winner sits on an edge."""
    present = surface.notna().to_numpy(dtype=float)
    counts = np.zeros_like(present)
    rows, cols = present.shape
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            src_rows = slice(max(0, -dr), rows - max(0, dr))
            dst_rows = slice(max(0, dr), rows - max(0, -dr))
            src_cols = slice(max(0, -dc), cols - max(0, dc))
            dst_cols = slice(max(0, dc), cols - max(0, -dc))
            counts[dst_rows, dst_cols] += present[src_rows, src_cols]
    return pd.DataFrame(counts, index=surface.index, columns=surface.columns)


def walk_forward(
    df: pd.DataFrame,
    symbol: str,
    streaks: list[int],
    exit_bars: list[int],
    train_months: int = 24,
    test_months: int = 6,
    costs: CostModel = FREE,
    exec_params: ExecParams = ExecParams(),
    metric: str = "total_pct",
    budget: SearchBudget | None = None,
    point: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling out-of-sample evaluation.

    Returns (folds, oos_trades): one row per fold saying which parameters the training
    window picked and what they earned on the untouched test window, plus the
    concatenated out-of-sample trades - the only equity curve worth showing.
    """
    # tz_localize(None) first: to_period would drop the timezone anyway and warn about
    # it, and the month a bar belongs to is a UTC question we have already answered.
    months = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None).dt.to_period("M")
    unique_months = months.drop_duplicates().sort_values().to_list()

    folds, oos_frames = [], []
    start = 0
    while start + train_months + test_months <= len(unique_months):
        train_span = unique_months[start : start + train_months]
        test_span = unique_months[start + train_months : start + train_months + test_months]

        train_df = df[months.isin(train_span)].reset_index(drop=True)
        test_df = df[months.isin(test_span)].reset_index(drop=True)

        # One point for every window: each slice would otherwise infer its own from
        # whatever decimals happen to appear in it.
        surface = parameter_surface(
            train_df, symbol, streaks, exit_bars, costs, exec_params,
            metric=metric, budget=budget, point=point,
        )
        if surface.isna().all().all():
            start += test_months
            continue
        best_streak, best_exit = _argmax_2d(surface)

        chosen = SignalParams(streak=best_streak)
        test_result = run_backtest(
            test_df, symbol, chosen, ExitRules(time_bars=best_exit), costs, exec_params, point=point
        )
        test_stats = compute_stats(test_result.trades)

        oos_frames.append(test_result.trades.assign(fold=len(folds)))
        folds.append(
            {
                "fold": len(folds),
                "train_from": str(train_span[0]), "train_to": str(train_span[-1]),
                "test_from": str(test_span[0]), "test_to": str(test_span[-1]),
                "chosen_streak": best_streak, "chosen_exit_bars": best_exit,
                "train_best": float(surface.loc[best_streak, best_exit]),
                "test_total_pct": test_stats.total_pct,
                "test_trades": test_stats.trades,
            }
        )
        start += test_months

    oos = pd.concat(oos_frames, ignore_index=True) if oos_frames else pd.DataFrame()
    return pd.DataFrame(folds), oos


def _argmax_2d(surface: pd.DataFrame) -> tuple[int, int]:
    flat = surface.stack()
    row, col = flat.idxmax()
    return int(row), int(col)


def shuffle_bars(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """A surrogate series: same bars, different order.

    Each bar keeps its shape - the open, high and low as ratios to its own close - and
    its close-to-close return; only the sequence is permuted, and the calendar and the
    spread column stay put. That removes the serial dependence a mean-reversion rule
    lives on while preserving bar size, volatility clustering at the bar level, and the
    cost structure. A strategy that scores as well here as on the real series is reading
    noise.
    """
    work = df.reset_index(drop=True)
    close = work["close"].to_numpy(dtype=float)
    log_returns = np.diff(np.log(close))

    shape = np.column_stack([work["open"] / work["close"], work["high"] / work["close"], work["low"] / work["close"]])

    order = rng.permutation(len(log_returns))
    new_close = close[0] * np.exp(np.concatenate([[0.0], np.cumsum(log_returns[order])]))
    new_shape = shape[np.concatenate([[0], order + 1])]

    out = work.copy()
    out["close"] = new_close
    out["open"] = new_close * new_shape[:, 0]
    out["high"] = new_close * new_shape[:, 1]
    out["low"] = new_close * new_shape[:, 2]
    # A permuted bar can leave high/low no longer bracketing open/close; re-impose it.
    out["high"] = out[["open", "high", "low", "close"]].max(axis=1)
    out["low"] = out[["open", "high", "low", "close"]].min(axis=1)

    # Quantise back to the instrument's tick. Without this the surrogate carries 15
    # decimals of float noise, and anything that infers precision from the prices -
    # data.point_size, most importantly - reads a point size of ~1e-15 and stops
    # charging the spread. The null would then be a GROSS distribution compared against
    # a NET result, which flatters the null and buries any real edge.
    from .data import point_size

    digits = int(round(-np.log10(point_size(df["close"]))))
    for column in ("open", "high", "low", "close"):
        out[column] = out[column].round(digits)
    return out


def permutation_test(
    df: pd.DataFrame,
    symbol: str,
    signal_params: SignalParams,
    exit_rules: ExitRules,
    costs: CostModel = FREE,
    exec_params: ExecParams = ExecParams(),
    n_permutations: int = 200,
    metric: str = "total_pct",
    columns: tuple[str, ...] = ("gross_pct", "net_pct"),
    seed: int = 20260924,
) -> dict:
    """Where the real result falls in the distribution of surrogate results.

    Scored on every column in `columns` from the SAME surrogates, because the
    interesting comparison is usually between them: a strategy can sit well outside the
    null on gross return - genuine structure it is reading - and inside it on net,
    meaning the structure is real but smaller than the cost of trading it. Those two
    findings point at completely different next steps, and one number cannot tell them
    apart.

    `p_value` is one-sided: the share of surrogates that did at least as well.
    """
    from .data import point_size

    rng = np.random.default_rng(seed)
    # Pin the point size to the REAL series and reuse it for every surrogate, so each
    # run is charged the same spread. Letting each surrogate infer its own is how the
    # null silently became a gross-return distribution.
    point = point_size(df["close"])

    def score(trades) -> dict[str, float]:
        return {c: getattr(compute_stats(trades, column=c), metric) for c in columns}

    actual = score(
        run_backtest(df, symbol, signal_params, exit_rules, costs, exec_params, point=point).trades
    )

    null: dict[str, list[float]] = {c: [] for c in columns}
    for _ in range(n_permutations):
        surrogate = shuffle_bars(df, rng)
        result = run_backtest(
            surrogate, symbol, signal_params, exit_rules, costs, exec_params, point=point
        )
        for column, value in score(result.trades).items():
            null[column].append(value)

    out = {"metric": metric, "n_permutations": n_permutations, "columns": columns, "by_column": {}}
    for column in columns:
        values = np.asarray(null[column], dtype=float)
        std = float(np.nanstd(values))
        out["by_column"][column] = {
            "actual": float(actual[column]),
            "null_mean": float(np.nanmean(values)),
            "null_std": std,
            "z": (float(actual[column]) - float(np.nanmean(values))) / std if std else float("nan"),
            "p_value": float((values >= actual[column]).sum() + 1) / (n_permutations + 1),
            "null": values,
        }

    # Back-compatible flat view of the last column, which is the net one by default.
    out.update(out["by_column"][columns[-1]])
    return out


def permutation_summary(result: dict) -> pd.DataFrame:
    """The by-column view of a permutation_test as a table."""
    return pd.DataFrame(
        {
            column: {
                "real": round(stats["actual"], 2),
                "nula (media)": round(stats["null_mean"], 2),
                "nula (desvio)": round(stats["null_std"], 2),
                "z": round(stats["z"], 2),
                "p": round(stats["p_value"], 4),
            }
            for column, stats in result["by_column"].items()
        }
    ).T
