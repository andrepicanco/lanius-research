"""Leitura dos inputs do EA (strings do relatorio do Tester) com tipo e default.

O relatorio traz tudo como texto ('true', '12', '1.61'). Cada estrategia declara seu esquema
(`nome: (tipo, default)`); input desconhecido e ignorado (magic number, comentario, log...) e input
ausente usa o default do EA. Valor que nao converte e erro explicito.
"""

TRADE_FILTERS = {
    "InpUseTradingHoursFilter": (bool, False), "InpStartHour": (int, 0), "InpEndHour": (int, 23),
    "InpUseTradingDaysFilter": (bool, False),
    "InpTradeMonday": (bool, True), "InpTradeTuesday": (bool, True), "InpTradeWednesday": (bool, True),
    "InpTradeThursday": (bool, True), "InpTradeFriday": (bool, True), "InpTradeSaturday": (bool, True),
    "InpTradeSunday": (bool, True), "InpMaxSpreadPoints": (int, 0),
}
SIZING = {"InpUseFixedLot": (bool, False), "InpFixedLot": (float, 0.10), "InpRiskPercent": (float, 1.0)}


class InputError(ValueError):
    pass


def _conv(kind, raw, name):
    s = str(raw).strip()
    try:
        if kind is bool:
            if s.lower() in ("true", "1"):
                return True
            if s.lower() in ("false", "0"):
                return False
            raise ValueError(s)
        return kind(float(s)) if kind is int else float(s)
    except ValueError:
        raise InputError(f"input {name}={raw!r} nao e {kind.__name__}") from None


def parse(inputs: dict, schema: dict) -> dict:
    return {k: (_conv(kind, inputs[k], k) if k in inputs else default) for k, (kind, default) in schema.items()}


def allowed_day(p: dict, weekday: int) -> bool:
    """weekday: 0 = domingo ... 6 = sabado."""
    names = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
    return p[f"InpTrade{names[weekday]}"]


def allowed_hour(p: dict, hour: int) -> bool:
    a, b = p["InpStartHour"], p["InpEndHour"]
    return (a <= hour <= b) if a <= b else (hour >= a or hour <= b)
