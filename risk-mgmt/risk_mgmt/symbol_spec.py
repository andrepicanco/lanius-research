"""Pricing fields needed to turn a raw fill (entry/exit price + lots) into $ P/L.

Deliberately smaller than IdxSwing91_Python's SymbolSpec (which also carries
volume_min/max/step for order sizing) - Risk Mgmt never places orders, it only
prices trades that already happened.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    tick_value: float
    tick_size: float

    def __post_init__(self):
        """Rejects a non-positive tick value or size at construction.

        Both are multiplied straight into every P/L figure, so a zero here doesn't fail
        - it silently reports $0.00 for every trade on the symbol while the trade count
        stays right, which reads like "the strategy broke even" rather than like a bug.
        That is exactly how it presented: ASXAUD showed 83 correctly parsed trades worth
        658.10 points and $0.00 of P/L, because MT5 returned trade_tick_value = 0 for an
        AUD-denominated CFD on a USD account.
        """
        if self.tick_value <= 0 or self.tick_size <= 0:
            raise ValueError(
                f"{self.symbol}: tick_value and tick_size must both be > 0, got "
                f"tick_value={self.tick_value}, tick_size={self.tick_size}. MT5 reports 0 "
                f"when it cannot resolve the conversion to the account currency - add the "
                f"symbol (and its quote-currency pair) to Market Watch, or set the values "
                f"by hand under `specs:` in config/symbols.yaml."
            )
