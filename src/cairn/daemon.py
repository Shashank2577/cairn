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
import urllib.request

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


def info() -> dict | None:
    """The running server, or None."""
    f = _state_file()
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if _health(int(data.get("port", 0))):
        return {**data, "url": f"http://127.0.0.1:{data['port']}"}
    return None


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
    if cur:
        return {**cur, "project_id": pid, "project_url": project_url(cur["url"], pid)}
    port = ServerConfig.load().port
    while not _free(port):
        port += 1
    home = cairn_home()
    home.mkdir(parents=True, exist_ok=True)
    log = open(home / "server.log", "a", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "cairn", "serve", "--port", str(port)], cwd=str(home),
                            stdout=log, stderr=log, stdin=subprocess.DEVNULL, **_spawn_kwargs())
    _state_file().write_text(json.dumps({"pid": proc.pid, "port": port, "started": time.time()}), encoding="utf-8")
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + wait
    while time.time() < deadline:
        if _health(port):
            break
        time.sleep(0.15)
    return {"pid": proc.pid, "port": port, "url": url, "project_id": pid, "project_url": project_url(url, pid)}


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
