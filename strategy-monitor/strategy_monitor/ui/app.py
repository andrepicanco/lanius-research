"""Tela local do strategy-monitor (Streamlit). Roda no seu PC, sem permissão de administrador:

    .\\interface.bat        (ou: python -m streamlit run strategy_monitor/ui/app.py)

Tudo o que a tela faz já existe na CLI; aqui só escolhem-se as opções com cliques e veem-se os resultados.
A lógica vive em `services.py` e `ui/logic.py`; este arquivo só desenha.
"""

import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from strategy_monitor import cli, discovery as dsc, services  # noqa: E402
from strategy_monitor.compare import ATENCAO, FALHA, NA, OK  # noqa: E402
from strategy_monitor.config import load_config  # noqa: E402
from strategy_monitor.discovery_config import (DiscoveryConfigError, ParamSpec, Settings,  # noqa: E402
                                               load_discovery_config)
from strategy_monitor.engine import parity  # noqa: E402
from strategy_monitor.engine.mt5data import DataError  # noqa: E402
from strategy_monitor.metrics import compute_metrics  # noqa: E402
from strategy_monitor.ui import logic  # noqa: E402
from strategy_monitor.ui.html_export import BASE_COLOR, NEW_COLOR, render_monitor_html  # noqa: E402

ICON = {OK: "🟢", ATENCAO: "🟡", FALHA: "🔴", NA: "⚪"}          # sempre acompanhado do texto do estado
DISCOVERY_YAML = ROOT / "config" / "discovery.yaml"


# ---------------------------------------------------------------- dados

@st.cache_resource(show_spinner=False)
def get_config():
    return load_config(os.environ.get("STRATEGY_MONITOR_CONFIG"))


@st.cache_data(show_spinner="Lendo os baselines…")
def load_baselines(_cfg, stamp: tuple):
    return cli.load_baselines(_cfg)


def baselines_for(cfg):
    stamp = tuple((f.name, f.stat().st_mtime) for f in services.baseline_dir_files(cfg))
    return load_baselines(cfg, stamp)


def cum_chart(series: dict[str, list[float]], colors: list[str]):
    """R acumulado por trade. Uma linha por série (cor = entidade: azul baseline/Tester, laranja novo/motor)."""
    series = {k: v for k, v in series.items() if v}
    if not series:
        st.caption("Sem R calculável para o gráfico.")
        return
    n = max(len(v) for v in series.values())
    df = pd.DataFrame({k: pd.Series(v) for k, v in series.items()})
    df.index = range(1, n + 1)
    st.line_chart(df, color=colors[: len(series)], x_label="trade nº (por ordem de fechamento)", y_label="R acumulado")


def metrics_table(base, cur) -> pd.DataFrame:
    f = lambda x, p=2, pct=False, sg=False: "n/d" if x is None else (
        f"{x * 100:.1f}%" if pct else f"{x:{'+' if sg else ''}.{p}f}")
    rows = {
        "Trades": (str(base.n), str(cur.n)),
        "Win rate": (f(base.win_rate, pct=True), f(cur.win_rate, pct=True)),
        "Breakeven": (f(base.breakeven_wr, pct=True), f(cur.breakeven_wr, pct=True)),
        "E[R] por trade": (f(base.expectancy_r, 3, sg=True), f(cur.expectancy_r, 3, sg=True)),
        "Profit factor": (f(base.profit_factor), f(cur.profit_factor)),
        "Drawdown (saldo fechado)": (f"{base.max_dd_pct:.2f}%", f"{cur.max_dd_pct:.2f}%"),
        "Trades/mês": (f(base.trades_per_month, 1), f(cur.trades_per_month, 1)),
    }
    return pd.DataFrame(rows, index=["Baseline", "Período novo"]).T


# ---------------------------------------------------------------- páginas

