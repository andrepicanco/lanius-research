"""Portao de fidelidade: roda o motor sobre o periodo do baseline e compara com os trades do proprio baseline
(que sao do Strategy Tester). E o que diz se o motor pode ser usado num ativo/estrategia.

Ativo SEM baseline: nao ha o que comparar. O motor so mede o que o EA faria sob o modelo de precos dele
(ver core.py); a fidelidade medida nos ativos com baseline e uma referencia, nao uma garantia.
"""

import datetime as dt
import statistics
from dataclasses import dataclass

from ..metrics import Metrics, attach_r_multiples, compute_metrics
from ..models import Report, Trade


@dataclass
class Parity:
    label: str
    baseline: Metrics
    engine: Metrics
    matched: int
    n_base: int
    n_engine: int
    entry_diff: float | None       # media (motor - Tester) do preco de entrada
    exit_diff: float | None
    sl_diff_max: float | None      # maior |diferenca| no SL inicial

    @property
    def ok(self) -> bool:
        """Criterio conservador: pareou >= 95% dos trades do baseline e nao inventou mais de 5% de extras."""
        return (self.n_base > 0 and self.matched >= 0.95 * self.n_base
                and self.n_engine - self.matched <= 0.05 * self.n_base)


def match_trades(base: list[Trade], eng: list[Trade], tol: dt.timedelta = dt.timedelta(hours=3)):
    """Pareia por direcao e horario de entrada (o mais proximo dentro da tolerancia); cada trade uma vez."""
    eng = sorted(eng, key=lambda t: t.open_time)
    used: set[int] = set()
    pairs = []
    for t in sorted(base, key=lambda t: t.open_time):
        cand = [(abs(e.open_time - t.open_time), n) for n, e in enumerate(eng)
                if n not in used and e.direction == t.direction and abs(e.open_time - t.open_time) <= tol]
        if cand:
            _, n = min(cand)
            used.add(n)
            pairs.append((t, eng[n]))
    return pairs


def compare_reports(base: Report, rep: Report, until_trade: bool = True) -> Parity:
    """`rep` deve cobrir o periodo do baseline. So os trades do baseline contam (o motor pode ter mais, depois)."""
    last = max(t.close_time for t in base.trades)
    eng = [t for t in rep.trades if t.open_time <= last] if until_trade else rep.trades
    attach_r_multiples(base.trades)
    attach_r_multiples(eng)
    pairs = match_trades(base.trades, eng)
    mean = lambda xs: statistics.mean(xs) if xs else None
    return Parity(
        label=base.label, baseline=compute_metrics(base.trades, base.deposit), engine=compute_metrics(eng, base.deposit),
        matched=len(pairs), n_base=len(base.trades), n_engine=len(eng),
        entry_diff=mean([e.open_price - t.open_price for t, e in pairs]),
        exit_diff=mean([e.close_price - t.close_price for t, e in pairs]),
        sl_diff_max=max((abs((e.sl0 or 0) - (t.sl0 or 0)) for t, e in pairs), default=None),
    )


def render(p: Parity) -> str:
    def line(name: str, m: Metrics) -> str:
        er = f"{m.expectancy_r:+.3f}" if m.expectancy_r is not None else "n/d"
        return (f"  {name:8s} n={m.n:<4d} win {m.win_rate * 100:5.1f}% | PF {m.profit_factor:.2f} | E[R] {er} | "
                f"líquido {m.net:+9.2f} | DD {m.max_dd_pct:.2f}%")
    out = [f"{p.label}  [{'OK' if p.ok else 'ATENCAO'}]",
           line("Tester", p.baseline), line("Motor", p.engine),
           f"  trades pareados: {p.matched}/{p.n_base} (motor gerou {p.n_engine})"]
    if p.entry_diff is not None:
        out.append(f"  diferença média de preço (motor − Tester): entrada {p.entry_diff:+.3f}, saída {p.exit_diff:+.3f}"
                   f" | maior diferença no SL inicial: {p.sl_diff_max:.3f}")
    return "\n".join(out)
