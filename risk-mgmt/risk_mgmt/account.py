"""Optional MT5 login config for --mode live. Same file convention as IdxSwing91_Python's
account.py (config/account.yaml, gitignored) - plus MT5_LOGIN/MT5_PASSWORD/MT5_SERVER env
vars, which take priority when set. The env vars exist so a GitHub Actions secret can be
injected without ever writing a plaintext credentials file onto the runner.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class AccountConfig:
    login: int | None = None
    password: str | None = None
    server: str | None = None
    path: str | None = None  # full path to terminal64.exe, only needed if MT5 can't find it itself

    def to_mt5_kwargs(self) -> dict:
        kwargs = {}
        if self.path:
            kwargs["path"] = self.path
        if self.login is not None:
            kwargs["login"] = self.login
        if self.password is not None:
            kwargs["password"] = self.password
        if self.server is not None:
            kwargs["server"] = self.server
        return kwargs


def _from_env() -> AccountConfig | None:
    login = os.environ.get("MT5_LOGIN")
    password = os.environ.get("MT5_PASSWORD")
    server = os.environ.get("MT5_SERVER")
    if not (login and password and server):
        return None
    return AccountConfig(login=int(login), password=password, server=server)


def load_account_config(path: str | Path) -> AccountConfig | None:
    """Env vars win if all three (MT5_LOGIN/MT5_PASSWORD/MT5_SERVER) are set. Otherwise
    falls back to `path` if it exists. Returns None if neither is available - callers
    should then fall back to whatever MT5 terminal is already open and logged in.
    """
    env_account = _from_env()
    if env_account is not None:
        return env_account

    path = Path(path)
    if not path.exists():
        return None

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return AccountConfig(
        login=data.get("login"),
        password=data.get("password"),
        server=data.get("server"),
        path=data.get("path"),
    )
