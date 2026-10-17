"""The local server: ONE background process serving every repository registered on this machine.

`cairn ui` / `cairn up` in any repository registers it with the platform (idempotent) and makes sure the
server is running; a second repository reuses the same server and port. State: `$CAIRN_HOME/server.json`.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .platform import Platform, ServerConfig
from .platform.config import cairn_home
from .project import Project

# Windows process-creation flags (named here: the subprocess constants only exist on Windows).
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _state_file():
    return cairn_home() / "server.json"


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.8) as r:
            data = json.loads(r.read())
            return data if data.get("ok") else None
    except (OSError, ValueError):
        return None


def _ui_ok(port: int) -> bool:
    """The server's own page loads. `/api/health` keeps answering when the files behind the page are gone."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.5) as r:
            return r.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def new_record(pid: int, port: int) -> dict:
    """What a server writes about itself at start: where its code lives, which version and interpreter it runs."""
    return {"pid": pid, "port": port, "started": time.time(), "code_root": str(Path(__file__).resolve().parent),
            "version": __version__, "python": sys.executable, "path": os.environ.get("PATH", "")}


RESTART_FIX = ("run `cairn up` (it replaces the server; every repository on this machine is interrupted "
               "for a few seconds)")


def verdict(record: dict, version: str = __version__, ui_ok: bool = True) -> dict | None:
    """None for a server that can be trusted; otherwise why it cannot, and the one command that fixes it.

    A record written by an older Cairn has no code location, so it is judged by whether its page loads."""
    root, python = record.get("code_root"), record.get("python")
    if root and not Path(root).exists():
        return {"stale": "code-missing", "reason": f"the server runs from code that no longer exists ({root})",
                "fix": RESTART_FIX}
    if python and not Path(python).exists():
        return {"stale": "python-missing", "reason": f"the server's Python no longer exists ({python})",
                "fix": RESTART_FIX}
    if record.get("version") and record["version"] != version:
        return {"stale": "version-differs", "fix": RESTART_FIX,
                "reason": f"the server is version {record['version']} but the installed cairn is {version}"}
    if not ui_ok:
        return {"stale": "ui-broken", "fix": RESTART_FIX,
                "reason": "the server answers but its page does not load (its files may have been removed)"}
    return None


def info() -> dict | None:
    """The running server (with a `stale` verdict, None when it can be trusted), or None."""
    f = _state_file()
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    port = int(data.get("port", 0))
    if _health(port):
        return {**data, "url": f"http://127.0.0.1:{port}", "stale": verdict(data, ui_ok=_ui_ok(port))}
    return None


