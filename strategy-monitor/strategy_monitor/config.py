"""Carrega config/monitor.yaml (opcional). Sem arquivo, vale o default. A pasta de baselines pode
vir do arquivo, da variável STRATEGY_MONITOR_BASELINE_DIR ou do --baseline-dir da CLI (em ordem
crescente de precedência)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "monitor.yaml"


@dataclass
class Thresholds:
    confidence: float = 0.95
    dd_fail_factor: float = 1.5
    max_drawdown_pct: float | None = None
    trades_per_month_ratio: tuple[float, float] = (0.5, 2.0)


@dataclass
class Mt5Cfg:
    """Instancia ABERTA do MT5 de onde o motor Python le barras (somente leitura) e onde guarda o cache."""
    terminal_path: Path | None = None
    cache_dir: Path = Path(__file__).resolve().parent.parent / "data_cache"


@dataclass
class Config:
    mt5: Mt5Cfg = field(default_factory=Mt5Cfg)
    baseline_dir: Path | None = None
    min_trades: int = 30
    thresholds: Thresholds = field(default_factory=Thresholds)
    outputs_dir: Path = Path(__file__).resolve().parent.parent / "outputs"


def load_config(path: str | Path | None = None, baseline_dir: str | Path | None = None) -> Config:
    cfg = Config()
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if raw.get("baseline_dir"):
            cfg.baseline_dir = Path(raw["baseline_dir"])
        cfg.min_trades = int(raw.get("min_trades", cfg.min_trades))
        t = raw.get("thresholds") or {}
        cfg.thresholds = Thresholds(
            confidence=float(t.get("confidence", 0.95)),
            dd_fail_factor=float(t.get("dd_fail_factor", 1.5)),
            max_drawdown_pct=t.get("max_drawdown_pct"),
            trades_per_month_ratio=tuple(t.get("trades_per_month_ratio", (0.5, 2.0))),
        )
        m = raw.get("mt5") or {}
        if m.get("terminal_path"):
            cfg.mt5.terminal_path = Path(m["terminal_path"])
        if m.get("cache_dir"):
            cfg.mt5.cache_dir = Path(m["cache_dir"])
        if raw.get("outputs_dir"):
            cfg.outputs_dir = Path(raw["outputs_dir"])
    elif path:
        raise FileNotFoundError(f"config não encontrada: {p}")
    env = os.environ.get("STRATEGY_MONITOR_BASELINE_DIR")
    if env:
        cfg.baseline_dir = Path(env)
    if baseline_dir:
        cfg.baseline_dir = Path(baseline_dir)
    return cfg
