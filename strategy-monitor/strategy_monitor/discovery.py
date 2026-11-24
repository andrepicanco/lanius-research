"""Descoberta de edges: varia SÓ os parâmetros liberados e propõe candidatos. Nunca aplica nada.

Método (uma passada por estratégia x ticker):
  1. Janelas: OOS = últimos `oos_days` até `until`; IS = `is_days` imediatamente antes. A busca
     nunca vê o OOS.
  2. Triagem no IS com o modelo rápido (1 min OHLC): a referência (inputs do baseline, inalterados)
     mais `trials` combinações sorteadas, sem repetição, com semente fixa (reprodutível).
  3. Objetivo: E[R] (expectância em R) com restrições: n >= min_trades_is e drawdown <= teto.
     Tentativa fora das restrições não entra no ranking.
  4. Os `top_k` melhores do IS (excluída a referência) vão ao OOS, junto com a referência, no
     modelo rápido e, se configurado, no modelo exato dos baselines ("Cada tick").
  5. Veredito do finalista, no OOS (exato quando existe): n >= min_trades_oos, DD <= teto,
     E[R] > 0 e E[R] > E[R] da referência no mesmo OOS. Só então vira CANDIDATO.

Por que o melhor do IS não é o resultado: com N tentativas, o máximo do IS é inflado por seleção
mesmo sem edge nenhum (máximo esperado de N normais ~ sqrt(2 ln N) desvios). O relatório mostra
quantos desvios o melhor do IS está acima da média das tentativas, para comparar com esse piso.
O número honesto é o do OOS, e ele também sofre seleção (top_k finalistas), por isso `top_k` é
pequeno e é informado.

Limites: um único OOS (um só regime); sem teste de vizinhança dos parâmetros; o modelo rápido
tem viés otimista (medido em 2026-11-17: ~+0,015 R no NAS100 H1). Ver README.
"""

import csv
import dataclasses
import datetime as dt
import itertools
import json
import math
import random
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .discovery_config import ParamSpec, Settings, StrategySpec, fmt_value, seed_for
from .metrics import Metrics, attach_r_multiples, compute_metrics
from .models import Report
from .tester_ini import write_ini

# (template já com ativo/timeframe do alvo, inputs completos, from_date, until, modelo, nome) -> Report
RunFn = Callable[[Report, dict, dt.date, dt.date, int, str], Report]

_MAX_ENUM = 50_000   # acima disso, amostra por rejeição em vez de enumerar o produto


class DiscoveryError(RuntimeError):
    pass


# ---------------------------------------------------------------- amostragem

def _norm(value: str, p: ParamSpec) -> str:
    return value.lower() if p.type == "bool" else fmt_value(float(value), p.type, p.step)


def sample_trials(params: list[ParamSpec], reference: dict, n: int, seed: int) -> list[dict]:
    """Até `n` combinações DISTINTAS e diferentes da referência. Cada uma é {parâmetro: valor}
    só com os parâmetros liberados; o resto fica como no baseline."""
    missing = [p.name for p in params if p.name not in reference]
    if missing:
        raise DiscoveryError("parâmetro(s) liberado(s) que não existem nos inputs do baseline: "
                             + ", ".join(missing))
    grids = [p.candidates(reference[p.name]) for p in params]
    ref_combo = tuple(_norm(reference[p.name], p) for p in params)
    size = math.prod(len(g) for g in grids)
    rng = random.Random(seed)
    combos: list[tuple] = []
    if size <= _MAX_ENUM:
        combos = [c for c in itertools.product(*grids) if tuple(_norm(v, p) for v, p in zip(c, params)) != ref_combo]
        rng.shuffle(combos)
        combos = combos[:n]
    else:
        seen = {ref_combo}
        guard = 0
        while len(combos) < n and guard < n * 50:
            guard += 1
            c = tuple(rng.choice(g) for g in grids)
            key = tuple(_norm(v, p) for v, p in zip(c, params))
            if key not in seen:
                seen.add(key)
                combos.append(c)
    return [{p.name: v for p, v in zip(params, c)} for c in combos]


