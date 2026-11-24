"""Historico de barras e especificacao dos simbolos, lidos da instancia ABERTA do MT5 pela API Python
(somente leitura: nunca envia ordens) e guardados em cache local (.npz / .json).

Fatos verificados em 2026-11-24 na instancia do Andre (build 6230):
  - o terminal entrega no maximo `maxbars` (100 000) barras por serie; M1 so alcanca ~3 meses. Timeframes
    maiores alcancam mais (M6 de DAX40: desde mai/2025), por isso o motor usa a MENOR sub-barra que
    divide o timeframe do EA e cobre a janela pedida;
  - `copy_rates_range` com muito mais que maxbars barras falha ("Invalid params"): busca em blocos;
  - os horarios sao o horario do servidor tratado como UTC (iguais aos do relatorio do Tester).
O cache cresce a cada uso, entao o historico disponivel aumenta com o tempo, mesmo alem do limite do terminal.
"""

import datetime as dt
import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

TF_MIN = {"M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6, "M10": 10, "M12": 12, "M15": 15, "M20": 20,
          "M30": 30, "H1": 60, "H2": 120, "H3": 180, "H4": 240, "H6": 360, "H8": 480, "H12": 720, "D1": 1440}
FIELDS = ("time", "open", "high", "low", "close", "tick_volume", "spread")
_DTYPE = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
          ("tick_volume", "i8"), ("spread", "i4")]
# Minutos de negociacao por dia corrido (1440 * 5/7): so estima quantas barras uma janela precisa.
_MIN_PER_DAY = 1029.0
_UTC = dt.timezone.utc


class DataError(RuntimeError):
    pass


@dataclass
class SymbolSpec:
    symbol: str
    point: float
    digits: int
    tick_size: float
    tick_value: float          # na moeda da conta, por tick e por lote
    volume_min: float
    volume_step: float
    volume_max: float
    stops_level: int
    swap_long: float           # em pontos, por lote e por noite (swap_mode em pontos: o caso dos CFDs)
    swap_short: float
    swap_rollover3days: int    # dia da semana do swap triplo (0 = domingo)
    swap_mode: int


def tf_from_mt5_enum(value: int | str, default: str) -> str:
    """ENUM_TIMEFRAMES do MT5 -> nome ('12' -> M12, '16385' -> H1). 0 = PERIOD_CURRENT -> `default`."""
    v = int(float(value))
    if v == 0:
        return default
    if v < 16384:
        name = f"M{v}"
    elif v < 32768:
        name = f"H{v - 16384}" if v != 16408 else "D1"
    else:
        raise DataError(f"timeframe {v} nao suportado")
    if name not in TF_MIN:
        raise DataError(f"timeframe {name} nao suportado")
    return name


def tf_to_mt5_enum(name: str) -> int:
    """Inverso de `tf_from_mt5_enum` ('M12' -> 12, 'H1' -> 16385, 'D1' -> 16408)."""
    if name not in TF_MIN:
        raise DataError(f"timeframe {name} nao suportado")
    if name == "D1":
        return 16408
    return int(name[1:]) if name[0] == "M" else 16384 + int(name[1:])


def epoch(d: dt.datetime) -> int:
    return int(d.replace(tzinfo=_UTC).timestamp())


