from pathlib import Path

import pytest

from risk_mgmt.logsource.idxswing91_csv import IdxSwing91CsvSource

_HEADER = "symbol,direction,entry_time,entry_price,exit_time,exit_price,exit_reason,lots,pnl_money,r_multiple\n"


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "US500_trades.csv"
    path.write_text(_HEADER + "".join(rows))
    return path


def test_risk_money_is_derived_from_pnl_over_r_multiple(tmp_path: Path):
    _write_csv(tmp_path, [
        "US500,buy,2026-01-02 09:00:00,100.0,2026-01-02 11:00:00,140.0,tp,1.0,80.0,2.0\n",
    ])

    trade = IdxSwing91CsvSource.from_directory(tmp_path).load_trades()[0]

    # pnl_money=80.0, r_multiple=2.0 -> risk_money=40.0 (the $ distance the r_multiple was measured against)
    assert trade.risk_money == pytest.approx(40.0)


def test_risk_money_is_none_when_r_multiple_is_zero(tmp_path: Path):
    _write_csv(tmp_path, [
        "US500,buy,2026-01-02 09:00:00,100.0,2026-01-02 11:00:00,100.0,unknown,1.0,0.0,0.0\n",
    ])

    trade = IdxSwing91CsvSource.from_directory(tmp_path).load_trades()[0]

    assert trade.risk_money is None
