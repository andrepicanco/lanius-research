"""Tipos compartilhados. Nada aqui depende de formato de arquivo."""

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class Trade:
    symbol: str
    direction: str                  # 'buy' | 'sell'
    volume: float
    open_time: dt.datetime
    close_time: dt.datetime
    open_price: float
    close_price: float
    profit: float
    swap: float
    commission: float
    exit_reason: str                # 'sl' | 'tp' | 'other'
    sl0: float | None = None        # stop inicial da ordem de entrada (Orders table)
    tp0: float | None = None
    r_multiple: float | None = None  # preenchido por metrics.attach_r_multiples

    @property
    def net(self) -> float:
        """Resultado líquido: lucro + swap + comissão (as duas últimas já vêm com sinal)."""
        return self.profit + self.swap + self.commission


@dataclass
class Report:
    """Um relatório do Strategy Tester, já lido. `source` é o arquivo de origem."""
    source: str
    expert: str
    symbol: str
    timeframe: str
    date_from: dt.date
    date_to: dt.date
    inputs: dict[str, str]
    deposit: float | None
    history_quality: str | None
    ticks: float | None
    trades: list[Trade]
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.expert}|{self.symbol}|{self.timeframe}"

    @property
    def label(self) -> str:
        return f"{self.expert} · {self.symbol} {self.timeframe}"
