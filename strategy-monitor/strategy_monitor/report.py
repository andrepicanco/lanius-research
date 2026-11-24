"""Texto do resumo (console e Telegram)."""

import html

from .compare import ATENCAO, FALHA, NA, OK, Verdict
from .metrics import Metrics
from .models import Report

_ICON = {OK: "🟢", ATENCAO: "🟡", FALHA: "🔴", NA: "⚪"}


def _window(m: Metrics) -> str:
    if not m.first or not m.last:
        return "sem trades"
    return f"{m.first:%Y-%m-%d} → {m.last:%Y-%m-%d}"


def render_text(report: Report, current: Metrics, verdict: Verdict, icons: bool = False) -> str:
    tag = (lambda s: f"{_ICON[s]} {s}") if icons else (lambda s: f"[{s}]")
    lines = [
        f"{report.label}  {tag(verdict.overall)}",
        *verdict.context,
        f"Janela corrente: {_window(current)} (n={current.n}, líquido {current.net:+.2f})",
    ]
    for c in verdict.checks:
        lines.append(f"  {tag(c.status)} {c.name}: {c.detail}")
    return "\n".join(lines)


def render_telegram(items: list[tuple[Report, Metrics, Verdict]]) -> str:
    """HTML (parse_mode=HTML) com escape: nomes de EA/ativo podem ter '_', que quebraria Markdown."""
    parts = []
    for report, cur, verdict in items:
        body = html.escape(render_text(report, cur, verdict, icons=True))
        title, _, rest = body.partition("\n")
        parts.append(f"<b>{title}</b>\n{rest}")
    return "\n\n".join(parts)
