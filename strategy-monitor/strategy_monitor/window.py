"""Janela julgada: quais trades de um run corrente o monitor avalia, e por quê.

Regra padrão (decisão do André, 2026-11-23): só valem as entradas **a partir do fim do período
do cabeçalho do baseline** (o dia final que o relatório mostra). O que o baseline já cobriu,
inclusive um período "Para frente", conta como visto. Se o último trade do baseline fechar depois
do cabeçalho (não deveria), vale o que for mais tarde, para nunca misturar trades do baseline.
"""

import datetime as dt
from dataclasses import dataclass

from .models import Report, Trade

RULE_DEFAULT = "fim do período do baseline"
RULE_SINCE = "--since"
RULE_FULL = "relatório inteiro (--full)"


@dataclass
class JudgedWindow:
    base_from: dt.date
    base_header_end: dt.date
    base_last_trade: dt.datetime
    base_end: dt.datetime          # corte padrão
    cutoff: dt.datetime | None     # None = sem corte (--full)
    rule: str
    n_report: int                  # trades no relatório corrente
    n_judged: int
    n_overlap: int                 # julgados com entrada ANTES do fim do baseline

    @property
    def n_excluded(self) -> int:
        return self.n_report - self.n_judged


def baseline_end(base: Report) -> dt.datetime:
    """Fim efetivo do baseline: o dia final do cabeçalho (00:00) ou o último trade, o que vier depois."""
    header_end = dt.datetime.combine(base.date_to, dt.time.min)
    return max(header_end, max(t.close_time for t in base.trades))


def judged_window(base: Report, current_trades: list[Trade], since: str | None,
                  full: bool) -> tuple[list[Trade], JudgedWindow]:
    end = baseline_end(base)
    if full:
        cutoff, rule = None, RULE_FULL
    elif since:
        cutoff, rule = dt.datetime.strptime(since, "%Y-%m-%d"), RULE_SINCE
    else:
        cutoff, rule = end, RULE_DEFAULT
    judged = [t for t in current_trades if cutoff is None or t.open_time >= cutoff]
    info = JudgedWindow(
        base_from=base.date_from, base_header_end=base.date_to,
        base_last_trade=max(t.close_time for t in base.trades), base_end=end, cutoff=cutoff, rule=rule,
        n_report=len(current_trades), n_judged=len(judged),
        n_overlap=sum(1 for t in judged if t.open_time < end),
    )
    return judged, info


def describe(w: JudgedWindow) -> list[str]:
    """Linhas de contexto para o resultado: o que é o baseline e o que foi julgado."""
    lines = [f"Baseline: {w.base_from} → {w.base_header_end} (último trade: {w.base_last_trade:%Y-%m-%d})"]
    if w.cutoff is None:
        lines.append(f"Julgando: {w.n_judged} trades, relatório inteiro, sem corte.")
    else:
        lines.append(f"Julgando entradas a partir de {w.cutoff:%Y-%m-%d} ({w.rule}): "
                     f"{w.n_judged} trades; {w.n_excluded} de {w.n_report} do relatório ficaram de fora.")
    if w.n_overlap:
        lines.append(f"[aviso] {w.n_overlap} dos {w.n_judged} trades julgados são do período do baseline "
                     f"(antes de {w.base_end:%Y-%m-%d}): já foram vistos na aprovação, não são evidência nova.")
    return lines
