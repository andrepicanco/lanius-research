"""Dimensionamento do lote no final, conexão compartilhada com o MT5 e trava de execução simultânea."""

import datetime as dt
import json
import random
import sys
import zlib
from types import SimpleNamespace

import pytest

from strategy_monitor import discovery as dsc
from strategy_monitor import services
from strategy_monitor.discovery_config import ParamSpec, Settings, StrategySpec
from strategy_monitor.engine import mt5data
from strategy_monitor.engine.mt5data import ConnectionLost, Mt5Source
from strategy_monitor.models import Report, Trade

A, B = dt.date(2026, 1, 1), dt.date(2026, 6, 1)
UNTIL = dt.date(2026, 11, 17)
BOUNDS = (0.01, 0.01, 100.0)


def _ev(dd_fn, n=50, calls=None):
    def ev(inputs, a, b):
        lot = float(inputs["InpFixedLot"])
        if calls is not None:
            calls.append(lot)
        return SimpleNamespace(n=n, max_dd_pct=dd_fn(lot))
    return ev


# ---------------------------------------------------------------- fit_lot

def test_fit_lot_finds_the_largest_lot_under_the_cap():
    calls = []
    sz = dsc.fit_lot(_ev(lambda lot: 0.5 * lot, calls=calls), {}, A, B, 5.0, BOUNDS)
    assert sz.lot == pytest.approx(10.0) and sz.dd == pytest.approx(5.0) and sz.reason is None
    assert len(calls) <= 24 and calls[0] == 0.01                           # começa no lote mínimo, poucas rodadas


def test_fit_lot_is_maximal_for_a_nonlinear_drawdown():
    f = lambda lot: 0.3 * lot ** 1.2
    sz = dsc.fit_lot(_ev(f), {}, A, B, 5.0, BOUNDS)
    assert f(sz.lot) <= 5.0 < f(sz.lot + 0.01)


def test_fit_lot_reports_when_even_the_minimum_lot_does_not_fit_or_the_cap_never_binds():
    sz = dsc.fit_lot(_ev(lambda lot: 20 + lot), {}, A, B, 5.0, BOUNDS)
    assert sz.lot is None and "nem com o lote mínimo" in sz.reason and sz.dd == pytest.approx(20.01)
    free = dsc.fit_lot(_ev(lambda lot: 0.001 * lot), {}, A, B, 50.0, BOUNDS)
    assert free.lot == 100.0 and "não restringe" in free.reason
    assert dsc.fit_lot(_ev(lambda lot: 1.0, n=0), {}, A, B, 5.0, BOUNDS).reason == "sem trades na janela"


# ---------------------------------------------------------------- fluxo do discover com lote no final

def _template(inputs=None):
    return Report(source="t", expert="EA", symbol="TST", timeframe="H1", date_from=A, date_to=B,
                  inputs=inputs or {"InpA": "1", "InpUseFixedLot": "false", "InpFixedLot": "1.0"},
                  deposit=10000.0, history_quality="100%", ticks=1.0, trades=[])


def lot_engine(edge_fn, calls=None):
    """Motor falso em que o lote ESCALA dinheiro e DD mas não o E[R] (como no motor real)."""
    def run_fn(tpl, inputs, from_date, until):
        if calls is not None:
            calls.append(dict(inputs))
        lot = float(inputs["InpFixedLot"]) if inputs.get("InpUseFixedLot") == "true" else 1.0
        live = {k: v for k, v in inputs.items() if k not in ("InpUseFixedLot", "InpFixedLot")}
        rng = random.Random(zlib.crc32(json.dumps(live, sort_keys=True).encode()) + from_date.toordinal())
        p_win, rr = edge_fn(inputs, from_date, until)
        span, trades = (until - from_date).days, []
        for i in range(80):
            o = dt.datetime.combine(from_date + dt.timedelta(days=int(i * span / 80)), dt.time(10))
            ex = 100 + 10 * rr if rng.random() < p_win else 90.0
            trades.append(Trade(symbol=tpl.symbol, direction="buy", volume=lot, open_time=o,
                                close_time=o + dt.timedelta(hours=2), open_price=100.0, close_price=ex,
                                profit=(ex - 100.0) * lot, swap=0.0, commission=0.0, exit_reason="sl", sl0=90.0))
        return Report(source="fake", expert=tpl.expert, symbol=tpl.symbol, timeframe=tpl.timeframe,
                      date_from=from_date, date_to=until, inputs=inputs, deposit=10000.0,
                      history_quality="100%", ticks=1.0, trades=trades)
    return run_fn


GOOD, BAD = (0.6, 1.5), (0.4, 1.0)


def _spec():
    return StrategySpec("EA", ["TST"], [ParamSpec("InpA", "int", values=[1, 2, 3, 4]),
                                        ParamSpec("InpUseFixedLot", "bool"),
                                        ParamSpec("InpFixedLot", "float", values=[0.5, 1.0])])


def _s(**kw):
    base = dict(trials=10, top_k=2, min_trades_is=10, min_trades_oos=5, max_drawdown_pct=0.3, is_days=240,
                oos_days=45, folds=3, min_positive_folds=2, seed=1, size_last=True)
    base.update(kw)
    return Settings(**base)


