"""Clone and refresh the repositories a Cairn server manages. ``git`` runs as a subprocess, never via a shell.

Remote URLs are validated before git sees them: remote-helper syntax (``ext::…``), option-looking values and
embedded credentials are refused, and local sources (``file://``, server paths) need explicit permission.
Credentials belong in the server's git credential helper or SSH deploy keys, never in a stored URL.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from .errors import GitError, InvalidInput

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")
_HELPER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*::")
_SCP_RE = re.compile(r"^(?:[A-Za-z0-9._~-]+@)?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?:(?!//)[^\s]+$")
SECURE_SCHEMES = {"https", "ssh", "git+ssh", "ssh+git"}
INSECURE_SCHEMES = {"http", "git"}
CLONE_TIMEOUT = 1800
FETCH_TIMEOUT = 900


def looks_like_url(target: str) -> bool:
    """True for something git would treat as a remote (``scheme://…`` or ``user@host:path``)."""
    t = target.strip()
    if _SCHEME_RE.match(t):
        return True
    return bool(_SCP_RE.match(t)) and not Path(t).exists() and not re.match(r"^[A-Za-z]:[\\/]", t)


def validate_url(url: str, *, allow_local: bool = False, allow_insecure: bool = False) -> str:
    url = (url or "").strip()
    if not url or len(url) > 2048:
        raise InvalidInput("git URL is empty or too long")
    if url.startswith("-") or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise InvalidInput("git URL contains characters that are not allowed")
    if _HELPER_RE.match(url):
        raise InvalidInput("remote-helper URLs (transport::address) are not allowed")
    m = _SCHEME_RE.match(url)
    if m:
        scheme = m.group(1).lower()
        if scheme == "file":
            if not allow_local:
                raise InvalidInput("file:// sources are disabled on this server (server.allow_local_git)")
            return url
        if scheme in INSECURE_SCHEMES and not allow_insecure:
            raise InvalidInput(f"{scheme}:// is unencrypted; use https:// or ssh (or set server.allow_insecure_git)")
        if scheme not in SECURE_SCHEMES | INSECURE_SCHEMES:
            raise InvalidInput(f"unsupported git URL scheme {scheme!r}")
        parts = urlsplit(url)
        if not parts.hostname or parts.hostname.startswith("-"):
            raise InvalidInput("git URL has no valid host")
        if parts.password is not None or (scheme in ("http", "https") and parts.username is not None):
            raise InvalidInput("remove the credentials from the URL; use a git credential helper or an SSH "
                               "deploy key on the server — Cairn never stores secrets in plain text")
        return url
    if _SCP_RE.match(url):
        return url
    if allow_local and Path(url).is_absolute():
        return url
    raise InvalidInput("not a supported git URL (https://, ssh:// or user@host:path)")


def validate_branch(branch: str | None) -> str | None:
    if branch is None or not branch.strip():
        return None
    b = branch.strip()
    if (b.startswith(("-", "/")) or b.endswith(("/", ".", ".lock")) or ".." in b or "//" in b or "@{" in b
            or len(b) > 255 or not re.fullmatch(r"[A-Za-z0-9._/+-]+", b)):
        raise InvalidInput(f"invalid branch name {branch!r}")
    return b


def repo_name(url: str) -> str:
    tail = re.split(r"[/:]", url.rstrip("/"))[-1]
    return tail[:-4] if tail.endswith(".git") else tail or "repository"


def _env(allow_local: bool) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_CONFIG_PARAMETERS")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    env["GIT_ALLOW_PROTOCOL"] = "https:ssh:http:git" + (":file" if allow_local else "")
    return env


def run(args: list[str], *, cwd: Path | None = None, allow_local: bool = False,
        timeout: int = FETCH_TIMEOUT) -> subprocess.CompletedProcess:
    cmd = ["git", "-c", "protocol.ext.allow=never", "-c", "core.hooksPath=" + os.devnull, *args]
    try:
        return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False,
                              errors="replace", timeout=timeout, env=_env(allow_local), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout}s") from exc
    except OSError as exc:
        raise GitError(f"git is not available: {exc}") from exc


def _fail(what: str, res: subprocess.CompletedProcess) -> GitError:
    lines = [ln for ln in (res.stderr or res.stdout or "").strip().splitlines() if ln.strip()]
    return GitError(f"{what} failed: {' / '.join(lines[-3:]) or f'exit {res.returncode}'}")


def head(root: Path) -> str | None:
    res = run(["-C", str(root), "rev-parse", "--verify", "-q", "HEAD"])
    return res.stdout.strip() or None if res.returncode == 0 else None


def current_branch(root: Path) -> str | None:
    res = run(["-C", str(root), "symbolic-ref", "--short", "-q", "HEAD"])
    return res.stdout.strip() or None if res.returncode == 0 else None


def clone(url: str, dest: Path, *, branch: str | None = None, allow_local: bool = False,
          timeout: int = CLONE_TIMEOUT) -> str | None:
    """Clone into ``dest`` (created atomically); returns the checked-out branch."""
    if dest.exists() and any(dest.iterdir()):
        raise GitError(f"{dest} already exists and is not empty")
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = dest.parent / f".{dest.name}.clone-{secrets.token_hex(4)}"
    args = ["clone", "--quiet", "--no-recurse-submodules"]
    if branch:
        args += ["--branch", branch, "--single-branch"]
    res = run([*args, "--", url, str(tmp)], allow_local=allow_local, timeout=timeout)
    if res.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise _fail("git clone", res)
    if dest.exists():
        dest.rmdir()
    tmp.rename(dest)
    return current_branch(dest)


def refresh(root: Path, branch: str | None, *, allow_local: bool = False, timeout: int = FETCH_TIMEOUT) -> dict:
    """Make the clone match ``origin/<branch>`` exactly (fetch + forced checkout). Local state under ignored
    paths such as ``.cairn/`` is left alone."""
    if not (root / ".git").exists():
        raise GitError(f"{root} is not a git clone")
    old = head(root)
    branch = branch or current_branch(root)
    if not branch:
        raise GitError("cannot tell which branch to follow; set the project's branch")
    res = run(["-C", str(root), "fetch", "--prune", "--quiet", "--no-recurse-submodules", "origin",
               f"+refs/heads/{branch}:refs/remotes/origin/{branch}"], allow_local=allow_local, timeout=timeout)
    if res.returncode != 0:
        raise _fail("git fetch", res)
    res = run(["-C", str(root), "checkout", "--quiet", "--force", "-B", branch, f"refs/remotes/origin/{branch}"],
              allow_local=allow_local)
    if res.returncode != 0:
        raise _fail("git checkout", res)
    new = head(root)
    return {"branch": branch, "old": old, "new": new, "changed": old != new}
