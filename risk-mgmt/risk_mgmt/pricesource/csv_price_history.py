"""Offline price history: a directory of <symbol>.csv files (columns: date, close),
usable in local/CI runs with no MT5 connection at all."""

from pathlib import Path

import pandas as pd


class CsvPriceSource:
    def __init__(self, directory: str | Path):
        self._directory = Path(directory)

    def load_daily_closes(self, symbols: list[str]) -> pd.DataFrame:
        series_by_symbol = {}
        for symbol in symbols:
            path = self._directory / f"{symbol}.csv"
            if not path.exists():
                continue
            df = pd.read_csv(path, parse_dates=["date"])
            series_by_symbol[symbol] = df.set_index("date")["close"].sort_index()

        if not series_by_symbol:
            return pd.DataFrame()
        return pd.DataFrame(series_by_symbol)
