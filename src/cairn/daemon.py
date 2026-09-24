"""Background daemon lifecycle: one local server per repository, loopback only."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from .project import Project


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.6) as r:
            return json.loads(r.read())
    except Exception:
        return None


def info(project: Project) -> dict | None:
    f = project.dir / "server.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
    except json.JSONDecodeError:
        return None
    h = _health(data.get("port", 0))
    if h and h.get("root") == str(project.root):
        return {**data, "url": f"http://127.0.0.1:{data['port']}"}
    return None


def start(project: Project, wait: float = 8.0) -> dict:
    cur = info(project)
    if cur:
        return cur
    port = int(project.cfg("server.port", 4747))
    while not _free(port):
        h = _health(port)
        if h and h.get("root") == str(project.root):
            break
        port += 1
    log = open(project.dir / "server.log", "a")
    kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000008}
    proc = subprocess.Popen([sys.executable, "-m", "cairn", "serve", "--port", str(port)], cwd=str(project.root),
                            stdout=log, stderr=log, stdin=subprocess.DEVNULL, **kwargs)
    (project.dir / "server.json").write_text(json.dumps({"pid": proc.pid, "port": port, "started": time.time()}))
    deadline = time.time() + wait
    while time.time() < deadline:
        if _health(port):
            return {"pid": proc.pid, "port": port, "url": f"http://127.0.0.1:{port}"}
        time.sleep(0.15)
    return {"pid": proc.pid, "port": port, "url": f"http://127.0.0.1:{port}", "starting": True}


def stop(project: Project) -> bool:
    f = project.dir / "server.json"
    if not f.exists():
        return False
    try:
        pid = json.loads(f.read_text()).get("pid")
        if pid:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, json.JSONDecodeError, PermissionError):
        pass
    f.unlink(missing_ok=True)
    return True
