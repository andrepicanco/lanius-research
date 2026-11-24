"""Gera o .ini de linha de comando do Strategy Tester a partir de um baseline.

Formato conferido nos .ini que o próprio Tester do André salvou em Profiles\\Tester
(ex.: BreakoutDonchian1.NAS100.H1.20260101_20261107.000.ini): UTF-16LE com BOM, seção [Tester]
e seção [TesterInputs]. Os valores de Model (0 = Cada tick) e ExecutionMode (20 = latência de
20 ms) são os do baseline conforme a tela do Tester informada pelo André em 2026-11-17.
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from .models import Report


@dataclass
class TesterSettings:
    model: int = 0             # 0 = Cada tick (gerado), 4 = ticks reais
    execution_mode: int = 20   # latência em ms
    leverage: int = 100
    currency: str = "USD"
    forward_mode: int = 0      # 0 = sem período forward: o run vai direto até to_date


def expert_path(expert: str, overrides: dict[str, str] | None = None) -> str:
    """Caminho do .ex5 relativo a MQL5\\Experts. Por convenção a pasta tem o nome do EA."""
    if overrides and expert in overrides:
        return overrides[expert]
    return f"{expert}\\{expert}.ex5"


def build_ini(baseline: Report, report_name: str, to_date: dt.date,
              settings: TesterSettings | None = None,
              expert_overrides: dict[str, str] | None = None,
              inputs: dict[str, str] | None = None,
              from_date: dt.date | None = None) -> str:
    """`from_date` (default: início do baseline) permite rodar só uma janela recente.
    `inputs` sobrepõe os do baseline (usado pela descoberta de parâmetros); sem ele, o run usa
    exatamente os inputs do baseline."""
    s = settings or TesterSettings()
    use_inputs = dict(baseline.inputs)
    if inputs:
        use_inputs.update(inputs)
    lev = s.leverage
    lines = [
        "[Tester]",
        f"Expert={expert_path(baseline.expert, expert_overrides)}",
        f"Symbol={baseline.symbol}",
        f"Period={baseline.timeframe}",
        "Optimization=0",
        f"Model={s.model}",
        f"FromDate={(from_date or baseline.date_from):%Y.%m.%d}",
        f"ToDate={to_date:%Y.%m.%d}",
        f"ForwardMode={s.forward_mode}",
        f"Deposit={baseline.deposit:g}" if baseline.deposit else "Deposit=10000",
        f"Currency={s.currency}",
        "ProfitInPips=0",
        f"Leverage={lev}",
        f"ExecutionMode={s.execution_mode}",
        "OptimizationCriterion=0",
        "Visual=0",
        f"Report={report_name}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
        "[TesterInputs]",
    ]
    lines += [f"{k}={v}" for k, v in use_inputs.items()]
    return "\r\n".join(lines) + "\r\n"


def write_ini(path: str | Path, text: str) -> None:
    """UTF-16LE com BOM, como o Tester grava e lê (um .ini em UTF-8 gerado por script é ignorado,
    segundo relato no fórum do MQL5; a confirmar no spike)."""
    Path(path).write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
