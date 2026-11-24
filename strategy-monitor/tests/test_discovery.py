import datetime as dt
import json
import random
import zlib

import pytest

from strategy_monitor import discovery as dsc
from strategy_monitor.discovery_config import (DiscoveryConfigError, ParamSpec, Settings, StrategySpec,
                                               fmt_value, load_discovery_config, seed_for)
from strategy_monitor.models import Report, Trade

UNTIL = dt.date(2026, 11, 17)


# ---------------------------------------------------------------- amostragem

def test_candidates_formats_like_the_tester():
    assert ParamSpec("A", "float", min=0.8, max=1.2, step=0.2).candidates("1") == ["0.8", "1", "1.2"]
    assert ParamSpec("A", "int", min=10, max=30, step=10).candidates("1") == ["10", "20", "30"]
    assert ParamSpec("A", "int", factors=[0.5, 1.0, 1.5]).candidates("600") == ["300", "600", "900"]
    assert ParamSpec("A", "bool").candidates("true") == ["true", "false"]
    assert ParamSpec("A", "float", values=[0, 1.5]).candidates("0") == ["0", "1.5"]
    assert fmt_value(0.161, "float", 0.001) == "0.161"


def _specs():
    return [ParamSpec("InpA", "int", values=[1, 2, 3, 4]), ParamSpec("InpB", "bool")]


def test_sample_only_touches_allowed_params_and_excludes_reference():
    ref = {"InpA": "1", "InpB": "true", "InpOther": "99"}
    trials = dsc.sample_trials(_specs(), ref, 100, seed=1)
    assert len(trials) == 7                                  # 4*2 = 8 combinações, menos a referência
    assert all(set(t) == {"InpA", "InpB"} for t in trials)   # InpOther nunca aparece
    assert {"InpA": "1", "InpB": "true"} not in trials
    assert len({json.dumps(t, sort_keys=True) for t in trials}) == 7


def test_sample_is_reproducible_by_seed():
    ref = {"InpA": "1", "InpB": "true"}
    a = dsc.sample_trials(_specs(), ref, 3, seed=7)
    assert a == dsc.sample_trials(_specs(), ref, 3, seed=7)
    assert a != dsc.sample_trials(_specs(), ref, 3, seed=8)
    assert seed_for(42, "x") == seed_for(42, "x") and seed_for(42, "x") != seed_for(42, "y")


def test_sample_rejects_param_missing_from_baseline():
    with pytest.raises(dsc.DiscoveryError, match="InpA"):
        dsc.sample_trials(_specs(), {"InpB": "true"}, 3, seed=1)


def test_sample_large_space_gives_distinct_combinations():
    big = [ParamSpec(f"P{i}", "int", min=1, max=100, step=1) for i in range(4)]   # 1e8 combinações
    ref = {f"P{i}": "1" for i in range(4)}
    t = dsc.sample_trials(big, ref, 40, seed=3)
    assert len(t) == 40 and len({json.dumps(x, sort_keys=True) for x in t}) == 40


# ---------------------------------------------------------------- config

def _write(tmp_path, text):
    p = tmp_path / "d.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_config_loads_and_validates(tmp_path):
    s, specs = load_discovery_config(_write(tmp_path, """
settings: {max_drawdown_pct: 4, trials: 5, top_k: 2}
strategies:
  EA1:
    tickers: [AAA]
    parameters:
      InpX: {type: float, min: 1, max: 2, step: 0.5}
      InpY: {type: bool}
"""))
    assert s.max_drawdown_pct == 4 and s.trials == 5 and specs[0].expert == "EA1"
    assert [p.name for p in specs[0].params] == ["InpX", "InpY"]


@pytest.mark.parametrize("param", ["InpMagicNumber", "InpRiskPercent", "InpFixedLot", "InpUseFixedLot"])
def test_config_refuses_sizing_and_identity_params(tmp_path, param):
    with pytest.raises(DiscoveryConfigError, match=param):
        load_discovery_config(_write(tmp_path, f"""
settings: {{max_drawdown_pct: 4}}
strategies:
  EA1: {{tickers: [A], parameters: {{{param}: {{type: int, min: 1, max: 2, step: 1}}}}}}
"""))


def test_config_requires_drawdown_cap_and_params(tmp_path):
    with pytest.raises(DiscoveryConfigError, match="max_drawdown_pct"):
        load_discovery_config(_write(tmp_path, "settings: {trials: 3}\nstrategies:\n  E: {tickers: [A], parameters: {X: {type: bool}}}\n"))
    with pytest.raises(DiscoveryConfigError, match="nenhum parâmetro"):
        load_discovery_config(_write(tmp_path, "settings: {max_drawdown_pct: 4}\nstrategies:\n  E: {tickers: [A], parameters: {}}\n"))
    with pytest.raises(DiscoveryConfigError, match="exatamente uma forma"):
        load_discovery_config(_write(tmp_path, "settings: {max_drawdown_pct: 4}\nstrategies:\n  E: {tickers: [A], parameters: {X: {type: int, min: 1, max: 5}}}\n"))


# ---------------------------------------------------------------- orquestração com um 'Tester' falso

