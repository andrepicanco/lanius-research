"""Builds the VaR chart, the correlation heatmap, and the Telegram message text from
already-computed VarResult/CorrelationResult objects."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless - this runs in a cron job / CI, never with a display
import matplotlib.pyplot as plt

from .correlation import CorrelationResult
from .monthly import AssetMonthStats, MonthSummary, OverallSummary
from .portfolio import PortfolioResult
from .var import VarResult


def plot_var_chart(result: VarResult, window_days: int, baseline_days: int, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 5))
    result.var_short.plot(ax=ax, label=f"VaR ({window_days}d)", linewidth=2)
    result.var_baseline.plot(ax=ax, label=f"VaR baseline ({baseline_days}d)", linewidth=2, linestyle="--")
    ax.set_ylabel("VaR ($)")
    ax.set_title("Parametric VaR")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_correlation_heatmap(result: CorrelationResult, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(result.rolling.values, vmin=-1, vmax=1, cmap="RdBu_r")
    symbols = list(result.rolling.columns)
    ax.set_xticks(range(len(symbols)))
    ax.set_xticklabels(symbols, rotation=45, ha="right")
    ax.set_yticks(range(len(symbols)))
    ax.set_yticklabels(symbols)
    fig.colorbar(im, ax=ax, label="rolling correlation")
    ax.set_title("Rolling price-return correlation")
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_pc1_chart(quarterly_pc1: list[tuple[str, float | None]], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    quarters = [q for q, _ in quarterly_pc1]
    values = [v * 100 if v is not None else float("nan") for _, v in quarterly_pc1]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(quarters, values, marker="o", linewidth=2)
    ax.set_ylabel("PC1 explained variance (%)")
    ax.set_title("Basket concentration (PCA, correlation matrix)")
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_equity_chart(portfolio: PortfolioResult, output_path: str | Path) -> Path:
    """The shared-balance equity curve, with each strategy's standalone contribution
    underneath it - the point of the chart is whether the combined line is smoother than
    its parts, which is only visible with both drawn together."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 5))
    portfolio.equity.plot(ax=ax, label="Combined (shared balance)", linewidth=2.5, color="black")

    for strategy in portfolio.daily_pnl_by_strategy.columns:
        standalone = portfolio.initial_balance + portfolio.daily_pnl_by_strategy[strategy].cumsum()
        standalone.plot(ax=ax, label=f"{strategy} (standalone)", linewidth=1.2, linestyle="--")

    ax.set_ylabel("Balance ($)")
    ax.set_title("Equity curve - realized P/L, marked on exit date")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    """Fixed-width table: first column left-aligned (it's always a label), the rest
    right-aligned (they're always numbers/short codes) - readable once wrapped in a
    Markdown code block, which is what makes the alignment actually render monospaced
    in Telegram instead of collapsing like normal message text does."""
    all_rows = [headers] + rows
    widths = [max(len(str(r[i])) for r in all_rows) for i in range(len(headers))]

    lines = []
    for r in all_rows:
        cells = [
            str(r[i]).ljust(widths[i]) if i == 0 else str(r[i]).rjust(widths[i])
            for i in range(len(r))
        ]
        lines.append("  ".join(cells))
    return "\n".join(lines)


def _code_block(text: str) -> str:
    return f"```\n{text}\n```"


_SUMMARY_METRICS = [
    ("Trades", lambda s: str(s.trades)),
    ("Win rate", lambda s: f"{s.win_rate:.1%}"),
    ("Total P/L", lambda s: f"{s.total_pnl:,.2f}"),
    ("Avg P/L/trade", lambda s: f"{s.avg_pnl:,.2f}"),
    ("P/L Std Dev", lambda s: f"{s.pnl_stdev:,.2f}" if s.pnl_stdev is not None else "n/a"),
    ("Best trade", lambda s: f"{s.best_trade:,.2f}"),
    ("Worst trade", lambda s: f"{s.worst_trade:,.2f}"),
    ("Max drawdown", lambda s: f"{s.max_drawdown:,.2f}"),
    ("Avg risk at entry", lambda s: f"{s.avg_risk_money:,.2f}" if s.avg_risk_money is not None else "n/a"),
]


def _monthly_summary_table(summaries: list[MonthSummary], confidence: float) -> str:
    headers = ["Metric"] + [s.month for s in summaries]
    rows = [[label] + [getter(s) for s in summaries] for label, getter in _SUMMARY_METRICS]
    var_row = [f"VaR ({confidence:.0%})"] + [
        f"{s.var_month:,.2f}" if s.var_month is not None else "n/a" for s in summaries
    ]
    rows.append(var_row)
    return _render_table(headers, rows)


def _by_asset_table_for_month(month: str, asset_stats: list[AssetMonthStats], symbols: list[str]) -> str:
    by_symbol = {a.symbol: a for a in asset_stats if a.month == month}
    headers = ["Metric"] + symbols

    def cell(symbol: str, attr: str, fmt) -> str:
        asset = by_symbol.get(symbol)
        return fmt(getattr(asset, attr)) if asset is not None else "-"

    rows = [
        ["Trades"] + [cell(s, "trades", str) for s in symbols],
        ["P/L ($)"] + [cell(s, "pnl", lambda v: f"{v:,.2f}") for s in symbols],
        ["Avg P/L ($)"] + [cell(s, "avg_pnl", lambda v: f"{v:,.2f}") for s in symbols],
    ]
    return _render_table(headers, rows)


