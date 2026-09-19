"""Parses raw MQL5 Strategy Tester Journal text into closed Trade records.

This reads the terminal's OWN trade-log lines (deal fills, TP/SL triggers), not the
[IdxSwing91]/[MeanRev1]-prefixed lines the EA prints itself - those only carry the
entry/exit *decision*, never the realized fill price. The EA's own "placed BUY/SELL
stop @ ..." / "opened BUY/SELL @ ... SL=... ticket=..." line is used for one thing only:
to recover the *original* SL distance for r_multiple, since a trailing stop can move the
SL shown in the later TP/SL-triggered line away from what was actually risked at entry.
An EA whose entry log uses different wording simply gets no r_multiple/risk_money.

What this source structurally cannot give you, because the Journal text does not carry
it: commission and swap. P/L here is price difference times the SymbolSpec, so it is a
*gross* figure and will read higher than the Tester's own net profit. Use
`mt5_report_html` when the difference matters.

Two on-disk shapes are both handled, since MetaTrader actually produces different text
depending on where it comes from:
  - The plain-text "Experts" tab, copy-pasted: UTF-8, each line starts with a wall-clock
    "YYYY.MM.DD HH:MM:SS.mmm" prefix followed by a tab, then the market-time timestamp.
  - The real .log file MetaTester writes to disk: UTF-16 (with a BOM), each line laid
    out as "<code>\\t<col>\\tHH:MM:SS.mmm\\t<source>\\t<market-time+message>" - no date on
    the wall-clock column at all. Confirmed against a real exported .log file.
In both cases the piece this parser actually wants - "YYYY.MM.DD HH:MM:SS   <message>" -
appears verbatim somewhere in the line, so the timestamp regex below searches for that
substring rather than assuming a fixed column layout ahead of it.

Known fragility, not solved here: the deal/order-triggered lines are strings the MT5
terminal itself generates in its own UI language. Everything above was verified against
English-terminal output only. A non-English terminal will produce different wording and
this parser will silently find nothing - if that happens, prefer the idxswing91_csv
source instead of patching around every locale.
"""

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from ..account import AccountConfig
from ..symbol_spec import SymbolSpec
from . import Trade

_TIMESTAMP_RE = re.compile(
    r"(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})(?!\.\d)\s+(.*)$"  # date+time, not the wall-clock's HH:MM:SS.mmm
)
_DEAL_RE = re.compile(
    r"deal #\d+ (?P<dir>buy|sell) (?P<lots>[\d.]+) (?P<symbol>\S+) at (?P<price>[\d.]+) "
    r"done \(based on order #(?P<order_id>\d+)\)"
)
_TRIGGERED_RE = re.compile(
    r"(?P<kind>take profit|stop loss) triggered #(?P<ticket>\d+) (?P<dir>buy|sell) "
    r"(?P<lots>[\d.]+) (?P<symbol>\S+) (?P<entry_price>[\d.]+) sl: (?P<sl>[\d.]+) "
    r"tp: (?P<tp>[\d.]+) \[#(?P<new_order>\d+) (?:buy|sell) [\d.]+ \S+ at (?P<exit_price>[\d.]+)\]"
)
_PLACED_RE = re.compile(
    r"(?:placed (?:BUY|SELL) stop|opened (?:BUY|SELL)) @ [\d.]+ SL=(?P<sl>[\d.]+) "
    r"TP=[\d.]+ lots=[\d.]+ ticket=(?P<ticket>\d+)"
)

_TIMESTAMP_FMT = "%Y.%m.%d %H:%M:%S"

# The EA's own lines are prefixed "[EaName][SYMBOL][LEVEL]" by CLogger. The symbol is
# what keeps one backtest's ticket numbers from being confused with another's.
_LOG_PREFIX_RE = re.compile(r"\[[^\[\]]+\]\[([^\[\]]+)\]\[[^\[\]]+\]")


@dataclass
class _OpenPosition:
    symbol: str
    direction: str
    entry_price: float
    entry_time: dt.datetime
    lots: float
    order_id: int


