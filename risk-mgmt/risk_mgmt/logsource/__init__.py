"""LogSource abstraction: anything that can produce a list of closed Trade records for
risk analysis, regardless of what file format it actually reads from disk."""

import datetime as dt
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Trade:
    symbol: str
    direction: str  # "buy" | "sell"
    entry_time: dt.datetime
    entry_price: float
    exit_time: dt.datetime
    exit_price: float
    exit_reason: str  # "tp" | "sl" | "time" | "unknown"
    lots: float
    pnl_money: float  # net of commission and swap where the source reports them
    r_multiple: float
    risk_money: float | None = None  # $ risked at entry (SL distance priced in); None when unrecoverable
    strategy: str = ""  # which EA produced it - the portfolio view groups on this
    commission: float = 0.0  # already included in pnl_money; kept for cost attribution
    swap: float = 0.0  # idem


class LogSource(Protocol):
    def load_trades(self) -> list[Trade]:
        """Returns every closed trade found in the source, in no particular order."""
        ...
