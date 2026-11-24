"""Janela julgada: quais trades de um run corrente o monitor avalia.

Regra (decisão do André, 2026-11-23): só valem as entradas **a partir do fim do período do cabeçalho do
baseline** (o dia final que o relatório mostra). O que o baseline já cobriu, inclusive um período "Para
frente", conta como visto. Se o último trade do baseline fechar depois do cabeçalho (não deveria), vale o que
for mais tarde, para nunca misturar trades do baseline.
"""

import datetime as dt
from dataclasses import dataclass

from .models import Report, Trade


@dataclass
class JudgedWindow:
    base_from: dt.date
    base_header_end: dt.date
    base_last_trade: dt.datetime
    cutoff: dt.datetime
    n_report: int                  # trades no relatório corrente
    n_judged: int

    @property
    def n_excluded(self) -> int:
        return self.n_report - self.n_judged


def baseline_end(base: Report) -> dt.datetime:
    """Fim efetivo do baseline: o dia final do cabeçalho (00:00) ou o último trade, o que vier depois."""
    header_end = dt.datetime.combine(base.date_to, dt.time.min)
    return max(header_end, max(t.close_time for t in base.trades))


def judged_window(base: Report, current_trades: list[Trade]) -> tuple[list[Trade], JudgedWindow]:
    cutoff = baseline_end(base)
    judged = [t for t in current_trades if t.open_time >= cutoff]
    return judged, JudgedWindow(
        base_from=base.date_from, base_header_end=base.date_to,
        base_last_trade=max(t.close_time for t in base.trades), cutoff=cutoff,
        n_report=len(current_trades), n_judged=len(judged))


def describe(w: JudgedWindow) -> list[str]:
    """Linhas de contexto para o resultado: o que é o baseline e o que foi julgado."""
    return [f"Baseline: {w.base_from} → {w.base_header_end} (último trade: {w.base_last_trade:%Y-%m-%d})",
            f"Julgando entradas a partir de {w.cutoff:%Y-%m-%d} (fim do período do baseline): "
            f"{w.n_judged} trades."]
