import pytest
import requests

from strategy_monitor import telegram
from strategy_monitor.envfile import EnvFileError, load_env_file, parse_env_text

TOKEN = "123456789:AAFakeTokenForTestsOnly_0123456789abcdef"   # fictício
CHAT = "-1001234567890"


def test_parse_handles_windows_line_endings_and_bom():
    """O .env do André tem CRLF: um \\r no fim do token faria o Telegram recusá-lo."""
    text = f"﻿TELEGRAM_BOT_TOKEN={TOKEN}\r\nTELEGRAM_CHAT_ID={CHAT}\r\n"
    got = parse_env_text(text)
    assert got == {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT}
    assert "\r" not in got["TELEGRAM_BOT_TOKEN"]


def test_parse_quotes_export_comments_and_blank_lines():
    text = "\n".join([
        "# comentário", "", "export A=1", "B = \"dois\"", "C='tres'", "D=quatro # inline",
        "E=\"com # dentro\"", "linha inválida sem igual", "1INVALIDO=x", "F=",
    ])
    assert parse_env_text(text) == {"A": "1", "B": "dois", "C": "tres", "D": "quatro",
                                    "E": "com # dentro", "F": ""}


def test_existing_environment_wins_over_the_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=do_arquivo\nB=do_arquivo\nC=do_arquivo\n", encoding="utf-8")
    env = {"A": "do_ambiente", "C": ""}                     # C vazia conta como ausente
    applied = load_env_file(f, env)
    assert env == {"A": "do_ambiente", "B": "do_arquivo", "C": "do_arquivo"}
    assert applied == ["B", "C"]                            # só nomes, nunca valores


def test_missing_default_file_is_fine_but_explicit_missing_is_an_error(tmp_path, monkeypatch):
    from strategy_monitor import envfile
    monkeypatch.setattr(envfile, "DEFAULT_ENV_PATH", tmp_path / "nao_existe" / ".env")
    assert load_env_file(None, {}) == []
    with pytest.raises(EnvFileError, match="não encontrado"):
        load_env_file(tmp_path / "outro.env", {})


def test_credentials_report_only_names_of_missing_variables(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(telegram.TelegramConfigError) as e:
        telegram._credentials()
    assert "TELEGRAM_CHAT_ID" in str(e.value) and "TELEGRAM_BOT_TOKEN" not in str(e.value)
    assert TOKEN not in str(e.value)


def test_credentials_are_stripped(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", f"  {TOKEN}\r")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", f"{CHAT} ")
    assert telegram._credentials() == (TOKEN, CHAT)


def test_network_failure_does_not_leak_the_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)

    def boom(url, **kw):   # o requests real põe a URL (com o token) na mensagem
        raise requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {url.split('.org')[1]}")

    monkeypatch.setattr(telegram.requests, "post", boom)
    with pytest.raises(RuntimeError) as e:
        telegram.send_message("oi")
    assert TOKEN not in str(e.value) and "<TOKEN>" in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_http_error_body_is_redacted(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)

    class R:
        ok, status_code, text = False, 401, f'{{"description":"Unauthorized {TOKEN}"}}'

    monkeypatch.setattr(telegram.requests, "post", lambda url, **kw: R())
    with pytest.raises(RuntimeError) as e:
        telegram.send_message("oi")
    assert "401" in str(e.value) and TOKEN not in str(e.value)


def test_cli_loads_the_env_file_before_running_telegram_test(tmp_path, monkeypatch, capsys):
    """Fim a fim sem rede: a CLI lê o .env (CRLF) e entrega ao cliente o token já limpo."""
    from strategy_monitor import cli
    for n in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setenv(n, "x")
        monkeypatch.delenv(n)                     # garante que o monkeypatch restaura o estado original
    f = tmp_path / "meu.env"
    f.write_bytes(f"TELEGRAM_BOT_TOKEN={TOKEN}\r\nTELEGRAM_CHAT_ID={CHAT}\r\n".encode())
    sent = {}
    posts = []

    class R:
        ok = True

    def fake_post(url, data=None, timeout=None):
        posts.append((url, data))
        return R()

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    assert cli.main(["--env-file", str(f), "telegram-test"]) == 0
    assert posts and posts[0][0] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert posts[0][1]["chat_id"] == CHAT
    out = capsys.readouterr().out
    assert TOKEN not in out and CHAT not in out


def test_cli_explicit_env_file_missing_exits_with_a_clear_message(tmp_path):
    from strategy_monitor import cli
    with pytest.raises(SystemExit, match="não encontrado"):
        cli.main(["--env-file", str(tmp_path / "nao_existe.env"), "baselines"])


@pytest.mark.parametrize("chat", ["123456789", "-987654321", "-1001234567890", "@meu_canal_x"])
def test_valid_chat_id_forms_are_accepted(monkeypatch, chat):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", chat)
    assert telegram._credentials() == (TOKEN, chat)


def test_bot_username_as_chat_id_is_diagnosed_without_echoing_it(monkeypatch):
    """Caso real do André (2026-11-23): TELEGRAM_CHAT_ID tinha o nome de usuário do bot."""
    bot_username = "fulano_teste_bot"          # fictício
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", bot_username)
    with pytest.raises(telegram.TelegramConfigError) as e:
        telegram._credentials()
    msg = str(e.value)
    assert "nome de usuário de um bot" in msg and "getUpdates" in msg
    assert bot_username not in msg and TOKEN not in msg


@pytest.mark.parametrize("bad", ["", "abc", "12.5", "meu chat", "https://t.me/x"])
def test_other_malformed_chat_ids_are_rejected(monkeypatch, bad):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", bad or " ")
    with pytest.raises(telegram.TelegramConfigError):
        telegram._credentials()


@pytest.mark.parametrize("bad", ["sem_dois_pontos", "123:curto", "abc:" + "x" * 30])
def test_malformed_token_is_rejected_without_echoing_it(monkeypatch, bad):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", bad)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT)
    with pytest.raises(telegram.TelegramConfigError) as e:
        telegram._credentials()
    assert bad not in str(e.value)
