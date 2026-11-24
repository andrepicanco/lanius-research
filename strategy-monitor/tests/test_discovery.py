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


@pytest.mark.parametrize("param", ["InpMagicNumber", "InpTradeComment", "InpLogLevel"])
def test_config_refuses_identity_params(tmp_path, param):
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


# ---------------------------------------------------------------- vizinhança e janelas

def test_neighbors_move_one_param_to_the_adjacent_grid_value():
    ref = {"InpA": "1", "InpB": "true"}
    nb = dsc.neighbors(_specs(), ref, {"InpA": "3", "InpB": "true"})
    assert {"InpA": "2"} in nb and {"InpA": "4"} in nb and {"InpB": "false"} in nb and len(nb) == 3
    edge = dsc.neighbors(_specs(), ref, {"InpA": "1", "InpB": "false"})            # na ponta da grade: 1 vizinho
    assert {"InpA": "2"} in edge and {"InpA": "0"} not in edge and {"InpB": "true"} in edge
    assert dsc.neighbors(_specs(), ref, {"InpA": "99", "InpB": "true"}) == [{"InpB": "false"}]   # fora da grade: ignora


def test_windows_split_the_in_sample_into_contiguous_folds():
    w = dsc.Windows.build(UNTIL, _settings())
    folds = w.folds(3)
    assert folds[0][0] == w.is_start and folds[-1][1] == w.oos_start
    assert all(a[1] == b[0] for a, b in zip(folds, folds[1:])) and len(folds) == 3
    assert w.oos_start == UNTIL - dt.timedelta(days=45) and w.is_start == w.oos_start - dt.timedelta(days=240)


# ---------------------------------------------------------------- orquestração com um motor falso

def _template(inputs=None):
    return Report(source="t", expert="EA", symbol="TST", timeframe="H1", date_from=dt.date(2026, 1, 1),
                  date_to=dt.date(2026, 6, 1), inputs=inputs or {"InpA": "1", "InpB": "true"},
                  deposit=10000.0, history_quality="100%", ticks=1.0, trades=[])


def fake_engine(edge_fn, calls, fail=None, dead=()):
    """edge_fn(inputs, from_date, until) -> (p_win, rr). Trades sorteados com semente fixa por
    (inputs, janela): o mesmo teste dá o mesmo resultado. `dead`: inputs sem efeito (fora da semente)."""
    def run_fn(tpl, inputs, from_date, until):
        calls.append(dict(inputs=dict(inputs), from_date=from_date, until=until))
        if fail and fail(inputs, from_date):
            raise RuntimeError("falha simulada")
        p_win, rr = edge_fn(inputs, from_date, until)
        live = {k: v for k, v in inputs.items() if k not in dead}
        rng = random.Random(zlib.crc32(json.dumps(live, sort_keys=True).encode()) + from_date.toordinal())
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
                      history_quality="100%", ticks=1.0, trades=trades)
    return run_fn


def _settings(**kw):
    base = dict(trials=10, top_k=2, min_trades_is=10, min_trades_oos=5, max_drawdown_pct=50.0,
                is_days=240, oos_days=45, folds=3, min_positive_folds=2, seed=1, size_last=False)
    base.update(kw)
    return Settings(**base)


def _spec():
    return StrategySpec("EA", ["TST"], [ParamSpec("InpA", "int", values=[1, 2, 3, 4]), ParamSpec("InpB", "bool")])


GOOD, BAD = (0.6, 1.5), (0.4, 1.0)      # E[R] positivo / negativo


def test_edge_that_exists_only_in_sample_is_rejected_out_of_sample():
    """O caso que importa: InpA=3 'funciona' no treino e não no teste. Tem que ser REPROVADO."""
    s = _settings()
    w = dsc.Windows.build(UNTIL, s)
    edge = lambda i, f, u: (0.75, 1.5) if (u <= w.oos_start and i["InpA"] == "3") else BAD
    calls = []
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL, fake_engine(edge, calls))
    assert res.finalists and res.finalists[0].changed["InpA"] == "3"
    assert not any(f.qualified for f in res.finalists)
    assert "E[R] no OOS <= 0" in res.finalists[0].verdict
    oos = [c for c in calls if c["until"] == UNTIL]                     # só o OOS chega ao fim
    assert oos and all(c["from_date"] == w.oos_start for c in oos)
    assert all(c["until"] <= w.oos_start for c in calls if c["until"] != UNTIL)   # busca, trechos e vizinhos nunca o veem