# ---------------------------------------------------------------- avaliação

@dataclass
class Windows:
    is_start: dt.date
    oos_start: dt.date
    until: dt.date           # exclusivo

    @staticmethod
    def build(until: dt.date, s: Settings) -> "Windows":
        oos_start = until - dt.timedelta(days=s.oos_days)
        return Windows(oos_start - dt.timedelta(days=s.is_days), oos_start, until)


def feasibility(m: Metrics, min_trades: int, max_dd: float) -> str | None:
    """None = viável; senão o motivo."""
    if m.n < min_trades:
        return f"n={m.n} < {min_trades}"
    if m.expectancy_r is None:
        return "sem R"
    if m.max_dd_pct > max_dd:
        return f"DD {m.max_dd_pct:.2f}% > {max_dd:g}%"
    return None


@dataclass
class Row:
    stage: str                      # 'IS' | 'OOS-triagem' | 'OOS-exato'
    trial: int                      # 0 = referência
    changed: dict                   # parâmetros que diferem da referência
    metrics: Metrics | None
    reason: str | None = None       # inviável / erro
    seconds: float = 0.0

    @property
    def er(self) -> float | None:
        return self.metrics.expectancy_r if self.metrics else None


@dataclass
class Finalist:
    trial: int
    changed: dict
    inputs: dict
    is_row: Row
    oos_screen: Row | None = None
    oos_exact: Row | None = None
    verdict: str = ""
    reasons: list[str] = field(default_factory=list)
    qualified: bool = False

    @property
    def decision_row(self) -> Row | None:
        return self.oos_exact if (self.oos_exact and self.oos_exact.metrics) else self.oos_screen


@dataclass
class TargetResult:
    label: str
    expert: str
    symbol: str
    timeframe: str
    windows: Windows
    reference_is: Row | None = None
    reference_oos_screen: Row | None = None
    reference_oos_exact: Row | None = None
    rows: list[Row] = field(default_factory=list)
    finalists: list[Finalist] = field(default_factory=list)
    n_trials: int = 0
    n_feasible: int = 0
    selection_note: str = ""
    errors: list[str] = field(default_factory=list)
    aborted: str | None = None


def selection_note(ers: list[float]) -> str:
    """Quão acima da média das tentativas está o melhor, em desvios, contra o piso de sorte."""
    if len(ers) < 3:
        return "poucas tentativas viáveis para estimar viés de seleção"
    mu, sd = statistics.mean(ers), statistics.pstdev(ers)
    if sd == 0:
        return "todas as tentativas viáveis têm o mesmo E[R]"
    z = (max(ers) - mu) / sd
    floor = math.sqrt(2 * math.log(len(ers)))
    verdict = ("compatível com sorte de seleção" if z <= floor + 0.25
               else "acima do que a sorte de seleção explicaria")
    return (f"melhor E[R] no IS = {max(ers):+.3f} contra média {mu:+.3f} (dp {sd:.3f}): z = {z:.2f}; "
            f"com {len(ers)} tentativas viáveis, só a sorte já daria z ≈ {floor:.2f} — {verdict}")


class Evaluator:
    """Roda uma janela e devolve métricas só dos trades com entrada >= since (o aquecimento fica fora)."""

    def __init__(self, run_fn: RunFn, template: Report, symbol: str, tf: str, settings: Settings):
        self.run_fn, self.template, self.symbol, self.tf, self.s = run_fn, template, symbol, tf, settings
        self.tpl = dataclasses.replace(template, symbol=symbol, timeframe=tf)

    def __call__(self, inputs: dict, since: dt.date, until: dt.date, model: int, name: str) -> Metrics:
        from_date = since - dt.timedelta(days=self.s.warmup_days)
        rep = self.run_fn(self.tpl, inputs, from_date, until, model, name)
        attach_r_multiples(rep.trades)
        cut = dt.datetime.combine(since, dt.time.min)
        trades = [t for t in rep.trades if t.open_time >= cut]
        return compute_metrics(trades, rep.deposit)