def page_monitor(cfg, baselines):
    st.header("Monitor")
    st.caption("Roda o motor Python só no período posterior ao baseline e compara com ele. "
               "Precisa do MT5 aberto e logado (leitura de barras).")
    labels = {b.label: b for b in baselines.values()}
    c1, c2 = st.columns([3, 1])
    chosen = c1.multiselect("Estratégias", list(labels), default=list(labels))
    until = c2.date_input("Testar até (exclusivo)", value=dt.date.today())
    if st.button("Rodar monitor", type="primary", disabled=not chosen):
        with st.status("Rodando o motor…", expanded=True) as status:
            try:
                items, problems = services.run_monitor(cfg, [labels[c] for c in chosen], until, log=st.write)
            except DataError as e:
                status.update(label="Erro", state="error")
                st.error(str(e))
                return
            status.update(label=f"Concluído: {len(items)} estratégia(s)", state="complete")
        st.session_state["monitor"] = (items, problems, until)
    res = st.session_state.get("monitor")
    if not res:
        st.info("Escolha as estratégias e clique em **Rodar monitor**.")
        return
    items, problems, until = res
    for p in problems:
        (st.error if p.startswith("[ERRO]") else st.info)(p)
    for it in items:
        v = it.verdict
        st.subheader(f"{ICON[v.overall]} {it.base.label} — {v.overall}")
        st.caption(" · ".join(v.context))
        left, right = st.columns([2, 3])
        with left:
            base_m = compute_metrics(it.base.trades, it.base.deposit, cfg.thresholds.confidence)
            st.dataframe(metrics_table(base_m, it.metrics), width="stretch")
        with right:
            cum_chart({f"Baseline (n={len(it.base.trades)})": services.cumulative_r(it.base.trades),
                       f"Período novo (n={len(it.judged)})": services.cumulative_r(it.judged)},
                      [BASE_COLOR[0], NEW_COLOR[0]])
        with st.expander("Verificações"):
            st.dataframe(pd.DataFrame([{"Estado": f"{ICON[c.status]} {c.status}", "Verificação": c.name,
                                        "Detalhe": c.detail} for c in v.checks]),
                         hide_index=True, width="stretch")
        with st.expander(f"Trades do período novo ({len(it.judged)})"):
            st.dataframe(pd.DataFrame([{"Entrada": t.open_time, "Saída": t.close_time, "Lado": t.direction,
                                        "Preço entrada": t.open_price, "Preço saída": t.close_price,
                                        "Motivo": t.exit_reason, "Líquido": round(t.net, 2),
                                        "R": None if t.r_multiple is None else round(t.r_multiple, 2)}
                                       for t in sorted(it.judged, key=lambda t: t.open_time)]),
                         hide_index=True, width="stretch")
    if items:
        st.divider()
        c1, c2 = st.columns(2)
        c1.download_button("Baixar relatório HTML", render_monitor_html(items, until).encode("utf-8"),
                           file_name=f"monitor_{dt.date.today():%Y%m%d}.html", mime="text/html")
        if c2.button("Enviar resumo ao Telegram"):
            try:
                services.send_monitor_summary(items)
                st.success("Resumo enviado.")
            except Exception as e:  # credencial ausente, rede
                st.error(f"Não enviou: {e}")


