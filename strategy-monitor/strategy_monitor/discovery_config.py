"""Configuração da descoberta de edges (config/discovery.yaml).

Princípio: **só varia o que o André liberar**. Um parâmetro que não está em `parameters` nunca é
alterado; um que não existe nos inputs do baseline é erro (evita typo silencioso); parâmetros de
dimensionamento (lote/risco) são recusados, porque o objetivo é E[R] e não pode ser melhorado por
tamanho de posição.
"""

import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Mexer nisto não cria edge (ou quebra a identificação do EA): recusado de propósito.
FORBIDDEN = {
    "InpMagicNumber": "identifica o EA",
    "InpTradeComment": "identifica o EA",
    "InpLogLevel": "não afeta o resultado",
}


class DiscoveryConfigError(ValueError):
    pass


@dataclass
class ParamSpec:
    name: str
    type: str                                 # 'int' | 'float' | 'bool'
    values: list[float] | None = None         # lista explícita (absoluta)
    min: float | None = None
    max: float | None = None
    step: float | None = None
    factors: list[float] | None = None        # multiplicadores do valor de referência

    def candidates(self, reference: str) -> list[str]:
        """Valores permitidos, já formatados como o MT5 espera nos inputs."""
        if self.type == "bool":
            return ["true", "false"]
        if self.values is not None:
            raw = list(self.values)
        elif self.factors is not None:
            ref = float(reference)
            raw = [ref * f for f in self.factors]
        else:
            n = int(math.floor((self.max - self.min) / self.step + 1e-9))
            raw = [self.min + i * self.step for i in range(n + 1)]
        if self.type == "int":
            raw = [round(v) for v in raw]
        out, seen = [], set()
        for v in raw:
            s = fmt_value(v, self.type, self.step)
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out


def fmt_value(v: float, type_: str, step: float | None = None) -> str:
    if type_ == "int":
        return str(int(round(v)))
    if type_ == "bool":
        return "true" if v else "false"
    decimals = 6
    if step:
        decimals = max(0, min(6, -int(math.floor(math.log10(step))) + 1))
    return f"{round(v, decimals):.{decimals}f}".rstrip("0").rstrip(".") or "0"


@dataclass
class Settings:
    is_days: int = 240
    oos_days: int = 60
    trials: int = 300
    top_k: int = 5
    seed: int = 42
    min_trades_is: int = 30
    min_trades_oos: int = 10
    folds: int = 3                          # trechos do IS para o teste de consistência (0 = não testar)
    min_positive_folds: int = 2
    size_last: bool = True                  # busca sem o lote; no fim acha o maior lote que cabe no teto de DD
    max_drawdown_pct: float | None = None   # obrigatorio: sem default de proposito


@dataclass
class StrategySpec:
    expert: str
    tickers: list[str]
    params: list[ParamSpec]
    template: str | None = None      # trecho do rótulo do baseline usado como base p/ tickers sem baseline
    timeframe: str | None = None     # só p/ tickers sem baseline (senão herda do template)


def seed_for(seed: int, key: str) -> int:
    """Semente estável entre processos (hash() do Python é aleatório por processo)."""
    return (seed * 1_000_003 + zlib.crc32(key.encode("utf-8"))) & 0xFFFFFFFF


def _spec(name: str, raw: dict) -> ParamSpec:
    if name in FORBIDDEN:
        raise DiscoveryConfigError(f"{name} não pode ser variado: {FORBIDDEN[name]}")
    raw = raw or {}
    t = raw.get("type")
    if t not in ("int", "float", "bool"):
        raise DiscoveryConfigError(f"{name}: type deve ser int, float ou bool")
    if t == "bool":
        return ParamSpec(name, t)
    forms = [k for k in ("values", "factors") if k in raw] + (["min/max/step"] if {"min", "max", "step"} <= raw.keys() else [])
    if len(forms) != 1:
        raise DiscoveryConfigError(f"{name}: informe exatamente uma forma: values, factors ou min+max+step")
    if "min" in raw and (raw["step"] <= 0 or raw["max"] < raw["min"]):
        raise DiscoveryConfigError(f"{name}: min/max/step inválidos")
    return ParamSpec(name, t, values=raw.get("values"), min=raw.get("min"), max=raw.get("max"),
                     step=raw.get("step"), factors=raw.get("factors"))


def load_discovery_config(path: str | Path) -> tuple[Settings, list[StrategySpec]]:
    p = Path(path)
    if not p.is_file():
        raise DiscoveryConfigError(f"config de descoberta não encontrada: {p} "
                                   "(copie config/discovery.example.yaml)")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    st = raw.get("settings") or {}
    settings = Settings(**{k: st[k] for k in st if k in Settings.__dataclass_fields__})
    if settings.max_drawdown_pct is None or settings.max_drawdown_pct <= 0:
        raise DiscoveryConfigError("settings.max_drawdown_pct é obrigatório (teto de drawdown, em %)")
    if settings.top_k < 1 or settings.trials < 1:
        raise DiscoveryConfigError("trials e top_k devem ser >= 1")
    specs = []
    for expert, body in (raw.get("strategies") or {}).items():
        body = body or {}
        params = [_spec(n, r) for n, r in (body.get("parameters") or {}).items()]
        if not params:
            raise DiscoveryConfigError(f"{expert}: nenhum parâmetro liberado em `parameters`")
        if not body.get("tickers"):
            raise DiscoveryConfigError(f"{expert}: lista `tickers` vazia")
        specs.append(StrategySpec(expert=expert, tickers=list(body["tickers"]), params=params,
                                  template=body.get("template"), timeframe=body.get("timeframe")))
    if not specs:
        raise DiscoveryConfigError("nenhuma estratégia em `strategies`")
    return settings, specs
