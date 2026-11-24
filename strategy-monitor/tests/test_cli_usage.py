"""O que a documentação de uso promete tem que ser o que a CLI faz."""

import pytest

from conftest import make_xlsx, trade_spec
from strategy_monitor import cli
from strategy_monitor import compare as cmp
from strategy_monitor.config import Config
from strategy_monitor.metrics import compute_metrics


def _baseline_dir(tmp_path):
    d = tmp_path / "baselines"
    d.mkdir()
    # baseline: 3 trades de fevereiro (o último fecha em 03/02 às 13:00); cabeçalho até 28/02
    make_xlsx(d / "qualquer_nome.xlsx", [trade_spec(1, 100, 110, 95), trade_spec(2, 100, 90, 95),
                                         trade_spec(3, 100, 110, 95)], expert="EA", symbol="TST", tf="H1",
              period=("2026.01.01", "2026.02.28"))
    return d


def _current(tmp_path):
    # corrente: 2 trades de fevereiro (já dentro do baseline) + 3 de março (depois dele)
    f = tmp_path / "corrente.xlsx"
    make_xlsx(f, [trade_spec(1, 100, 110, 95), trade_spec(2, 100, 90, 95),
                  trade_spec(1, 100, 112, 95, month=3), trade_spec(2, 100, 90, 95, month=3),
                  trade_spec(3, 100, 111, 95, month=3)], expert="EA", symbol="TST", tf="H1")
    return f


def _run(tmp_path, capsys, *extra, current=None):
    env = tmp_path / "vazio.env"
    env.write_text("", encoding="utf-8")      # nunca carregar o .env real dos testes
    rc = cli.main(["--env-file", str(env), "--baseline-dir", str(_baseline_dir_cached(tmp_path)),
                   "compare", "--current", str(current or _current(tmp_path)), *extra])
    return rc, capsys.readouterr()


def _baseline_dir_cached(tmp_path):
    d = tmp_path / "baselines"
    return d if d.exists() else _baseline_dir(tmp_path)


def test_default_judges_only_trades_after_the_baseline_ended(tmp_path, capsys):
    rc, out = _run(tmp_path, capsys)
    assert rc == 0 and "n=3" in out.out            # só os 3 de março


def test_full_judges_every_trade_in_the_report(tmp_path, capsys):
    _, out = _run(tmp_path, capsys, "--full")
    assert "n=5" in out.out


def test_since_sets_the_first_day_judged(tmp_path, capsys):
    _, out = _run(tmp_path, capsys, "--since", "2026-03-02")
    assert "n=2" in out.out                        # março dia 2 e 3


def test_since_and_full_cannot_be_combined(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        _run(tmp_path, capsys, "--since", "2026-03-02", "--full")
    assert e.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["01/11/2026", "2026-13-01", "amanha", "2026-11"])
def test_bad_date_format_gets_a_clear_message_not_a_traceback(tmp_path, capsys, bad):
    with pytest.raises(SystemExit) as e:
        _run(tmp_path, capsys, "--since", bad)
    assert e.value.code == 2
    assert "AAAA-MM-DD" in capsys.readouterr().err


def test_current_must_be_an_existing_file(tmp_path, capsys):
    with pytest.raises(SystemExit, match="ARQUIVO"):
        _run(tmp_path, capsys, current=tmp_path / "nao_existe.xlsx")
    with pytest.raises(SystemExit, match="ARQUIVO"):
        _run(tmp_path, capsys, current=tmp_path)   # uma pasta não serve


def test_current_without_a_matching_baseline_lists_the_known_ones(tmp_path, capsys):
    outro = tmp_path / "outro.xlsx"
    make_xlsx(outro, [trade_spec(1, 100, 110, 95)], expert="OUTRO_EA", symbol="XYZ", tf="M5")
    with pytest.raises(SystemExit, match="nenhum baseline para OUTRO_EA"):
        _run(tmp_path, capsys, current=outro)


def test_empty_judged_window_is_not_reported_as_ok(tmp_path, capsys):
    # --since depois do último trade: nada para julgar. Antes isto saía "[OK]" (o DD de 0% passava).
    rc, out = _run(tmp_path, capsys, "--since", "2026-12-01")
    first = out.out.splitlines()[0]
    assert "[NA]" in first and "[OK]" not in first
    assert "nenhum trade" in out.out


def test_compare_verdict_is_na_when_there_are_no_trades():
    empty = compute_metrics([], 10000.0)
    v = cmp.compare(empty, empty, Config())
    assert v.overall == cmp.NA and len(v.checks) == 1


def test_exit_code_does_not_reflect_the_traffic_light(tmp_path, capsys):
    """Documentado no README: ATENCAO/FALHA não mudam o código de saída (só erros de uso mudam)."""
    f = tmp_path / "ruim.xlsx"
    make_xlsx(f, [trade_spec(d, 100, 90, 95, month=3) for d in range(1, 9)], expert="EA", symbol="TST", tf="H1")
    rc, out = _run(tmp_path, capsys, current=f)
    assert rc == 0 and ("[FALHA]" in out.out or "[ATENCAO]" in out.out)
