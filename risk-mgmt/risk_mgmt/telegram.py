"""Telegram Bot API client - sendMessage/sendPhoto only. Reads the bot token and chat id
from environment variables ONLY (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) - never from YAML
or a CLI flag, so they can't accidentally end up committed in a config file.
"""

import os
from pathlib import Path

import requests

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_MAX_MESSAGE_LENGTH = 4096  # Telegram's hard cap on a single sendMessage's text


class TelegramConfigError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise TelegramConfigError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set as environment variables"
        )
    return token, chat_id


def _split_into_chunks(text: str, max_length: int = _MAX_MESSAGE_LENGTH) -> list[str]:
    """Splits `text` on line boundaries into chunks no longer than `max_length`. A
    monthly report with many months/symbols can easily exceed Telegram's per-message
    cap; a single line longer than max_length on its own is still emitted whole rather
    than corrupted mid-line, since that shouldn't happen with this module's own output.
    """
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 for the newline that joins it back
        if current and current_len + line_len > max_length:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def send_message(text: str) -> None:
    token, chat_id = _credentials()
    url = _API_BASE.format(token=token, method="sendMessage")
    for chunk in _split_into_chunks(text):
        resp = requests.post(url, data={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}, timeout=30)
        resp.raise_for_status()


def send_photo(path: str | Path, caption: str | None = None) -> None:
    token, chat_id = _credentials()
    url = _API_BASE.format(token=token, method="sendPhoto")
    with open(path, "rb") as f:
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        resp = requests.post(url, data=data, files={"photo": f}, timeout=60)
    resp.raise_for_status()
