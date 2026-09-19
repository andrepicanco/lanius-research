"""YAML loading for config/default.yaml and config/symbols.yaml."""

from pathlib import Path

import yaml

from .config import RiskConfig
from .symbol_spec import SymbolSpec


def load_default_config(path: str | Path) -> RiskConfig:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return RiskConfig(**data)


def load_symbols_file(path: str | Path) -> tuple[list[str], dict[str, SymbolSpec]]:
    """Returns (basket symbol list, {symbol: SymbolSpec}). SymbolSpec entries are only
    required for symbols priced via the offline MQL5 journal source - a live MT5 run
    can fetch spec fields itself and doesn't need this file to be complete.
    """
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    symbols = data.get("symbols", [])
    specs = {}
    for symbol, fields in (data.get("specs") or {}).items():
        specs[symbol] = SymbolSpec(
            symbol=symbol,
            tick_value=fields["tick_value"],
            tick_size=fields["tick_size"],
        )

    return symbols, specs
