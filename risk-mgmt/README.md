# risk-mgmt

Risk report for the Lanius strategies: parametric VaR, cross-asset correlation, monthly
trade results, and a shared-balance portfolio view that puts several strategies on one
account. Output goes to Telegram, or to stdout with `--dry-run`.

Strategy-agnostic on purpose. It reads backtest files that MetaTrader produces, has no
import dependency on any EA's repo, and identifies a strategy by the trade comment its
EA writes. Promoted out of `strategies/9-1_lanius-cap/Risk Mgmt/` so that a combined
report covering more than one strategy has somewhere to live.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest tests/    # no MT5 connection or credentials needed
```

`--mode live` additionally needs the `MetaTrader5` package (Windows-only, talks to an
already-open, logged-in desktop terminal) and either `config/account.yaml` (copy from
`config/account.example.yaml`) or `MT5_LOGIN`/`MT5_PASSWORD`/`MT5_SERVER` environment
variables. `--mode local` needs none of that.

## The three scenarios

Run each backtest in the MT5 Strategy Tester, then save its report: right-click the
**Backtest** tab → **Report** → **HTML**. One symbol per pass, so a basket of N symbols
gives N report files. Put each strategy's reports in their own directory.

```bash
# 1. One strategy's basket
python scripts/run_risk_report.py --mode local \
    --strategy MeanRev1=reports/meanrev --dry-run

# 2. The other strategy's basket
python scripts/run_risk_report.py --mode local \
    --strategy IdxSwing91=reports/91 --dry-run

# 3. Both on the same balance, same period
python scripts/run_risk_report.py --mode local \
    --strategy MeanRev1=reports/meanrev \
    --strategy IdxSwing91=reports/91 \
    --title "Lanius - cesta combinada" --dry-run
```

Scenario 3 adds the *Portfolio (shared balance)* section: one equity curve, combined
drawdown, per-strategy attribution, how often the strategies were in the market at the
same time, and the correlation between their daily P/L.

## What this does

1. **VaR.** "Returns" = realized daily P/L in $ from closed trades, not price
   mark-to-market — the natural definition for a stop/target strategy. Two parametric
   curves: a short window (`var_window_days`, default 10) as the current read, and a
   longer baseline (`var_baseline_days`, default 60). See `risk_mgmt/var.py` for the
   formula and its limitations.
2. **Correlation.** Daily close-to-close price returns across the symbols actually
   traded, with a rolling-window matrix, the top/bottom correlating pairs against their
   full-history baseline, and a quarterly PC1 concentration series.
3. **Trade results.** Overall summary plus a monthly breakdown (per-asset P/L, win rate,
   best/worst trade, intra-month drawdown, monthly VaR), in the Telegram text and in a
   printable `monthly_report.xlsx`.
4. **Portfolio.** See `risk_mgmt/portfolio.py` — the shared-balance replay, its two
   caveats below, and the concurrency figures that stand in for margin.

## Sources and `--log-dir`

The old single-directory form still works, and `--source` defaults to `auto`, which
picks the parser from the files present — so no flag is needed:

```bash
# a directory of Journal .log files, exactly as before
python scripts/run_risk_report.py --mode local --log-dir path/to/logs --dry-run
```

`--strategy NAME=DIR` is the same thing with a label attached; without one the report
calls the strategy `unnamed`. Use `--strategy` when you want the name in the output, or
when comparing more than one strategy.

`--source` (local mode only) can be forced when auto-detection would be ambiguous:

| value | reads | notes |
|---|---|---|
| `auto` (default) | — | `.html` → report, `.log` → journal, `*_trades.csv` → csv. Refuses to guess if a directory holds more than one kind. |
| `mt5_report_html` | `*.htm*` | The Strategy Tester's HTML report. Works for any EA. Joins the Orders table by order number to recover each position's original stop loss, so `r_multiple` and "avg risk at entry" are real numbers, and carries commission/swap. Prefer this for new work. |
| `mql5_journal` | `*.log` | The raw Strategy Tester Journal. Covers every symbol of a run in one file, but its P/L is **gross** — see below. |
| `idxswing91_csv` | `*_trades.csv` | `IdxSwing91_Python`'s own backtest output. |

### `mql5_journal`: P/L is gross

**The Journal text carries no commission and no swap at all.** This source prices a
trade as its price difference times the SymbolSpec, so its P/L is a *gross* figure and
will not match the Tester's own net profit. Measured on a real MeanRev1 run (F40EUR H1,
45 trades): Journal `+278.67` against the report's `+278.89` of price P/L and `-38.89`
of swap, for a net `+240.00`. The gap is entirely the swap the log never mentions.

Use `mt5_report_html` whenever the net number matters — costs are exactly the thing that
decided whether recent backtests were profitable, so this is rarely a detail.

It also needs `tick_value`/`tick_size` per symbol to turn fills into money, since the
Journal text doesn't carry those either: with MT5 open it fetches them live, otherwise
fill in `specs:` in `config/symbols.yaml`. The HTML report source needs none of this —
the broker already priced every deal in account currency.

None of this is about the terminal's UI language: MetaTrader writes its Journal trade
lines (`deal #2 sell 0.3 NAS100 at ... done`, `take profit triggered #2 ...`) in English
regardless of interface language.

One more limit: the original stop loss is read from the EA's own entry line (`placed
BUY stop @ ... SL=...` for IdxSwing91, `opened BUY @ ... SL=...` for MeanRev1). An EA
that phrases its entry log differently still gets its trades, just no
`r_multiple`/`risk_money`.

Correlation's price history has its own local/live split via `--price-source {mt5,csv}`
(`--price-dir` for a directory of `<symbol>.csv` files with `date,close` columns), so
correlation can run offline even when VaR comes from a live account.

## Reading the portfolio numbers

Two things the shared-balance view is *not*:

- **It is exact only with fixed-lot backtests.** A trade's P/L in account currency then
  doesn't depend on the balance it was opened against, so adding the streams is
  legitimate. If a strategy sized by `InpRiskPercent` (`InpUseFixedLot=false`), each EA
  in a real combined account would have sized off the *combined* balance, which can't be
  reconstructed from realized P/L. The report detects this from the volumes in the data
  and prints a NOTE rather than implying precision it doesn't have.
- **Margin is not modeled.** No per-symbol margin rates are available here. The
  concurrency figures (max positions open at once, % of time more than one was open) are
  the exposure proxy; cross-check against the Tester's own margin-level field per run.

Drawdown walks the merged trades in close order, which is what the Tester's own
**balance** drawdown does — a single-strategy run reconciles with its report exactly.
The Tester's **equity** drawdown marks open positions tick by tick and reads higher than
both.

## Known limitations

- Parametric VaR assumes roughly normal daily P/L. A strategy with capped R-multiples
  doesn't really produce normal outcomes; historical/empirical VaR would be more
  faithful, if noisier.
- Live-mode trades (`risk_mgmt/live_state.py`) carry no `r_multiple` — MT5's deal history
  has no record of the stop distance a position was opened against. The HTML report
  source does not have this gap.
- Deals are paired FIFO per symbol. Exact for an EA holding one position at a time (both
  current strategies do); a reversal deal (`in/out`) is rejected rather than guessed at.
