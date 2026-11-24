"""Tela: lógica pura (parâmetros, YAML, HTML) e um fumaça do app Streamlit sem MT5 (serviços simulados)."""

import datetime as dt
from pathlib import Path

import pytest
import yaml

from conftest import make_xlsx, trade_spec
from strategy_monitor import services
from strategy_monitor.config import load_config
from strategy_monitor.engine import mt5data
from strategy_monitor.discovery_config import ParamSpec, Settings, load_discovery_config
from strategy_monitor.tester_report import read_report
from strategy_monitor.ui import logic
from strategy_monitor.ui.html_export import render_monitor_html

APP = Path(__file__).resolve().parents[1] / "strategy_monitor" / "ui" / "app.py"


# ---------------------------------------------------------------- lógica

def test_tunable_params_exclude_identity_risk_percent_and_enums():
    t = logic.tunable_params("IdxSwing91")
    assert t["InpEMAPeriod"] == "int" and t["InpTP_RMultiple"] == "float" and t["InpUseTrailing"] == "bool"
    assert t["InpUseFixedLot"] == "bool" and t["InpFixedLot"] == "float" and t["InpRiskPercent"] == "float"
    for banned in ("InpMagicNumber", "InpTradeComment", "InpLogLevel", "InpTimeframe"):
        assert banned not in t
    assert logic.tunable_params("EaDesconhecido") == {}


def test_dependency_warnings_flag_params_whose_switch_is_off():
    base = {"InpUseFixedLot": "false", "InpUseTrailing": "true"}
    w = logic.dependency_warnings(["InpFixedLot"], base)
    assert len(w) == 1 and "InpUseFixedLot" in w[0] and "InpFixedLot" in w[0]
    assert logic.dependency_warnings(["InpFixedLot", "InpUseFixedLot"], base) == []     # o interruptor também varia
    assert logic.dependency_warnings(["InpATRMultiplier"], base) == []                   # trailing já está ligado
    assert logic.dependency_warnings(["InpFixedLot"], {"InpUseFixedLot": "true"}) == []
    # InpRiskPercent vale com o lote fixo DESLIGADO (o inverso)
    assert len(logic.dependency_warnings(["InpRiskPercent"], {"InpUseFixedLot": "true"})) == 1
    assert logic.dependency_warnings(["InpRiskPercent"], {"InpUseFixedLot": "false"}) == []


def test_spec_from_form_modes_and_errors():
    assert logic.spec_from_form("A", "float", "faixa", lo=1, hi=2, step=0.5).candidates("1") == ["1", "1.5", "2"]
    assert logic.spec_from_form("A", "int", "valores", values="10, 20;30").candidates("1") == ["10", "20", "30"]
    assert logic.spec_from_form("A", "int", "múltiplos", factors="0.5 1 2").candidates("600") == ["300", "600", "1200"]
    assert logic.spec_from_form("A", "bool", "faixa").candidates("true") == ["true", "false"]
    with pytest.raises(ValueError, match="faixa inválida"):
        logic.spec_from_form("A", "int", "faixa", lo=5, hi=1, step=1)
    with pytest.raises(ValueError, match="números"):
        logic.spec_from_form("A", "int", "valores", values="a, b")
    with pytest.raises(ValueError, match="pelo menos um"):
        logic.spec_from_form("A", "int", "valores", values=" ")


def test_build_strategy_spec_requires_params_and_tickers():
    p = [ParamSpec("A", "bool")]
    with pytest.raises(ValueError, match="parâmetro"):
        logic.build_strategy_spec("EA", ["X"], [])
    with pytest.raises(ValueError, match="ativo"):
        logic.build_strategy_spec("EA", [], p)
    assert logic.build_strategy_spec("EA", ["X"], p).tickers == ["X"]


