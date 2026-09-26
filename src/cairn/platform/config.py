"""Where the platform keeps its state (``$CAIRN_HOME``) and how a Cairn server is configured.

Server settings come from, in increasing precedence: built-in defaults, ``$CAIRN_HOME/server.toml``
(``[server]`` table), overrides passed by the caller (for example a project's own ``[server]`` table),
and ``CAIRN_SERVER_MODE`` / ``CAIRN_SERVER_HOST`` / ``CAIRN_SERVER_PORT`` / ``CAIRN_PUBLIC_URL``.
"""
from __future__ import annotations

import ipaddress
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

LOCAL = "local"
TEAM = "team"
SERVER_FILE = "server.toml"

ENV_KEYS = {"mode": "CAIRN_SERVER_MODE", "host": "CAIRN_SERVER_HOST", "port": "CAIRN_SERVER_PORT",
            "public_url": "CAIRN_PUBLIC_URL"}


def cairn_home() -> Path:
    """User-level Cairn directory: ``$CAIRN_HOME`` or ``~/.cairn``."""
    env = os.environ.get("CAIRN_HOME")
    return Path(env).expanduser().resolve() if env else Path.home() / ".cairn"


def ensure_home(home: Path | None = None) -> Path:
    h = home or cairn_home()
    h.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(h, 0o700)
    except OSError:
        pass
    return h


def is_loopback(host: str) -> bool:
    h = host.strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"expected true/false, got {v!r}")


def _strs(v: Any) -> tuple[str, ...]:
    if isinstance(v, str):
        v = v.split(",")
    return tuple(s.strip() for s in (v or ()) if str(s).strip())


@dataclass(frozen=True)
class ServerConfig:
    mode: str = LOCAL                  # local: one developer, no login | team: login or bearer token required
    host: str = "127.0.0.1"
    port: int = 4747
    public_url: str = ""               # e.g. https://cairn.example.com — used for links, webhooks and origin checks
    allowed_hosts: tuple[str, ...] = ()    # Host header allow-list; local mode always allows loopback names
    allowed_origins: tuple[str, ...] = ()  # extra browser origins allowed to make state-changing requests
    cookie_secure: str = "auto"        # auto (Secure unless plain-http loopback) | true | false
    trust_proxy: bool = False          # honour X-Forwarded-For / X-Forwarded-Proto
    session_days: float = 14.0
    token_days: float = 90.0           # default API token lifetime; 0 = never expires
    invite_days: float = 7.0
    login_attempts: int = 10           # failed logins per client+email per window before 429
    login_window: int = 900            # seconds
    team_creation: str = "admins"      # admins | anyone — who may create new teams
    allow_local_git: bool = False      # permit file:// and server paths as git sources over HTTP
    allow_insecure_git: bool = False   # permit http:// and git:// remotes
    repos_dir: str = ""                # where cloned projects live; default $CAIRN_HOME/repos

    @property
    def team(self) -> bool:
        return self.mode == TEAM

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ServerConfig:
        kw: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            v = data[f.name]
            if f.type in ("bool",):
                v = _bool(v)
            elif f.type in ("int",):
                v = int(v)
            elif f.type in ("float",):
                v = float(v)
            elif f.type.startswith("tuple"):
                v = _strs(v)
            else:
                v = str(v).strip()
            kw[f.name] = v
        if "cookie_secure" in kw and kw["cookie_secure"].lower() in ("1", "yes", "on"):
            kw["cookie_secure"] = "true"
        if "cookie_secure" in kw and kw["cookie_secure"].lower() in ("0", "no", "off"):
            kw["cookie_secure"] = "false"
        cfg = cls(**kw)
        cfg.validate()
        return cfg

    @classmethod
    def load(cls, overrides: Mapping[str, Any] | None = None, *, home: Path | None = None,
             env: Mapping[str, str] | None = None) -> ServerConfig:
        env = os.environ if env is None else env
        data: dict[str, Any] = {}
        path = (home or cairn_home()) / SERVER_FILE
        if path.exists():
            try:
                data.update(tomllib.loads(path.read_text()).get("server", {}))
            except tomllib.TOMLDecodeError as exc:
                raise ValueError(f"{path}: {exc}") from exc
        if overrides:
            data.update(overrides)
        for key, var in ENV_KEYS.items():
            if env.get(var):
                data[key] = env[var]
        return cls.from_mapping(data)

    def validate(self) -> None:
        if self.mode not in (LOCAL, TEAM):
            raise ValueError(f"server.mode must be 'local' or 'team', not {self.mode!r}")
        if self.cookie_secure.lower() not in ("auto", "true", "false"):
            raise ValueError("server.cookie_secure must be auto, true or false")
        if self.team_creation not in ("admins", "anyone"):
            raise ValueError("server.team_creation must be 'admins' or 'anyone'")
        if not 0 < self.port < 65536:
            raise ValueError("server.port must be between 1 and 65535")
        if self.mode == LOCAL and not is_loopback(self.host):
            raise ValueError(f"local mode has no login, so it only listens on loopback; host {self.host!r} "
                             "needs server.mode = \"team\"")
        if self.public_url and not self.public_url.startswith(("http://", "https://")):
            raise ValueError("server.public_url must start with http:// or https://")