def from_epoch(s: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(int(s), _UTC).replace(tzinfo=None)


def _is_running(exe: str | Path) -> bool:
    """Há um processo com este executável rodando? (a API Python do MT5 pode ABRIR o terminal se ele estiver
    fechado, o que acionaria o LiveUpdate e o pedido de administrador: por isso só conectamos a um já aberto)."""
    import psutil
    target = str(Path(exe).resolve()).lower()
    for p in psutil.process_iter(["exe"]):
        try:
            if p.info["exe"] and str(Path(p.info["exe"]).resolve()).lower() == target:
                return True
        except (OSError, psutil.Error):
            continue
    return False


class ConnectionLost(DataError):
    """A conexão com o MT5 caiu no meio da execução (ex.: outra ação do mesmo processo a fechou, ou o terminal
    foi fechado). Não adianta tentar as demais combinações: a execução para."""


# A biblioteca MetaTrader5 tem UMA conexão global por processo. O Streamlit atende várias ações no mesmo processo:
# se cada uma abre e fecha a conexão por conta própria, a que termina primeiro derruba a que ainda roda
# (reproduzido em 2026-11-24: 'No IPC connection'). Por isso a conexão é contada: a última saída é que encerra.
_CONN_LOCK = threading.RLock()
_CONN_COUNT = 0


class Mt5Source:
    """Conexao somente-leitura. `terminal_path` = terminal64.exe da instancia JA ABERTA (se nao estiver
    aberta, a API abriria uma: evite). Use como contexto: `with Mt5Source(path) as s:`. Pode ser aninhado e usado
    por varias threads: a conexao so e encerrada quando o ultimo uso sai."""

    def __init__(self, terminal_path: str | Path):
        self.path = str(terminal_path)
        self._mt5 = None

    def __enter__(self):
        global _CONN_COUNT
        if not _is_running(self.path):
            raise DataError(f"o MT5 não está aberto ({self.path}). Abra-o e faça login antes: o motor só lê barras "
                            "de uma instância já aberta (se fosse aberto por aqui, poderia acionar o LiveUpdate).")
        import MetaTrader5 as mt5
        with _CONN_LOCK:
            if _CONN_COUNT == 0 and not mt5.initialize(path=self.path):
                raise DataError(f"nao consegui conectar ao MT5 em {self.path}: {mt5.last_error()}. "
                                "A instancia precisa estar aberta e logada.")
            _CONN_COUNT += 1
        self._mt5 = mt5
        return self

    def __exit__(self, *exc):
        global _CONN_COUNT
        if self._mt5 is None:
            return
        with _CONN_LOCK:
            _CONN_COUNT -= 1
            if _CONN_COUNT == 0:
                self._mt5.shutdown()
        self._mt5 = None

    def _terminal(self):
        """terminal_info() ou ConnectionLost: sem isso, uma conexão caída vira erros confusos mais adiante
        ('símbolo não existe', 'histórico insuficiente')."""
        info = self._mt5.terminal_info()
        if info is None:
            raise ConnectionLost(f"a conexão com o MT5 caiu ({self._mt5.last_error()}). Confira se o MT5 continua "
                                 "aberto e logado e rode de novo; o que já foi calculado ficou no results.csv.")
        return info

    @property
    def maxbars(self) -> int:
        return int(self._terminal().maxbars)

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        self._terminal()
        self._mt5.symbol_select(symbol, True)
        si = self._mt5.symbol_info(symbol)
        if si is None:
            raise DataError(f"simbolo {symbol} nao existe nessa corretora: {self._mt5.last_error()}")
        return SymbolSpec(symbol, si.point, si.digits, si.trade_tick_size, si.trade_tick_value, si.volume_min,
                          si.volume_step, si.volume_max, max(si.trade_stops_level, si.trade_freeze_level),
                          si.swap_long, si.swap_short, si.swap_rollover3days, si.swap_mode)

    def symbols(self) -> list[tuple[str, str, str]]:
        """(nome, descrição, caminho/grupo) de todos os símbolos que a corretora oferece neste terminal."""
        self._terminal()
        found = self._mt5.symbols_get()
        if found is None:
            raise DataError(f"não consegui listar os símbolos: {self._mt5.last_error()}")
        return sorted((s.name, s.description, s.path) for s in found)

    def rates(self, symbol: str, tf: str, start: dt.datetime, end: dt.datetime) -> np.ndarray:
        """Barras com abertura em [start, end), em blocos que cabem no limite do terminal."""
        mt5 = self._mt5
        self._terminal()
        step = dt.timedelta(minutes=TF_MIN[tf] * int(self.maxbars * 0.4 / (_MIN_PER_DAY / 1440.0)))
        parts, t = [], start
        while t < end:
            t2 = min(t + step, end)
            r = mt5.copy_rates_range(symbol, getattr(mt5, f"TIMEFRAME_{tf}"),
                                     t.replace(tzinfo=_UTC), t2.replace(tzinfo=_UTC))
            if r is not None and len(r):
                conv = np.empty(len(r), dtype=_DTYPE)
                for f in FIELDS:
                    conv[f] = r[f]
                parts.append(conv)
            t = t2
        if not parts:
            return np.empty(0, dtype=_DTYPE)
        out = np.concatenate(parts)
        return out[(out["time"] >= epoch(start)) & (out["time"] < epoch(end))]   # duplicatas: BarCache._merge_save


class BarCache:
    """Cache em disco por (simbolo, timeframe). `get` devolve as barras [start, end) e so vai ao MT5
    se o cache nao cobre a janela (ou se faltam as barras mais recentes)."""

    def __init__(self, cache_dir: str | Path, source: Mt5Source | None):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.source = source
        self._head: dict[tuple[str, str], int] = {}   # inicio ja pedido ao terminal, por serie (neste processo)
        self._tail: dict[tuple[str, str], bool] = {}  # rabo (barras novas) ja atualizado, por serie
        self._specs: dict[str, SymbolSpec] = {}       # especificacao do simbolo, uma consulta por processo

    def _file(self, symbol: str, tf: str) -> Path:
        return self.dir / f"{symbol}_{tf}.npz"

    def _load(self, symbol: str, tf: str) -> np.ndarray:
        f = self._file(symbol, tf)
        if not f.is_file():
            return np.empty(0, dtype=_DTYPE)
        with np.load(f) as z:
            return z["bars"]

    def _merge_save(self, symbol: str, tf: str, old: np.ndarray, new: np.ndarray) -> np.ndarray:
        both = np.concatenate([old, new]) if len(old) else new
        if not len(both):
            return both
        both = both[np.argsort(both["time"], kind="stable")]
        keep = np.concatenate([[True], np.diff(both["time"]) > 0])
        both = both[keep]
        np.savez_compressed(self._file(symbol, tf), bars=both)
        return both

    def get(self, symbol: str, tf: str, start: dt.datetime, end: dt.datetime) -> np.ndarray:
        """Barras com abertura em [start, end). Vai ao MT5 (uma vez por processo e serie, para nao repetir a
        cada backtest) so para completar o INICIO (cache comeca depois de `start`) e o FIM (barras novas)."""
        cached = self._load(symbol, tf)
        s, e = epoch(start), epoch(end)
        if self.source is not None:
            key = (symbol, tf)
            if not len(cached):
                cached = self._merge_save(symbol, tf, cached, self.source.rates(symbol, tf, start, end))
                self._head[key], self._tail[key] = s, True
            else:
                if cached["time"][0] > s + 5 * 86400 and s < self._head.get(key, cached["time"][0]):
                    head = self.source.rates(symbol, tf, start, from_epoch(int(cached["time"][0])))
                    cached = self._merge_save(symbol, tf, cached, head)
                    self._head[key] = s          # ja tentei: o terminal pode simplesmente nao ter mais historia
                if key not in self._tail:
                    now = epoch(dt.datetime.now(_UTC).replace(tzinfo=None))
                    if cached["time"][-1] < now - 6 * 3600:
                        tail = self.source.rates(symbol, tf, from_epoch(int(cached["time"][-1]) - 86400),
                                                 from_epoch(now + 86400))
                        cached = self._merge_save(symbol, tf, cached, tail)
                    self._tail[key] = True
        return cached[(cached["time"] >= s) & (cached["time"] < e)]

    def spec(self, symbol: str) -> SymbolSpec:
        if symbol in self._specs:
            return self._specs[symbol]
        f = self.dir / f"{symbol}_spec.json"
        if self.source is not None:
            spec = self.source.symbol_spec(symbol)
            f.write_text(json.dumps(asdict(spec)), encoding="utf-8")
        elif f.is_file():
            spec = SymbolSpec(**json.loads(f.read_text(encoding="utf-8")))
        else:
            raise DataError(f"sem cache da especificacao de {symbol} e sem conexao ao MT5")
        self._specs[symbol] = spec
        return spec

    def sub_tf(self, work_tf: str, start: dt.datetime, end: dt.datetime, symbol: str) -> str:
        """Menor sub-barra (divide o TF do EA) cuja janela cabe no limite do terminal e cuja historia
        comeca antes de `start`. Quanto menor, mais fiel a ordem SL x TP dentro da barra."""
        w = TF_MIN[work_tf]
        maxbars = self.source.maxbars if self.source else 100_000
        days = max(1.0, (end - start).total_seconds() / 86400)
        tried = []
        for name, m in TF_MIN.items():
            if m > w or w % m:
                continue
            fits = days * _MIN_PER_DAY / m <= 0.92 * maxbars
            cached = self._load(symbol, name)
            if not fits and not (len(cached) and cached["time"][0] <= epoch(start) + 5 * 86400):
                continue
            bars = self.get(symbol, name, start, end)
            tried.append(name)
            if len(bars) and bars["time"][0] <= epoch(start) + 5 * 86400:
                return name
        raise DataError(f"nenhuma sub-barra de {work_tf} cobre {symbol} desde {start:%Y-%m-%d} "
                        f"(tentei {tried or 'nenhuma'}). O historico do terminal e limitado.")
