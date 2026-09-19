"""PriceSource abstraction: anything that can produce a daily close-price series per
symbol for correlation analysis, independent of whether that comes from a live MT5
terminal or an offline CSV directory."""

from typing import Protocol

import pandas as pd


class PriceSource(Protocol):
    def load_daily_closes(self, symbols: list[str]) -> pd.DataFrame:
        """Returns a DataFrame indexed by date, one column per symbol, of daily close
        prices. Symbols with no data available are simply absent from the columns."""
        ...
