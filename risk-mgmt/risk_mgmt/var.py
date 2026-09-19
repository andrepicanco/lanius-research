"""Daily P/L aggregation and parametric VaR.

'Returns' here means realized daily P/L in $ from closed trades, not mark-to-market of
price - a trade's full P/L is attributed to the day it closed. This is a reasonable,
standard treatment for a stop/target swing strategy where risk is genuinely
path-dependent on trade outcomes rather than continuous price exposure - but it has real
limitations worth remembering when reading the output: days with no trade closes are
flat (0), an open position's unrealized risk isn't reflected until it closes, and
parametric VaR's normality assumption sits less comfortably on a P/L series shaped by
capped R-multiples (stop-loss/take-profit) than it would on continuous price returns.
Historical/empirical VaR would relax that last assumption but isn't implemented here.
"""

import math
from dataclasses import dataclass

import pandas as pd

from .logsource import Trade

# Peter Acklam's rational approximation of the standard normal quantile function
# (inverse CDF), ~1e-9 max error - avoids pulling in scipy for a single function.
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00)
_P_LOW = 0.02425


def _z_score(confidence: float) -> float:
    """Standard normal quantile (inverse CDF) at `confidence`, e.g. 0.95 -> ~1.645."""
    p = confidence
    if not (0.0 < p < 1.0):
        raise ValueError("confidence must be in (0, 1)")

    if p < _P_LOW:
        q = math.sqrt(-2 * math.log(p))
        return (((((_C[0]*q+_C[1])*q+_C[2])*q+_C[3])*q+_C[4])*q+_C[5]) / \
               ((((_D[0]*q+_D[1])*q+_D[2])*q+_D[3])*q+1)
    if p <= 1 - _P_LOW:
        q = p - 0.5
        r = q * q
        return (((((_A[0]*r+_A[1])*r+_A[2])*r+_A[3])*r+_A[4])*r+_A[5])*q / \
               (((((_B[0]*r+_B[1])*r+_B[2])*r+_B[3])*r+_B[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((_C[0]*q+_C[1])*q+_C[2])*q+_C[3])*q+_C[4])*q+_C[5]) / \
            ((((_D[0]*q+_D[1])*q+_D[2])*q+_D[3])*q+1)


def daily_pnl_series(trades: list[Trade]) -> pd.Series:
    """One value per calendar day, indexed by exit date, summed across every symbol -
    the portfolio-level daily 'return' the VaR curves are computed from."""
    if not trades:
        return pd.Series(dtype=float)

    df = pd.DataFrame({"exit_date": [pd.Timestamp(t.exit_time).normalize() for t in trades],
                        "pnl_money": [t.pnl_money for t in trades]})
    daily = df.groupby("exit_date")["pnl_money"].sum().sort_index()

    full_range = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    return daily.reindex(full_range, fill_value=0.0)


def rolling_parametric_var(daily_pnl: pd.Series, window_days: int, confidence: float) -> pd.Series:
    """Parametric VaR at each day t, using the trailing `window_days` daily P/L ending at
    t. Expressed as a positive $ figure (potential loss); NaN before enough history exists.
    """
    z = _z_score(confidence)
    mean = daily_pnl.rolling(window_days).mean()
    std = daily_pnl.rolling(window_days).std(ddof=1)
    var = -(mean - z * std)
    return var.clip(lower=0.0)


def parametric_var_of_series(pnl: pd.Series, confidence: float) -> float | None:
    """Same formula as rolling_parametric_var, but over the whole given series at once
    rather than a trailing window - used for a fixed period (e.g. "VaR for March") where
    there's no meaningful "trailing window ending at t" to speak of. None if fewer than
    2 points, since sample std is undefined for n<2.
    """
    if len(pnl) < 2:
        return None
    z = _z_score(confidence)
    var = -(pnl.mean() - z * pnl.std(ddof=1))
    return max(var, 0.0)


@dataclass
class VarResult:
    daily_pnl: pd.Series
    var_short: pd.Series      # rolling window (var_window_days), the "current" risk
    var_baseline: pd.Series   # longer window (var_baseline_days), the long-run comparison

    @property
    def latest_short(self) -> float | None:
        return _latest(self.var_short)

    @property
    def latest_baseline(self) -> float | None:
        return _latest(self.var_baseline)


def _latest(series: pd.Series) -> float | None:
    valid = series.dropna()
    return float(valid.iloc[-1]) if len(valid) else None


def compute_var(trades: list[Trade], window_days: int, baseline_days: int, confidence: float) -> VarResult:
    daily_pnl = daily_pnl_series(trades)
    return VarResult(
        daily_pnl=daily_pnl,
        var_short=rolling_parametric_var(daily_pnl, window_days, confidence),
        var_baseline=rolling_parametric_var(daily_pnl, baseline_days, confidence),
    )
