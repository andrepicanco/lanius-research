"""Cross-asset correlation, computed from underlying daily price returns (not strategy
trade P/L - most symbols in a basket won't have a trade on any given day, so a trade-P/L
correlation matrix would mostly be measuring co-incidence of trade timing, not market
co-movement). The asset universe here is always the symbols actually traded in the
loaded run, not necessarily the full configured basket - diversification-checking a
symbol that was never traded isn't actionable.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


def daily_returns(closes: pd.DataFrame) -> pd.DataFrame:
    return closes.pct_change().dropna(how="all")


def rolling_correlation(returns: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Correlation matrix over the trailing `window_days` returns ending at the most
    recent available day."""
    recent = returns.tail(window_days)
    return recent.corr()


def baseline_correlation(returns: pd.DataFrame) -> pd.DataFrame:
    """Correlation matrix over the full available return history - the long-run
    comparison point for `rolling_correlation`."""
    return returns.corr()


@dataclass
class CorrelatedPair:
    symbol_a: str
    symbol_b: str
    rolling_corr: float
    baseline_corr: float

    @property
    def delta(self) -> float:
        return self.rolling_corr - self.baseline_corr


def _all_pairs(rolling: pd.DataFrame, baseline: pd.DataFrame) -> list[CorrelatedPair]:
    symbols = list(rolling.columns)
    pairs = []
    for i, sym_a in enumerate(symbols):
        for sym_b in symbols[i + 1:]:
            r = rolling.loc[sym_a, sym_b]
            b = baseline.loc[sym_a, sym_b] if sym_a in baseline.index and sym_b in baseline.columns else float("nan")
            if pd.isna(r):
                continue
            pairs.append(CorrelatedPair(sym_a, sym_b, float(r), float(b) if not pd.isna(b) else float("nan")))
    return pairs


def top_correlated_pairs(rolling: pd.DataFrame, baseline: pd.DataFrame, top_n: int) -> list[CorrelatedPair]:
    """The `top_n` symbol pairs by rolling correlation (highest first), each pairing
    listed once, self-pairs excluded."""
    pairs = _all_pairs(rolling, baseline)
    pairs.sort(key=lambda p: p.rolling_corr, reverse=True)
    return pairs[:top_n]


def bottom_correlated_pairs(rolling: pd.DataFrame, baseline: pd.DataFrame, top_n: int) -> list[CorrelatedPair]:
    """The `top_n` symbol pairs by rolling correlation (lowest/most negative first) -
    the best risk-reducing candidates, since a true (partial) hedge lowers portfolio
    risk more than two assets that simply don't relate to each other."""
    pairs = _all_pairs(rolling, baseline)
    pairs.sort(key=lambda p: p.rolling_corr)
    return pairs[:top_n]


def pc1_explained_variance(returns: pd.DataFrame) -> float | None:
    """Fraction of variance explained by the first principal component of the
    correlation matrix (not covariance - returns are standardized first, since raw
    covariance would just be dominated by whichever instrument has the largest
    volatility). A correlation matrix's diagonal is always 1, so its trace - the sum of
    its eigenvalues - is always n; PC1's explained-variance ratio is therefore simply
    the largest eigenvalue divided by n. Close to 1/n means the basket is close to
    independent (real diversification); close to 1 means it's moving as one factor.

    Returns None if there are fewer than 2 assets, or too little data to correlate at
    all (fewer rows than columns after dropping any all-NaN rows).
    """
    clean = returns.dropna(how="any")
    n = clean.shape[1]
    if n < 2 or len(clean) < n:
        return None

    corr = clean.corr().to_numpy()
    if np.isnan(corr).any():
        return None

    eigenvalues = np.linalg.eigvalsh(corr)  # ascending order, matrix is symmetric
    return float(max(eigenvalues) / n)


def _quarter_key(when) -> str:
    timestamp = pd.Timestamp(when)
    return f"{timestamp.year}-Q{(timestamp.month - 1) // 3 + 1}"


def quarterly_pc1_series(closes: pd.DataFrame) -> list[tuple[str, float | None]]:
    """PC1 explained-variance ratio per calendar quarter, using the SAME fixed set of
    columns (assets) in every quarter - callers should already have restricted `closes`
    to exactly the asset universe they want held constant across the series, so the 1/n
    floor doesn't shift between quarters and the trend is actually comparable point to
    point."""
    returns = daily_returns(closes)
    if returns.empty:
        return []

    quarters = returns.index.map(_quarter_key)
    result = []
    for quarter in sorted(set(quarters)):
        quarter_returns = returns[quarters == quarter]
        result.append((quarter, pc1_explained_variance(quarter_returns)))
    return result


@dataclass
class CorrelationResult:
    rolling: pd.DataFrame
    baseline: pd.DataFrame
    top_pairs: list[CorrelatedPair]
    bottom_pairs: list[CorrelatedPair]


def compute_correlation(closes: pd.DataFrame, window_days: int, top_n: int) -> CorrelationResult:
    returns = daily_returns(closes)
    rolling = rolling_correlation(returns, window_days)
    baseline = baseline_correlation(returns)
    return CorrelationResult(
        rolling=rolling,
        baseline=baseline,
        top_pairs=top_correlated_pairs(rolling, baseline, top_n),
        bottom_pairs=bottom_correlated_pairs(rolling, baseline, top_n),
    )