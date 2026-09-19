from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd


def init() -> None:
    if not mt5.initialize():
        raise RuntimeError(f"Falha ao conectar ao terminal MT5: {mt5.last_error()}")


def get_rates_df(symbol: str, timeframe: int, start: datetime, end: datetime) -> pd.DataFrame:

    rates = mt5.copy_rates_range(symbol, timeframe, start, end)
    if rates is None:
        raise RuntimeError(f"Falha ao obter dados de {symbol}: {mt5.last_error()}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def shutdown() -> None:
    mt5.shutdown()
