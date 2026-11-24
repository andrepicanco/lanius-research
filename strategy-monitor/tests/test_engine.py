"""Motor Python: indicadores, simulacao de posicao e um rompimento de ponta a ponta com dados sinteticos.
A fidelidade contra o Tester NAO e testada aqui (precisa do MT5): ela e o comando `parity`."""

import numpy as np
import pytest

from strategy_monitor.engine import indicators as ind
from strategy_monitor.engine.breakout_donchian1 import simulate as simulate_breakout
from strategy_monitor.engine.core import Market, lot_size, run_position, swap_money
from strategy_monitor.engine.inputs import InputError, allowed_hour, parse
from strategy_monitor.engine.mt5data import SymbolSpec, tf_from_mt5_enum

SPEC = SymbolSpec("TST", 0.01, 2, 0.01, 0.01, 0.01, 0.01, 500.0, 0, -600.0, -300.0, 3, 1)
_D = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4")]


def bars(times, o, h, l, c, spread=0):
    a = np.zeros(len(times), dtype=_D)
    a["time"], a["open"], a["high"], a["low"], a["close"], a["spread"] = times, o, h, l, c, spread
    a["tick_volume"] = 100
    return a


def market(sub_ohlc, per_bar=2, period=3600, spread=0, work_ohlc=None):
    """Mercado sintetico: `sub_ohlc` = lista de (o,h,l,c) das sub-barras; `per_bar` sub-barras por barra do EA."""
    n = len(sub_ohlc)
    step = period // per_bar
    st = np.arange(n) * step + 1_000_000 * 3600
    o, h, l, c = (np.array([x[i] for x in sub_ohlc], float) for i in range(4))
    sub = bars(st, o, h, l, c, spread)
    nw = n // per_bar
    if work_ohlc is None:
        wo = o[::per_bar][:nw]
        wh = h[:nw * per_bar].reshape(nw, per_bar).max(axis=1)
        wl = l[:nw * per_bar].reshape(nw, per_bar).min(axis=1)
        wc = c[per_bar - 1::per_bar][:nw]
    else:
        wo, wh, wl, wc = (np.array(v, float) for v in work_ohlc)
    work = bars(st[::per_bar][:nw], wo, wh, wl, wc, spread)
    return Market.build(SPEC, work, sub, period)


# ------------------------------------------------------------ indicadores

def test_sma_ema_and_nan_prefix():
    x = np.array([1.0, 2, 3, 4, 5])
    s = ind.sma(x, 3)
    assert np.isnan(s[:2]).all() and s[2:].tolist() == [2.0, 3.0, 4.0]
    e = ind.ema(x, 3)                                  # alfa 0.5, semente = primeiro valor
    assert e.tolist() == pytest.approx([1.0, 1.5, 2.25, 3.125, 4.0625])


def test_rsi_is_100_on_a_monotone_rise_and_0_on_a_fall():
    up = np.arange(1.0, 30.0)
    assert ind.rsi(up, 5)[-1] == 100.0
    assert ind.rsi(up[::-1].copy(), 5)[-1] == pytest.approx(0.0)


def test_atr_is_a_simple_average_of_true_range_by_default():
    h, l, c = np.array([11.0, 12, 13, 14, 15]), np.array([9.0, 10, 11, 12, 13]), np.array([10.0, 11, 12, 13, 14])
    a = ind.atr(h, l, c, 2)
    assert np.isnan(a[:2]).all() and a[2:].tolist() == [2.0, 2.0, 2.0]


def test_donchian_window_excludes_the_signal_bar():
    h = np.array([1.0, 2, 3, 9, 4])
    out = ind.rolling_max_before(h, 3)
    assert np.isnan(out[:3]).all() and out[3] == 3.0 and out[4] == 9.0       # out[3] nao ve o 9 da propria barra


# ------------------------------------------------------------ lote, swap, inputs

def test_lot_size_floors_to_the_step_and_respects_limits():
    # risco 100 / (sl 1.00 = 100 ticks * 0.01) = 100 lotes -> limitado ao maximo
    assert lot_size(SPEC, 10_000, 1.0, 1.0, False, 0.1) == 100.0
    assert lot_size(SPEC, 10_000, 0.3, 123.0, False, 0.1) == 0.24            # 0.2439 -> 0.24 (para baixo)
    assert lot_size(SPEC, 10_000, 0.01, 500.0, False, 0.1) == 0.01           # abaixo do minimo -> minimo
    assert lot_size(SPEC, 10_000, 1.0, 1.0, True, 0.137) == 0.13


def test_swap_counts_nights_and_triples_the_rollover_day():
    day = 86400
    # dia d (desde 1970) tem dia da semana (d+4)%7; escolhe uma quarta (3) e segura da noite de d para d+1
    d = next(x for x in range(20000, 20010) if (x + 4) % 7 == 3)
    assert swap_money(SPEC, 1, d * day + 3600, (d + 1) * day + 3600, 1.0) == pytest.approx(-600 * 0.01 / 0.01 * 0.01 * 3)
    assert swap_money(SPEC, 1, (d + 1) * day + 10, (d + 1) * day + 20, 1.0) == 0.0     # mesmo dia: nenhuma virada


