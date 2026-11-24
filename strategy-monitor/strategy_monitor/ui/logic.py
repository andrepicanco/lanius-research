"""Lógica da tela que não depende de Streamlit (testável): quais parâmetros podem ser liberados no `discover`
e como transformar o que o usuário preenche em `ParamSpec`."""

import re

from ..discovery_config import FORBIDDEN, ParamSpec, StrategySpec
from ..engine.run import STRATEGIES

# Enumerações do MT5 (timeframe) não fazem sentido numa grade de números.
_NOT_TUNABLE = {"InpTimeframe", "InpHTFTimeframe"}
DEFAULT_FACTORS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
MODES = ("faixa", "valores", "múltiplos")     # min/max/passo | lista | múltiplos do valor do baseline


# Parâmetro -> (interruptor que o ativa, valor que o ativa). Variar o parâmetro com o interruptor fora de
# jogo não muda nada: o resultado repete e a busca desperdiça tentativas.
DEPENDS = {
    "InpFixedLot": ("InpUseFixedLot", True), "InpRiskPercent": ("InpUseFixedLot", False),
    "InpATRPeriod": ("InpUseTrailing", True), "InpATRMultiplier": ("InpUseTrailing", True),
    "InpTrailATRPeriod": ("InpUseTrailing", True), "InpTrailATRMultiplier": ("InpUseTrailing", True),
    "InpSMAFilterPeriod": ("InpUseSMAFilter", True), "InpHTFSMAPeriod": ("InpUseHTFFilter", True),
    "InpTickVolMAPeriod": ("InpUseTickVolFilter", True), "InpTickVolMinRatio": ("InpUseTickVolFilter", True),
    "InpStartHour": ("InpUseTradingHoursFilter", True), "InpEndHour": ("InpUseTradingHoursFilter", True),
}


def dependency_warnings(selected: list[str], baseline_inputs: dict) -> list[str]:
    """Avisos para parâmetros liberados que não têm efeito porque o interruptor deles está desligado no baseline
    e também não foi liberado para variar."""
    out = []
    for name in selected:
        dep = DEPENDS.get(name)
        if not dep or dep[0] in selected:
            continue
        switch, needed = dep
        on = str(baseline_inputs.get(switch, "")).strip().lower() == "true"
        if on != needed:
            out.append(f"{name} só tem efeito com {switch} ligado, e no baseline ele está desligado: "
                       f"libere também {switch}, senão {name} não muda nada.")
    return out


def tunable_params(expert: str) -> dict[str, str]:
    """{nome: tipo} dos inputs do EA que o `discover` pode variar (tipo = 'int' | 'float' | 'bool')."""
    mod = STRATEGIES.get(expert)
    if mod is None:
        return {}
    kinds = {int: "int", float: "float", bool: "bool"}
    return {name: kinds[kind] for name, (kind, _) in mod.SCHEMA.items()
            if name not in FORBIDDEN and name not in _NOT_TUNABLE}


def mode_of(spec: ParamSpec | None) -> str:
    if spec is None or spec.factors is not None or (spec.values is None and spec.min is None):
        return "múltiplos"
    return "valores" if spec.values is not None else "faixa"


def spec_from_form(name: str, kind: str, mode: str, *, lo=None, hi=None, step=None,
                   values: str = "", factors: str = "") -> ParamSpec:
    """Monta o ParamSpec do que a tela recebeu. Erro de preenchimento vira ValueError com mensagem clara."""
    if kind == "bool":
        return ParamSpec(name, "bool")
    if mode == "faixa":
        if lo is None or hi is None or not step or step <= 0 or hi < lo:
            raise ValueError(f"{name}: faixa inválida (mínimo, máximo e passo > 0, com máximo ≥ mínimo)")
        return ParamSpec(name, kind, min=float(lo), max=float(hi), step=float(step))
    raw = values if mode == "valores" else factors
    try:
        nums = [float(x) for x in re.split(r"[,; ]+", raw.strip()) if x]      # ponto decimal; vírgula separa itens
    except ValueError:
        raise ValueError(f"{name}: use números separados por vírgula (ex.: 0.5, 1, 2)") from None
    if not nums:
        raise ValueError(f"{name}: informe pelo menos um valor")
    return ParamSpec(name, kind, values=nums) if mode == "valores" else ParamSpec(name, kind, factors=nums)


def numbers_text(nums: list[float] | None) -> str:
    return ", ".join(f"{n:g}" for n in nums) if nums else ""


def build_strategy_spec(expert: str, tickers: list[str], params: list[ParamSpec], template: str | None = None,
                        timeframe: str | None = None) -> StrategySpec:
    if not params:
        raise ValueError("libere pelo menos um parâmetro")
    if not tickers:
        raise ValueError("informe pelo menos um ativo")
    return StrategySpec(expert=expert, tickers=tickers, params=params, template=template, timeframe=timeframe)


def spec_to_yaml(p: ParamSpec) -> dict:
    if p.type == "bool":
        return {"type": "bool"}
    if p.values is not None:
        return {"type": p.type, "values": p.values}
    if p.factors is not None:
        return {"type": p.type, "factors": p.factors}
    return {"type": p.type, "min": p.min, "max": p.max, "step": p.step}


def save_discovery_yaml(path, settings, spec: StrategySpec) -> None:
    """Grava a seleção atual como PADRÃO do `discover`: atualiza `settings` e a estratégia escolhida em
    config/discovery.yaml, mantendo as outras estratégias do arquivo. (Comentários do arquivo se perdem; o
    modelo comentado continua em discovery.example.yaml.)"""
    from dataclasses import asdict
    from pathlib import Path

    import yaml
    p = Path(path)
    raw = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.is_file() else {}
    raw["settings"] = {k: v for k, v in asdict(settings).items() if v is not None}
    entry = {"tickers": list(spec.tickers), "parameters": {q.name: spec_to_yaml(q) for q in spec.params}}
    if spec.template:
        entry["template"] = spec.template
    if spec.timeframe:
        entry["timeframe"] = spec.timeframe
    raw.setdefault("strategies", {})[spec.expert] = entry
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
