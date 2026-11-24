"""CLI sob demanda (sem agendador):

  monitor [ALVO] [--telegram]      (monitor.bat: o comando do dia a dia)
  python -m strategy_monitor baselines
  python -m strategy_monitor compare --current <relatorio.xlsx> [--since 2026-11-08] [--telegram]
  python -m strategy_monitor run [--only DAX40] [--until 2026-11-17] [--telegram]
  python -m strategy_monitor discover [--only DAX40] [--trials 30] [--dry-run]
  python -m strategy_monitor telegram-test
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

from .compare import ATENCAO, Check, compare
from .config import Config, load_config
from .metrics import attach_r_multiples, compute_metrics
from .models import Report
from .report import render_telegram, render_text
from .discovery import DiscoveryError
from .discovery_config import DiscoveryConfigError
from .envfile import EnvFileError, load_env_file
from .telegram import TelegramConfigError
from .tester_report import ReportFormatError, read_report
from .window import baseline_end, judged_window
from .window import describe as describe_window


def load_baselines(cfg: Config) -> dict[str, Report]:
    if cfg.baseline_dir is None:
        raise SystemExit("baseline_dir não configurado: use config/monitor.yaml, "
                         "STRATEGY_MONITOR_BASELINE_DIR ou --baseline-dir")
    if not cfg.baseline_dir.is_dir():
        raise SystemExit(f"pasta de baselines não existe: {cfg.baseline_dir}")
    out: dict[str, Report] = {}
    for f in sorted(cfg.baseline_dir.glob("*.xlsx")):
        if f.name.startswith("~$"):  # arquivo de bloqueio do Excel
            continue
        try:
            r = read_report(f)
        except ReportFormatError as e:
            print(f"[aviso] ignorando {f.name}: {e}", file=sys.stderr)
            continue
        if r.key in out:
            raise SystemExit(f"dois baselines para {r.label}: {Path(out[r.key].source).name} e {f.name}")
        attach_r_multiples(r.trades)
        out[r.key] = r
    return out


def _cmd_baselines(cfg: Config, args) -> int:
    for r in load_baselines(cfg).values():
        m = compute_metrics(r.trades, r.deposit, cfg.thresholds.confidence)
        print(f"{r.label}  [{Path(r.source).name}]")
        print(f"  período {r.date_from} → {r.date_to} | histórico {r.history_quality} | ticks {r.ticks:,.0f}"
              if r.ticks else f"  período {r.date_from} → {r.date_to}")
        print(f"  n={m.n} win {m.win_rate * 100:.1f}% | BE {m.breakeven_wr * 100:.1f}% | "
              f"folga {m.wr_margin * 100:+.1f} pp | IC {m.wr_ci[0] * 100:.1f}–{m.wr_ci[1] * 100:.1f}%")
        er = f"{m.expectancy_r:+.3f} R" if m.expectancy_r is not None else "n/d"
        print(f"  líquido {m.net:+.2f} | PF {m.profit_factor:.2f} | E[R] {er} | DD {m.max_dd_pct:.2f}%")
        gap = (dt.datetime.combine(r.date_to, dt.time.min) - max(t.close_time for t in r.trades)).days
        if gap > 7:
            print(f"  [aviso] cabeçalho vai até {r.date_to}, mas o último trade fecha em "
                  f"{max(t.close_time for t in r.trades):%Y-%m-%d} ({gap} dias antes): provável período "
                  "forward separado, sem trades no arquivo.")
        print(f"  o monitor julga só entradas a partir de {baseline_end(r):%Y-%m-%d} (fim do período do baseline)")
        for w in r.warnings:
            print(f"  [aviso] {w}")
    return 0


def _evaluate(cfg: Config, base: Report, current: Report, since_arg: str | None, full: bool):
    """Metricas do baseline e do run corrente (so a janela julgada, ver window.py) e o veredito."""
    attach_r_multiples(current.trades)  # k e estimado sobre o relatorio inteiro, depois filtra
    trades, win = judged_window(base, current.trades, since_arg, full)
    cur_m = compute_metrics(trades, current.deposit, cfg.thresholds.confidence)
    base_m = compute_metrics(base.trades, base.deposit, cfg.thresholds.confidence)
    verdict = compare(base_m, cur_m, cfg)
    verdict.context = describe_window(win)
    if win.n_overlap:
        # Misturar trades do baseline com os novos infla o veredito: nunca pode sair "OK" assim.
        verdict.checks.insert(0, Check(
            "Sobreposição com o baseline", ATENCAO,
            f"{win.n_overlap} de {win.n_judged} trades julgados são do período do baseline; "
            "use o padrão ou --since a partir do fim dele"))
    return cur_m, verdict


def _cmd_run(cfg: Config, args) -> int:
    from .runner import RunnerConfig, RunnerError, run_and_read
    from .tester_ini import TesterSettings
    te = cfg.tester
    if te.terminal_dir is None:
        raise SystemExit("tester.terminal_dir nao configurado (config/monitor.yaml)")
    baselines = load_baselines(cfg)
    chosen = [b for b in baselines.values() if not args.only or args.only.lower() in b.label.lower()]
    if not chosen:
        raise SystemExit(f"nenhum baseline casa com {args.only!r}. Existentes: "
                         + ", ".join(b.label for b in baselines.values()))
    until = dt.datetime.strptime(args.until, "%Y-%m-%d").date() if args.until else dt.date.today()
    rcfg = RunnerConfig(te.terminal_dir, te.timeout_minutes * 60,
                        TesterSettings(model=te.model, execution_mode=te.execution_mode), te.experts,
                        te.min_free_gb, te.max_terminal_mb, te.keep_reports)
    items, failures = [], 0
    for base in chosen:
        since_dt = (dt.datetime.strptime(args.since, "%Y-%m-%d") if args.since else baseline_end(base))
        # Janela do run: so o pos-baseline + aquecimento dos indicadores (nao os meses do baseline).
        # Motivo: espaco em disco e memoria de ticks do Tester (NAS100 H1 de 10 meses = 194 M de ticks
        # estourou o cache de ~2 GB do agente e o teste parou sozinho em abril).
        from_date = base.date_from if args.full else max(base.date_from, since_dt.date() - dt.timedelta(days=te.warmup_days))
        if from_date >= until or (not args.full and since_dt.date() >= until):
            print(f"{base.label}: nada novo a testar (o baseline cobre até {baseline_end(base):%Y-%m-%d}; "
                  f"o teste iria até {until}, exclusivo).")
            continue
        name = f"sm_{base.expert}_{base.symbol}_{base.timeframe}_{dt.datetime.now():%Y%m%d_%H%M%S}"
        print(f"Rodando {base.label}: {from_date} -> {until} (exclusivo)...", flush=True)
        try:
            current = run_and_read(base, rcfg, until, name, cfg.outputs_dir, log=lambda m: print(m, flush=True), from_date=from_date)
        except RunnerError as e:
            print(f"[ERRO] {base.label}: {e}", file=sys.stderr)
            failures += 1
            continue
        cur_m, verdict = _evaluate(cfg, base, current, args.since, args.full)
        print(render_text(current, cur_m, verdict))
        items.append((current, cur_m, verdict))
    if args.telegram and items:
        from .telegram import send_message
        send_message(render_telegram(items))
        print("Resumo enviado ao Telegram.")
    return 1 if failures else 0


def _open_engine_data(cfg: Config):
    """Conexao somente-leitura com a instancia ABERTA do MT5 + cache de barras, para o motor Python."""
    from .engine.mt5data import BarCache, Mt5Source
    if cfg.mt5.terminal_path is None:
        raise SystemExit("mt5.terminal_path nao configurado (config/monitor.yaml): caminho do terminal64.exe do "
                         "MT5 que voce usa, ja aberto e logado")
    return Mt5Source(cfg.mt5.terminal_path), lambda src: BarCache(cfg.mt5.cache_dir, src)


def _cmd_parity(cfg: Config, args) -> int:
    """Roda o motor Python sobre o periodo de cada baseline e compara com os trades do Tester."""
    from .engine import parity
    from .engine.mt5data import DataError
    from .engine.run import run_engine, supported
    chosen = [b for b in load_baselines(cfg).values() if not args.only or args.only.lower() in b.label.lower()]
    if not chosen:
        raise SystemExit(f"nenhum baseline casa com {args.only!r}")
    source, make_cache = _open_engine_data(cfg)
    try:
        with source as src:
            cache = make_cache(src)
            for b in chosen:
                if not supported(b.expert):
                    print(f"{b.label}: o motor ainda nao implementa {b.expert}")
                    continue
                until = min(b.date_to, (max(t.close_time for t in b.trades) + dt.timedelta(days=1)).date())
                rep = run_engine(b, cache, None, b.date_from, until)
                print(parity.render(parity.compare_reports(b, rep)))
    except DataError as e:
        raise SystemExit(f"motor: {e}") from None
    return 0


def _cmd_monitor(cfg: Config, args) -> int:
    """O comando do dia a dia: roda o Tester so no periodo posterior ao baseline, para todos os
    baselines (ou os que casam com ALVO), compara e imprime. Equivale a `run` com os padroes."""
    return _cmd_run(cfg, argparse.Namespace(only=args.alvo, until=args.until, since=None, full=False,
                                            telegram=args.telegram))


def _cmd_compare(cfg: Config, args) -> int:
    baselines = load_baselines(cfg)
    if not Path(args.current).is_file():
        raise SystemExit(f"--current precisa ser um ARQUIVO de relatorio existente: {args.current}")
    try:
        current = read_report(args.current)
    except ReportFormatError as e:
        raise SystemExit(f"nao consegui ler {args.current} como relatorio do Tester: {e}") from None
    base = baselines.get(current.key)
    if base is None:
        known = ", ".join(r.label for r in baselines.values())
        raise SystemExit(f"nenhum baseline para {current.label}. Existentes: {known}")

    cur_m, verdict = _evaluate(cfg, base, current, args.since, args.full)

    if base.inputs != current.inputs:
        diff = sorted(k for k in set(base.inputs) | set(current.inputs) if base.inputs.get(k) != current.inputs.get(k))
        print(f"[aviso] inputs diferentes do baseline: {', '.join(diff)}", file=sys.stderr)

    print(render_text(current, cur_m, verdict))
    if args.telegram:
        from .telegram import send_message
        send_message(render_telegram([(current, cur_m, verdict)]))
        print("Resumo enviado ao Telegram.")
    return 0


def _cmd_discover(cfg: Config, args) -> int:
    import shutil
    from . import discovery as dsc
    from .discovery_config import load_discovery_config
    settings, specs = load_discovery_config(args.discovery_config)
    if args.trials:
        settings.trials = args.trials
    baselines = load_baselines(cfg)
    until = dt.datetime.strptime(args.until, "%Y-%m-%d").date() if args.until else dt.date.today()
    items = dsc.plan(specs, settings, baselines, args.only, until)
    if not items:
        raise SystemExit(f"nada a executar (--only {args.only!r}?)")
    est_s = 25  # ~17-20 s medidos por teste no modelo rapido + abertura do terminal
    total_runs = sum(i["runs"] for i in items)
    w = items[0]["windows"]
    print(f"Janelas: IS {w.is_start} -> {w.oos_start} | OOS {w.oos_start} -> {w.until}. "
          f"Triagem modelo {settings.screen_model}, confirmacao modelo {settings.confirm_model}. "
          f"Teto de DD {settings.max_drawdown_pct:g}%.")
    for i in items:
        own = "" if i["own"] else "  [sem baseline proprio: referencia = inputs do template, NAO validada nesse ativo]"
        print(f"  {i['spec'].expert} · {i['symbol']} {i['timeframe']}: espaco {i['space']} combinacoes, "
              f"{i['trials']} tentativas, {i['runs']} testes{own}")
    print(f"Total: {total_runs} testes (~{total_runs * est_s / 60:.0f} min).")
    if args.dry_run:
        return 0
    if cfg.tester.terminal_dir is None:
        raise SystemExit("tester.terminal_dir nao configurado (config/monitor.yaml)")

    out_dir = cfg.outputs_dir / "discovery" / f"{dt.datetime.now():%Y%m%d_%H%M%S}"
    tmp = out_dir / "_tmp"
    csvw = dsc.ResultsCsv(out_dir / "results.csv")
    run_fn = dsc.make_run_fn(cfg, tmp)
    results, tpl_inputs = [], {}
    try:
        for i in items:
            print(f"\n== {i['spec'].expert} · {i['symbol']} {i['timeframe']}", flush=True)
            res = dsc.discover_target(i["spec"], i["symbol"], i["template"], i["timeframe"], settings,
                                      until, run_fn, log=lambda m: print(m, flush=True), on_row=csvw.add)
            tpl_inputs[res.label] = i["template"].inputs
            results.append(res)
            if res.aborted == "disco":
                print("Interrompido: pouco espaco em disco.", file=sys.stderr)
                break
    finally:
        csvw.close()
        shutil.rmtree(tmp, ignore_errors=True)
    dsc.write_report(out_dir / "report.md", results, settings, tpl_inputs, until)
    sets = dsc.write_candidate_sets(out_dir, results)
    text = dsc.render_summary(results)
    print("\n" + text)
    print(f"\nRelatorio: {out_dir / 'report.md'}  |  CSV: {out_dir / 'results.csv'}  |  .set candidatos: {len(sets)}")
    if args.telegram:
        import html

        from .telegram import send_message
        send_message("<b>Descoberta de edges</b>\n" + html.escape(text))
        print("Resumo enviado ao Telegram.")
    return 0


def _cmd_telegram_test(cfg: Config, args) -> int:
    from .telegram import send_message
    send_message("<b>strategy-monitor</b>: teste de conexão OK ✅")
    print("Mensagem de teste enviada.")
    return 0


def _date_arg(s: str) -> str:
    """Formato AAAA-MM-DD, conferido na leitura dos argumentos (erro claro em vez de traceback)."""
    try:
        dt.datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"data '{s}' invalida: use AAAA-MM-DD, ex.: 2026-11-01") from None
    return s


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")  # console do Windows não é UTF-8 por padrão
    p = argparse.ArgumentParser(prog="strategy_monitor", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--config", help="caminho do monitor.yaml (default: config/monitor.yaml)")
    p.add_argument("--baseline-dir", help="pasta dos baselines (sobrepõe a config)")
    p.add_argument("--env-file", help="arquivo .env com as credenciais do Telegram (default: strategy-monitor/.env)")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("monitor", help="O COMANDO DO DIA A DIA: roda o Tester no período novo de cada baseline e compara")
    m.add_argument("alvo", nargs="?", metavar="ALVO",
                   help="só baselines cujo nome contenha este texto (ex.: DAX40, nas100, breakout). Sem ALVO, roda todos")
    m.add_argument("--until", type=_date_arg, metavar="AAAA-MM-DD",
                   help="data final EXCLUSIVA do teste (padrão: hoje, ou seja, até ontem)")
    m.add_argument("--telegram", action="store_true", help="enviar o resumo ao Telegram")
    sub.add_parser("baselines", help="lista os baselines e suas métricas")
    pa = sub.add_parser("parity", help="confere o motor Python contra os trades do Tester em cada baseline (precisa do MT5 aberto)")
    pa.add_argument("--only", help="só baselines cujo nome contenha este texto")
    c = sub.add_parser("compare", help="compara um relatório corrente com o baseline do mesmo EA/ativo/timeframe")
    c.add_argument("--current", required=True, metavar="ARQUIVO",
                   help="relatório do Tester (.xlsx ou .htm) que você quer julgar: UM arquivo, não uma pasta. "
                        "Precisa ser do mesmo EA, ativo e timeframe de algum baseline")
    cw = c.add_mutually_exclusive_group()
    cw.add_argument("--since", type=_date_arg, metavar="AAAA-MM-DD",
                    help="julga só os trades com entrada a partir desta data "
                         "(padrão: fim do período do baseline = data final do cabeçalho dele)")
    cw.add_argument("--full", action="store_true",
                    help="DIAGNÓSTICO: julga TODOS os trades do relatório, inclusive os do período do baseline "
                         "(o veredito sai com aviso de sobreposição; não use como evidência)")
    c.add_argument("--telegram", action="store_true", help="enviar o resumo ao Telegram")
    r = sub.add_parser("run", help="roda o Strategy Tester (terminal portatil) para os baselines e compara")
    r.add_argument("--only", help="so baselines cujo nome contenha este texto (ex.: DAX40)")
    r.add_argument("--until", type=_date_arg, metavar="AAAA-MM-DD",
                   help="data final EXCLUSIVA do teste (padrao: hoje, ou seja, ate ontem)")
    rw = r.add_mutually_exclusive_group()
    rw.add_argument("--since", type=_date_arg, metavar="AAAA-MM-DD",
                    help="comeca a julgar nesta data (o teste comeca 30 dias antes, para aquecer os indicadores). "
                         "Padrao: fim do periodo do baseline (data final do cabecalho)")
    rw.add_argument("--full", action="store_true",
                    help="roda e julga a janela INTEIRA do baseline, desde o inicio dele (lento; pode estourar disco/memoria)")
    r.add_argument("--telegram", action="store_true", help="enviar o resumo ao Telegram")
    d = sub.add_parser("discover", help="busca parametros melhores (so os liberados em discovery.yaml); nunca aplica")
    d.add_argument("--discovery-config", default=str(Path(__file__).resolve().parent.parent / "config" / "discovery.yaml"))
    d.add_argument("--only", help="so alvos cujo 'EA ativo' contenha este texto")
    d.add_argument("--trials", type=int, help="sobrepoe settings.trials")
    d.add_argument("--until", type=_date_arg, metavar="AAAA-MM-DD", help="data final EXCLUSIVA (padrao: hoje)")
    d.add_argument("--dry-run", action="store_true", help="mostra o plano e o tempo estimado, sem rodar nada")
    d.add_argument("--telegram", action="store_true", help="enviar o resumo ao Telegram")
    sub.add_parser("telegram-test", help="envia uma mensagem de teste (valida token e chat)")

    args = p.parse_args(argv)
    try:
        load_env_file(args.env_file)   # credenciais do Telegram; nao sobrescreve o ambiente do Windows
    except EnvFileError as e:
        raise SystemExit(str(e)) from None
    cfg = load_config(args.config, args.baseline_dir)
    try:
        return {"monitor": _cmd_monitor, "parity": _cmd_parity, "baselines": _cmd_baselines, "compare": _cmd_compare, "run": _cmd_run, "discover": _cmd_discover, "telegram-test": _cmd_telegram_test}[args.cmd](cfg, args)
    except (DiscoveryConfigError, DiscoveryError) as e:
        raise SystemExit(f"descoberta: {e}") from None
    except TelegramConfigError as e:
        raise SystemExit(f"Telegram não configurado: {e}") from None