def _parse_lines(lines: list[str]) -> list[dict]:
    """Pairs deals into trades with a FIFO queue per symbol.

    A deal closes the oldest open position on its symbol when it runs the opposite way,
    and opens a new one otherwise. Keying off the `triggered` lines alone - as this
    parser originally did - only ever recognised exits the *broker* caused, so every
    position an EA closed by itself was left open and silently dropped. That is not a
    rounding error: on a MeanRev1 run that closes on a bar count, it discarded 569 of
    657 trades and kept a residue of 70 stop losses against 18 take profits, turning a
    profitable run into a loss. The `triggered` lines are still read, but now only to
    label an exit as "sl"/"tp" rather than to discover it.
    """
    placed_sl: dict[tuple[str | None, int], float] = {}
    open_by_symbol: dict[str, list[_OpenPosition]] = {}
    exit_reason_by_order: dict[int, str] = {}
    raw_trades: list[dict] = []

    for line in lines:
        m = _TIMESTAMP_RE.search(line)
        if not m:
            continue
        ts = dt.datetime.strptime(m.group(1), _TIMESTAMP_FMT)
        rest = m.group(2)

        placed_m = _PLACED_RE.search(rest)
        if placed_m:
            prefix = _LOG_PREFIX_RE.search(rest)
            log_symbol = prefix.group(1) if prefix else None
            placed_sl[(log_symbol, int(placed_m.group("ticket")))] = float(placed_m.group("sl"))
            continue

        triggered_m = _TRIGGERED_RE.search(rest)
        if triggered_m:
            reason = "tp" if triggered_m.group("kind") == "take profit" else "sl"
            exit_reason_by_order[int(triggered_m.group("new_order"))] = reason
            continue

        deal_m = _DEAL_RE.search(rest)
        if not deal_m:
            continue

        order_id = int(deal_m.group("order_id"))
        price = float(deal_m.group("price"))
        lots = float(deal_m.group("lots"))
        symbol = deal_m.group("symbol")
        direction = deal_m.group("dir")

        queue = open_by_symbol.setdefault(symbol, [])
        if queue and queue[0].direction != direction:
            position = queue.pop(0)
            raw_trades.append(
                {
                    "symbol": position.symbol,
                    "direction": position.direction,
                    "entry_time": position.entry_time,
                    "entry_price": position.entry_price,
                    "exit_time": ts,
                    "exit_price": price,
                    # no `triggered` line means the EA asked for the close itself
                    "exit_reason": exit_reason_by_order.pop(order_id, "time"),
                    "lots": position.lots,
                    "entry_order_id": position.order_id,
                }
            )
        else:
            queue.append(
                _OpenPosition(
                    symbol=symbol,
                    direction=direction,
                    entry_price=price,
                    entry_time=ts,
                    lots=lots,
                    order_id=order_id,
                )
            )

    # Resolved here rather than when the deal was read, because the two lines are not
    # in a fixed order: IdxSwing91 logs its SL when it *places* a pending order, long
    # before the deal that fills it, while MeanRev1 enters at market and the terminal
    # records the deal first, logging the SL a line later. Matching after the whole file
    # is parsed works either way.
    for trade in raw_trades:
        key = trade.pop("entry_order_id")
        # symbol-scoped first: a log holding several backtests restarts ticket numbering
        # at 2 in each, so a bare ticket number is ambiguous across runs. The unscoped
        # key is the fallback for an EA whose lines carry no [Ea][SYMBOL][LEVEL] prefix.
        trade["original_sl"] = placed_sl.get((trade["symbol"], key), placed_sl.get((None, key)))

    return raw_trades  # priced later, once we know which SymbolSpec applies