def _template(inputs=None):
    return Report(source="t", expert="EA", symbol="TST", timeframe="H1", date_from=dt.date(2026, 1, 1),
                  date_to=dt.date(2026, 6, 1), inputs=inputs or {"InpA": "1", "InpB": "true"},
                  deposit=10000.0, history_quality="100%", ticks=1.0, trades=[], bars=100.0)


def fake_tester(edge_fn, calls, fail=None):
    """edge_fn(inputs, from_date, until, model) -> (p_win, rr). Trades são sorteados com semente
    fixa por (inputs, janela), então o mesmo teste dá o mesmo resultado."""
    def run_fn(tpl, inputs, from_date, until, model, name):
        calls.append(dict(name=name, inputs=dict(inputs), from_date=from_date, until=until, model=model))
        if fail and fail(inputs, from_date, model):
            raise RuntimeError("falha simulada")
        p_win, rr = edge_fn(inputs, from_date, until, model)
        seed = zlib.crc32(json.dumps(inputs, sort_keys=True).encode()) + from_date.toordinal() + model
        rng = random.Random(seed)
        span = (until - from_date).days
        trades = []
        for i in range(80):
            o = dt.datetime.combine(from_date + dt.timedelta(days=int(i * span / 80)), dt.time(10))
            win = rng.random() < p_win
            ex = 100 + 10 * rr if win else 90.0
            trades.append(Trade(symbol=tpl.symbol, direction="buy", volume=1.0, open_time=o,
                                close_time=o + dt.timedelta(hours=2), open_price=100.0, close_price=ex,
                                profit=ex - 100.0, swap=0.0, commission=0.0, exit_reason="sl", sl0=90.0))
        return Report(source="fake", expert=tpl.expert, symbol=tpl.symbol, timeframe=tpl.timeframe,
                      date_from=from_date, date_to=until, inputs=inputs, deposit=10000.0,
                      history_quality="100%", ticks=1.0, trades=trades, bars=100.0)
    return run_fn


def _settings(**kw):
    base = dict(trials=10, top_k=2, min_trades_is=10, min_trades_oos=5, max_drawdown_pct=50.0,
                is_days=240, oos_days=45, warmup_days=30, seed=1)
    base.update(kw)
    return Settings(**base)


def _spec():
    return StrategySpec("EA", ["TST"], [ParamSpec("InpA", "int", values=[1, 2, 3, 4]), ParamSpec("InpB", "bool")])


def test_edge_that_exists_only_in_sample_is_rejected_out_of_sample():
    """O caso que importa: InpA=3 'funciona' no treino e não no teste. Tem que ser REPROVADO."""
    s = _settings()
    w = dsc.Windows.build(UNTIL, s)

    def edge(inputs, f, until, model):
        in_sample = until <= w.oos_start
        if in_sample and inputs["InpA"] == "3":
            return 0.75, 1.5          # E[R] bem positivo só no IS
        return 0.35, 1.0              # E[R] negativo no resto (incluindo todo o OOS)

    calls = []
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL, fake_tester(edge, calls))
    assert res.finalists and res.finalists[0].changed["InpA"] == "3"
    assert not any(f.qualified for f in res.finalists)
    assert "E[R] no OOS <= 0" in res.finalists[0].verdict
    # nenhuma tentativa de IS enxergou o OOS; toda janela de OOS começa a partir do corte (menos o aquecimento)
    is_calls = [c for c in calls if "_IS_" in c["name"]]
    oos_calls = [c for c in calls if "_OOS" in c["name"]]
    assert is_calls and oos_calls
    assert all(c["until"] <= w.oos_start for c in is_calls)
    assert all(c["from_date"] >= w.oos_start - dt.timedelta(days=s.warmup_days) for c in oos_calls)
    assert all(c["until"] == UNTIL for c in oos_calls)


def test_real_edge_that_holds_out_of_sample_becomes_candidate():
    s = _settings()

    def edge(inputs, f, until, model):
        return (0.6, 1.5) if inputs["InpA"] == "3" else (0.4, 1.0)     # InpA=3 vale em qualquer janela

    calls = []
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL, fake_tester(edge, calls))
    best = res.finalists[0]
    assert best.changed["InpA"] == "3" and best.qualified and best.verdict == "CANDIDATO"
    assert best.decision_row is best.oos_exact            # decide pelo modelo exato quando existe
    # o que não tem edge não passa
    assert all(not f.qualified for f in res.finalists if f.changed["InpA"] != "3")


def test_models_and_run_counts():
    s = _settings(top_k=2)
    calls = []
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL,
                              fake_tester(lambda i, f, u, m: (0.5, 1.2), calls))
    is_calls = [c for c in calls if "_IS_" in c["name"]]
    assert len(is_calls) == 1 + res.n_trials and {c["model"] for c in is_calls} == {s.screen_model}
    assert len([c for c in calls if "_OOS-triagem_" in c["name"]]) == 1 + 2
    exact = [c for c in calls if "_OOS-exato_" in c["name"]]
    assert len(exact) == 1 + 2 and {c["model"] for c in exact} == {0}


