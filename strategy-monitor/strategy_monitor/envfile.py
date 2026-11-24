"""Leitor mínimo de arquivo `.env` (sem dependência extra).

Existe para as credenciais do Telegram: elas ficam em `strategy-monitor/.env`, que o `.gitignore`
do projeto exclui, em vez de em YAML versionável ou numa flag de linha de comando.

Regras:
  - formato `CHAVE=valor`, uma por linha; aceita `export CHAVE=valor`, linhas em branco e `# comentário`;
  - valor entre aspas simples ou duplas perde as aspas; sem aspas, ` # ...` no fim é comentário;
  - tolera BOM UTF-8 e quebra de linha do Windows (CRLF): um `\\r` grudado no fim do token faria o
    Telegram recusá-lo;
  - **variável já definida no ambiente do processo vence** o arquivo (convenção dos leitores de .env),
    a menos que esteja vazia;
  - nunca imprime valores; funções devolvem só nomes.
"""

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class EnvFileError(RuntimeError):
    pass


def _value(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    m = re.search(r"\s#", v)          # comentário no fim de valor sem aspas
    return (v[: m.start()] if m else v).strip()


def parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.lstrip("﻿").splitlines():   # splitlines trata \r\n e \n
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _LINE.match(s)
        if m:
            out[m.group(1)] = _value(m.group(2))
    return out


def load_env_file(path: str | Path | None = None,
                  environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Carrega o `.env` em `environ` (default: os.environ) sem sobrescrever o que já está definido.
    Devolve os NOMES das variáveis efetivamente definidas por este arquivo.

    `path` None = `<projeto>/.env`, e a ausência dele é normal (devolve []). Um `path` explícito
    que não existe é erro."""
    env = os.environ if environ is None else environ
    p = Path(path) if path else DEFAULT_ENV_PATH
    if not p.is_file():
        if path:
            raise EnvFileError(f"arquivo de ambiente não encontrado: {p}")
        return []
    applied = []
    for k, v in parse_env_text(p.read_text(encoding="utf-8", errors="replace")).items():
        if not env.get(k):            # ausente ou vazia
            env[k] = v
            applied.append(k)
    return applied