def discover_target(spec: StrategySpec, symbol: str, template: Report, tf: str, s: Settings,
                    until: dt.date, run_fn: RunFn, log: Callable[[str], None] = lambda m: None,
                    on_row: Callable[[str, Row], None] = lambda label, r: None) -> TargetResult:
    label = f"{spec.expert} · {symbol} {tf}"
    w = Windows.build(until, s)
    res = TargetResult(label, spec.expert, symbol, tf, w)
    ref_inputs = dict(template.inputs)
    trials = sample_trials(spec.params, ref_inputs, s.trials, seed_for(s.seed, label))
    res.n_trials = len(trials)
    ev = Evaluator(run_fn, template, symbol, tf, s)
    tag = f"sm_disc_{spec.expert}_{symbol}"
    total = 1 + len(trials)
    consecutive_errors = 0

    def run_row(stage, trial, changed, inputs, since, until_, model, k=None):
        nonlocal consecutive_errors
        t0 = time.monotonic()
        try:
            m = ev(inputs, since, until_, model, f"{tag}_{stage}_{trial}")
            consecutive_errors = 0
            row = Row(stage, trial, changed, m, seconds=time.monotonic() - t0)
            if stage == "IS":   # antes de on_row, para o motivo ir ao CSV
                row.reason = feasibility(m, s.min_trades_is, s.max_drawdown_pct)
        except Exception as e:  # RunnerError, relatório vazio, parse
            consecutive_errors += 1
            msg = f"{type(e).__name__}: {e}"
            res.errors.append(f"{stage} #{trial}: {msg}")
            row = Row(stage, trial, changed, None, reason=msg, seconds=time.monotonic() - t0)
            if "pouco espaco" in msg:
                res.aborted = "disco"
        on_row(label, row)
        if k:
            er = f"{row.er:+.3f}" if row.er is not None else "—"
            n = row.metrics.n if row.metrics else 0
            log(f"  [{k}/{total}] {stage} #{trial}: E[R]={er} n={n} ({row.seconds:.0f}s)"
                + (f"  ERRO: {row.reason}" if row.reason and not row.metrics else ""))
        return row

    # 1) IS: referência + tentativas (modelo rápido)
    res.reference_is = run_row("IS", 0, {}, ref_inputs, w.is_start, w.oos_start, s.screen_model, 1)
    res.rows.append(res.reference_is)
    if res.reference_is.metrics is None and consecutive_errors:
        res.aborted = res.aborted or "referência não rodou (ativo sem dados no terminal de teste?)"
        return res
    for i, ch in enumerate(trials, start=1):
        if res.aborted:
            break
        inputs = {**ref_inputs, **ch}
        row = run_row("IS", i, ch, inputs, w.is_start, w.oos_start, s.screen_model, i + 1)
        res.rows.append(row)
        if consecutive_errors >= 3:
            res.aborted = "3 erros seguidos"
    viable = [r for r in res.rows if r.trial > 0 and r.metrics and r.reason is None]
    res.n_feasible = len(viable)
    res.selection_note = selection_note([r.er for r in viable])
    if res.aborted or not viable:
        return res

    # 2) finalistas
    viable.sort(key=lambda r: r.er, reverse=True)
    for r in viable[: s.top_k]:
        res.finalists.append(Finalist(r.trial, r.changed, {**ref_inputs, **r.changed}, r))

    # 3) OOS: referência e finalistas, triagem e (opcional) exato
    exact = s.confirm_model is not None and s.confirm_model != s.screen_model
    oos_total = (1 + len(res.finalists)) * (2 if exact else 1)
    total = 1 + len(trials) + oos_total
    k = 1 + len(trials)
    for model, stage in [(s.screen_model, "OOS-triagem")] + ([(s.confirm_model, "OOS-exato")] if exact else []):
        subjects = [(0, {}, ref_inputs)] + [(f.trial, f.changed, f.inputs) for f in res.finalists]
        for trial, changed, inputs in subjects:
            k += 1
            row = run_row(stage, trial, changed, inputs, w.oos_start, w.until, model, k)
            if trial == 0:
                if stage == "OOS-triagem":
                    res.reference_oos_screen = row
                else:
                    res.reference_oos_exact = row
            else:
                f = next(f for f in res.finalists if f.trial == trial)
                if stage == "OOS-triagem":
                    f.oos_screen = row
                else:
                    f.oos_exact = row
            res.rows.append(row)
            if res.aborted:
                return res
    judge(res, s, exact)
    return res