def test_real_edge_that_holds_everywhere_becomes_candidate():
    s = _settings()
    edge = lambda i, f, u: GOOD if int(i["InpA"]) >= 3 else BAD          # patamar: InpA 3 e 4 valem em qualquer janela
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL, fake_engine(edge, []))
    best = res.finalists[0]
    assert int(best.changed["InpA"]) >= 3 and best.qualified and best.verdict == "CANDIDATO"
    assert best.positive_folds == 3 and best.neighbor_median > 0
    assert all(not f.qualified for f in res.finalists if int(f.changed["InpA"]) < 3)


def test_isolated_peak_is_rejected_by_the_neighbourhood_even_if_it_holds_out_of_sample():
    edge = lambda i, f, u: GOOD if (i["InpA"] == "3" and i["InpB"] == "true") else BAD
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _settings(), UNTIL, fake_engine(edge, []))
    peak = next(f for f in res.finalists if f.inputs["InpA"] == "3" and f.inputs["InpB"] == "true")
    assert peak.oos.er > 0 and peak.positive_folds == 3                  # passa em OOS e consistência...
    assert not peak.qualified and "pico isolado" in peak.verdict         # ...mas é um pico cercado de vizinhos ruins


def test_edge_that_lives_in_one_stretch_of_the_in_sample_fails_consistency():
    s = _settings()
    w = dsc.Windows.build(UNTIL, s)
    first_third_end = w.folds(3)[0][1]
    edge = lambda i, f, u: GOOD if (i["InpA"] == "3" and f < first_third_end) else BAD
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL, fake_engine(edge, []))
    best = res.finalists[0]
    assert best.changed["InpA"] == "3" and best.positive_folds == 1
    assert not best.qualified and "consistência" in best.verdict


def test_dead_parameter_neighbours_do_not_inflate_the_neighbourhood():
    """Parâmetro sem efeito (vizinho idêntico ao finalista) não pode contar como 'vizinho bom'."""
    specs = [ParamSpec("InpA", "int", values=[1, 2, 3, 4]), ParamSpec("InpDead", "int", values=[1, 2, 3])]
    spec = StrategySpec("EA", ["TST"], specs)
    tpl = _template({"InpA": "1", "InpDead": "1"})
    # só InpA=3 é bom; InpDead não muda nada (o resultado ignora o parâmetro)
    edge = lambda i, f, u: GOOD if i["InpA"] == "3" else BAD
    res = dsc.discover_target(spec, "TST", tpl, "H1", _settings(), UNTIL, fake_engine(edge, [], dead=("InpDead",)))
    best = res.finalists[0]
    assert best.inputs["InpA"] == "3"
    assert len(best.live_neighbors) < len(best.neighbor_rows)           # os vizinhos de InpDead saíram da conta
    assert best.neighbor_median is not None and best.neighbor_median <= 0   # sobram InpA=2 e 4: ruins
    assert not best.qualified and "pico isolado" in best.verdict


def test_lot_size_neighbours_do_not_count_because_they_do_not_change_er():
    """O lote escala o DD mas não o E[R]: o vizinho que só troca o lote não pode servir de 'robustez'."""
    specs = [ParamSpec("InpA", "int", values=[1, 2, 3, 4]), ParamSpec("InpFixedLot", "float", values=[0.1, 0.2, 0.5])]
    spec = StrategySpec("EA", ["TST"], specs)
    tpl = _template({"InpA": "1", "InpFixedLot": "0.1"})
    edge = lambda i, f, u: GOOD if i["InpA"] == "3" else BAD
    res = dsc.discover_target(spec, "TST", tpl, "H1", _settings(), UNTIL,
                              fake_engine(edge, [], dead=("InpFixedLot",)))
    best = res.finalists[0]
    assert best.inputs["InpA"] == "3"
    assert all("InpFixedLot" not in r.changed for r in best.live_neighbors)
    assert not best.qualified and "pico isolado" in best.verdict


