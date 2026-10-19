"""Transaction-cost model.

Notebook 01 charged nothing, which is why it reported +67% on an AUDCHF M30 setup whose
average per-trade edge (0.01037%) is smaller than its average round-trip spread
(0.01069%). Costs are not a refinement here - they decide the sign of the result.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CostModel:
    """Costs of one round turn.

    `use_bar_spread` charges the spread the terminal actually recorded on the entry bar
    rather than an average, which matters because the distribution is skewed: AUDCHF M30
    has a median of 2 points but a mean of 6.1, the tail being rollover and thin hours.

    A round turn crosses the spread once in total (buy at ask / sell at bid, or the
    reverse), so the charge is one full spread, not two.
    """

    use_bar_spread: bool = True
    fixed_spread_points: float = 0.0  # used when use_bar_spread is False
    slippage_points: float = 0.0      # per side
    commission_per_lot: float = 0.0   # account currency, round turn
    swap_long_per_lot: float = 0.0    # account currency, per night
    swap_short_per_lot: float = 0.0

    def spread_cost_price(self, spread_points: pd.Series | np.ndarray | float, point: float):
        """Cost in price units of crossing the spread once, plus slippage on both sides."""
        points = spread_points if self.use_bar_spread else self.fixed_spread_points
        return (np.asarray(points, dtype=float) + 2 * self.slippage_points) * point

    def commission_money(self, lots: pd.Series | np.ndarray | float):
        return np.asarray(lots, dtype=float) * self.commission_per_lot

    def swap_money(self, lots, nights, direction: pd.Series | np.ndarray | str):
        per_lot = np.where(
            np.asarray(direction) == "buy", self.swap_long_per_lot, self.swap_short_per_lot
        )
        return np.asarray(lots, dtype=float) * np.asarray(nights, dtype=float) * per_lot


FREE = CostModel(use_bar_spread=False, fixed_spread_points=0.0)
"""No costs at all - the notebook-01 assumption, kept named so that using it is a
visible choice in the code rather than an omission."""