def judge(res: TargetResult, s: Settings, exact: bool) -> None:
    ref_row = res.reference_oos_exact if (exact and res.reference_oos_exact and res.reference_oos_exact.metrics) \
        else res.reference_oos_screen
    ref_er = ref_row.er if ref_row else None
    for f in res.finalists:
        row = f.decision_row
        reasons = []
        if row is None or row.metrics is None:
            reasons.append("OOS não rodou")
        else:
            m = row.metrics
            if m.n < s.min_trades_oos:
                reasons.append(f"OOS n={m.n} < {s.min_trades_oos}")
            if m.expectancy_r is None or m.expectancy_r <= 0:
                reasons.append("E[R] no OOS <= 0")
            if m.max_dd_pct > s.max_drawdown_pct:
                reasons.append(f"DD OOS {m.max_dd_pct:.2f}% > {s.max_drawdown_pct:g}%")
            if ref_er is not None and m.expectancy_r is not None and m.expectancy_r <= ref_er:
                reasons.append(f"não supera a referência no OOS ({m.expectancy_r:+.3f} <= {ref_er:+.3f})")
        if exact and not (f.oos_exact and f.oos_exact.metrics):
            reasons.append("confirmação no modelo exato falhou: resultado só da triagem")
        f.reasons = reasons
        f.qualified = not reasons
        f.verdict = "CANDIDATO" if f.qualified else "REPROVADO: " + "; ".join(reasons)


# ---------------------------------------------------------------- saída

_CSV_COLS = ["alvo", "etapa", "tentativa", "parametros_alterados", "n", "win_rate", "breakeven_wr",
             "expectancia_r", "profit_factor", "dd_pct", "liquido", "motivo", "segundos"]