def test_run_counts():
    s = _settings(top_k=2)
    calls = []
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL, fake_engine(lambda i, f, u: (0.5, 1.2), calls))
    w = dsc.Windows.build(UNTIL, s)
    n_neighbors = sum(len(f.neighbor_rows) for f in res.finalists)
    assert len(calls) == (1 + res.n_trials) + (1 + 2) + 2 * 3 + n_neighbors
    assert len([c for c in calls if c["from_date"] == w.is_start and c["until"] == w.oos_start]) == 1 + res.n_trials + n_neighbors


def test_infeasible_trials_are_not_ranked():
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _settings(min_trades_is=10_000), UNTIL,
                              fake_engine(lambda i, f, u: GOOD, []))
    assert res.n_feasible == 0 and res.finalists == []


def test_drawdown_cap_excludes_trials():
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _settings(max_drawdown_pct=0.0001), UNTIL,
                              fake_engine(lambda i, f, u: BAD, []))
    assert res.n_feasible == 0
    assert any(r.reason and "DD" in r.reason for r in res.rows if r.trial > 0)


def test_aborts_after_three_consecutive_errors():
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _settings(), UNTIL,
                              fake_engine(lambda i, f, u: (0.5, 1), [],
                                          fail=lambda inputs, f: inputs != {"InpA": "1", "InpB": "true"}))
    assert res.aborted == "3 erros seguidos" and res.finalists == []


def test_reference_failure_aborts_with_clear_reason():
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _settings(), UNTIL,
                              fake_engine(lambda i, f, u: (0.5, 1), [], fail=lambda *a: True))
    assert res.aborted and "referência" in res.aborted


def test_new_timeframe_for_an_asset_without_baseline_sets_the_input():
    seen = []
    tpl = _template({"InpA": "1", "InpB": "true", "InpTimeframe": "16385"})       # H1
    dsc.discover_target(_spec(), "NEW", tpl, "M12", _settings(trials=1), UNTIL,
                        fake_engine(lambda i, f, u: GOOD, seen))
    assert seen[0]["inputs"]["InpTimeframe"] == "12"


def test_selection_note_flags_luck_level_maxima():
    rng = random.Random(0)
    noise = [rng.gauss(0, 0.1) for _ in range(30)]
    assert "compatível com sorte" in dsc.selection_note(noise)
    assert "acima do que a sorte" in dsc.selection_note(noise + [3.0])
    assert "poucas tentativas" in dsc.selection_note([0.1])


# ---------------------------------------------------------------- saídas

def test_outputs_candidates_only_for_qualified_and_csv_is_incremental(tmp_path):
    s = _settings()
    edge = lambda i, f, u: GOOD if int(i["InpA"]) >= 3 else BAD
    csvw = dsc.ResultsCsv(tmp_path / "results.csv")
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL, fake_engine(edge, []), on_row=csvw.add)
    csvw.close()
    rows = (tmp_path / "results.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].startswith("alvo,etapa,tentativa") and len(rows) > 1 + res.n_trials
    assert any(",FOLD," in r for r in rows) and any(",VIZINHO," in r for r in rows)
    sets = dsc.write_candidate_sets(tmp_path, [res])
    assert len(sets) == sum(f.qualified for f in res.finalists) >= 1
    raw = sets[0].read_bytes()
    assert raw[:2] == b"\xff\xfe" and "InpA=" in raw[2:].decode("utf-16-le")
    dsc.write_report(tmp_path / "report.md", [res], s, {res.label: _template().inputs}, UNTIL)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Nada foi aplicado" in md and "CANDIDATO" in md and "Limites" in md and "mediana vizinhos" in md
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
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", s, UNTIL, fake_engine(lambda i, f, u: BAD, []), on_row=csvw.add)
    csvw.close()
    lines = (tmp_path / "r.csv").read_text(encoding="utf-8-sig").splitlines()
    assert any("DD" in l and ",IS," in l for l in lines[1:])
    dsc.write_report(tmp_path / "rep.md", [res], s, {res.label: _template().inputs}, UNTIL)
    md = (tmp_path / "rep.md").read_text(encoding="utf-8")
    assert "a própria referência" in md and "Nenhuma tentativa ficou dentro das restrições" in md
