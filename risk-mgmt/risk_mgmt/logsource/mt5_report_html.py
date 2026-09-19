"""Reads closed trades from a MetaTrader 5 Strategy Tester HTML report - the file the
Tester's "Report -> Save as Report" produces for any backtest, whichever EA ran it.

Why this source rather than mql5_journal: the Journal parser can only pair an exit that
came from a TP/SL trigger, so an EA that closes its own positions (MeanRev1's time-based
exit) leaves it with entries it can never match. It also reads the original stop loss out
of one specific EA's log phrasing. This report carries the Deals and Orders tables
instead, which every backtest produces the same way.

On localization: MetaTrader translates this report's column headers to the terminal's UI
language, but writes the *values* in English - `buy`/`sell`/`in`/`out`/`balance`/
`filled`, and the `sl `/`tp ` prefixes on an exit deal's comment. This parser reads
values and column positions only, never header text. Verified against a
Portuguese-language terminal.

It also recovers what live_state.py structurally cannot: the original stop loss, joined
from the Orders table by order number, which is what makes r_multiple/risk_money real
numbers rather than placeholders.

Pairing assumption: deals are matched FIFO per symbol - an `out` deal closes the oldest
still-open position on that symbol. That is exact for an EA holding one position at a
time (both IdxSwing91 and MeanRev1 do), and is MetaTrader's own default close order
otherwise.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import Trade

_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_SETTING_RE = re.compile(r"^([A-Za-z_]\w*)=(.*)$")

_TIME_FMT = "%Y.%m.%d %H:%M:%S"
_DEAL_COLS = 13   # Time Deal Symbol Type Direction Volume Price Order Commission Swap Profit Balance Comment
_ORDER_COLS = 11  # OpenTime Order Symbol Type Volume Price S/L T/P Time State Comment


def _read_text(path: Path) -> str:
    """The Tester writes this report as UTF-16 with a BOM; a hand-saved or re-encoded
    copy can be UTF-8. Sniffing the BOM is more reliable than trusting the extension."""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8", errors="replace")


def _cells(row_html: str) -> list[str]:
    out = []
    for cell in _CELL_RE.findall(row_html):
        text = _TAG_RE.sub("", cell).replace("&nbsp;", " ").replace("\xa0", " ")
        out.append(text.strip())
    return out


def _num(text: str) -> float | None:
    """MT5 formats numbers for the terminal's locale: '10 000.00' with a space as the
    thousands separator, and a comma as the decimal mark in locales that use one.
    Returns None for an empty cell (an exit order's blank S/L, for instance)."""
    if not text:
        return None
    cleaned = text.replace(" ", "")
    # Whichever separator comes last is the decimal mark; the other is grouping.
    if cleaned.rfind(",") > cleaned.rfind("."):
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    if cleaned in ("", "-", "."):
        return None
    return float(cleaned)


def _exit_reason(comment: str) -> str:
    """An exit deal's comment is '' for a close the EA requested itself (the time-based
    exit), or 'sl <price>' / 'tp <price>' when the broker's stop did it. Classifying by
    comparing the fill against the order's SL/TP instead would misread every stop that
    filled with slippage or through a gap."""
    head = comment.strip().split(" ", 1)[0].lower()
    if head in ("sl", "tp"):
        return head
    return "time" if not comment.strip() else "unknown"


@dataclass
class _Order:
    sl: float | None
    tp: float | None
    comment: str


@dataclass
class _OpenPosition:
    symbol: str
    direction: str
    entry_time: dt.datetime
    entry_price: float
    lots: float
    commission: float
    order_no: int


@dataclass
class ParsedReport:
    """One Strategy Tester report: its trades plus the run metadata the portfolio view
    needs (which strategy, which symbol, what the account started with)."""

    path: Path
    strategy: str
    symbol: str
    initial_balance: float | None
    trades: list[Trade] = field(default_factory=list)


def _parse_orders(rows: list[list[str]]) -> dict[int, _Order]:
    orders: dict[int, _Order] = {}
    for c in rows:
        if len(c) != _ORDER_COLS or not c[1].isdigit():
            continue
        kind = c[3].split(" ", 1)[0].lower()  # 'buy', 'sell', or 'buy stop'/'sell limit'
        if kind not in ("buy", "sell"):
            continue
        orders[int(c[1])] = _Order(sl=_num(c[6]), tp=_num(c[7]), comment=c[10])
    return orders


def _parse_settings(rows: list[list[str]]) -> dict[str, str]:
    """The 'Input parameters' block, as `InpName=value` lines. The label column beside
    it is translated, so this keys off the EA's own input names instead."""
    settings = {}
    for c in rows:
        if len(c) != 2:
            continue
        m = _SETTING_RE.match(c[1].strip())
        if m:
            settings[m.group(1)] = m.group(2).strip()
    return settings


def _parse_report(path: Path, strategy_override: str | None) -> ParsedReport:
    rows = [_cells(r) for r in _TR_RE.findall(_read_text(path))]
    orders = _parse_orders(rows)
    settings = _parse_settings(rows)

    initial_balance = None
    open_by_symbol: dict[str, list[_OpenPosition]] = {}
    trades: list[Trade] = []
    priced: list[tuple[Trade, float, float, float]] = []
    entry_comments: list[str] = []
    symbols: list[str] = []

    for c in rows:
        if len(c) != _DEAL_COLS:
            continue

        if c[3] == "balance" and initial_balance is None:
            initial_balance = _num(c[11])
            continue

        direction, leg = c[3], c[4].strip().lower()
        if direction not in ("buy", "sell"):
            continue

        if leg == "in/out":
            raise ValueError(
                f"{path.name} contains a reversal deal (direction 'in/out'), which this "
                f"parser does not model - it would have to split one deal across two "
                f"positions. Re-run the backtest on a hedging account, or open an issue."
            )
        if leg not in ("in",) and not leg.startswith("out"):
            continue

        when = dt.datetime.strptime(c[0], _TIME_FMT)
        symbol, lots, price = c[2], _num(c[5]), _num(c[6])
        order_no = int(c[7]) if c[7].isdigit() else -1
        commission = _num(c[8]) or 0.0

        if leg == "in":
            symbols.append(symbol)
            entry_comments.append(c[12])
            open_by_symbol.setdefault(symbol, []).append(
                _OpenPosition(symbol, direction, when, price, lots, commission, order_no)
            )
            continue

        queue = open_by_symbol.get(symbol) or []
        if not queue:
            continue  # an exit for a position this report never showed opening
        pos = queue.pop(0)

        swap = _num(c[9]) or 0.0
        gross = _num(c[10]) or 0.0
        commission += pos.commission

        order = orders.get(pos.order_no)
        sl = order.sl if order else None
        risk_distance = abs(pos.entry_price - sl) if sl else 0.0
        profit_points = (price - pos.entry_price) if pos.direction == "buy" else (pos.entry_price - price)

        trade = Trade(
            symbol=symbol,
            direction=pos.direction,
            entry_time=pos.entry_time,
            entry_price=pos.entry_price,
            exit_time=when,
            exit_price=price,
            exit_reason=_exit_reason(c[12]),
            lots=pos.lots,
            pnl_money=gross + commission + swap,
            r_multiple=profit_points / risk_distance if risk_distance else 0.0,
            risk_money=None,  # priced below, once the whole file gives a stable $/point
            strategy="",  # filled in below, once the whole file has been read
            commission=commission,
            swap=swap,
        )
        trades.append(trade)
        priced.append((trade, gross, profit_points, risk_distance))

    _apply_risk_money(priced)

    strategy = strategy_override or _detect_strategy(entry_comments, settings, path)
    for trade in trades:
        trade.strategy = strategy

    return ParsedReport(
        path=path,
        strategy=strategy,
        symbol=max(set(symbols), key=symbols.count) if symbols else "",
        initial_balance=initial_balance,
        trades=trades,
    )


def _apply_risk_money(priced: list[tuple[Trade, float, float, float]]) -> None:
    """Prices each trade's SL distance in account currency.

    The broker already converted points to money once, in the deal's own profit figure,
    so the conversion rate is recoverable as profit/points - no tick_value/tick_size
    lookup and no live MT5 connection needed. Taken per (symbol, lots) as a median
    rather than trade by trade, because a trade that closed a hair from breakeven has a
    near-zero denominator and would otherwise produce an absurd rate.
    """
    rates: dict[tuple[str, float], list[float]] = {}
    for trade, gross, profit_points, _ in priced:
        if not profit_points or not gross:
            continue
        rates.setdefault((trade.symbol, trade.lots), []).append(abs(gross / profit_points))

    medians = {}
    for key, values in rates.items():
        values.sort()
        medians[key] = values[len(values) // 2]

    for trade, _, _, risk_distance in priced:
        rate = medians.get((trade.symbol, trade.lots))
        if risk_distance and rate:
            trade.risk_money = risk_distance * rate


def _detect_strategy(entry_comments: list[str], settings: dict[str, str], path: Path) -> str:
    """An entry deal carries the EA's InpTradeComment as its comment, which is the most
    direct label available and needs no flag from the user. Falls back to the input
    block, then to the file name."""
    named = [c for c in entry_comments if c]
    if named:
        return max(set(named), key=named.count)
    if settings.get("InpTradeComment"):
        return settings["InpTradeComment"]
    return path.stem


class Mt5ReportHtmlSource:
    def __init__(self, paths: list[Path], strategy: str | None = None):
        self._paths = paths
        self._strategy = strategy

    @classmethod
    def from_directory(cls, directory: str | Path, strategy: str | None = None,
                       pattern: str = "*.htm*") -> "Mt5ReportHtmlSource":
        directory = Path(directory)
        paths = sorted(p for p in directory.glob(pattern) if p.is_file())
        if not paths:
            raise FileNotFoundError(f"No files matching '{pattern}' found in {directory}")
        return cls(paths, strategy)

    def load_reports(self) -> list[ParsedReport]:
        reports = [_parse_report(p, self._strategy) for p in self._paths]
        if not any(r.trades for r in reports):
            names = ", ".join(r.path.name for r in reports)
            raise ValueError(
                f"No closed trades found in: {names}. These should be MetaTrader 5 "
                f"Strategy Tester reports saved as HTML - a bare 'Graph'/'Backtest' page "
                f"without the Deals table won't work."
            )
        return reports

    def load_trades(self) -> list[Trade]:
        return [t for report in self.load_reports() for t in report.trades]
