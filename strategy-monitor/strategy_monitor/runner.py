"""Executa o Strategy Tester por linha de comando num terminal PORTÁTIL dedicado.

Por que um terminal separado (verificado em 2026-11-17):
  - `terminal64.exe /config:x.ini` com o terminal ao vivo já aberto é IGNORADO: o processo novo
    sai em segundos, sem teste e sem relatório;
  - com `ShutdownTerminal=1` o terminal que roda o teste se fecha no fim, o que derrubaria os
    EAs do terminal ao vivo se fosse o mesmo.
Então o runner só executa `<terminal_dir>\\terminal64.exe /portable` e RECUSA rodar se essa
instância estiver aberta (o /config seria ignorado do mesmo jeito).

Observado: a data final (ToDate) é EXCLUSIVA: com ToDate=2026.10.05 o Tester não gerou os trades
de 05/10. Por isso o parâmetro aqui se chama `until` e vai direto para ToDate.
"""

import datetime as dt
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from .cleanup import free_gb, prune_reports, purge_after_run
from .models import Report
from .tester_ini import TesterSettings, build_ini, write_ini
from .tester_report import read_report


class RunnerError(RuntimeError):
    pass


@dataclass
class RunnerConfig:
    terminal_dir: Path
    timeout_s: int = 1800
    settings: TesterSettings = field(default_factory=TesterSettings)
    experts: dict[str, str] = field(default_factory=dict)   # EA -> caminho relativo a MQL5\Experts
    min_free_gb: float = 1.0        # recusa rodar com menos que isso livre no disco
    max_terminal_mb: float = 500.0  # acima disso, apaga o historico baixado depois do run
    keep_reports: int = 10          # relatorios mantidos por baseline em out_dir


def _exe(cfg: RunnerConfig) -> Path:
    exe = cfg.terminal_dir / "terminal64.exe"
    if not exe.is_file():
        raise RunnerError(f"terminal64.exe não encontrado em {cfg.terminal_dir}")
    return exe


def _is_running(exe: Path) -> bool:
    target = str(exe.resolve()).lower()
    for p in psutil.process_iter(["exe"]):
        try:
            if p.info["exe"] and str(Path(p.info["exe"]).resolve()).lower() == target:
                return True
        except (OSError, psutil.Error):
            continue
    return False


def _stop_instances(exe: Path, wait_s: float = 20.0) -> int:
    """Encerra instancias do terminal de TESTE (so as do exe dado). Usado depois de um LiveUpdate,
    que fecha o terminal e o reabre sem o /config. Nunca toca em outro terminal64.exe."""
    target = str(exe.resolve()).lower()
    victims = []
    for p in psutil.process_iter(["exe"]):
        try:
            if p.info["exe"] and str(Path(p.info["exe"]).resolve()).lower() == target:
                victims.append(p)
        except (OSError, psutil.Error):
            continue
    for p in victims:
        try:
            p.terminate()
        except psutil.Error:
            pass
    psutil.wait_procs(victims, timeout=wait_s)
    return len(victims)


