"""Loading and caching of MT5 rates.

Notebook 01 cached with a bare `try: read_csv except: download`, which meant a cache
file covering a shorter period than requested was used silently, and `time` came back
from disk as a string instead of a timestamp. Both are the kind of thing that produces
a plausible wrong answer rather than an error, so this module makes them loud.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "notebooks" / "import"

# How far back the broker's API actually serves each timeframe, as measured in
# notebook 01. These are not MT5 limits in general - they are what this account
# returned - so re-check them if the broker or the account changes.
HISTORY_STARTS = {
    "M15": dt.date(2024, 1, 1),
    "M30": dt.date(2022, 1, 1),
    "H1": dt.date(2020, 1, 1),
}

_RATE_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]

# MT5's SYMBOL_DIGITS tops out at 8 in practice; anything beyond that is float noise.
MAX_DIGITS = 8


def cache_path(symbol: str, tf_label: str, cache_dir: Path | None = None) -> Path:
    return (cache_dir or CACHE_DIR) / f"{symbol}_{tf_label}.csv"


def load_rates(
    symbol: str,
    tf_label: str,
    start: dt.datetime,
    end: dt.datetime,
    cache_dir: Path | None = None,
    allow_download: bool = True,
) -> pd.DataFrame:
    """Returns OHLCV+spread bars for [start, end], from cache when it covers the range.

    Raises rather than silently truncating: a backtest run over less data than you
    asked for is a wrong result, not a partial one.
    """
    start, end = _as_utc(start), _as_utc(end)
    _check_history_limit(tf_label, start)

    path = cache_path(symbol, tf_label, cache_dir)
    df = _read_cache(path)

    if df is None or not _covers(df, start, end):
        if not allow_download:
            have = "no cache file" if df is None else f"cache covers {df['time'].min()} .. {df['time'].max()}"
            raise FileNotFoundError(
                f"{symbol} {tf_label}: need {start} .. {end} but {have}, and "
                f"allow_download=False. Open MT5 and re-run, or widen the cache."
            )
        df = _download(symbol, tf_label, start, end)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)

    window = df[(df["time"] >= start) & (df["time"] <= end)].reset_index(drop=True)
    if window.empty:
        raise ValueError(f"{symbol} {tf_label}: no bars in {start} .. {end}")
    return window


def _as_utc(moment: dt.datetime) -> pd.Timestamp:
    ts = pd.Timestamp(moment)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _check_history_limit(tf_label: str, start: pd.Timestamp) -> None:
    limit = HISTORY_STARTS.get(tf_label)
    if limit is not None and start.date() < limit:
        raise ValueError(
            f"{tf_label} history on this account starts around {limit}, but {start.date()} "
            f"was requested. MT5 would return a shorter series without complaining; "
            f"adjust the start date or use a higher timeframe."
        )


def _read_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    # utc=True, not a bare parse: MT5 writes naive UTC timestamps, and letting pandas
    # infer leaves them tz-naive, which then compares wrong against tz-aware bounds.
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


# Markets are shut on weekends and holidays, so a cache downloaded for Jan 1 legitimately
# starts on Jan 3. Without slack, every request for a round date would look uncovered and
# trigger a pointless re-download - or fail outright with allow_download=False.
_COVERAGE_SLACK = pd.Timedelta(days=4)


def _covers(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    return (
        df["time"].min() <= start + _COVERAGE_SLACK
        and df["time"].max() >= end - _COVERAGE_SLACK
    )


def _download(symbol: str, tf_label: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    import sys

    import MetaTrader5 as mt5  # imported lazily so the package works without a terminal

    # mt5_data.py sits at the project root, one level above this package. Adding it here
    # rather than relying on the caller's sys.path means a script that imports meanrev
    # from anywhere can still download, instead of failing only on a cache miss.
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    import mt5_data

    mt5_data.init()
    tf = getattr(mt5, f"TIMEFRAME_{tf_label}")
    df = mt5_data.get_rates_df(symbol, tf, start.to_pydatetime(), end.to_pydatetime())
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df[_RATE_COLUMNS]


def point_size(close: pd.Series) -> float:
    """Infers the instrument's point (1 unit of the `spread` column) from price scale.

    The rates CSV carries `spread` in points but not the point size, and the symbol
    spec lives in MT5. For a 5-decimal FX pair a point is 1e-5, for a 2-decimal index
    it is 0.01. Inferred from the number of decimals actually present in the prices,
    which is exact for MT5 exports since they are written at the symbol's digits.

    Capped at MAX_DIGITS because the inference is only valid for prices that came from
    a terminal. Feed it a computed series - a surrogate built with exp(cumsum(...)), say
    - and float noise puts 15 decimals on every value, yielding a point of 1e-15 and a
    spread cost of essentially zero. That failure is silent and looks like a strategy
    that stopped paying costs, so callers working with synthetic prices should pass
    `point` explicitly rather than rely on this.
    """
    decimals = (
        close.dropna().astype(str).str.split(".").str[-1].str.len().max()
    )
    return float(10 ** -min(int(decimals), MAX_DIGITS))
