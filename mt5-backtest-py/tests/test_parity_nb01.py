"""Parity with notebook 01.

The engine is only worth trusting if it can first reproduce the thing it replaces. So
`notebook_01_returns` below is a literal transcription of the notebook's cells - column
arithmetic, same formulas, same sign convention - and the engine in NOTEBOOK_01 mode has
to land on the same number.

This proves internal consistency, not correctness. It is what stops a refactor from
quietly changing the baseline; the external reconciliation against a Strategy Tester run
is the separate, stronger check described in the notebook.
"""

import numpy as np
import pandas as pd
import pytest
from conftest import make_bars

from meanrev.data import CACHE_DIR
from meanrev.engine import NOTEBOOK_01, run_backtest
from meanrev.exits import ExitRules
from meanrev.signals import SignalParams

AUDCHF_M30 = CACHE_DIR / "AUDCHF_M30.csv"

# The parameters notebook 01 recorded for AUDCHF M30 in its "Parametros ja descobertos"
# cell, and the gross total the engine reproduces for them.
NB01_STREAK = 1
NB01_EXIT_BARS = 7
NB01_TOTAL_PCT = 67.05


def notebook_01_returns(df, streak, exit_bars, sma_short=21, sma_long=88, rsi_period=2,
                        overbought=80, oversold=20, drop_warmup=True):
    """Notebook 01's calculation, transcribed rather than reimplemented."""
    d = df.copy()
    d["sma_21"] = d["close"].rolling(sma_short).mean()
    d["sma_88"] = d["close"].rolling(sma_long).mean()

    delta = d["close"].diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / rsi_period, adjust=False).mean()
    d["rsi_2"] = 100 - (100 / (1 + avg_gain / avg_loss))

    d["trend"] = (d["sma_21"] > d["sma_88"]).astype(int)
    d["overbought"] = (d["rsi_2"] > overbought).astype(int)
    d["oversold"] = (d["rsi_2"] < oversold).astype(int)

    for name in ("overbought", "oversold"):
        flag = d[name]
        d[f"{name}_seq"] = flag.groupby((flag != flag.shift()).cumsum()).cumsum()

    d["follows_trend"] = ((d["trend"] == 1) & (d["oversold"] == 1)) | (
        (d["trend"] == 0) & (d["overbought"] == 1)
    )

    raw = (d["close"].shift(-exit_bars) / d["close"] - 1) * 100
    d["ret"] = np.where(d["trend"] == 0, -raw, raw)

    selected = d["follows_trend"] & (
        (d["overbought_seq"] == streak) | (d["oversold_seq"] == streak)
    )
    if drop_warmup:
        # The notebook did NOT do this. With sma_88 still NaN the comparison is False,
        # so every warmup bar is labelled a downtrend and can emit a spurious sell;
        # test_warmup_bars_are_the_only_divergence measures what that costs.
        selected &= d[["sma_21", "sma_88", "rsi_2"]].notna().all(axis=1)

    return d.loc[selected, "ret"].dropna()


def synthetic_walk(bars=4000, seed=11):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(scale=0.002, size=bars)))
    wiggle = rng.normal(scale=0.001, size=bars)
    return make_bars(
        [
            (c * (1 + w), c * (1 + abs(w) + 0.001), c * (1 - abs(w) - 0.001), c)
            for c, w in zip(close, wiggle)
        ]
    )


@pytest.mark.parametrize("streak,exit_bars", [(1, 7), (2, 3), (3, 5)])
def test_engine_reproduces_notebook_01_on_synthetic_bars(streak, exit_bars):
    df = synthetic_walk()
    expected = notebook_01_returns(df, streak, exit_bars)

    result = run_backtest(
        df, "SYN", SignalParams(streak=streak), ExitRules(time_bars=exit_bars),
        exec_params=NOTEBOOK_01,
    )
    # The engine's final trade runs out of bars where the notebook's shift produced NaN.
    engine_returns = result.trades.loc[result.trades["exit_reason"] == "time", "gross_pct"]

    assert len(engine_returns) == len(expected)
    assert engine_returns.sum() == pytest.approx(expected.sum(), rel=1e-9)


def test_warmup_bars_are_the_only_divergence_from_the_notebook():
    """Quantifies the one place the transcription and the engine disagree.

    Notebook 01 read `sma_21 > sma_88` while both were still NaN, which evaluates False
    and therefore reads as a downtrend - letting overbought bars inside the warmup fire
    sells the strategy could never have taken. The engine masks the warmup instead.
    """
    df = synthetic_walk()
    with_warmup = notebook_01_returns(df, 1, 7, drop_warmup=False)
    without = notebook_01_returns(df, 1, 7, drop_warmup=True)

    assert len(with_warmup) > len(without)
    assert with_warmup.sum() != pytest.approx(without.sum())


@pytest.mark.skipif(not AUDCHF_M30.exists(), reason="AUDCHF_M30.csv not in notebooks/import")
def test_engine_reproduces_the_recorded_audchf_total():
    """Pins the headline number the notebook's gross-to-net waterfall starts from."""
    df = pd.read_csv(AUDCHF_M30, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)

    result = run_backtest(
        df, "AUDCHF", SignalParams(streak=NB01_STREAK), ExitRules(time_bars=NB01_EXIT_BARS),
        exec_params=NOTEBOOK_01,
    )
    assert result.trades["gross_pct"].sum() == pytest.approx(NB01_TOTAL_PCT, abs=0.01)
    assert result.trades["net_pct"].sum() == pytest.approx(NB01_TOTAL_PCT, abs=0.01)  # costs off


@pytest.mark.skipif(not AUDCHF_M30.exists(), reason="AUDCHF_M30.csv not in notebooks/import")
def test_realistic_execution_halves_the_notebook_result_before_any_cost():
    """Fills and the one-position rule, on their own, are worth more than half the edge."""
    from meanrev.engine import ExecParams

    df = pd.read_csv(AUDCHF_M30, parse_dates=["time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    args = (df, "AUDCHF", SignalParams(streak=NB01_STREAK), ExitRules(time_bars=NB01_EXIT_BARS))

    notebook = run_backtest(*args, exec_params=NOTEBOOK_01)
    realistic = run_backtest(*args, exec_params=ExecParams())

    assert realistic.signals_skipped_overlap > 0
    assert realistic.trades["gross_pct"].sum() < notebook.trades["gross_pct"].sum() / 2