def _price_trade(raw: dict, spec: SymbolSpec) -> Trade:
    direction = raw["direction"]
    entry_price = raw["entry_price"]
    exit_price = raw["exit_price"]
    lots = raw["lots"]

    profit_points = (exit_price - entry_price) if direction == "buy" else (entry_price - exit_price)
    pnl_money = (profit_points / spec.tick_size) * spec.tick_value * lots

    original_sl = raw["original_sl"]
    risk_distance = abs(entry_price - original_sl) if original_sl is not None else 0.0
    r_multiple = profit_points / risk_distance if risk_distance > 0 else 0.0
    risk_money = (risk_distance / spec.tick_size) * spec.tick_value * lots if original_sl is not None else None

    return Trade(
        symbol=raw["symbol"],
        direction=direction,
        entry_time=raw["entry_time"],
        entry_price=entry_price,
        exit_time=raw["exit_time"],
        exit_price=exit_price,
        exit_reason=raw["exit_reason"],
        lots=lots,
        pnl_money=pnl_money,
        r_multiple=r_multiple,
        risk_money=risk_money,
    )


def _read_text(path: Path) -> str:
    """Auto-detects the .log file's actual encoding: the real on-disk MetaTester .log is
    UTF-16 with a BOM; a plain-text "Experts" tab paste is UTF-8. Sniffing the BOM is
    more reliable than guessing from the file extension, since both end in .log."""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8", errors="replace")


def _fetch_spec_from_mt5(symbol: str, account: AccountConfig | None) -> SymbolSpec | None:
    """Best-effort live lookup, used only as a fallback when a symbol has no explicit
    entry in config/symbols.yaml. Returns None (never raises) on any failure - the
    caller is responsible for turning "still not found" into a clear error, since a
    silent None here just means "try the next thing" in both call sites that use it.
    """
    try:
        import MetaTrader5 as mt5

        from ..pricesource.mt5_price_history import ensure_connection

        ensure_connection(account)
        if not mt5.symbol_select(symbol, True):
            return None
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return SymbolSpec(symbol=symbol, tick_value=info.trade_tick_value, tick_size=info.trade_tick_size)
    except Exception:
        return None


class MQL5JournalSource:
    """Reads one or more raw MQL5 Strategy Tester Journal files (the plain-text "Experts"
    tab paste, or the real .log file MetaTester writes to disk) and reconstructs closed
    trades.

    `specs` is optional: any symbol not listed there is looked up live via MT5 instead
    (same connection convention as --mode live), since a symbol's tick value/size rarely
    changes and re-typing it into YAML is unnecessary busywork whenever a terminal is
    reachable. Explicit `specs` entries remain useful for pure offline/CI runs with no
    MT5 connection at all, or to override what the broker reports.
    """

    def __init__(self, paths: list[Path], specs: dict[str, SymbolSpec] | None = None,
                 account: AccountConfig | None = None, spec_fetcher=None):
        self._paths = paths
        self._specs = dict(specs or {})
        # spec_fetcher is injectable so tests can force the "no MT5 reachable" branch
        # deterministically, regardless of whether *this* machine happens to have a
        # live terminal open - defaults to the real live lookup otherwise.
        self._spec_fetcher = spec_fetcher or (lambda symbol: _fetch_spec_from_mt5(symbol, account))

    @classmethod
    def from_directory(cls, directory: str | Path, specs: dict[str, SymbolSpec] | None = None,
                        pattern: str = "*.log", account: AccountConfig | None = None,
                        spec_fetcher=None) -> "MQL5JournalSource":
        directory = Path(directory)
        paths = sorted(directory.glob(pattern))
        if not paths:
            raise FileNotFoundError(f"No files matching '{pattern}' found in {directory}")
        return cls(paths, specs, account, spec_fetcher)

    def _resolve_spec(self, symbol: str, path: Path) -> SymbolSpec:
        if symbol in self._specs:
            return self._specs[symbol]

        fetched = self._spec_fetcher(symbol)
        if fetched is not None:
            self._specs[symbol] = fetched
            return fetched

        raise KeyError(
            f"No SymbolSpec for '{symbol}' in config/symbols.yaml, and it couldn't be "
            f"fetched live from MT5 either (no terminal reachable, or the symbol isn't "
            f"in this account's Market Watch) - can't price its trades in {path}"
        )

    def load_trades(self) -> list[Trade]:
        trades: list[Trade] = []
        for path in self._paths:
            lines = _read_text(path).splitlines()
            for raw in _parse_lines(lines):
                spec = self._resolve_spec(raw["symbol"], path)
                trades.append(_price_trade(raw, spec))
        return trades