def test_save_discovery_yaml_roundtrips_and_keeps_other_strategies(tmp_path):
    f = tmp_path / "d.yaml"
    f.write_text("settings: {max_drawdown_pct: 9}\nstrategies:\n  Outra:\n    tickers: [Z]\n    parameters:\n      InpQ: {type: bool}\n",
                 encoding="utf-8")
    spec = logic.build_strategy_spec("IdxSwing91", ["DAX40", "UK100"], [
        ParamSpec("InpATRMultiplier", "float", min=1.0, max=2.0, step=0.5), ParamSpec("InpUseTrailing", "bool"),
        ParamSpec("InpSLBufferPoints", "int", factors=[0.5, 1.0])], template="DAX40", timeframe="M12")
    logic.save_discovery_yaml(f, Settings(max_drawdown_pct=7.5, trials=50, folds=0), spec)
    settings, specs = load_discovery_config(f)
    assert settings.max_drawdown_pct == 7.5 and settings.trials == 50 and settings.folds == 0
    by = {s.expert: s for s in specs}
    assert set(by) == {"Outra", "IdxSwing91"}                                  # a outra estratégia foi mantida
    mine = by["IdxSwing91"]
    assert mine.tickers == ["DAX40", "UK100"] and mine.template == "DAX40" and mine.timeframe == "M12"
    assert [(p.name, p.type) for p in mine.params] == [("InpATRMultiplier", "float"), ("InpUseTrailing", "bool"),
                                                       ("InpSLBufferPoints", "int")]
    assert yaml.safe_load(f.read_text(encoding="utf-8"))["strategies"]["IdxSwing91"]["parameters"]["InpSLBufferPoints"]["factors"] == [0.5, 1.0]


# ---------------------------------------------------------------- fixtures de app

def _baseline_dir(tmp_path):
    d = tmp_path / "bl"
    d.mkdir()
    make_xlsx(d / "b.xlsx", [trade_spec(1, 100, 110, 95), trade_spec(2, 100, 90, 95), trade_spec(3, 100, 110, 95)],
              expert="IdxSwing91", symbol="TST", tf="M12", period=("2026.01.01", "2026.02.28"),
              inputs={"InpEMAPeriod": "9", "InpTP_RMultiple": "2.0", "InpUseTrailing": "true", "InpTimeframe": "12"})
    return d


def _items(cfg, base_dir):
    base = next(iter(_load(cfg).values()))
    cur_f = base_dir.parent / "cur.xlsx"
    make_xlsx(cur_f, [trade_spec(1, 100, 112, 95, month=3), trade_spec(2, 100, 90, 95, month=3),
                      trade_spec(3, 100, 111, 95, month=3)], expert="IdxSwing91", symbol="TST", tf="M12")
    items = [services.evaluate(cfg, base, read_report(cur_f))]
    for t in items[0].base.trades + items[0].judged:          # trades sintéticos não têm R estimável: fixa um
        t.r_multiple = 1.0 if t.net > 0 else -1.0
    return items


def _load(cfg):
    from strategy_monitor import cli
    return cli.load_baselines(cfg)


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    import streamlit as st
    d = _baseline_dir(tmp_path)
    cfg_f = tmp_path / "monitor.yaml"
    cfg_f.write_text(f"baseline_dir: '{d}'\nmt5:\n  terminal_path: 'C:\\\\nao\\\\existe\\\\terminal64.exe'\n", encoding="utf-8")
    monkeypatch.setenv("STRATEGY_MONITOR_CONFIG", str(cfg_f))
    st.cache_resource.clear()
    st.cache_data.clear()
    return load_config(cfg_f), d


def _apptest():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(APP), default_timeout=60)


# ---------------------------------------------------------------- app

def test_app_loads_every_page_without_errors(app_env):
    at = _apptest().run()
    assert not at.exception and at.sidebar.radio[0].options == ["Monitor", "Baselines", "Descoberta"]
    assert at.multiselect[0].options == ["IdxSwing91 · TST M12"]
    for page in ("Baselines", "Descoberta"):
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page
    assert any("Parâmetros liberados" in s.value for s in at.subheader)


def test_monitor_page_shows_verdict_comparison_and_export(app_env, monkeypatch):
    cfg, d = app_env
    items = _items(cfg, d)
    monkeypatch.setattr(services, "run_monitor", lambda cfg_, bases, until, log=None: (items, ["aviso de teste"]))
    at = _apptest().run()
    next(b for b in at.button if b.label == "Rodar monitor").click().run()
    assert not at.exception
    assert any("IdxSwing91 · TST M12" in s.value for s in at.subheader)      # selo com texto, não só cor
    assert any("aviso de teste" in i.value for i in at.info)
    html = render_monitor_html(items, dt.date(2026, 4, 1))
    assert "IdxSwing91 · TST M12" in html and "<svg" in html and "Período novo" in html and "Baseline" in html