def _listeners(lo: int, hi: int) -> dict[int, int]:
    """Loopback ports lo..hi that something listens on, as {port: pid}. POSIX only (`lsof`); anywhere else, or
    without `lsof`, nothing is reported rather than guessed."""
    if os.name == "nt":
        return {}
    try:
        res = subprocess.run(["lsof", "-nP", f"-iTCP:{lo}-{hi}", "-sTCP:LISTEN", "-Fpn"], capture_output=True,
                             text=True, timeout=5, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    found: dict[int, int] = {}
    pid = 0
    for line in res.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid:
            port = line.rsplit(":", 1)[-1]
            if port.isdigit():
                found[int(port)] = pid
    return found


def stray_servers() -> list[dict]:
    """Cairn servers running on the usual ports that the record does not know about: a server left from an older
    install or a deleted worktree. `cairn down` cannot reach them, and one on the default port is the page a
    bookmark opens."""
    f = _state_file()
    try:
        known = int(json.loads(f.read_text(encoding="utf-8")).get("pid") or 0) if f.exists() else 0
    except (ValueError, json.JSONDecodeError):
        known = 0
    first = ServerConfig.load().port
    out = []
    for port, pid in sorted(_listeners(first, first + 20).items()):
        if pid == known:
            continue
        cmd = _command_line(pid)
        if "cairn serve" in cmd:
            out.append({"port": port, "pid": pid, "command": cmd.strip()[:160]})
    return out


def stop_strays() -> list[int]:
    """Stop every stray Cairn server; returns the ports freed."""
    return [s["port"] for s in stray_servers() if _terminate(s["pid"])]


def stop_legacy(project: Project) -> bool:
    """Earlier versions ran one server per repository (state in `<repo>/.cairn/server.json`). Stop such a
    leftover server so it can't hold the repository's stores open, and forget it."""
    f = project.dir / "server.json"
    if not f.exists():
        return False
    try:
        pid = int(json.loads(f.read_text(encoding="utf-8")).get("pid") or 0)
    except (ValueError, json.JSONDecodeError):
        pid = 0
    stopped = False
    if pid and _is_cairn_server(pid):
        stopped = _terminate(pid)
    f.unlink(missing_ok=True)
    return stopped


def register(project: Project) -> str:
    """Add this repository to the local project list; returns its project id (stable per path)."""
    platform = Platform(config=ServerConfig.load())
    try:
        platform.ensure_local_owner()
        return platform.register_local_project(project.root).id
    finally:
        platform.close()


def project_url(base: str, project_id: str | None) -> str:
    return f"{base}/#/p/{project_id}/overview" if project_id else base


def start(project: Project | None = None, wait: float = 10.0) -> dict:
    if project is not None:
        stop_legacy(project)
    pid = register(project) if project is not None else None
    cur = info()
    replaced = None
    if cur and not cur.get("stale"):
        return {**cur, "project_id": pid, "project_url": project_url(cur["url"], pid)}
    old_port = 0
    if cur:  # running but untrustworthy: replace it rather than reuse it
        replaced = cur["stale"]
        old_port = int(cur.get("port") or 0)
        stop()
        deadline = time.time() + 8  # the old process needs a moment to let go of its port
        while old_port and not _free(old_port) and time.time() < deadline:
            time.sleep(0.2)
    port = old_port if old_port and _free(old_port) else ServerConfig.load().port  # keep the address bookmarks use
    while not _free(port):
        port += 1
    home = cairn_home()
    home.mkdir(parents=True, exist_ok=True)
    log = open(home / "server.log", "a", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "cairn", "serve", "--port", str(port)], cwd=str(home),
                            stdout=log, stderr=log, stdin=subprocess.DEVNULL, **_spawn_kwargs())
    _state_file().write_text(json.dumps(new_record(proc.pid, port)), encoding="utf-8")
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + wait
    while time.time() < deadline:
        if _health(port):
            break
        time.sleep(0.15)
    out = {"pid": proc.pid, "port": port, "url": url, "project_id": pid, "project_url": project_url(url, pid)}
    if replaced:
        out["replaced"] = replaced
    return out


def _spawn_kwargs() -> dict:
    """Detach the server from this terminal: its own session on POSIX; on Windows a detached process (no console
    window) in its own process group, so Ctrl+C in this terminal never reaches it."""
    if os.name == "nt":
        return {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _command_line(pid: int) -> str:
    """The full command line of process ``pid``, or '' when it can't be read. POSIX: ``ps``. Windows has no ``ps``
    (and ``wmic`` is gone from Windows 11): PowerShell's CIM ``Win32_Process``, which is empty for processes of
    other users, so those are never mistaken for ours."""
    if os.name == "nt":
        script = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"
        attempts = [([exe, "-NoProfile", "-NonInteractive", "-Command", script], {"creationflags": CREATE_NO_WINDOW})
                    for exe in ("powershell.exe", "pwsh.exe")]
        timeout = 20
    else:
        attempts = [(["ps", "-o", "command=", "-p", str(int(pid))], {})]
        timeout = 5
    for argv, kwargs in attempts:
        try:
            res = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout, check=False,
                                 stdin=subprocess.DEVNULL, **kwargs, encoding="utf-8")
        except FileNotFoundError:
            continue  # try the next shell
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return res.stdout if res.returncode == 0 else ""
    return ""


def _is_cairn_server(pid: int) -> bool:
    return pid > 0 and "cairn serve" in _command_line(pid)


def _terminate(pid: int) -> bool:
    """Stop the process. On Windows ``os.kill`` with SIGTERM is TerminateProcess (there is no signal delivery;
    SQLite's journal keeps the stores consistent); a process that is already gone raises a plain OSError there."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:  # gone meanwhile (ProcessLookupError on POSIX), or not ours to stop (PermissionError)
        return False


def stop() -> bool:
    """Stop the server this machine started (never another process that happens to reuse its pid)."""
    f = _state_file()
    if not f.exists():
        return False
    try:
        pid = int(json.loads(f.read_text(encoding="utf-8")).get("pid") or 0)
    except (ValueError, json.JSONDecodeError):
        pid = 0
    stopped = _terminate(pid) if pid and _is_cairn_server(pid) else False
    f.unlink(missing_ok=True)
    return stopped