class ResultsCsv:
    """results.csv é gravado linha a linha: uma queda no meio não perde o que já rodou."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "w", newline="", encoding="utf-8-sig")
        self.w = csv.writer(self.f)
        self.w.writerow(_CSV_COLS)

    def add(self, label: str, r: Row) -> None:
        m = r.metrics
        fmt = lambda x, p=4: "" if x is None else round(x, p)
        self.w.writerow([label, r.stage, r.trial, json.dumps(r.changed, ensure_ascii=False),
                         m.n if m else "", fmt(m.win_rate if m else None), fmt(m.breakeven_wr if m else None),
                         fmt(m.expectancy_r if m else None), fmt(m.profit_factor if m else None),
                         fmt(m.max_dd_pct if m else None, 2), fmt(m.net if m else None, 2),
                         r.reason or "", round(r.seconds, 1)])
        self.f.flush()

    def close(self) -> None:
        self.f.close()


def _cell(r: Row | None) -> str:
    if r is None:
        return "—"
    if r.metrics is None:
        return "erro"
    m = r.metrics
    er = f"{m.expectancy_r:+.3f}" if m.expectancy_r is not None else "n/d"
    return f"{er} (n={m.n}, DD {m.max_dd_pct:.1f}%)"


def _changed(ch: dict, ref: dict) -> str:
    return ", ".join(f"{k}={v} (era {ref.get(k, '?')})" for k, v in ch.items()) or "(baseline)"


def write_report(path: Path, results: list[TargetResult], s: Settings, template_inputs: dict[str, dict],
                 until: dt.date) -> None:
    exact = s.confirm_model is not None and s.confirm_model != s.screen_model
    models = {0: "Cada tick", 1: "1 min OHLC", 2: "Open prices", 4: "Ticks reais"}
    L = [f"# Descoberta de edges — {dt.datetime.now():%Y-%m-%d %H:%M}", "",
         f"Objetivo: **E[R]** com teto de drawdown **{s.max_drawdown_pct:g}%**, n mínimo {s.min_trades_is} (IS) / "
         f"{s.min_trades_oos} (OOS). Triagem: {models.get(s.screen_model, s.screen_model)}"
         + (f"; confirmação: {models.get(s.confirm_model, s.confirm_model)}." if exact else ".")
         + f" Semente {s.seed}. Janela final até {until} (exclusivo).", "",
         "> **Nada foi aplicado.** Candidatos são propostas para você revisar; o número que vale é o do OOS, "
         "e ele também sofre seleção (top_k finalistas).", ""]
    for r in results:
        ref = template_inputs.get(r.label, {})
        L += [f"## {r.label}", "",
              f"IS {r.windows.is_start} → {r.windows.oos_start} ({s.is_days} d) · OOS {r.windows.oos_start} → "
              f"{r.windows.until} ({s.oos_days} d). Tentativas: {r.n_trials} (+ referência); viáveis no IS: {r.n_feasible}.", ""]
        if r.aborted:
            L += [f"**Interrompido:** {r.aborted}.", ""]
        if r.errors:
            L += ["Erros:"] + [f"- {e}" for e in r.errors[:6]] + [""]
        if r.reference_is and r.reference_is.metrics and r.reference_is.reason:
            L += [f"**Atenção:** a própria referência (inputs do baseline) fica fora das restrições no IS "
                  f"({r.reference_is.reason}); E[R] da referência no IS = {_cell(r.reference_is)}. "
                  "Se o teto de drawdown for apertado demais para essa janela, nenhuma tentativa é viável.", ""]
        if not r.finalists and not r.aborted:
            L += ["Nenhuma tentativa ficou dentro das restrições; não há finalistas.", ""]
        if r.selection_note:
            L += [f"Seleção: {r.selection_note}.", ""]
        if r.reference_is:
            head = "| | parâmetros alterados | IS E[R] | OOS triagem | " + ("OOS exato | " if exact else "") + "veredito |"
            L += [head, "|" + "---|" * (head.count("|") - 1)]
            ref_cells = f"| ref | (baseline) | {_cell(r.reference_is)} | {_cell(r.reference_oos_screen)} | " \
                        + (f"{_cell(r.reference_oos_exact)} | " if exact else "") + "— |"
            L.append(ref_cells)
            for i, f in enumerate(r.finalists, 1):
                L.append(f"| #{i} | {_changed(f.changed, ref)} | {_cell(f.is_row)} | {_cell(f.oos_screen)} | "
                         + (f"{_cell(f.oos_exact)} | " if exact else "") + f"{f.verdict} |")
            L.append("")
    L += ["## Limites", "",
          "- Um único OOS (um só regime de mercado); sem teste de vizinhança dos parâmetros.",
          "- A triagem usa o modelo rápido, com viés otimista (medido +0,015 R no NAS100 H1).",
          "- Janela de OOS curta: poucos trades, intervalo de confiança largo.",
          "- Antes de levar um candidato para a conta, rode-o por mais tempo em forward (demo)."]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def render_summary(results: list[TargetResult]) -> str:
    lines = []
    for r in results:
        if r.aborted:
            lines.append(f"{r.label}: interrompido ({r.aborted})")
            continue
        cands = [f for f in r.finalists if f.qualified]
        lines.append(f"{r.label}: {len(cands)} candidato(s) de {len(r.finalists)} finalista(s); "
                     f"{r.n_feasible}/{r.n_trials} tentativas viaveis no IS")
        for i, f in enumerate(r.finalists, 1):
            row = f.decision_row
            lines.append(f"  #{i} {'CANDIDATO' if f.qualified else 'reprovado'}: IS E[R] {f.is_row.er:+.3f} -> "
                         f"OOS {_cell(row)}" + ("" if f.qualified else f" ({'; '.join(f.reasons)})"))
    return "\n".join(lines)


def write_candidate_sets(out_dir: Path, results: list[TargetResult]) -> list[Path]:
    """Um .set por CANDIDATO (UTF-16 com BOM, como o MT5 grava), nunca para reprovados."""
    written = []
    for r in results:
        for i, f in enumerate(r.finalists, 1):
            if not f.qualified:
                continue
            p = out_dir / "candidates" / f"{r.expert}_{r.symbol}_{r.timeframe}_cand{i}.set"
            p.parent.mkdir(parents=True, exist_ok=True)
            write_ini(p, "\r\n".join(f"{k}={v}" for k, v in f.inputs.items()) + "\r\n")
            written.append(p)
    return written


# ---------------------------------------------------------------- execução real

def make_run_fn(cfg: Config, tmp_dir: Path, log: Callable[[str], None] = lambda m: None) -> RunFn:
    """RunFn que usa o terminal portátil. O relatório de cada tentativa é lido e APAGADO (disco)."""
    from .runner import RunnerConfig, run_and_read
    from .tester_ini import TesterSettings
    te = cfg.tester

    def run_fn(tpl, inputs, from_date, until, model, name):
        rc = RunnerConfig(te.terminal_dir, te.timeout_minutes * 60,
                          TesterSettings(model=model, execution_mode=te.execution_mode), te.experts,
                          te.min_free_gb, te.max_terminal_mb, 1)
        try:
            return run_and_read(tpl, rc, until, name, tmp_dir, inputs=inputs, from_date=from_date)
        finally:
            for f in tmp_dir.glob(f"{name}*"):
                f.unlink(missing_ok=True)

    return run_fn


def pick_template(spec: StrategySpec, symbol: str, baselines: dict[str, Report]) -> tuple[Report, str]:
    """Baseline do próprio ativo, se existir; senão o `template` da estratégia (com o timeframe
    do config ou o do template). Devolve (baseline, timeframe)."""
    mine = [b for b in baselines.values() if b.expert == spec.expert]
    own = [b for b in mine if b.symbol == symbol]
    if own:
        return own[0], own[0].timeframe
    if not mine:
        raise DiscoveryError(f"{spec.expert}: nenhum baseline para servir de base (inputs) ao ativo {symbol}")
    if spec.template:
        hit = [b for b in mine if spec.template.lower() in b.label.lower()]
        if not hit:
            raise DiscoveryError(f"{spec.expert}: template {spec.template!r} não casa com nenhum baseline")
        tpl = hit[0]
    else:
        tpl = mine[0]
    return tpl, spec.timeframe or tpl.timeframe


def plan(specs: list[StrategySpec], s: Settings, baselines: dict[str, Report], only: str | None,
         until: dt.date) -> list[dict]:
    """O que seria executado (para --dry-run e para estimar tempo)."""
    out = []
    w = Windows.build(until, s)
    exact = s.confirm_model is not None and s.confirm_model != s.screen_model
    for spec in specs:
        for sym in spec.tickers:
            if only and only.lower() not in f"{spec.expert} {sym}".lower():
                continue
            tpl, tf = pick_template(spec, sym, baselines)
            grids = [p.candidates(tpl.inputs.get(p.name, "1")) for p in spec.params]
            space = math.prod(len(g) for g in grids)
            n = min(s.trials, max(space - 1, 0))
            runs = 1 + n + (1 + s.top_k) * (2 if exact else 1)
            out.append(dict(spec=spec, symbol=sym, template=tpl, timeframe=tf, space=space, trials=n,
                            runs=runs, own=tpl.symbol == sym, windows=w))
    return out