def _portfolio_section(portfolio: PortfolioResult) -> list[str]:
    lines = ["", "*Portfolio (shared balance)*"]
    lines.append(
        f"Start: ${portfolio.initial_balance:,.2f} -> End: ${portfolio.final_balance:,.2f} "
        f"(P/L ${portfolio.total_pnl:+,.2f})"
    )
    lines.append(f"Max drawdown: ${portfolio.max_drawdown:,.2f} ({portfolio.max_drawdown_pct:.2f}%)")

    headers = ["Metric"] + [s.strategy for s in portfolio.by_strategy]
    rows = [
        ["Trades"] + [str(s.trades) for s in portfolio.by_strategy],
        ["Total P/L"] + [f"{s.total_pnl:,.2f}" for s in portfolio.by_strategy],
        ["Win rate"] + [f"{s.win_rate:.1%}" for s in portfolio.by_strategy],
        ["Avg P/L"] + [f"{s.avg_pnl:,.2f}" for s in portfolio.by_strategy],
        ["Max DD alone"] + [f"{s.max_drawdown:,.2f}" for s in portfolio.by_strategy],
        ["Sizing"] + [s.sizing for s in portfolio.by_strategy],
    ]
    lines.append(_code_block(_render_table(headers, rows)))

    if len(portfolio.by_strategy) > 1:
        lines.append(
            f"Diversification: ${portfolio.diversification_benefit:+,.2f} "
            f"(sum of standalone drawdowns minus the combined one - positive means "
            f"running them together was smoother than running them apart)"
        )

    concurrent = portfolio.concurrency
    lines.append(
        f"Max positions open at once: {concurrent.max_concurrent} | "
        f"time with >1 open: {concurrent.pct_time_multi:.1f}%"
    )
    for (a, b), count in sorted(concurrent.overlap_by_pair.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {a} overlapped {b}: {count}x")

    if not portfolio.strategy_correlation.empty:
        lines.append("Daily P/L correlation between strategies:")
        names = list(portfolio.strategy_correlation.columns)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                lines.append(f"  {a}/{b}: {portfolio.strategy_correlation.loc[a, b]:+.2f}")

    variable = [s.strategy for s in portfolio.by_strategy if s.sizing == "variable"]
    if variable:
        lines.append(
            "NOTE: " + ", ".join(variable) + " used variable position sizing, so this "
            "shared-balance replay is indicative, not exact - in a real combined account "
            "those lots would have been sized off the combined balance. Re-run those "
            "backtests with a fixed lot for an exact merge."
        )
    lines.append("Margin is not modeled - see the concurrency figures above for exposure overlap.")
    return lines


def build_message_text(
    var_result: VarResult,
    corr_result: CorrelationResult | None,
    confidence: float,
    window_days: int,
    baseline_days: int,
    overall: OverallSummary | None = None,
    asset_stats: list[AssetMonthStats] | None = None,
    month_summaries_list: list[MonthSummary] | None = None,
    quarterly_pc1: list[tuple[str, float | None]] | None = None,
    portfolio: PortfolioResult | None = None,
    title: str = "Lanius Risk Report",
) -> str:
    lines = [f"*{title}*", ""]

    short = var_result.latest_short
    baseline = var_result.latest_baseline
    lines.append(f"VaR ({confidence:.0%}, {window_days}d): " + (f"${short:,.2f}" if short is not None else "n/a - not enough history"))
    lines.append(f"VaR baseline ({confidence:.0%}, {baseline_days}d): " + (f"${baseline:,.2f}" if baseline is not None else "n/a - not enough history"))

    if portfolio is not None:
        lines.extend(_portfolio_section(portfolio))

    if overall is not None:
        lines.append("")
        lines.append("*Overall*")
        lines.append(f"Period: {overall.date_from} to {overall.date_to}")
        lines.append(
            f"Trades: {overall.trades} | Win rate: {overall.win_rate:.0%} | "
            f"Total P/L: ${overall.total_pnl:,.2f} | Avg P/L/trade: ${overall.avg_pnl:,.2f}"
        )

    if month_summaries_list:
        asset_stats = asset_stats or []
        symbols = sorted({a.symbol for a in asset_stats})

        lines.append("")
        lines.append("*Monthly Summary*")
        lines.append(_code_block(_monthly_summary_table(month_summaries_list, confidence)))

        lines.append("")
        lines.append("*By Asset*")
        for month_summary in month_summaries_list:
            lines.append(month_summary.month)
            lines.append(_code_block(_by_asset_table_for_month(month_summary.month, asset_stats, symbols)))

    if corr_result is not None and corr_result.top_pairs:
        lines.append("")
        lines.append("Top correlated pairs (rolling window):")
        for pair in corr_result.top_pairs:
            delta_str = f"{pair.delta:+.2f}" if pair.delta == pair.delta else "n/a"  # NaN check
            lines.append(f"  {pair.symbol_a}/{pair.symbol_b}: {pair.rolling_corr:+.2f} (Δ vs baseline: {delta_str})")

    if corr_result is not None and corr_result.bottom_pairs:
        lines.append("")
        lines.append("Top uncorrelated pairs (risk-reducing, rolling window):")
        for pair in corr_result.bottom_pairs:
            delta_str = f"{pair.delta:+.2f}" if pair.delta == pair.delta else "n/a"  # NaN check
            lines.append(f"  {pair.symbol_a}/{pair.symbol_b}: {pair.rolling_corr:+.2f} (Δ vs baseline: {delta_str})")

    if quarterly_pc1:
        latest_quarter, latest_value = quarterly_pc1[-1]
        value_str = f"{latest_value:.1%}" if latest_value is not None else "n/a"
        lines.append("")
        lines.append(f"PC1 concentration ({latest_quarter}): {value_str} of variance explained (see chart)")

    return "\n".join(lines)
