"""A zero tick value doesn't fail anything on its own - it multiplies into every P/L
figure and reports $0.00 for the whole symbol while the trade count stays correct, which
reads like a flat strategy rather than a broken config. ASXAUD presented exactly that
way: 83 correctly parsed trades, 658.10 points of price movement, $0.00 reported.
"""

import pytest

from risk_mgmt.config_io import load_symbols_file
from risk_mgmt.symbol_spec import SymbolSpec


def test_valid_spec_is_accepted():
    spec = SymbolSpec(symbol="ASXAUD", tick_value=0.066, tick_size=0.1)

    assert spec.tick_value == pytest.approx(0.066)


@pytest.mark.parametrize(
    "tick_value,tick_size",
    [
        (0.0, 0.1),    # what MT5 returns for a cross-currency CFD it can't convert
        (0.1, 0.0),
        (0.0, 0.0),
        (-1.0, 0.1),
        (0.1, -0.1),
    ],
)
def test_non_positive_pricing_fields_are_rejected(tick_value, tick_size):
    with pytest.raises(ValueError, match="must both be > 0"):
        SymbolSpec(symbol="ASXAUD", tick_value=tick_value, tick_size=tick_size)


def test_error_names_the_symbol_and_the_way_out():
    with pytest.raises(ValueError) as excinfo:
        SymbolSpec(symbol="ASXAUD", tick_value=0.0, tick_size=0.1)

    message = str(excinfo.value)
    assert "ASXAUD" in message
    assert "Market Watch" in message
    assert "config/symbols.yaml" in message


def test_zero_in_the_yaml_is_caught_on_load(tmp_path):
    path = tmp_path / "symbols.yaml"
    path.write_text(
        "symbols: [ASXAUD]\nspecs:\n  ASXAUD: {tick_value: 0.0, tick_size: 0.1}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ASXAUD"):
        load_symbols_file(path)
