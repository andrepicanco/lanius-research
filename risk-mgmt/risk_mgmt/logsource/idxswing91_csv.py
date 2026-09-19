"""Reads trades directly from IdxSwing91_Python's own backtest output
(backtest_results/<symbol>_trades.csv, written by idxswing91/backtest/runner.py). Already
structured and already priced - no parsing or SymbolSpec needed, just a column rename.
"""

import datetime as dt
from pathlib import Path

import pandas as pd

from . import Trade


class IdxSwing91CsvSource:
    def __init__(self, paths: list[Path]):
        self._paths = paths

    @classmethod
    def from_directory(cls, directory: str | Path, pattern: str = "*_trades.csv") -> "IdxSwing91CsvSource":
        directory = Path(directory)
        paths = sorted(directory.glob(pattern))
        if not paths:
            raise FileNotFoundError(f"No files matching '{pattern}' found in {directory}")
        return cls(paths)

    def load_trades(self) -> list[Trade]:
        trades: list[Trade] = []
        for path in self._paths:
            df = pd.read_csv(path, parse_dates=["entry_time", "exit_time"])
            for row in df.itertuples(index=False):
                # r_multiple and pnl_money are both linear in profit_points, so their
                # ratio recovers the $ risk distance (risk_distance * tick_value/tick_size
                # * lots) without needing the raw SL price this CSV doesn't carry. Only
                # undefined when r_multiple is exactly 0 (risk_distance was 0, or unset).
                risk_money = row.pnl_money / row.r_multiple if row.r_multiple else None
                trades.append(
                    Trade(
                        symbol=row.symbol,
                        direction=row.direction,
                        entry_time=_to_datetime(row.entry_time),
                        entry_price=row.entry_price,
                        exit_time=_to_datetime(row.exit_time),
                        exit_price=row.exit_price,
                        exit_reason=row.exit_reason,
                        lots=row.lots,
                        pnl_money=row.pnl_money,
                        r_multiple=row.r_multiple,
                        risk_money=risk_money,
                    )
                )
        return trades


def _to_datetime(value) -> dt.datetime:
    return value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
