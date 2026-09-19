"""Daily close price history via the MT5 terminal's own API - only usable where a real
MT5 terminal is reachable (a local machine, or a self-hosted GitHub Actions runner with
one open and logged in). Mirrors the connection convention of
IdxSwing91_Python/idxswing91/data/mt5_history.py, duplicated rather than imported since
Risk Mgmt has no dependency on that sibling project.
"""

import datetime as dt

import pandas as pd

from ..account import AccountConfig


def ensure_connection(account: AccountConfig | None = None) -> None:
    import MetaTrader5 as mt5

    if mt5.terminal_info():
        return
    kwargs = account.to_mt5_kwargs() if account else {}
    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"mt5.initialize() failed: {mt5.last_error()}")


class Mt5PriceSource:
    def __init__(self, date_from: dt.datetime, date_to: dt.datetime, account: AccountConfig | None = None):
        self._date_from = date_from
        self._date_to = date_to
        self._account = account

    def load_daily_closes(self, symbols: list[str]) -> pd.DataFrame:
        import MetaTrader5 as mt5

        ensure_connection(self._account)
        series_by_symbol = {}

        for symbol in symbols:
            if not mt5.symbol_select(symbol, True):
                continue
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_D1, self._date_from, self._date_to)
            if rates is None or len(rates) == 0:
                continue
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s").dt.normalize()
            series_by_symbol[symbol] = df.set_index("time")["close"].sort_index()

        if not series_by_symbol:
            return pd.DataFrame()
        return pd.DataFrame(series_by_symbol)