def test_no_confirmation_when_confirm_model_is_none():
    s = _settings(confirm_model=None)
    calls = []
    dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL, fake_tester(lambda i, f, u, m: (0.5, 1.2), calls))
    assert not [c for c in calls if "_OOS-exato_" in c["name"]]


def test_failed_confirmation_blocks_candidate():
    s = _settings()
    edge = lambda i, f, u, m: (0.6, 1.5) if i["InpA"] == "3" else (0.4, 1.0)
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL,
                              fake_tester(edge, [], fail=lambda inputs, f, model: model == 0 and inputs["InpA"] == "3"))
    best = res.finalists[0]
    assert not best.qualified and "confirmação no modelo exato falhou" in best.verdict


def test_infeasible_trials_are_not_ranked():
    s = _settings(min_trades_is=10_000)           # ninguém chega a esse n
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL,
                              fake_tester(lambda i, f, u, m: (0.6, 1.5), []))
    assert res.n_feasible == 0 and res.finalists == []


def test_drawdown_cap_excludes_trials():
    s = _settings(max_drawdown_pct=0.0001)
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL,
                              fake_tester(lambda i, f, u, m: (0.4, 1.0), []))
    assert res.n_feasible == 0
    assert any(r.reason and "DD" in r.reason for r in res.rows if r.trial > 0)


def test_aborts_after_three_consecutive_errors():
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _settings(), UNTIL,
                              fake_tester(lambda i, f, u, m: (0.5, 1), [],
                                          fail=lambda inputs, f, model: inputs != {"InpA": "1", "InpB": "true"}))
    assert res.aborted == "3 erros seguidos" and res.finalists == []


def test_reference_failure_aborts_with_clear_reason():
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _settings(), UNTIL,
                              fake_tester(lambda i, f, u, m: (0.5, 1), [], fail=lambda *a: True))
    assert res.aborted and "referência" in res.aborted


def test_low_disk_aborts_whole_target():
    def run_fn(*a, **k):
        raise RuntimeError("pouco espaco no disco: 0.2 GB livres")
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _settings(), UNTIL, run_fn)
    assert res.aborted == "disco"


def test_selection_note_flags_luck_level_maxima():
    rng = random.Random(0)
    noise = [rng.gauss(0, 0.1) for _ in range(30)]
    assert "compatível com sorte" in dsc.selection_note(noise)
    assert "acima do que a sorte" in dsc.selection_note(noise + [3.0])
    assert "poucas tentativas" in dsc.selection_note([0.1])


# ---------------------------------------------------------------- saídas

def test_outputs_candidates_only_for_qualified_and_csv_is_incremental(tmp_path):
    s = _settings()
    edge = lambda i, f, u, m: (0.6, 1.5) if i["InpA"] == "3" else (0.4, 1.0)
    csvw = dsc.ResultsCsv(tmp_path / "results.csv")
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL, fake_tester(edge, []), on_row=csvw.add)
    csvw.close()
    rows = (tmp_path / "results.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].startswith("alvo,etapa,tentativa") and len(rows) > 1 + res.n_trials
    sets = dsc.write_candidate_sets(tmp_path, [res])
    assert len(sets) == sum(f.qualified for f in res.finalists) >= 1
    raw = sets[0].read_bytes()
    assert raw[:2] == b"\xff\xfe" and "InpA=3" in raw[2:].decode("utf-16-le")
    dsc.write_report(tmp_path / "report.md", [res], s, {res.label: _template().inputs}, UNTIL)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Nada foi aplicado" in md and "CANDIDATO" in md and "Limites" in md
    assert "InpA=3 (era 1)" in md
    assert "CANDIDATO" in dsc.render_summary([res])


def test_pick_template_prefers_own_baseline_then_template():
    a = _template({"InpA": "1"})
    b = Report(**{**a.__dict__, "symbol": "OTH", "timeframe": "M12", "inputs": {"InpA": "9"}})
    base = {a.key: a, b.key: b}
    spec = StrategySpec("EA", ["TST", "NEW"], [ParamSpec("InpA", "int", values=[1])], template="OTH")
    assert dsc.pick_template(spec, "TST", base) == (a, "H1")
    assert dsc.pick_template(spec, "NEW", base) == (b, "M12")
    with pytest.raises(dsc.DiscoveryError, match="nenhum baseline"):
        dsc.pick_template(StrategySpec("OUTRO", ["X"], []), "X", base)


def test_infeasibility_reason_reaches_the_csv(tmp_path):
    s = _settings(max_drawdown_pct=0.0001)
    csvw = dsc.ResultsCsv(tmp_path / "r.csv")
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL,
                              fake_tester(lambda i, f, u, m: (0.4, 1.0), []), on_row=csvw.add)
    csvw.close()
    lines = (tmp_path / "r.csv").read_text(encoding="utf-8-sig").splitlines()
    assert any("DD" in l and ",IS," in l for l in lines[1:])
    dsc.write_report(tmp_path / "rep.md", [res], s, {res.label: _template().inputs}, UNTIL)
    md = (tmp_path / "rep.md").read_text(encoding="utf-8")
    assert "a própria referência" in md and "Nenhuma tentativa ficou dentro das restrições" in md
