"""Live MT5 account state for --mode live: current equity, open position count, and
closed-trade history for VaR. Deal history is far simpler to reconstruct here than from
a Journal text file - MT5 groups every deal belonging to one position under the same
`position_id`, and the broker already computes each closing deal's `profit` in the
account currency, so no SymbolSpec/tick math is needed like the offline journal parser
needs.

Known gap: r_multiple is left at 0.0 for live-fetched trades - the original SL isn't
part of MT5's deal history, so the risk distance a live trade was opened against isn't
recoverable from this API alone. Only pnl_money (what VaR actually needs) is exact.
"""

import datetime as dt

from .account import AccountConfig
from .logsource import Trade
from .pricesource.mt5_price_history import ensure_connection


def fetch_current_equity(account: AccountConfig | None = None) -> float:
    import MetaTrader5 as mt5

    ensure_connection(account)
    info = mt5.account_info()
    if info is None:
        raise RuntimeError(f"mt5.account_info() returned None: {mt5.last_error()}")
    return float(info.equity)


def fetch_open_position_count(account: AccountConfig | None = None) -> int:
    import MetaTrader5 as mt5

    ensure_connection(account)
    positions = mt5.positions_get()
    return len(positions) if positions is not None else 0


def fetch_closed_trades(
    date_from: dt.datetime, date_to: dt.datetime, account: AccountConfig | None = None
) -> list[Trade]:
    import MetaTrader5 as mt5

    ensure_connection(account)
    deals = mt5.history_deals_get(date_from, date_to)
    if deals is None:
        return []

    by_position: dict[int, list] = {}
    for deal in deals:
        by_position.setdefault(deal.position_id, []).append(deal)

    trades: list[Trade] = []
    for group in by_position.values():
        group.sort(key=lambda d: d.time)
        entry_deal = next((d for d in group if d.entry == mt5.DEAL_ENTRY_IN), None)
        exit_deal = next((d for d in group if d.entry == mt5.DEAL_ENTRY_OUT), None)
        if entry_deal is None or exit_deal is None:
            continue  # still open, or a deal we don't model (partial close, balance op)

        direction = "buy" if entry_deal.type == mt5.DEAL_TYPE_BUY else "sell"
        trades.append(
            Trade(
                symbol=entry_deal.symbol,
                direction=direction,
                entry_time=dt.datetime.fromtimestamp(entry_deal.time),
                entry_price=entry_deal.price,
                exit_time=dt.datetime.fromtimestamp(exit_deal.time),
                exit_price=exit_deal.price,
                exit_reason="unknown",
                lots=entry_deal.volume,
                pnl_money=sum(d.profit for d in group),
                r_multiple=0.0,
            )
        )
    return trades
