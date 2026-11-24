"""Cliente mínimo da Bot API do Telegram (sendMessage). Token e chat vêm SÓ do ambiente
(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID), nunca de YAML versionado ou flag. O ambiente é preenchido
pelo Windows (`setx`) ou pelo arquivo `strategy-monitor/.env` (ignorado pelo git; ver envfile.py).
Segue o padrão do risk-mgmt, copiado de propósito: não há import entre projetos.
"""

import os
import re

import requests

_API = "https://api.telegram.org/bot{token}/sendMessage"
_MAX = 4096  # limite do Telegram por mensagem
_NAMES = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
# O Telegram aceita como chat_id um inteiro (positivo: conversa privada; negativo: grupo, ou -100... para
# supergrupo/canal) ou @nome_do_canal. O NOME DE USUARIO DE UM BOT (ex.: algo_bot) NAO e um chat_id.
_CHAT_ID_RE = re.compile(r"-?\d{5,20}|@[A-Za-z][A-Za-z0-9_]{4,31}")
_TOKEN_RE = re.compile(r"\d{5,15}:[A-Za-z0-9_-]{20,}")


class TelegramConfigError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    values = {n: (os.environ.get(n) or "").strip() for n in _NAMES}
    missing = [n for n, v in values.items() if not v]
    if missing:
        # Só os NOMES: nunca o valor de nenhuma credencial em mensagem de erro.
        raise TelegramConfigError(
            f"faltando: {', '.join(missing)}. Defina no arquivo strategy-monitor/.env "
            "(veja .env.example) ou como variável de ambiente."
        )
    # Formato conferido localmente, para o erro apontar a causa em vez de um "chat not found" da rede.
    # As mensagens dizem o que esperar, nunca repetem o valor.
    if not _TOKEN_RE.fullmatch(values["TELEGRAM_BOT_TOKEN"]):
        raise TelegramConfigError(
            "TELEGRAM_BOT_TOKEN não tem o formato do BotFather (<números>:<texto>, sem aspas nem espaços).")
    chat = values["TELEGRAM_CHAT_ID"]
    if not _CHAT_ID_RE.fullmatch(chat):
        hint = (" O valor parece o nome de usuário de um bot (termina em 'bot'), não o id do chat."
                if chat.lower().endswith("bot") and not chat.startswith("@") else "")
        raise TelegramConfigError(
            "TELEGRAM_CHAT_ID não parece um id de chat: esperado um número (ex.: 123456789, ou -100... "
            "para grupo/canal) ou @nome_do_canal." + hint +
            " Para achar o id: mande uma mensagem ao seu bot e abra no navegador "
            "https://api.telegram.org/bot<SEU_TOKEN>/getUpdates, procurando message.chat.id.")
    return values["TELEGRAM_BOT_TOKEN"], chat


def _redact(text: str, token: str) -> str:
    """O token vai na URL (`/bot<token>/sendMessage`), e as exceções do `requests` trazem a URL
    na mensagem. Sem isto, uma falha de rede imprimiria o token no terminal e em logs."""
    return text.replace(token, "<TOKEN>")


def split_chunks(text: str, limit: int = _MAX) -> list[str]:
    """Quebra em fronteiras de linha para não passar do limite nem cortar uma linha ao meio."""
    if len(text) <= limit:
        return [text]
    chunks, cur, n = [], [], 0
    for line in text.split("\n"):
        if cur and n + len(line) + 1 > limit:
            chunks.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def send_message(text: str, parse_mode: str | None = "HTML") -> None:
    token, chat = _credentials()
    for chunk in split_chunks(text):
        data = {"chat_id": chat, "text": chunk}
        if parse_mode:
            data["parse_mode"] = parse_mode
        try:
            resp = requests.post(_API.format(token=token), data=data, timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"falha de rede ao falar com o Telegram: {_redact(str(e), token)}") from None
        if not resp.ok:
            # O corpo da resposta diz o motivo (chat inválido, HTML mal formado...).
            raise RuntimeError(f"Telegram respondeu {resp.status_code}: {_redact(resp.text[:300], token)}")
