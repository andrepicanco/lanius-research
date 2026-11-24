"""CLI sob demanda (sem agendador). O dia a dia é o `monitor.bat`; os demais comandos são de apoio.

  monitor [ALVO] [--until AAAA-MM-DD] [--telegram]   compara o período novo de cada baseline (motor Python)
  baselines                                          lista os baselines e suas métricas
  parity [--only ALVO]                               confere o motor contra os trades do Tester
  discover [--only ALVO] [--trials N] [--dry-run]    busca parâmetros melhores (nunca aplica)
  telegram-test                                      valida token e chat
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

from .compare import compare
from .config import Config, load_config
from .discovery import DiscoveryError
from .discovery_config import DiscoveryConfigError
from .envfile import EnvFileError, load_env_file
from .metrics import attach_r_multiples, compute_metrics
from .models import Report
from .report import render_telegram, render_text
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


def _pick(baselines: dict[str, Report], only: str | None) -> list[Report]:
    chosen = [b for b in baselines.values() if not only or only.lower() in b.label.lower()]
    if not chosen:
        raise SystemExit(f"nenhum baseline casa com {only!r}. Existentes: "
                         + ", ".join(b.label for b in baselines.values()))
    return chosen


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


def _evaluate(cfg: Config, base: Report, current: Report):
    """Métricas do run corrente (só a janela julgada, ver window.py) e o veredito contra o baseline."""
    attach_r_multiples(current.trades)
    trades, win = judged_window(base, current.trades)
    cur_m = compute_metrics(trades, current.deposit, cfg.thresholds.confidence)
    base_m = compute_metrics(base.trades, base.deposit, cfg.thresholds.confidence)
    verdict = compare(base_m, cur_m, cfg)
    verdict.context = describe_window(win)
    return cur_m, verdict


def _open_engine_data(cfg: Config):
    """Conexão somente-leitura com a instância ABERTA do MT5 + cache de barras, para o motor Python.
    Devolve (fonte, fábrica do cache); a fonte é um contexto: `with fonte as src: cache = fábrica(src)`."""
    from .engine.mt5data import BarCache, Mt5Source
    if cfg.mt5.terminal_path is None:
        raise SystemExit("mt5.terminal_path não configurado (config/monitor.yaml): caminho do terminal64.exe do "
                         "MT5 que você usa, já aberto e logado")
    return Mt5Source(cfg.mt5.terminal_path), lambda src: BarCache(cfg.mt5.cache_dir, src)


def _cmd_parity(cfg: Config, args) -> int:
    """Roda o motor Python sobre o período de cada baseline e compara com os trades do Tester."""
    from .engine import parity
    from .engine.mt5data import DataError
    from .engine.run import run_engine, supported
    chosen = _pick(load_baselines(cfg), args.only)
    source, make_cache = _open_engine_data(cfg)
    try:
        with source as src:
            cache = make_cache(src)
            for b in chosen:
                if not supported(b.expert):
                    print(f"{b.label}: o motor ainda não implementa {b.expert}")
                    continue
                until = min(b.date_to, (max(t.close_time for t in b.trades) + dt.timedelta(days=1)).date())
                rep = run_engine(b, cache, None, b.date_from, until)
                print(parity.render(parity.compare_reports(b, rep)))
    except DataError as e:
        raise SystemExit(f"motor: {e}") from None
    return 0


def _cmd_monitor(cfg: Config, args) -> int:
    """O comando do dia a dia: para cada baseline (ou os que casam com ALVO), roda o motor Python só no período
    posterior ao baseline, compara com ele e imprime. Não abre o Strategy Tester."""
    from .engine.mt5data import DataError
    from .engine.run import run_engine, supported
    chosen = _pick(load_baselines(cfg), args.alvo)
    until = dt.datetime.strptime(args.until, "%Y-%m-%d").date() if args.until else dt.date.today()
    source, make_cache = _open_engine_data(cfg)
    items, failures = [], 0
    try:
        with source as src:
            cache = make_cache(src)
            for base in chosen:
                if not supported(base.expert):
                    print(f"{base.label}: o motor ainda não implementa {base.expert}", file=sys.stderr)
                    failures += 1
                    continue
                cut = baseline_end(base)
                if cut.date() >= until:
                    print(f"{base.label}: nada novo a testar (o baseline cobre até {cut:%Y-%m-%d}; "
                          f"o teste iria até {until}, exclusivo).")
                    continue
                try:
                    current = run_engine(base, cache, None, cut.date(), until)
                except DataError as e:
                    print(f"[ERRO] {base.label}: {e}", file=sys.stderr)
                    failures += 1
                    continue
                cur_m, verdict = _evaluate(cfg, base, current)
                print(render_text(current, cur_m, verdict))
                items.append((current, cur_m, verdict))
    except DataError as e:
        raise SystemExit(f"motor: {e}") from None
    if args.telegram and items:
        from .telegram import send_message
        send_message(render_telegram(items))
        print("Resumo enviado ao Telegram.")
    return 1 if failures else 0


def _cmd_discover(cfg: Config, args) -> int:
    from . import discovery as dsc
    from .discovery_config import load_discovery_config
    from .engine.mt5data import DataError
    settings, specs = load_discovery_config(args.discovery_config)
    if args.trials:
        settings.trials = args.trials
    baselines = load_baselines(cfg)
    until = dt.datetime.strptime(args.until, "%Y-%m-%d").date() if args.until else dt.date.today()
    items = dsc.plan(specs, settings, baselines, args.only, until)
    if not items:
        raise SystemExit(f"nada a executar (--only {args.only!r}?)")
    est_s = 0.3  # medido: ~0,1 s por backtest com cache + leitura dos dados
    total_runs = sum(i["runs"] for i in items)
    w = items[0]["windows"]
    print(f"Janelas: IS {w.is_start} -> {w.oos_start} | OOS {w.oos_start} -> {w.until}. "
          f"Teto de DD {settings.max_drawdown_pct:g}%.")
    for i in items:
        own = "" if i["own"] else "  [sem baseline proprio: referencia = inputs do template, NAO validada nesse ativo]"
        print(f"  {i['spec'].expert} · {i['symbol']} {i['timeframe']}: espaco {i['space']} combinacoes, "
              f"{i['trials']} tentativas, ~{i['runs']} backtests{own}")
    print(f"Total: ~{total_runs} backtests (~{max(1, round(total_runs * est_s / 60))} min).")
    if args.dry_run:
        return 0

    out_dir = cfg.outputs_dir / "discovery" / f"{dt.datetime.now():%Y%m%d_%H%M%S}"
    csvw = dsc.ResultsCsv(out_dir / "results.csv")
    source, make_cache = _open_engine_data(cfg)
    results, tpl_inputs = [], {}
    try:
        with source as src:
            run_fn = dsc.make_run_fn(make_cache(src))
            for i in items:
                print(f"\n== {i['spec'].expert} · {i['symbol']} {i['timeframe']}", flush=True)
                res = dsc.discover_target(i["spec"], i["symbol"], i["template"], i["timeframe"], settings,
                                          until, run_fn, log=lambda m: print(m, flush=True), on_row=csvw.add)
                tpl_inputs[res.label] = i["template"].inputs
                results.append(res)
    except DataError as e:
        raise SystemExit(f"motor: {e}") from None
    finally:
        csvw.close()
    dsc.write_report(out_dir / "report.md", results, settings, tpl_inputs, until)
    sets = dsc.write_candidate_sets(out_dir, results)
    text = dsc.render_summary(results)
    print("\n" + text)
    print(f"\nRelatorio: {out_dir / 'report.md'}  |  CSV: {out_dir / 'results.csv'}  |  .set candidatos: {len(sets)}")
    if args.telegram:
        import html

        from .telegram import send_message
        send_message("<b>Descoberta de parâmetros</b>\n" + html.escape(text))
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
    m = sub.add_parser("monitor", help="O COMANDO DO DIA A DIA: roda o motor Python no período novo de cada baseline e compara")
    m.add_argument("alvo", nargs="?", metavar="ALVO",
                   help="só baselines cujo nome contenha este texto (ex.: DAX40, nas100, breakout). Sem ALVO, roda todos")
    m.add_argument("--until", type=_date_arg, metavar="AAAA-MM-DD",
                   help="data final EXCLUSIVA do teste (padrão: hoje, ou seja, até ontem)")
    m.add_argument("--telegram", action="store_true", help="enviar o resumo ao Telegram")
    sub.add_parser("baselines", help="lista os baselines e suas métricas")
    pa = sub.add_parser("parity", help="confere o motor Python contra os trades do Tester em cada baseline (precisa do MT5 aberto)")
    pa.add_argument("--only", help="só baselines cujo nome contenha este texto")
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
        return {"monitor": _cmd_monitor, "parity": _cmd_parity, "baselines": _cmd_baselines,
                "discover": _cmd_discover, "telegram-test": _cmd_telegram_test}[args.cmd](cfg, args)
    except (DiscoveryConfigError, DiscoveryError) as e:
        raise SystemExit(f"descoberta: {e}") from None
    except TelegramConfigError as e:
        raise SystemExit(f"Telegram não configurado: {e}") from None
