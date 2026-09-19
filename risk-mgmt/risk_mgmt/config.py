"""Risk-analysis parameters - the knobs in config/default.yaml."""

from dataclasses import dataclass


@dataclass
class RiskConfig:
    # Parametric VaR
    var_confidence: float = 0.95
    var_window_days: int = 10
    var_baseline_days: int = 60

    # Correlation
    corr_window_days: int = 20
    top_n_pairs: int = 5

    def validate(self) -> None:
        if not (0.5 < self.var_confidence < 1.0):
            raise ValueError("var_confidence must be in (0.5, 1.0)")
        if self.var_window_days <= 0 or self.var_baseline_days <= 0:
            raise ValueError("var_window_days/var_baseline_days must be > 0")
        if self.var_baseline_days <= self.var_window_days:
            raise ValueError("var_baseline_days must be longer than var_window_days")
        if self.corr_window_days <= 0:
            raise ValueError("corr_window_days must be > 0")
        if self.top_n_pairs <= 0:
            raise ValueError("top_n_pairs must be > 0")
