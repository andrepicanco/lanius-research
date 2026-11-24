"""Relatório HTML autocontido do monitor (abre em qualquer navegador, sem servidor): resumo por estratégia,
métricas baseline × período novo, verificações e a curva de R acumulado dos dois, com legenda e rótulo direto.

Cores: azul = baseline, laranja = período novo (slots 1 e 2 da paleta de referência, nos dois modos de tema).
O estado nunca depende só de cor: cada selo traz texto e ícone."""

import datetime as dt
import html

from ..compare import ATENCAO, FALHA, NA, OK
from ..services import MonitorItem, cumulative_r

BASE_COLOR = ("#2a78d6", "#3987e5")     # claro, escuro
NEW_COLOR = ("#eb6834", "#d95926")
_ICON = {OK: "●", ATENCAO: "▲", FALHA: "✖", NA: "○"}

_CSS = """
:root{color-scheme:light dark;--bg:#fcfcfb;--ink:#0b0b0b;--mute:#52514e;--line:#dcdbd6;--base:%s;--new:%s}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--ink:#f0efec;--mute:#c3c2b7;--line:#3a3a38;--base:%s;--new:%s}}
body{background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:860px;padding:24px 16px}
h1{font-size:22px}h2{font-size:18px;margin:32px 0 4px}table{border-collapse:collapse;width:100%%;margin:8px 0}
th,td{text-align:left;padding:4px 10px;border-bottom:1px solid var(--line)}th{color:var(--mute);font-weight:600}
.badge{display:inline-block;padding:1px 8px;border:1px solid var(--line);border-radius:10px;font-size:13px}
.mute{color:var(--mute)}svg text{fill:var(--ink);font-size:12px}svg .grid{stroke:var(--line)}
.b{stroke:var(--base)}.n{stroke:var(--new)}.tb{fill:var(--base)!important}.tn{fill:var(--new)!important}
""" % (BASE_COLOR + NEW_COLOR)


def _fmt(x, p=1, pct=False, sign=False):
    if x is None:
        return "n/d"
    v = x * 100 if pct else x
    return f"{v:{'+' if sign else ''}.{p}f}" + ("%" if pct else "")


def _chart(base_r: list[float], new_r: list[float]) -> str:
    w, h, ml, mr, mt, mb = 640, 220, 44, 120, 12, 28
    series = [s for s in (base_r, new_r) if s]
    if not series:
        return '<p class="mute">Sem R calculável para o gráfico.</p>'
    n = max(len(s) for s in series)
    lo, hi = min(0.0, *[min(s) for s in series]), max(0.0, *[max(s) for s in series])
    if hi == lo:
        hi = lo + 1
    X = lambda i: ml + (w - ml - mr) * (i / max(n - 1, 1))
    Y = lambda v: mt + (h - mt - mb) * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="R acumulado por trade: baseline e período novo" width="100%">']
    for v in (lo, 0.0, hi):
        out.append(f'<line class="grid" x1="{ml}" x2="{w - mr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                   f'<text x="{ml - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:+.1f}R</text>')
    out.append(f'<text x="{ml}" y="{h - 8}">trade nº (por ordem de fechamento)</text>')
    for s, cls, tcls, name in ((base_r, "b", "tb", "Baseline"), (new_r, "n", "tn", "Período novo")):
        if not s:
            continue
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(s))
        out.append(f'<polyline class="{cls}" fill="none" stroke-width="2" stroke-linejoin="round" points="{pts}"/>')
        out.append(f'<circle class="{cls}" cx="{X(len(s) - 1):.1f}" cy="{Y(s[-1]):.1f}" r="4" fill="var(--bg)" stroke-width="2"/>'
                   f'<text class="{tcls}" x="{X(len(s) - 1) + 8:.1f}" y="{Y(s[-1]) + 4:.1f}">{name} {s[-1]:+.2f}R</text>')
    out.append("</svg>")
    return "".join(out)


def render_monitor_html(items: list[MonitorItem], until: dt.date) -> str:
    parts = [f'<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
             f"<title>Monitor das estratégias</title><style>{_CSS}</style><body>",
             f"<h1>Monitor das estratégias</h1><p class='mute'>Gerado em {dt.datetime.now():%Y-%m-%d %H:%M}; "
             f"testes até {until} (exclusivo). Motor Python: aproximação do Strategy Tester.</p>"]
    for it in items:
        v, cm = it.verdict, it.metrics
        parts.append(f"<h2>{html.escape(it.base.label)} <span class='badge'>{_ICON[v.overall]} {v.overall}</span></h2>")
        parts.append("<p class='mute'>" + "<br>".join(html.escape(c) for c in v.context) + "</p>")
        parts.append("<table><tr><th>Verificação</th><th>Estado</th><th>Detalhe</th></tr>" + "".join(
            f"<tr><td>{html.escape(c.name)}</td><td>{_ICON[c.status]} {c.status}</td><td>{html.escape(c.detail)}</td></tr>"
            for c in v.checks) + "</table>")
        parts.append(f"<table><tr><th>Trades julgados</th><th>Win rate</th><th>Breakeven</th><th>E[R]</th><th>PF</th><th>DD</th></tr>"
                     f"<tr><td>{cm.n}</td><td>{_fmt(cm.win_rate, pct=True)}</td><td>{_fmt(cm.breakeven_wr, pct=True)}</td>"
                     f"<td>{_fmt(cm.expectancy_r, 3, sign=True)}</td><td>{_fmt(cm.profit_factor, 2)}</td>"
                     f"<td>{_fmt(cm.max_dd_pct, 2)}%</td></tr></table>")
        parts.append(_chart(cumulative_r(it.base.trades), cumulative_r(it.judged)))
    parts.append("</body></html>")
    return "".join(parts)
