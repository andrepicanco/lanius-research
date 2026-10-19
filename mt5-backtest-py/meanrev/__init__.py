"""Research toolkit for the mean_rev_1 strategy.

Notebook 01 (`notebooks/01_forex_mean_rev.ipynb`) stays as it is, as the exploratory
reference. This package is the version that can be trusted with a decision: it fills on
the bar after the signal, holds one position at a time, charges the spread the terminal
recorded, simulates stops and targets, and separates in-sample fitting from
out-of-sample measurement.

Typical use, and the order the notebook follows:

    df      = data.load_rates("AUDCHF", "M30", start, end)
    result  = engine.run_backtest(df, "AUDCHF", SignalParams(), EA_DEFAULT, CostModel())
    priced  = metrics.to_money(result.trades, value_per_point, point)
    stats   = metrics.compute_stats(priced)
    export.write_trades_csv(priced, "reports/meanrev")   # -> risk-mgmt
"""

from . import costs, data, engine, exits, export, indicators, metrics, signals, validation
from .costs import FREE, CostModel
from .engine import NOTEBOOK_01, BacktestResult, ExecParams, run_backtest
from .exits import EA_DEFAULT, TIME_ONLY_6, ExitRules
from .metrics import Sizing, compute_stats, to_money
from .signals import SignalParams

__all__ = [
    "costs", "data", "engine", "exits", "export", "indicators", "metrics", "signals",
    "validation", "CostModel", "FREE", "ExecParams", "NOTEBOOK_01", "BacktestResult",
    "run_backtest", "ExitRules", "EA_DEFAULT", "TIME_ONLY_6", "SignalParams", "Sizing",
    "compute_stats", "to_money",
]