def test_search_ignores_the_lot_and_the_dd_cap_then_sizes_the_finalists():
    edge = lambda i, f, u: GOOD if int(i["InpA"]) >= 3 else BAD
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _s(), UNTIL, lot_engine(edge),
                              lot_bounds=lambda sym: BOUNDS)
    # o lote liberado não entrou na busca; o teto de DD (0,3%, violado no dimensionamento original) não filtrou o IS
    assert all(set(r.changed) <= {"InpA"} for r in res.rows if r.stage == "IS")
    assert res.n_feasible > 0 and res.reference_is.reason is None
    best = res.finalists[0]
    assert int(best.changed["InpA"]) >= 3 and best.size.lot is not None and best.size.dd <= 0.3
    assert res.reference_size.lot is not None                                   # referência também dimensionada
    assert best.qualified and best.verdict == "CANDIDATO"
    assert best.set_inputs["InpUseFixedLot"] == "true" and float(best.set_inputs["InpFixedLot"]) == best.size.lot
    assert any(r.stage == "LOTE" for r in res.rows)                             # evidência no CSV
    assert "lote no teto" in "".join(dsc.render_summary([res])) or "lote" in dsc.render_summary([res])


def test_a_finalist_that_cannot_fit_the_cap_even_with_the_minimum_lot_is_rejected():
    edge = lambda i, f, u: GOOD if int(i["InpA"]) >= 3 else BAD
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _s(max_drawdown_pct=1e-9), UNTIL, lot_engine(edge),
                              lot_bounds=lambda sym: BOUNDS)
    assert res.finalists and all(not f.qualified for f in res.finalists)
    assert all("lote:" in f.verdict and "nem com o lote mínimo" in f.verdict for f in res.finalists)
    assert dsc.write_candidate_sets(__import__("pathlib").Path("."), [res]) == []


def test_only_lot_params_released_with_size_last_is_an_error():
    spec = StrategySpec("EA", ["TST"], [ParamSpec("InpFixedLot", "float", values=[0.5, 1.0])])
    with pytest.raises(dsc.DiscoveryError, match="nenhum parâmetro para a busca"):
        dsc.discover_target(spec, "TST", _template(), "H1", _s(), UNTIL, lot_engine(lambda *a: GOOD))


def test_without_size_last_the_lot_is_searched_like_any_other_param():
    res = dsc.discover_target(_spec(), "TST", _template(), "H1", _s(size_last=False, max_drawdown_pct=50.0), UNTIL,
                              lot_engine(lambda *a: GOOD))
    assert any("InpFixedLot" in r.changed for r in res.rows if r.stage == "IS") and res.reference_size is None


def test_connection_loss_stops_the_whole_run_instead_of_cascading_errors():
    n = {"calls": 0}

    def run_fn(tpl, inputs, from_date, until):
        n["calls"] += 1
        if n["calls"] == 4:
            raise ConnectionLost("a conexão com o MT5 caiu")
        return lot_engine(lambda *a: GOOD)(tpl, inputs, from_date, until)

    with pytest.raises(ConnectionLost):
        dsc.discover_target(_spec(), "TST", _template(), "H1", _s(), UNTIL, run_fn)
    assert n["calls"] == 4                                                       # parou na falha, sem insistir


# ---------------------------------------------------------------- conexão compartilhada

class FakeMt5:
    def __init__(self):
        self.init, self.shut, self.alive = 0, 0, True

    def initialize(self, path=None):
        self.init += 1
        return True

    def shutdown(self):
        self.shut += 1
        self.alive = False

    def terminal_info(self):
        return SimpleNamespace(maxbars=100000) if self.alive else None

    def last_error(self):
        return (-10004, "No IPC connection")

    def symbols_get(self):
        return [SimpleNamespace(name="XAUUSD", description="Gold", path="Metals/XAUUSD"),
                SimpleNamespace(name="DAX40", description="Germany 40", path="Indices/DAX40")]


@pytest.fixture
def fake_mt5(monkeypatch):
    fake = FakeMt5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    monkeypatch.setattr(mt5data, "_is_running", lambda exe: True)
    monkeypatch.setattr(mt5data, "_CONN_COUNT", 0)
    return fake


def test_nested_sources_share_one_connection_and_only_the_last_exit_closes_it(fake_mt5):
    """Reproduz o bug de 2026-11-24: a segunda ação fechava a conexão da descoberta ('No IPC connection')."""
    with Mt5Source("x") as a:
        with Mt5Source("x") as b:
            assert b.maxbars == 100000
        assert fake_mt5.shut == 0 and a.maxbars == 100000                       # a ainda enxerga o terminal
    assert fake_mt5.init == 1 and fake_mt5.shut == 1


def test_a_dead_connection_raises_a_clear_error_not_a_nonetype_crash(fake_mt5):
    with Mt5Source("x") as s:
        fake_mt5.alive = False                                                  # o terminal/IPC some no meio
        for call in (lambda: s.maxbars, lambda: s.symbol_spec("DAX40"), lambda: s.symbols()):
            with pytest.raises(ConnectionLost, match="caiu"):
                call()


def test_symbols_are_listed_sorted(fake_mt5):
    with Mt5Source("x") as s:
        assert [x[0] for x in s.symbols()] == ["DAX40", "XAUUSD"]
        assert s.symbols()[0] == ("DAX40", "Germany 40", "Indices/DAX40")


# ---------------------------------------------------------------- uma execução por vez

def test_only_one_long_job_runs_at_a_time():
    with services.exclusive("descoberta"):
        with pytest.raises(services.BusyError, match="descoberta"):
            with services.exclusive("monitor"):
                pass
    with services.exclusive("monitor"):                                          # liberou ao sair
        pass