def test_inputs_parse_types_defaults_and_errors():
    schema = {"A": (int, 1), "B": (bool, False), "C": (float, 0.5)}
    assert parse({"A": "12", "B": "true", "X": "ignorado"}, schema) == {"A": 12, "B": True, "C": 0.5}
    with pytest.raises(InputError, match="InpX"):
        parse({"InpX": "abc"}, {"InpX": (int, 0)})
    p = {"InpStartHour": 22, "InpEndHour": 6}
    assert allowed_hour(p, 23) and allowed_hour(p, 3) and not allowed_hour(p, 12)     # janela que vira a meia-noite


def test_timeframe_enum_names():
    assert tf_from_mt5_enum("12", "H1") == "M12" and tf_from_mt5_enum("16385", "M5") == "H1"
    assert tf_from_mt5_enum("0", "H4") == "H4"


# ------------------------------------------------------------ posicao

def flat(n, p=100.0):
    return [(p, p + 0.5, p - 0.5, p)] * n


def test_buy_hits_stop_loss_and_pays_the_spread_on_entry_only():
    m = market(flat(2) + [(100, 100.5, 96.5, 97)] + flat(3, 97), per_bar=1)
    ex = run_position(m, 1, 0, 0, 100.0, 97.0, 106.0, None, 0.0, stop_fill=False)
    assert (ex.reason, ex.price, ex.k) == ("sl", 97.0, 2)


def test_take_profit_and_sell_side_use_the_ask():
    up = market(flat(1) + [(100, 107, 99.8, 106)], per_bar=1)
    assert run_position(up, 1, 0, 0, 100.0, 95.0, 106.0, None, 0.0, False).reason == "tp"
    # venda: SL dispara pelo ASK = maxima + spread (1.0): maxima 103.5 + 1.0 >= 104
    sold = market(flat(1) + [(100, 103.5, 99.0, 100)], per_bar=1, spread=100)
    ex = run_position(sold, -1, 0, 0, 100.0, 104.0, 90.0, None, 0.0, False)
    assert ex.reason == "sl"


def test_sl_and_tp_in_the_same_subbar_resolve_to_the_stop():
    m = market(flat(1) + [(100, 110, 90, 100)], per_bar=1)
    assert run_position(m, 1, 0, 0, 100.0, 95.0, 105.0, None, 0.0, False).reason == "sl"


def test_gap_through_the_stop_exits_at_the_open_not_at_the_stop():
    m = market(flat(1) + [(90, 91, 89, 90)], per_bar=1)
    assert run_position(m, 1, 0, 0, 100.0, 95.0, 120.0, None, 0.0, False).price == 90.0
    # mas a sub-barra em que a ordem stop acabou de ser preenchida nao tem gap
    assert run_position(m, 1, 1, 1, 100.0, 95.0, 120.0, None, 0.0, True).price == 95.0


def test_trailing_only_tightens_and_applies_on_the_next_bar_open():
    # barras de 2 sub-barras; ATR constante 2 x mult 1 = distancia 2; preco sobe para 110 e cai a 105
    sub = [(100, 100.5, 99.5, 100), (100, 100.5, 99.5, 100),          # barra 0 (entrada)
           (110, 110.5, 109.5, 110), (110, 110.5, 109.5, 110),        # barra 1: SL sobe para 108
           (110, 110.5, 104.0, 105), (105, 105.5, 104.5, 105)]        # barra 2: cai a 104 -> bate no SL de 108
    m = market(sub, per_bar=2)
    ex = run_position(m, 1, 0, 0, 100.0, 90.0, 0.0, np.full(m.n, 2.0), 1.0, False)
    assert ex.reason == "sl" and ex.sl_final == 108.0 and ex.price == 108.0 and ex.j == 2


def test_an_open_position_is_closed_at_the_last_price_like_the_tester():
    m = market(flat(4, 100.0), per_bar=1)
    ex = run_position(m, 1, 0, 0, 100.0, 50.0, 0.0, None, 0.0, False)
    assert ex.reason == "other" and ex.price == 100.0


# ------------------------------------------------------------ rompimento de ponta a ponta

def test_breakout_enters_at_the_next_open_with_atr_stop_and_rmultiple_target():
    # 40 barras laterais (range 98..102 => ATR 4), fecha acima do canal na 41a, entra na abertura da 42a
    n_flat = 40
    sub = [(100, 102, 98, 100)] * n_flat
    sub += [(100, 106, 100, 105)]                    # barra de sinal: close 105 > maxima do canal 102 + buffer
    sub += [(105, 105.5, 104.5, 105)]                # barra de entrada (abre em 105)
    # ATR(14) na barra de sinal = (13*4 + 6)/14 = 4.143 => SL 100.86, TP (2R) 113.29
    sub += [(105, 114, 105, 105.5)]                  # toca o TP e fecha dentro do canal (sem novo rompimento)
    sub += flat(5, 105.5)
    m = market(sub, per_bar=1)
    inputs = {"InpDonchianPeriod": "20", "InpBufferATRMultiplier": "0.0", "InpSLATRPeriod": "14",
              "InpSLATRMultiplier": "1.0", "InpTP_RMultiple": "2.0", "InpUseTrailing": "false",
              "InpUseSMAFilter": "false", "InpRiskPercent": "1.0"}
    trades = simulate_breakout(inputs, m, 10_000.0, 25)
    assert len(trades) == 1
    t = trades[0]
    assert t.side == 1 and t.price_in == 105.0
    assert t.sl0 == pytest.approx(100.86, abs=0.011) and t.tp0 == pytest.approx(113.29, abs=0.011)
    assert t.reason == "tp" and t.price_out == pytest.approx(113.29, abs=0.011) and t.profit > 0
