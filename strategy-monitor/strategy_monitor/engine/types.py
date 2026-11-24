"""Resultado bruto de uma simulacao (antes de virar `models.Trade`)."""

from dataclasses import dataclass


@dataclass
class SimTrade:
    side: int              # +1 compra, -1 venda
    t_in: int              # epoch (horario do servidor tratado como UTC)
    t_out: int
    price_in: float
    price_out: float
    lots: float
    sl0: float
    tp0: float             # 0 = sem TP
    reason: str            # 'sl' | 'tp'
    profit: float
    swap: float