def _wait_for_relaunch(exe: Path, report: Path, timeout_s: float, appear_s: float = 25.0) -> bool:
    """O LiveUpdate do MT5 pode fechar o terminal e RELANCA-LO com o mesmo /config (visto nos logs
    em 2026-11-17: 'LiveUpdate start ... /update /path /portable /config'); a instancia relancada
    roda o teste normalmente. Depois que o processo que lancamos sai, espera ate `appear_s` por uma
    instancia relancada e, se ela vier, espera ela terminar. Devolve se o relatorio existe."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < appear_s and not _is_running(exe):
        time.sleep(1)
    while _is_running(exe) and time.monotonic() - t0 < timeout_s:
        time.sleep(2)
    return report.is_file()


def run_backtest(baseline: Report, cfg: RunnerConfig, until: dt.date, name: str,
                 out_dir: Path, inputs: dict[str, str] | None = None,
                 log: Callable[[str], None] = lambda s: None,
                 from_date: dt.date | None = None) -> Path:
    """Roda o EA/ativo/timeframe do baseline de `baseline.date_from` até `until` (exclusivo),
    com os inputs do baseline (ou sobrepostos por `inputs`). Devolve o caminho do relatório .htm
    já copiado para `out_dir`; os arquivos temporários do terminal são apagados."""
    exe = _exe(cfg)
    if _is_running(exe):
        raise RunnerError(
            f"o terminal de teste ({cfg.terminal_dir}) está aberto: feche a janela dele. "
            "Com ele aberto o /config seria ignorado."
        )
    free = free_gb(cfg.terminal_dir)
    if free < cfg.min_free_gb:
        raise RunnerError(f"pouco espaco no disco: {free:.2f} GB livres (minimo {cfg.min_free_gb:g} GB). "
                          "Libere espaco ou ajuste tester.min_free_gb.")
    cfg_dir = cfg.terminal_dir / "Config"
    cfg_dir.mkdir(exist_ok=True)
    ini = cfg_dir / f"{name}.ini"
    write_ini(ini, build_ini(baseline, name, until, cfg.settings, cfg.experts, inputs, from_date))

    leftovers = [*cfg.terminal_dir.glob(f"{name}.*"), *cfg.terminal_dir.glob(f"{name}-*")]
    for f in leftovers:
        f.unlink()

    report = cfg.terminal_dir / f"{name}.htm"
    # Observado em 2026-11-17: o primeiro disparo apos um tempo parado pode acionar o LiveUpdate do
    # MT5, que fecha o terminal e o RELANCA com o mesmo /config (a instancia relancada roda o teste;
    # ver _wait_for_relaunch). So se nenhuma vier, tenta de novo uma vez.
    for attempt in (1, 2):
        t0 = time.monotonic()
        proc = subprocess.Popen([str(exe), "/portable", f"/config:{ini}"], cwd=cfg.terminal_dir)
        try:
            proc.wait(timeout=cfg.timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RunnerError(f"o teste passou de {cfg.timeout_s}s e foi encerrado ({name})") from None
        elapsed = time.monotonic() - t0
        if report.is_file() or _wait_for_relaunch(exe, report, cfg.timeout_s):
            break
        _stop_instances(exe)
        if attempt == 2:
            raise RunnerError(
                f"o terminal terminou em {elapsed:.0f}s sem gerar {report.name} (2 tentativas). Veja "
                f"{cfg.terminal_dir}\\logs e {cfg.terminal_dir}\\Tester\\logs"
            )
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / report.name
    shutil.move(str(report), dest)
    for f in [*cfg.terminal_dir.glob(f"{name}-*"), *cfg.terminal_dir.glob(f"{name}.png")]:
        f.unlink(missing_ok=True)   # gráficos que o Tester gera junto
    ini.unlink(missing_ok=True)
    c = purge_after_run(cfg.terminal_dir, cfg.max_terminal_mb)   # so em sucesso: em falha os logs ficam
    log(f"Limpeza: {c.freed_mb:.0f} MB liberados; terminal {c.terminal_mb:.0f} MB"
        + (" (historico baixado apagado)" if c.purged_history else "")
        + f"; C: livre {free_gb(cfg.terminal_dir):.2f} GB")
    for old in prune_reports(out_dir, cfg.keep_reports):
        log(f"Relatorio antigo removido: {old}")
    return dest


def run_and_read(baseline: Report, cfg: RunnerConfig, until: dt.date, name: str, out_dir: Path,
                 inputs: dict[str, str] | None = None,
                 log: Callable[[str], None] = lambda s: None,
                 from_date: dt.date | None = None) -> Report:
    report = read_report(run_backtest(baseline, cfg, until, name, out_dir, inputs, log, from_date))
    if not report.bars:
        # Barras 0 = o Tester nao teve dados para o simbolo/periodo; nao e "estrategia sem trades".
        raise RunnerError(f"o Tester rodou sem dados ({baseline.symbol} {report.date_from}..{report.date_to}): "
                          "relatorio com 0 barras")
    return report