def page_baselines(cfg, baselines):
    st.header("Baselines")
    st.caption(f"Pasta: `{cfg.baseline_dir}`. Para adicionar uma estratégia, ponha o `.xlsx` do Tester nessa pasta.")
    rows = []
    for b in baselines.values():
        m = compute_metrics(b.trades, b.deposit, cfg.thresholds.confidence)
        rows.append({"Estratégia": b.label, "Período": f"{b.date_from} → {b.date_to}", "Trades": m.n,
                     "Win rate": f"{m.win_rate * 100:.1f}%", "Breakeven": f"{m.breakeven_wr * 100:.1f}%",
                     "PF": round(m.profit_factor, 2), "E[R]": round(m.expectancy_r, 3) if m.expectancy_r is not None else None,
                     "DD": f"{m.max_dd_pct:.2f}%", "Julgado a partir de": f"{services.baseline_end(b):%Y-%m-%d}"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    labels = {b.label: b for b in baselines.values()}
    pick = st.selectbox("Conferir o motor contra o Tester em", list(labels))
    st.caption("Roda o motor Python no período do baseline e compara trade a trade com o relatório do Tester. "
               "É o que diz se dá para confiar no motor nessa estratégia.")
    if st.button("Conferir motor (paridade)"):
        with st.spinner("Rodando o motor…"):
            try:
                par, rep, base = services.run_parity(cfg, [labels[pick]])[0]
            except DataError as e:
                st.error(str(e))
                return
        if par is None:
            st.warning(f"O motor ainda não implementa {base.expert}.")
            return
        (st.success if par.ok else st.warning)(
            f"{ICON[OK if par.ok else ATENCAO]} {'OK' if par.ok else 'ATENCAO'}: {par.matched}/{par.n_base} trades pareados")
        st.code(parity.render(par), language=None)
        last = max(t.close_time for t in base.trades)
        cum_chart({f"Tester (n={len(base.trades)})": services.cumulative_r(base.trades),
                   f"Motor (n={len([t for t in rep.trades if t.open_time <= last])})":
                       services.cumulative_r([t for t in rep.trades if t.open_time <= last])},
                  [BASE_COLOR[0], NEW_COLOR[0]])


def _default_discovery():
    try:
        return load_discovery_config(DISCOVERY_YAML)
    except DiscoveryConfigError:
        return Settings(max_drawdown_pct=5.0), []


def page_discovery(cfg, baselines):
    st.header("Descoberta de parâmetros")
    st.caption("Busca parâmetros melhores para uma estratégia que você já tem, com validação fora da amostra. "
               "**Nada é aplicado**: o resultado é um relatório e arquivos `.set` candidatos. "
               "Os valores iniciais vêm de `config/discovery.yaml`.")
    d_settings, d_specs = _default_discovery()
    experts = sorted({b.expert for b in baselines.values() if logic.tunable_params(b.expert)})
    if not experts:
        st.warning("Nenhum baseline de uma estratégia que o motor implemente.")
        return
    expert = st.selectbox("Estratégia", experts)
    mine = [b for b in baselines.values() if b.expert == expert]
    d_spec = next((s for s in d_specs if s.expert == expert), None)
    own_symbols = [b.symbol for b in mine]
    tickers_txt = st.text_input(
        "Ativos (separados por vírgula)", value=", ".join(d_spec.tickers if d_spec else own_symbols),
        help="Qualquer símbolo do seu MT5. Ativo sem baseline usa os inputs de outro baseline como referência "
             "(não validada nesse ativo; stops em pontos mudam de escala).")
    tickers = [t.strip() for t in tickers_txt.split(",") if t.strip()]
    template = timeframe = None
    new_assets = [t for t in tickers if t not in own_symbols]
    if new_assets:
        st.info(f"Sem baseline próprio: {', '.join(new_assets)}. A paridade do motor não foi medida nesse(s) ativo(s).")
        template = st.selectbox("Baseline usado como referência de inputs", [b.label for b in mine])
        timeframe = st.text_input("Timeframe nesses ativos (vazio = o do baseline de referência)", value="") or None

    st.subheader("Parâmetros liberados")
    tun = logic.tunable_params(expert)
    base_inputs = mine[0].inputs
    default_sel = [p.name for p in d_spec.params] if d_spec else []
    selected = st.multiselect("Quais parâmetros podem variar", list(tun),
                              default=[p for p in default_sel if p in tun],
                              help="Só o que estiver aqui é alterado; o resto fica como no baseline. "
                                   "Magic number, comentário e dimensionamento de lote não aparecem: não criam edge.")
    for w in logic.dependency_warnings(selected, base_inputs):
        st.warning(w)
    specs, form_error = [], None
    by_name = {p.name: p for p in (d_spec.params if d_spec else [])}
    for name in selected:
        kind, cur = tun[name], base_inputs.get(name, "?")
        prior = by_name.get(name)
        with st.expander(f"{name} (baseline: {cur})", expanded=False):
            if kind == "bool":
                st.caption("Testa ligado e desligado.")
                specs.append(ParamSpec(name, "bool"))
                continue
            mode = st.radio("Como definir os valores", logic.MODES, horizontal=True, key=f"m_{expert}_{name}",
                            index=logic.MODES.index(logic.mode_of(prior)),
                            help="faixa = mínimo/máximo/passo · valores = lista · múltiplos = fatores do valor do baseline")
            try:
                if mode == "faixa":
                    a, b, c = st.columns(3)
                    lo = a.number_input("mínimo", value=float(prior.min) if prior and prior.min is not None else 0.0,
                                        key=f"lo_{expert}_{name}")
                    hi = b.number_input("máximo", value=float(prior.max) if prior and prior.max is not None else 1.0,
                                        key=f"hi_{expert}_{name}")
                    stp = c.number_input("passo", value=float(prior.step) if prior and prior.step else 0.1,
                                         key=f"st_{expert}_{name}")
                    specs.append(logic.spec_from_form(name, kind, mode, lo=lo, hi=hi, step=stp))
                elif mode == "valores":
                    txt = st.text_input("valores", value=logic.numbers_text(prior.values if prior else None),
                                        key=f"v_{expert}_{name}")
                    specs.append(logic.spec_from_form(name, kind, mode, values=txt))
                else:
                    txt = st.text_input("múltiplos do valor do baseline",
                                        value=logic.numbers_text(prior.factors if prior and prior.factors else logic.DEFAULT_FACTORS),
                                        key=f"f_{expert}_{name}")
                    specs.append(logic.spec_from_form(name, kind, mode, factors=txt))
                grid = specs[-1].candidates(cur if cur != "?" else "1")
                st.caption(f"{len(grid)} valores: {', '.join(grid[:12])}{' …' if len(grid) > 12 else ''}")
            except (ValueError, TypeError) as e:
                form_error = str(e)
                st.error(form_error)

    st.subheader("Janelas e validação")
    c = st.columns(4)
    is_days = c[0].number_input("Dias de busca (IS)", 30, 1500, d_settings.is_days, step=10)
    oos_days = c[1].number_input("Dias de teste (OOS)", 14, 400, d_settings.oos_days, step=5,
                                 help="A busca nunca vê esta janela.")
    trials = c[2].number_input("Tentativas", 10, 5000, d_settings.trials, step=10)
    top_k = c[3].number_input("Finalistas", 1, 30, d_settings.top_k)
    c = st.columns(4)
    dd = c[0].number_input("Teto de drawdown (%)", 0.5, 100.0, float(d_settings.max_drawdown_pct or 5.0), step=0.5,
                           help="Tentativa acima do teto sai do ranking. Obrigatório.")
    folds = c[1].number_input("Trechos de consistência", 0, 8, d_settings.folds,
                              help="O IS é dividido nesse nº de trechos; 0 desliga o teste.")
    min_pos = c[2].number_input("Trechos com E[R] > 0 (mín.)", 0, 8, d_settings.min_positive_folds)
    seed = c[3].number_input("Semente", 0, 10**6, d_settings.seed)
    c = st.columns(3)
    min_is = c[0].number_input("Trades mínimos no IS", 5, 500, d_settings.min_trades_is)
    min_oos = c[1].number_input("Trades mínimos no OOS", 3, 500, d_settings.min_trades_oos)
    until = c[2].date_input("Até (exclusivo)", value=dt.date.today())

    settings = Settings(is_days=int(is_days), oos_days=int(oos_days), trials=int(trials), top_k=int(top_k),
                        seed=int(seed), min_trades_is=int(min_is), min_trades_oos=int(min_oos), folds=int(folds),
                        min_positive_folds=int(min_pos), max_drawdown_pct=float(dd))
    try:
        spec = logic.build_strategy_spec(expert, tickers, specs, template, timeframe)
    except ValueError as e:
        st.warning(str(e))
        return
    if form_error:
        return
    try:
        plan = dsc.plan([spec], settings, baselines, None, until)
    except dsc.DiscoveryError as e:
        st.error(str(e))
        return
    total = sum(i["runs"] for i in plan)
    st.dataframe(pd.DataFrame([{"Ativo": i["symbol"], "Timeframe": i["timeframe"], "Combinações possíveis": i["space"],
                                "Tentativas": i["trials"], "Backtests (~)": i["runs"]} for i in plan]),
                 hide_index=True, width="stretch")
    st.caption(f"Total ≈ {total} backtests, ~{max(1, round(total * 0.3 / 60))} min. "
               f"Janelas: IS {plan[0]['windows'].is_start} → {plan[0]['windows'].oos_start}, "
               f"OOS {plan[0]['windows'].oos_start} → {until}.")
    c1, c2 = st.columns([1, 1])
    if c2.button("Salvar como padrão (discovery.yaml)"):
        logic.save_discovery_yaml(DISCOVERY_YAML, settings, spec)
        st.success("Padrão salvo. A seleção de outras estratégias no arquivo foi mantida.")
    if c1.button("Rodar descoberta", type="primary"):
        with st.status("Rodando a descoberta…", expanded=True) as status:
            try:
                results, out_dir, sets = services.run_discovery(cfg, settings, [spec], baselines, None, until, log=st.write)
            except (DataError, dsc.DiscoveryError) as e:
                status.update(label="Erro", state="error")
                st.error(str(e))
                return
            status.update(label="Concluído", state="complete")
        st.session_state["discovery"] = (results, out_dir, sets)
    show_discovery(st.session_state.get("discovery"))


def show_discovery(res):
    if not res:
        return
    results, out_dir, sets = res
    st.divider()
    st.subheader("Resultado")
    for r in results:
        st.markdown(f"**{r.label}** — {r.n_feasible}/{r.n_trials} tentativas viáveis no IS")
        if r.aborted:
            st.error(f"Interrompido: {r.aborted}")
            continue
        if r.selection_note:
            st.caption(f"Seleção: {r.selection_note}")
        if r.reference_is and r.reference_is.reason:
            st.warning(f"A própria referência fica fora das restrições no IS ({r.reference_is.reason}). "
                       "Se o teto de drawdown estiver apertado demais para a janela, nenhuma tentativa é viável.")
        if not r.finalists:
            st.info("Nenhuma tentativa ficou dentro das restrições: não há finalistas.")
            continue
        fmt = lambda row: "—" if row is None or row.metrics is None else (
            f"{row.er:+.3f} (n={row.metrics.n}, DD {row.metrics.max_dd_pct:.1f}%)" if row.er is not None else "n/d")
        rows = [{"": "ref", "Parâmetros alterados": "(baseline)", "IS E[R]": fmt(r.reference_is),
                 "Trechos > 0": "—", "Mediana vizinhos": "—", "OOS E[R]": fmt(r.reference_oos), "Veredito": "—"}]
        for i, f in enumerate(r.finalists, 1):
            nm = f.neighbor_median
            rows.append({"": f"#{i}", "Parâmetros alterados": dsc._changed(f.changed, r.ref_inputs),
                         "IS E[R]": fmt(f.is_row), "Trechos > 0": f"{f.positive_folds}/{len(f.folds)}",
                         "Mediana vizinhos": "—" if nm is None else f"{nm:+.3f}", "OOS E[R]": fmt(f.oos),
                         "Veredito": ("✅ " if f.qualified else "❌ ") + f.verdict})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(f"Arquivos em `{out_dir}`")
    c = st.columns(3)
    rep, csv_ = out_dir / "report.md", out_dir / "results.csv"
    c[0].download_button("report.md", rep.read_bytes(), file_name=rep.name, mime="text/markdown")
    c[1].download_button("results.csv", csv_.read_bytes(), file_name=csv_.name, mime="text/csv")
    for s in sets:
        st.download_button(f"{s.name} (candidato)", s.read_bytes(), file_name=s.name, key=f"set_{s.name}")
    if not sets:
        c[2].caption("Nenhum candidato passou nos três testes.")


# ---------------------------------------------------------------- main

def main():
    st.set_page_config(page_title="Strategy Monitor", page_icon="📈", layout="wide")
    cfg = get_config()
    st.sidebar.title("Strategy Monitor")
    page = st.sidebar.radio("Página", ["Monitor", "Baselines", "Descoberta"])
    st.sidebar.caption(f"MT5: `{cfg.mt5.terminal_path}`" if cfg.mt5.terminal_path else "MT5: não configurado")
    if st.sidebar.button("Recarregar baselines"):
        load_baselines.clear()
    if cfg.baseline_dir is None or not cfg.baseline_dir.is_dir():
        st.error("`baseline_dir` não configurado ou inexistente em config/monitor.yaml.")
        return
    baselines = baselines_for(cfg)
    if not baselines:
        st.warning(f"Nenhum baseline `.xlsx` em {cfg.baseline_dir}.")
        return
    {"Monitor": page_monitor, "Baselines": page_baselines, "Descoberta": page_discovery}[page](cfg, baselines)


main()
