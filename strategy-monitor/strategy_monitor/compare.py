"""Compara as métricas de um run corrente com as do baseline e produz um veredito.

Estados, do pior para o melhor: FALHA > ATENCAO > OK. NA = sem dado para julgar.
O veredito geral é o pior entre as verificações; a frequência de trades só gera aviso (ATENCAO).
"""

from dataclasses import dataclass, field

from .config import Config
from .metrics import Metrics

OK, ATENCAO, FALHA, NA = "OK", "ATENCAO", "FALHA", "NA"
_RANK = {NA: 0, OK: 1, ATENCAO: 2, FALHA: 3}


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Verdict:
    checks: list[Check] = field(default_factory=list)
    small_sample: bool = False
    context: list[str] = field(default_factory=list)   # linhas sobre a janela julgada (ver window.py)

    @property
    def overall(self) -> str:
        return max((c.status for c in self.checks), key=lambda s: _RANK[s], default=NA)


def _pct(x: float | None) -> str:
    return "n/d" if x is None else f"{x * 100:.1f}%"


def compare(baseline: Metrics, current: Metrics, cfg: Config) -> Verdict:
    th = cfg.thresholds
    v = Verdict(small_sample=current.n < cfg.min_trades)
    add = v.checks.append

    if current.n == 0:
        # Sem nenhum trade julgado nao ha o que avaliar: nem o drawdown (0%) pode virar "OK".
        add(Check("Periodo julgado", NA,
                  "nenhum trade com entrada na janela julgada (veja --since/--full e o periodo do relatorio)"))
        return v

    # 1) Win rate vs breakeven do próprio run corrente (o payoff realizado muda de run para run).
    if current.n == 0 or current.breakeven_wr is None:
        add(Check("Win rate vs breakeven", NA, "sem trades com ganho e perda no período"))
    else:
        lo, hi = current.wr_ci
        be = current.breakeven_wr
        if current.win_rate >= be:
            st = OK
        elif hi >= be:
            st = ATENCAO   # abaixo do BE, mas estatisticamente indistinguível dele
        else:
            st = FALHA     # significativamente abaixo
        if v.small_sample and st == FALHA:
            st = ATENCAO
        note = f" [amostra pequena: n={current.n} < {cfg.min_trades}]" if v.small_sample else ""
        add(Check(
            "Win rate vs breakeven", st,
            f"win {_pct(current.win_rate)} | BE {_pct(be)} | folga {current.wr_margin * 100:+.1f} pp "
            f"| IC{int(th.confidence * 100)} {_pct(lo)}–{_pct(hi)} "
            f"| baseline: win {_pct(baseline.win_rate)}, BE {_pct(baseline.breakeven_wr)}{note}",
        ))

    # 2) Expectativa em R.
    if current.expectancy_r is None:
        add(Check("Expectativa (R)", NA, "sem R (falta SL inicial nas ordens)"))
    else:
        er = current.expectancy_r
        st = OK if er > 0 else (ATENCAO if v.small_sample else FALHA)
        base = f" | baseline {baseline.expectancy_r:+.3f} R" if baseline.expectancy_r is not None else ""
        add(Check("Expectativa (R)", st, f"{er:+.3f} R/trade (n={current.n_with_r}){base}"))

    # 3) Profit factor.
    if current.profit_factor is None:
        add(Check("Profit factor", NA, "sem ganhos ou sem perdas no período"))
    else:
        base = f" | baseline {baseline.profit_factor:.2f}" if baseline.profit_factor else ""
        add(Check("Profit factor", OK if current.profit_factor >= 1 else ATENCAO,
                  f"{current.profit_factor:.2f}{base}"))

    # 4) Drawdown: teto absoluto (se configurado) e relação com o baseline.
    dd, bdd = current.max_dd_pct, baseline.max_dd_pct
    st, why = OK, ""
    if th.max_drawdown_pct is not None and dd > th.max_drawdown_pct:
        st, why = FALHA, f" > teto {th.max_drawdown_pct:.1f}%"
    elif bdd > 0 and dd > bdd * th.dd_fail_factor:
        st, why = FALHA, f" > {th.dd_fail_factor:g}x o baseline"
    elif bdd > 0 and dd > bdd:
        st, why = ATENCAO, " > baseline"
    add(Check("Drawdown (saldo fechado)", st, f"{dd:.2f}% | baseline {bdd:.2f}%{why}"))

    # 5) Frequência de trades: mudança de comportamento do EA.
    if baseline.trades_per_month and current.trades_per_month:
        ratio = current.trades_per_month / baseline.trades_per_month
        lo_r, hi_r = th.trades_per_month_ratio
        add(Check("Trades/mês", OK if lo_r <= ratio <= hi_r else ATENCAO,
                  f"{current.trades_per_month:.1f} | baseline {baseline.trades_per_month:.1f} ({ratio:.2f}x)"))
    return v
