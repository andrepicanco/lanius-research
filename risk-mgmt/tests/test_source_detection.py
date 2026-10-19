"""--log-dir has to keep working without the caller naming a parser, so the source is
picked from what the directory actually holds."""

import pytest

from risk_mgmt.cli import _detect_source


def test_html_report_directory(tmp_path):
    (tmp_path / "ReportTester-1.html").write_text("x")

    assert _detect_source(str(tmp_path)) == "mt5_report_html"


def test_journal_directory(tmp_path):
    (tmp_path / "20260824.log").write_text("x")

    assert _detect_source(str(tmp_path)) == "mql5_journal"


def test_idxswing91_csv_directory(tmp_path):
    (tmp_path / "NAS100_trades.csv").write_text("x")

    assert _detect_source(str(tmp_path)) == "idxswing91_csv"


def test_unrelated_files_do_not_pick_a_source(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "prices.csv").write_text("x")  # not *_trades.csv

    with pytest.raises(SystemExit, match="Nothing recognizable"):
        _detect_source(str(tmp_path))


def test_mixed_directory_refuses_to_guess(tmp_path):
    (tmp_path / "report.html").write_text("x")
    (tmp_path / "journal.log").write_text("x")

    with pytest.raises(SystemExit, match="more than one source"):
        _detect_source(str(tmp_path))


def test_missing_directory_is_reported_clearly(tmp_path):
    with pytest.raises(SystemExit, match="Not a directory"):
        _detect_source(str(tmp_path / "nope"))


# --- --strategy vs --log-dir ---------------------------------------------------------

from risk_mgmt.cli import build_parser  # noqa: E402


def test_naming_both_a_strategy_and_a_log_dir_is_refused():
    """They used to be silently combined, with --strategy winning: a run passing both
    failed complaining about the --strategy path while never opening --log-dir at all."""
    from risk_mgmt.cli import _parse_strategy_args

    args = build_parser().parse_args(
        ["--mode", "local", "--strategy", "MeanRev1=reports/meanrev", "--log-dir", "logs"]
    )

    with pytest.raises(SystemExit, match="alternatives, not a pair"):
        _parse_strategy_args(args)


def test_log_dir_alone_is_the_unnamed_strategy():
    from risk_mgmt.cli import _parse_strategy_args

    args = build_parser().parse_args(["--mode", "local", "--log-dir", "logs"])

    assert _parse_strategy_args(args) == [(None, "logs")]
