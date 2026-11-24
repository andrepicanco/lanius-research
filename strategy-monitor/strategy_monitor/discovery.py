"""Descoberta de parâmetros melhores para as estratégias atuais, sobre o motor Python. Nunca aplica nada.

Método (uma passada por estratégia x ativo):
  1. Janelas: OOS = últimos `oos_days` até `until`; IS = `is_days` imediatamente antes. A busca nunca vê o OOS.
  2. Busca no IS: a referência (inputs do baseline) mais `trials` combinações sorteadas, sem repetição, com
     semente fixa (reprodutível), só nos parâmetros liberados.
  3. Objetivo: E[R] (expectância em R) com restrições: n >= min_trades_is e drawdown <= teto.
  4. Os `top_k` melhores do IS (fora a referência) passam por três testes que a busca nunca viu:
       - OOS: n mínimo, DD <= teto, E[R] > 0 e E[R] > o da referência no mesmo OOS;
       - consistência: o IS é dividido em `folds` trechos; E[R] > 0 em pelo menos `min_positive_folds`;
       - vizinhança: cada parâmetro é movido para o valor vizinho da grade, um por vez; a MEDIANA do E[R] dos
         vizinhos no IS precisa ser > 0 (um pico isolado cercado de vizinhos ruins é sorte, não edge).
     Só quem passa nos três vira CANDIDATO.

Por que o melhor do IS não é o resultado: com N tentativas, o máximo do IS é inflado por seleção mesmo sem edge
nenhum (máximo esperado de N normais ~ sqrt(2 ln N) desvios). O relatório mostra quantos desvios o melhor do IS
está acima da média das tentativas. Com centenas de tentativas isso pesa: por isso os três testes acima.

Limites: um único OOS (um só regime); o motor é uma aproximação do Tester (ver README, comando `parity`);
a seleção também ocorre entre os `top_k` finalistas.
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

from .discovery_config import ParamSpec, Settings, StrategySpec, fmt_value, seed_for
from .metrics import Metrics, attach_r_multiples, compute_metrics
from .models import Report

# (baseline já com ativo/timeframe do alvo, inputs completos, de, até exclusivo) -> Report
RunFn = Callable[[Report, dict, dt.date, dt.date], Report]

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


def neighbors(params: list[ParamSpec], reference: dict, inputs: dict) -> list[dict]:
    """Variações de UM parâmetro por vez para o valor vizinho da grade (±1 passo); bool inverte.
    Cada item é {parâmetro: novo valor}. Valor fora da grade (ex.: o do baseline) é ignorado."""
    out = []
    for p in params:
        grid = p.candidates(reference[p.name])
        cur = _norm(inputs[p.name], p)
        if cur not in grid:
            continue
        i = grid.index(cur)
        if p.type == "bool":
            out.append({p.name: grid[1 - i]})
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(grid):
                out.append({p.name: grid[j]})
    return out


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

    def folds(self, k: int) -> list[tuple[dt.date, dt.date]]:
        span = (self.oos_start - self.is_start).days
        edges = [self.is_start + dt.timedelta(days=round(span * i / k)) for i in range(k + 1)]
        return list(zip(edges[:-1], edges[1:]))


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
    stage: str                      # 'IS' | 'OOS' | 'FOLD' | 'VIZINHO'
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
    oos: Row | None = None
    folds: list[Row] = field(default_factory=list)
    neighbor_rows: list[Row] = field(default_factory=list)
    verdict: str = ""
    reasons: list[str] = field(default_factory=list)
    qualified: bool = False

    @property
    def positive_folds(self) -> int:
        return sum(1 for r in self.folds if r.er is not None and r.er > 0)

    @property
    def live_neighbors(self) -> list[Row]:
        """Vizinhos que trazem informação sobre o E[R]. Um parâmetro que não muda o E[R] (morto, como o
        multiplicador do trailing com o trailing desligado; ou o tamanho do lote, que só escala o DD) repete o
        E[R] do finalista e inflaria a mediana: fica de fora."""
        m = self.is_row.metrics
        return [r for r in self.neighbor_rows if r.metrics is not None and m is not None
                and (r.metrics.n, round(r.metrics.expectancy_r or 0.0, 6)) != (m.n, round(m.expectancy_r or 0.0, 6))]

    @property
    def neighbor_median(self) -> float | None:
        ers = [r.er for r in self.live_neighbors if r.er is not None]
        return statistics.median(ers) if ers else None


@dataclass
class TargetResult:
    label: str
    expert: str
    symbol: str
    timeframe: str
    windows: Windows
    reference_is: Row | None = None
    reference_oos: Row | None = None
    rows: list[Row] = field(default_factory=list)
    finalists: list[Finalist] = field(default_factory=list)
    n_trials: int = 0
    n_feasible: int = 0
    selection_note: str = ""
    errors: list[str] = field(default_factory=list)
    aborted: str | None = None
    ref_inputs: dict = field(default_factory=dict)     # inputs do baseline/template (o "era X" do relatório)


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
    """Roda uma janela e devolve as métricas dos trades com entrada dentro dela."""

    def __init__(self, run_fn: RunFn, template: Report, symbol: str, tf: str):
        self.run_fn = run_fn
        self.tpl = dataclasses.replace(template, symbol=symbol, timeframe=tf)

    def __call__(self, inputs: dict, since: dt.date, until: dt.date) -> Metrics:
        rep = self.run_fn(self.tpl, inputs, since, until)
        attach_r_multiples(rep.trades)
        return compute_metrics(rep.trades, rep.deposit)


def discover_target(spec: StrategySpec, symbol: str, template: Report, tf: str, s: Settings,
                    until: dt.date, run_fn: RunFn, log: Callable[[str], None] = lambda m: None,
                    on_row: Callable[[str, Row], None] = lambda label, r: None) -> TargetResult:
    label = f"{spec.expert} · {symbol} {tf}"
    w = Windows.build(until, s)
    res = TargetResult(label, spec.expert, symbol, tf, w)
    ref_inputs = dict(template.inputs)
    if tf != template.timeframe:    # ativo sem baseline em outro timeframe: o input manda no motor
        from .engine.mt5data import tf_to_mt5_enum
        ref_inputs["InpTimeframe"] = str(tf_to_mt5_enum(tf))
    trials = sample_trials(spec.params, ref_inputs, s.trials, seed_for(s.seed, label))
    res.n_trials = len(trials)
    res.ref_inputs = ref_inputs
    ev = Evaluator(run_fn, template, symbol, tf)
    consecutive_errors = 0

    def run_row(stage, trial, changed, inputs, since, until_) -> Row:
        nonlocal consecutive_errors
        t0 = time.monotonic()
        try:
            m = ev(inputs, since, until_)
            consecutive_errors = 0
            row = Row(stage, trial, changed, m, seconds=time.monotonic() - t0)
            if stage == "IS":   # antes de on_row, para o motivo ir ao CSV
                row.reason = feasibility(m, s.min_trades_is, s.max_drawdown_pct)
        except Exception as e:  # dados insuficientes, input inválido
            consecutive_errors += 1
            msg = f"{type(e).__name__}: {e}"
            res.errors.append(f"{stage} #{trial}: {msg}")
            row = Row(stage, trial, changed, None, reason=msg, seconds=time.monotonic() - t0)
        on_row(label, row)
        return row

    # 1) IS: referência + tentativas
    res.reference_is = run_row("IS", 0, {}, ref_inputs, w.is_start, w.oos_start)
    res.rows.append(res.reference_is)
    if res.reference_is.metrics is None:
        res.aborted = "a referência não rodou: " + (res.errors[-1] if res.errors else "sem dados")
        return res
    log(f"  referência no IS: E[R]={res.reference_is.er:+.3f} n={res.reference_is.metrics.n}")
    for i, ch in enumerate(trials, start=1):
        row = run_row("IS", i, ch, {**ref_inputs, **ch}, w.is_start, w.oos_start)
        res.rows.append(row)
        if consecutive_errors >= 3:
            res.aborted = "3 erros seguidos"
            return res
        if i % 50 == 0:
            log(f"  {i}/{len(trials)} tentativas")
    viable = [r for r in res.rows if r.trial > 0 and r.metrics and r.reason is None]
    res.n_feasible = len(viable)
    res.selection_note = selection_note([r.er for r in viable])
    if not viable:
        return res

    # 2) finalistas e os três testes que a busca nunca viu
    viable.sort(key=lambda r: r.er, reverse=True)
    for r in viable[: s.top_k]:
        res.finalists.append(Finalist(r.trial, r.changed, {**ref_inputs, **r.changed}, r))
    res.reference_oos = run_row("OOS", 0, {}, ref_inputs, w.oos_start, w.until)
    res.rows.append(res.reference_oos)
    for f in res.finalists:
        f.oos = run_row("OOS", f.trial, f.changed, f.inputs, w.oos_start, w.until)
        res.rows.append(f.oos)
        for a, b in w.folds(s.folds):
            f.folds.append(run_row("FOLD", f.trial, f.changed, f.inputs, a, b))
        for nb in neighbors(spec.params, ref_inputs, f.inputs):
            f.neighbor_rows.append(run_row("VIZINHO", f.trial, nb, {**f.inputs, **nb}, w.is_start, w.oos_start))
        res.rows += f.folds + f.neighbor_rows
    judge(res, s)
    return res


def judge(res: TargetResult, s: Settings) -> None:
    ref_er = res.reference_oos.er if res.reference_oos else None
    for f in res.finalists:
        reasons = []
        row = f.oos
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
        if s.folds and f.positive_folds < s.min_positive_folds:
            reasons.append(f"consistência: E[R] > 0 em {f.positive_folds}/{len(f.folds)} trechos do IS "
                           f"(mínimo {s.min_positive_folds})")
        nm = f.neighbor_median
        if nm is None and f.neighbor_rows:
            reasons.append("vizinhança: nenhum vizinho muda o resultado, robustez não avaliável")
        elif nm is not None and nm <= 0:
            reasons.append(f"vizinhança: mediana do E[R] dos vizinhos = {nm:+.3f} <= 0 (pico isolado)")
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
                         r.reason or "", round(r.seconds, 2)])
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
    L = [f"# Descoberta de parâmetros — {dt.datetime.now():%Y-%m-%d %H:%M}", "",
         f"Objetivo: **E[R]** com teto de drawdown **{s.max_drawdown_pct:g}%**, n mínimo {s.min_trades_is} (IS) / "
         f"{s.min_trades_oos} (OOS). Motor Python (aproximação do Tester). Semente {s.seed}. "
         f"Janela final até {until} (exclusivo).", "",
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
            L += ["| | parâmetros alterados | IS E[R] | trechos do IS > 0 | mediana vizinhos | OOS E[R] | veredito |",
                  "|---|---|---|---|---|---|---|",
                  f"| ref | (baseline) | {_cell(r.reference_is)} | — | — | {_cell(r.reference_oos)} | — |"]
            for i, f in enumerate(r.finalists, 1):
                nm = f"{f.neighbor_median:+.3f}" if f.neighbor_median is not None else "—"
                L.append(f"| #{i} | {_changed(f.changed, ref)} | {_cell(f.is_row)} | "
                         f"{f.positive_folds}/{len(f.folds)} | {nm} | {_cell(f.oos)} | {f.verdict} |")
            L.append("")
    L += ["## Limites", "",
          "- Um único OOS (um só regime de mercado): poucos trades e intervalo de confiança largo.",
          "- O motor aproxima o Tester (ver `parity`); em ativo sem baseline essa fidelidade não foi medida.",
          "- Os testes de consistência e vizinhança reduzem, mas não eliminam, o viés de seleção.",
          "- Antes de levar um candidato para a conta, rode-o por mais tempo em forward (demo) e refaça o baseline."]
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
            lines.append(f"  #{i} {'CANDIDATO' if f.qualified else 'reprovado'}: IS E[R] {f.is_row.er:+.3f} -> "
                         f"OOS {_cell(f.oos)}" + ("" if f.qualified else f" ({'; '.join(f.reasons)})"))
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
            text = "\r\n".join(f"{k}={v}" for k, v in f.inputs.items()) + "\r\n"
            p.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
            written.append(p)
    return written


# ---------------------------------------------------------------- execução real

def make_run_fn(cache) -> RunFn:
    """RunFn do motor Python sobre o cache de barras do MT5 (BarCache já conectado)."""
    from .engine.run import run_engine

    def run_fn(tpl: Report, inputs: dict, from_date: dt.date, until: dt.date) -> Report:
        return run_engine(tpl, cache, inputs, from_date, until)

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
    for spec in specs:
        for sym in spec.tickers:
            if only and only.lower() not in f"{spec.expert} {sym}".lower():
                continue
            tpl, tf = pick_template(spec, sym, baselines)
            grids = [p.candidates(tpl.inputs.get(p.name, "1")) for p in spec.params]
            space = math.prod(len(g) for g in grids)
            n = min(s.trials, max(space - 1, 0))
            per_finalist = 1 + s.folds + 2 * len(spec.params)          # OOS + trechos + vizinhos (≈2 por parâmetro)
            runs = 1 + n + 1 + s.top_k * per_finalist
            out.append(dict(spec=spec, symbol=sym, template=tpl, timeframe=tf, space=space, trials=n,
                            runs=runs, own=tpl.symbol == sym, windows=w))
    return out
