"""Casos de uso compartilhados pela CLI e pela tela: nada aqui imprime nem depende de Streamlit."""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .compare import Verdict, compare
from .config import Config
from .engine.mt5data import BarCache, DataError, Mt5Source
from .engine.run import run_engine, supported
from .metrics import Metrics, attach_r_multiples, compute_metrics
from .models import Report
from .window import JudgedWindow, baseline_end, judged_window
from .window import describe as describe_window


@dataclass
class MonitorItem:
    base: Report
    report: Report              # run do motor (inclui o aquecimento antes do corte)
    metrics: Metrics            # só da janela julgada
    verdict: Verdict
    window: JudgedWindow
    judged: list                # trades julgados (Trade)


def open_engine(cfg: Config) -> tuple[Mt5Source, Callable[[Mt5Source], BarCache]]:
    """Conexão somente-leitura com a instância ABERTA do MT5 + fábrica do cache de barras.
    A fonte é um contexto: `with fonte as src: cache = fabrica(src)`."""
    if cfg.mt5.terminal_path is None:
        raise DataError("mt5.terminal_path não configurado (config/monitor.yaml): caminho do terminal64.exe do "
                        "MT5 que você usa, já aberto e logado")
    return Mt5Source(cfg.mt5.terminal_path), lambda src: BarCache(cfg.mt5.cache_dir, src)


def evaluate(cfg: Config, base: Report, current: Report) -> MonitorItem:
    """Métricas do run corrente (só a janela julgada, ver window.py) e o veredito contra o baseline."""
    attach_r_multiples(current.trades)
    trades, win = judged_window(base, current.trades)
    cur_m = compute_metrics(trades, current.deposit, cfg.thresholds.confidence)
    base_m = compute_metrics(base.trades, base.deposit, cfg.thresholds.confidence)
    verdict = compare(base_m, cur_m, cfg)
    verdict.context = describe_window(win)
    return MonitorItem(base, current, cur_m, verdict, win, trades)


def run_monitor(cfg: Config, bases: list[Report], until: dt.date,
                log: Callable[[str], None] = lambda m: None) -> tuple[list[MonitorItem], list[str]]:
    """Roda o motor no período posterior a cada baseline. Devolve (itens, avisos/erros por baseline)."""
    source, make_cache = open_engine(cfg)
    items, problems = [], []
    with source as src:
        cache = make_cache(src)
        for base in bases:
            if not supported(base.expert):
                problems.append(f"{base.label}: o motor ainda não implementa {base.expert}")
                continue
            cut = baseline_end(base)
            if cut.date() >= until:
                problems.append(f"{base.label}: nada novo a testar (o baseline cobre até {cut:%Y-%m-%d}; "
                                f"o teste iria até {until}, exclusivo).")
                continue
            log(f"Rodando {base.label}: {cut.date()} → {until} (exclusivo)")
            try:
                current = run_engine(base, cache, None, cut.date(), until)
            except DataError as e:
                problems.append(f"[ERRO] {base.label}: {e}")
                continue
            items.append(evaluate(cfg, base, current))
    return items, problems


def run_discovery(cfg: Config, settings, specs, baselines: dict[str, Report], only: str | None, until: dt.date,
                  log: Callable[[str], None] = lambda m: None):
    """Executa a descoberta e grava relatório, CSV e .set dos candidatos em outputs/discovery/<data>/.
    Devolve (resultados, pasta de saída, lista de .set)."""
    from . import discovery as dsc
    items = dsc.plan(specs, settings, baselines, only, until)
    if not items:
        raise dsc.DiscoveryError(f"nada a executar (filtro {only!r}?)")
    out_dir = cfg.outputs_dir / "discovery" / f"{dt.datetime.now():%Y%m%d_%H%M%S}"
    csvw = dsc.ResultsCsv(out_dir / "results.csv")
    source, make_cache = open_engine(cfg)
    results, tpl_inputs = [], {}
    try:
        with source as src:
            run_fn = dsc.make_run_fn(make_cache(src))
            for i in items:
                log(f"== {i['spec'].expert} · {i['symbol']} {i['timeframe']}")
                res = dsc.discover_target(i["spec"], i["symbol"], i["template"], i["timeframe"], settings,
                                          until, run_fn, log=log, on_row=csvw.add)
                tpl_inputs[res.label] = i["template"].inputs
                results.append(res)
    finally:
        csvw.close()
    dsc.write_report(out_dir / "report.md", results, settings, tpl_inputs, until)
    return results, out_dir, dsc.write_candidate_sets(out_dir, results)


def send_monitor_summary(items: list[MonitorItem]) -> None:
    """Envia o resumo ao Telegram. Carrega o `.env` AGORA (a CLI faz isso na largada; a tela não passa por lá),
    sem sobrescrever variáveis já definidas no ambiente do Windows."""
    from .envfile import load_env_file
    from .report import render_telegram
    from .telegram import send_message
    load_env_file()
    send_message(render_telegram([(i.report, i.metrics, i.verdict) for i in items]))


def run_parity(cfg: Config, bases: list[Report]):
    """Roda o motor sobre o período de cada baseline e compara com os trades do Tester.
    Devolve [(Parity | None, Report do motor | None, base)]; None quando o motor não implementa a estratégia."""
    from .engine import parity
    source, make_cache = open_engine(cfg)
    out = []
    with source as src:
        cache = make_cache(src)
        for b in bases:
            if not supported(b.expert):
                out.append((None, None, b))
                continue
            until = min(b.date_to, (max(t.close_time for t in b.trades) + dt.timedelta(days=1)).date())
            rep = run_engine(b, cache, None, b.date_from, until)
            out.append((parity.compare_reports(b, rep), rep, b))
    return out


def cumulative_r(trades) -> list[float]:
    """R acumulado por trade, na ordem de fechamento (trades sem R ficam de fora)."""
    out, acc = [], 0.0
    for t in sorted(trades, key=lambda t: t.close_time):
        if t.r_multiple is not None:
            acc += t.r_multiple
            out.append(round(acc, 4))
    return out


def baseline_dir_files(cfg: Config) -> list[Path]:
    return sorted(cfg.baseline_dir.glob("*.xlsx")) if cfg.baseline_dir and cfg.baseline_dir.is_dir() else []
