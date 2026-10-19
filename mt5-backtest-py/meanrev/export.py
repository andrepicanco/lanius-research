"""Writes the ledger in the shape risk-mgmt already reads.

`risk_mgmt/logsource/idxswing91_csv.py` picks up `*_trades.csv` from a directory and
needs exactly these columns. Matching that format means a Python backtest can be fed
into the risk report with `--strategy MeanRev1=<dir>` without either project importing
the other - the contract is the file, not the code, which is what the Lanius CLAUDE.md
asks for.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

RISK_MGMT_COLUMNS = [
    "symbol", "direction", "entry_time", "entry_price", "exit_time", "exit_price",
    "exit_reason", "lots", "pnl_money", "r_multiple",
]

# risk-mgmt's Trade.exit_reason is typed as "tp" | "sl" | "time" | "unknown"; the
# research-only exit rules have no counterpart there, so they collapse to "time"
# (they are all "the strategy decided to close", as opposed to the broker doing it).
_EXIT_REASON_MAP = {
    "sl": "sl", "tp": "tp", "time": "time",
    "rsi_revert": "time", "first_profitable_close": "time", "sma_touch": "time",
    "end_of_data": "unknown",
}


def write_trades_csv(trades: pd.DataFrame, directory: str | Path, symbol: str | None = None) -> Path:
    """Writes <symbol>_trades.csv into `directory` and returns the path."""
    missing = [c for c in ("pnl_money", "lots", "r_multiple") if c not in trades.columns]
    if missing:
        raise ValueError(
            f"ledger is not priced yet - missing {missing}. Run metrics.to_money() first; "
            f"the engine works in price units and cannot know the symbol's value per point."
        )

    symbol = symbol or trades["symbol"].iloc[0]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    out = trades.copy()
    out["exit_reason"] = out["exit_reason"].map(_EXIT_REASON_MAP).fillna("unknown")
    # tz-aware timestamps round-trip through read_csv(parse_dates=...) fine, but
    # risk-mgmt compares them against naive datetimes from other sources, so drop the
    # offset here rather than have the comparison fail there.
    for column in ("entry_time", "exit_time"):
        out[column] = pd.to_datetime(out[column], utc=True).dt.tz_localize(None)

    path = directory / f"{symbol}_trades.csv"
    out[RISK_MGMT_COLUMNS].to_csv(path, index=False)
    return path