def test_telegram_button_loads_the_env_file_the_cli_would_have_loaded(app_env, monkeypatch, tmp_path):
    """Regressão: a tela não passava por main() da CLI e dizia 'faltando TELEGRAM_*' mesmo com o .env preenchido."""
    from strategy_monitor import envfile, telegram
    cfg, d = app_env
    items = _items(cfg, d)
    env = tmp_path / "t.env"
    env.write_text("TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_0123456\nTELEGRAM_CHAT_ID=123456789\n",
                   encoding="utf-8")
    monkeypatch.setattr(envfile, "DEFAULT_ENV_PATH", env)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    sent = []

    class Resp:                                    # só a REDE é falsa: a checagem das credenciais é a verdadeira
        ok = True
    monkeypatch.setattr(telegram.requests, "post", lambda url, data, timeout: sent.append(data["text"]) or Resp())
    monkeypatch.setattr(services, "run_monitor", lambda *a, **k: (items, []))
    at = _apptest().run()
    next(b for b in at.button if b.label == "Rodar monitor").click().run()
    next(b for b in at.button if b.label == "Enviar resumo ao Telegram").click().run()
    assert not at.exception, [e.value for e in at.error]
    assert not at.error, [e.value for e in at.error]
    assert any("Resumo enviado" in s.value for s in at.success)
    assert sent and "IdxSwing91" in sent[0]
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def test_discovery_page_builds_a_plan_from_the_selected_parameters(app_env):
    at = _apptest().run()
    at.sidebar.radio[0].set_value("Descoberta").run()
    sel = next(m for m in at.multiselect if m.label == "Quais parâmetros podem variar")
    sel.set_value(["InpEMAPeriod", "InpUseTrailing"]).run()
    assert not at.exception
    assert any("backtests" in c.value for c in at.caption)                    # plano com o total estimado
    assert not at.error


def test_discovery_page_lists_mt5_symbols_adds_them_and_explains_the_terms(app_env, monkeypatch):
    monkeypatch.setattr(services, "list_symbols", lambda cfg: [("DAX40", "Germany 40", "Indices/DAX40"),
                                                               ("XAUUSD", "Gold", "Metals/XAUUSD")])
    at = _apptest().run()
    at.sidebar.radio[0].set_value("Descoberta").run()
    assert not at.exception
    # tooltips com o significado dos termos
    help_of = {n.label: (n.help or "") for n in at.number_input}
    assert "In-sample" in help_of["Dias de busca (IS)"] and "Out-of-sample" in help_of["Dias de teste (OOS)"]
    assert "R" in help_of["Trechos com E[R] > 0 (mín.)"] and "Expectância" in help_of["Trechos com E[R] > 0 (mín.)"]
    box = next(c for c in at.checkbox if c.label == "Dimensionar o lote no final")
    assert "maior lote" in box.help
    # lista de ativos: só depois do clique (não exige MT5 ao abrir a página)
    assert not any("Símbolo" in getattr(d.value, "columns", []) for d in at.dataframe)
    next(b for b in at.button if b.label == "Carregar / atualizar a lista").click().run()
    assert not at.exception
    assert any("DAX40" in list(d.value["Símbolo"]) for d in at.dataframe if "Símbolo" in d.value.columns)
    # atalho: escolher na lista e adicionar ao campo de ativos
    key = "tickers_IdxSwing91"
    at.multiselect(key="sym_pick").set_value(["XAUUSD"]).run()
    next(b for b in at.button if b.label == "Adicionar aos ativos da busca").click().run()
    assert not at.exception and "XAUUSD" in at.text_input(key=key).value


def test_discovery_page_shows_the_error_when_mt5_is_closed(app_env, monkeypatch):
    def closed(cfg):
        raise mt5data.DataError("o MT5 não está aberto")
    monkeypatch.setattr(services, "list_symbols", closed)
    at = _apptest().run()
    at.sidebar.radio[0].set_value("Descoberta").run()
    next(b for b in at.button if b.label == "Carregar / atualizar a lista").click().run()
    assert not at.exception and any("não está aberto" in e.value for e in at.error)
