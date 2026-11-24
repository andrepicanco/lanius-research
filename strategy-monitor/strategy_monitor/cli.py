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

from .config import Config, load_config
from .discovery import DiscoveryError
from .discovery_config import DiscoveryConfigError
from .envfile import EnvFileError, load_env_file
from .metrics import attach_r_multiples, compute_metrics
from .models import Report
from . import services
from .report import render_telegram, render_text
from .telegram import TelegramConfigError
from .tester_report import ReportFormatError, read_report
from .window import baseline_end


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


def _cmd_parity(cfg: Config, args) -> int:
    """Roda o motor Python sobre o período de cada baseline e compara com os trades do Tester."""
    from .engine import parity
    from .engine.mt5data import DataError
    chosen = _pick(load_baselines(cfg), args.only)
    try:
        results = services.run_parity(cfg, chosen)
    except DataError as e:
        raise SystemExit(f"motor: {e}") from None
    for par, _, base in results:
        print(parity.render(par) if par else f"{base.label}: o motor ainda não implementa {base.expert}")
    return 0


def _cmd_monitor(cfg: Config, args) -> int:
    """O comando do dia a dia: para cada baseline (ou os que casam com ALVO), roda o motor Python só no período
    posterior ao baseline, compara com ele e imprime. Não abre o Strategy Tester."""
    from .engine.mt5data import DataError
    chosen = _pick(load_baselines(cfg), args.alvo)
    until = dt.datetime.strptime(args.until, "%Y-%m-%d").date() if args.until else dt.date.today()
    try:
        items, problems = services.run_monitor(cfg, chosen, until)
    except DataError as e:
        raise SystemExit(f"motor: {e}") from None
    for it in items:
        print(render_text(it.report, it.metrics, it.verdict))
    for pr in problems:
        print(pr, file=sys.stderr if pr.startswith("[ERRO]") or "não implementa" in pr else sys.stdout)
    if args.telegram and items:
        from .telegram import send_message
        send_message(render_telegram([(it.report, it.metrics, it.verdict) for it in items]))
        print("Resumo enviado ao Telegram.")
    return 1 if any(p.startswith("[ERRO]") or "não implementa" in p for p in problems) else 0


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
    try:
        results, out_dir, sets = services.run_discovery(cfg, settings, specs, baselines, args.only, until,
                                                        log=lambda m: print(m, flush=True))
    except DataError as e:
        raise SystemExit(f"motor: {e}") from None
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
