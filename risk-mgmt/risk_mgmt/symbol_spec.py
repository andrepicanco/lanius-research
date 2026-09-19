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
