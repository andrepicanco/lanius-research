"""Controle de espaço em disco do terminal de teste (o C: do André tem pouco espaço livre).

Onde o espaço vai (medido em 2026-11-17 no terminal portátil, 450 MB no total):
  - Bases/<servidor>/history  (histórico M1 baixado do servidor; cresce a cada símbolo novo)  ~123 MB
  - Bases/Default             (histórico de exemplo que veio com a instalação, nunca usado)   ~44 MB
  - Tester/bases, cache, logs e logs do terminal (regerados a cada run)                        ~12 MB
  - MetaEditor64.exe / uninstall.exe, que o LiveUpdate recoloca na pasta (nao usados)          ~115 MB
  - relatórios: ~0,2 MB cada .htm; os PNG que o Tester gera junto são apagados no runner.

Política: antes de cada run exige `min_free_gb` livres no disco; depois de cada run apaga o que
é regerado (caches/logs do Tester) e, se o terminal passou de `max_terminal_mb`, apaga também o
histórico baixado (volta a ser baixado no próximo run; a reprodução do baseline com histórico
recém-baixado foi exata em 2026-11-17). Os relatórios mais antigos que `keep_reports` por
baseline são removidos.

Tudo aqui só mexe dentro de `terminal_dir` (o terminal portátil) e de `outputs_dir`.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CleanupReport:
    freed_mb: float = 0.0
    terminal_mb: float = 0.0
    purged_history: bool = False
    removed_reports: list[str] = field(default_factory=list)


def dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024


def free_gb(path: Path) -> float:
    p = path
    while not p.exists() and p != p.parent:   # sobe até uma pasta que exista
        p = p.parent
    return shutil.disk_usage(p).free / 1024 ** 3


def _rm_contents(folder: Path) -> float:
    """Apaga o conteúdo de `folder` (mantém a pasta). Devolve MB liberados."""
    if not folder.is_dir():
        return 0.0
    freed = dir_size_mb(folder)
    for child in folder.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    return freed


def purge_after_run(terminal_dir: Path, max_terminal_mb: float) -> CleanupReport:
    rep = CleanupReport()
    tester = terminal_dir / "Tester"
    # Regerado a cada run: cache do Tester, logs dos agentes e do terminal.
    for sub in ("cache", "logs", "bases"):
        rep.freed_mb += _rm_contents(tester / sub)
    for agent in tester.glob("Agent-*"):
        rep.freed_mb += _rm_contents(agent / "logs")
        rep.freed_mb += _rm_contents(agent / "cache")
    rep.freed_mb += _rm_contents(terminal_dir / "logs")
    # O LiveUpdate do MT5 reinstala o editor (~110 MB) na pasta; os testes nao o usam.
    for extra in ("MetaEditor64.exe", "uninstall.exe"):
        f = terminal_dir / extra
        if f.is_file():
            rep.freed_mb += f.stat().st_size / 1024 / 1024
            f.unlink(missing_ok=True)

    if dir_size_mb(terminal_dir) > max_terminal_mb:
        for hist in (terminal_dir / "Bases").glob("*/history"):
            if hist.parent.name == "Default":
                continue
            rep.freed_mb += _rm_contents(hist)
            rep.purged_history = True
        # histórico de exemplo da instalação: nunca usado pelos baselines
        rep.freed_mb += _rm_contents(terminal_dir / "Bases" / "Default" / "History")
    rep.terminal_mb = dir_size_mb(terminal_dir)
    return rep


def prune_reports(out_dir: Path, keep: int) -> list[str]:
    """Mantém os `keep` relatórios mais recentes de cada baseline (prefixo até o carimbo de data)."""
    groups: dict[str, list[Path]] = {}
    for f in out_dir.glob("sm_*.htm"):
        prefix = f.stem.rsplit("_", 2)[0]       # sm_<EA>_<ATIVO>_<TF>
        groups.setdefault(prefix, []).append(f)
    removed = []
    for files in groups.values():
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)
            removed.append(old.name)
    return removed
