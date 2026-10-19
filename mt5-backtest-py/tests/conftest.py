import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meanrev.signals import SignalParams  # noqa: E402

# Short periods so a few dozen bars are enough to produce a signal, while still leaving
# the trend SMAs far enough apart that one dip does not flip the trend - which is
# exactly what a naive 2/3 pair does, silently producing zero signals.
FAST = SignalParams(sma_short=3, sma_long=10, rsi_period=2, streak=1)

WARMUP_BARS = 30
SIGNAL_BAR = WARMUP_BARS   # the dip/spike bar
ENTRY_BAR = SIGNAL_BAR + 1  # where a next_open fill lands


def make_bars(rows, start="2024-01-01", freq="30min", spread=0):
    """Builds a rates frame from (open, high, low, close) tuples.

    Synthetic bars are how the engine tests stay readable: a hand-written range that
    straddles both the stop and the target says more than any slice of real data.
    """
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame.insert(0, "time", pd.date_range(start, periods=len(frame), freq=freq, tz="UTC"))
    frame["tick_volume"] = 1
    frame["spread"] = spread
    frame["real_volume"] = 0
    return frame


def up_then_dip(tail, dip=5.0):
    """A clean uptrend, one sharp dip that drives RSI-2 under 20, then `tail`.

    Produces exactly one buy signal, on bar SIGNAL_BAR. `tail` supplies the bars from
    ENTRY_BAR onward, so a test controls precisely what the position runs into.
    """
    rows = [(100.0 + i, 100.5 + i, 99.5 + i, 100.0 + i) for i in range(WARMUP_BARS)]
    close = 100.0 + (WARMUP_BARS - 1) - dip
    rows.append((100.5 + WARMUP_BARS - 1, 100.6 + WARMUP_BARS - 1, close - 0.5, close))
    return make_bars(rows + list(tail))


def down_then_spike(tail, spike=5.0):
    """Mirror image: a downtrend and one sharp spike, producing a single sell signal."""
    rows = [(100.0 - i, 100.5 - i, 99.5 - i, 100.0 - i) for i in range(WARMUP_BARS)]
    close = 100.0 - (WARMUP_BARS - 1) + spike
    rows.append((100.5 - WARMUP_BARS + 1, close + 0.5, 99.5 - WARMUP_BARS + 1, close))
    return make_bars(rows + list(tail))


def steady(start_price, count, step=0.2):
    """Quiet bars that trigger nothing, for padding out a holding period."""
    return [
        (start_price + i * step, start_price + i * step + 0.3,
         start_price + i * step - 0.3, start_price + i * step + step)
        for i in range(count)
    ]


@pytest.fixture
def flat_bars():
    """200 identical bars - no signal can fire, useful as a null baseline."""
    return make_bars([(1.0, 1.0, 1.0, 1.0)] * 200)
