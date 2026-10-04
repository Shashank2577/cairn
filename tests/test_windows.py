"""Windows support for what Cairn writes (T066): git hooks, agent hook commands, `cairn up` / `cairn down`.

Three layers:
* text: with ``os.name`` patched to "nt" and a Windows ``sys.executable``, the generated script and commands are
  checked exactly (only string-building code runs while patched: on Python 3.12 a patched ``os.name`` turns
  every new ``Path`` into a ``WindowsPath``, so file-touching tests patch ``shellcmd.is_windows`` instead);
* execution: the generated sh runs under a real /bin/sh (and dash) with a shim at the literal relative path
  ``C:/Program Files/...``, proving the quoting is valid shell and that the hook never waits;
* real runs on every OS, including the Windows CI runner: git itself runs the installed hook, and ``cairn down``
  stops a real process found through ``ps`` / PowerShell CIM.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from cairn import agents, daemon, hooks, shellcmd, sync
from cairn.engines.recall import integrations as ig
from cairn.project import Project

WIN_PY = r"C:\Program Files\Python 3.12\python.exe"
WIN_SHORT = r"C:\PROGRA~1\PYTHON~1.12\python.exe"
# Legal in a Windows file name, and hostile to naive quoting: non-ASCII, ', &, $, backtick, parentheses.
NASTY_PY = "C:\\Users\\Zoë O'Brien & Co\\$HOME `id` (x86)\\python.exe"
REPO = Path(__file__).resolve().parents[1]
POSIX_SHELLS = [s for s in ("/bin/sh", shutil.which("dash")) if s and os.path.exists(s)]
on_windows = pytest.mark.skipif(os.name != "nt", reason="needs a real Windows machine (runs in CI)")
not_windows = pytest.mark.skipif(os.name == "nt", reason="simulates Windows paths as relative POSIX paths")

SHIM = """#!/bin/sh
sleep "${SHIM_DELAY:-0}"
printf '%s\\n' "$@" > "$SHIM_OUT.tmp" && mv "$SHIM_OUT.tmp" "$SHIM_OUT"
"""


@pytest.fixture()
def windows(monkeypatch):
    """Pretend to be Windows, for code that only builds strings: `cairn` not on PATH, a spaced interpreter path."""
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(sys, "executable", WIN_PY)
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(shellcmd, "short_path", lambda p: None)  # 8.3 names disabled, unless a test enables them
    monkeypatch.setattr(shellcmd, "has_git_bash", lambda: True)  # the usual Claude Code setup


@contextmanager
def as_windows(monkeypatch, short: str | None = None):
    """Windows only while the text is built; files are touched after the block (see module doc)."""
    with monkeypatch.context() as m:
        m.setattr(os, "name", "nt")
        m.setattr(shutil, "which", lambda *a, **k: None)
        m.setattr(shellcmd, "short_path", lambda p: short)
        m.setattr(shellcmd, "has_git_bash", lambda: True)
        yield


@pytest.fixture()
def windows_fs(monkeypatch):
    """Windows command spelling for code that also touches files (``os.name`` stays real, see module doc)."""
    monkeypatch.setattr(shellcmd, "is_windows", lambda: True)
    monkeypatch.setattr(shellcmd, "short_path", lambda p: None)
    monkeypatch.setattr(shellcmd, "has_git_bash", lambda: True)
    monkeypatch.setattr(sys, "executable", WIN_PY)


def shim_at(base: Path, windows_path: str) -> Path:
    """An executable standing in for a Windows interpreter: on POSIX, ``C:/...`` is a relative path under base."""
    shim = base / windows_path.replace("\\", "/")
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(SHIM, encoding="utf-8")
    shim.chmod(0o755)
    return shim


def wait_for(path: Path, timeout: float = 15.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise AssertionError(f"{path} was never written")


def run_sh(shell: str, script: Path, cwd: Path, out: Path, delay: float = 0) -> subprocess.CompletedProcess:
    env = {**os.environ, "SHIM_OUT": str(out), "SHIM_DELAY": str(delay)}
    return subprocess.run([shell, str(script)], cwd=cwd, env=env, capture_output=True, text=True, timeout=30,
                          check=False, encoding="utf-8", errors="replace")


# ---- shell quoting primitives ------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["plain", "with space", "$HOME", "`id`", 'say "hi"', "back\\slash", "O'Brien",
                                  "Zoë ümlaut", "a&b;c|d>e(f)", "*?[x]~"])
@pytest.mark.parametrize("shell", POSIX_SHELLS)
def test_posix_quote_round_trips_through_a_real_shell(shell, text):
    res = subprocess.run([shell, "-c", f"printf '%s' {shellcmd.posix_quote(text)}"], capture_output=True,
                         text=True, encoding="utf-8", check=True)
    assert res.stdout == text


def test_powershell_quote_doubles_every_single_quote_kind():
    assert shellcmd.powershell_quote(r"C:\a b\x.exe") == r"'C:\a b\x.exe'"
    assert shellcmd.powershell_quote("O'Brien") == "'O''Brien'"
    assert shellcmd.powershell_quote("O\u2019Brien") == "'O\u2019\u2019Brien'"  # PowerShell ends strings on ’ too
    assert shellcmd.powershell_quote("$env:X `n") == "'$env:X `n'"  # literal: no expansion inside '...'
    for bad in ("a\nb", "a\rb", "a\0b"):
        with pytest.raises(ValueError):
            shellcmd.powershell_quote(bad)
        with pytest.raises(ValueError):
            shellcmd.posix_quote(bad)


def test_neutral_paths(windows, monkeypatch):
    assert shellcmd.neutral_path(r"C:\Python312\python.exe") == "C:/Python312/python.exe"
    assert shellcmd.neutral_path(r"C:\Users\Zoë\venv\Scripts\python.exe") == "C:/Users/Zoë/venv/Scripts/python.exe"
    assert shellcmd.neutral_path(WIN_PY) is None  # spaces, and no short name
    monkeypatch.setattr(shellcmd, "short_path", lambda p: WIN_SHORT)
    assert shellcmd.neutral_path(WIN_PY) == "C:/PROGRA~1/PYTHON~1.12/python.exe"
    monkeypatch.setattr(shellcmd, "short_path", lambda p: r"C:\A&B\python.exe")
    assert shellcmd.neutral_path(NASTY_PY) is None
    with pytest.raises(ValueError):
        shellcmd.python_prefix(WIN_PY, "fish")


def test_short_path_is_only_asked_on_windows(monkeypatch):
    monkeypatch.setattr(shellcmd, "is_windows", lambda: False)
    assert shellcmd.short_path(WIN_PY) is None
    assert shellcmd.short_path(r"C:\no\such\dir\python.exe") is None  # missing path (or no windll): None


# ---- git hooks ---------------------------------------------------------------------------------------------
def test_git_hook_line_on_windows(windows):
    line = hooks.hook_line()
    assert line == ('"C:/Program Files/Python 3.12/python.exe" -m cairn hook git </dev/null >/dev/null 2>&1 & '
                    "# cairn-hook")
    assert "\\" not in line and not line.startswith("cairn ")  # forward slashes; never `cairn` from PATH
    assert hooks.hook_line(NASTY_PY) == ('"C:/Users/Zoë O\'Brien & Co/\\$HOME \\`id\\` (x86)/python.exe" -m cairn '
                                         "hook git </dev/null >/dev/null 2>&1 & # cairn-hook")


def test_git_hook_line_elsewhere_is_unchanged_in_shape(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/opt/py env/bin/python3")
    assert hooks.hook_line() == '"/opt/py env/bin/python3" -m cairn hook git </dev/null >/dev/null 2>&1 & # cairn-hook'


@not_windows
@pytest.mark.parametrize("python", [WIN_PY, NASTY_PY])
@pytest.mark.parametrize("shell", POSIX_SHELLS)
def test_windows_git_hook_is_valid_sh_and_never_waits(monkeypatch, tmp_path, shell, python):
    with as_windows(monkeypatch):
        line = hooks.hook_line(python)
    script = tmp_path / "post-commit"
    script.write_bytes(hooks.with_hook_line(None, line).encode("utf-8"))
    shim_at(tmp_path, python)
    out = tmp_path / "args.txt"
    started = time.time()
    res = run_sh(shell, script, tmp_path, out, delay=2)
    assert time.time() - started < 1.5, "the hook waited for the interpreter"
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""
    assert wait_for(out).splitlines() == ["-m", "cairn", "hook", "git"]


@pytest.mark.parametrize("shell", POSIX_SHELLS)
def test_git_hook_with_a_missing_interpreter_is_silent(tmp_path, shell):
    script = tmp_path / "post-commit"
    script.write_bytes(hooks.with_hook_line(None, hooks.hook_line(str(tmp_path / "gone" / "python"))).encode())
    res = subprocess.run([shell, str(script)], capture_output=True, text=True, timeout=30, check=False, encoding="utf-8", errors="replace")
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")


def _set_hooks_path(repo: Path, hooks_dir: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", hooks_dir.as_posix()], check=True)


def test_install_into_a_hooks_path_with_spaces_simulating_windows(repo, tmp_path, windows_fs):
    hooks_dir = tmp_path / "Team Hooks – ü"
    _set_hooks_path(repo, hooks_dir)
    proj = Project.discover(repo)
    assert hooks.install_git_hooks(proj) == list(hooks.GIT_EVENTS)
    line = hooks.hook_line()
    assert line.startswith('"C:/Program Files/Python 3.12/python.exe" -m cairn hook git')
    for ev in hooks.GIT_EVENTS:
        p = hooks_dir / ev
        assert p.read_bytes() == f"#!/bin/sh\n{line}\n".encode("utf-8")  # LF only, UTF-8, sh
        if os.name != "nt":  # Windows has no exec bit; git for Windows runs hooks through its own sh
            assert p.stat().st_mode & stat.S_IXUSR
    assert hooks.install_git_hooks(proj) == []  # idempotent
    assert hooks.remove_git_hooks(proj) == list(hooks.GIT_EVENTS)
    assert not any((hooks_dir / ev).exists() for ev in hooks.GIT_EVENTS)


def test_existing_hooks_are_upgraded_preserved_or_left_alone(repo, tmp_path, windows_fs):
    hooks_dir = tmp_path / "hooks dir"
    hooks_dir.mkdir()
    _set_hooks_path(repo, hooks_dir)
    user_bytes = "#!/bin/sh\r\necho 'mine – ü'\r\nexit 0\r\n".encode("utf-8") + b"\xff raw\r\n"
    (hooks_dir / "post-commit").write_bytes(user_bytes)
    (hooks_dir / "post-merge").write_text("#!/bin/sh\ncairn hook git >/dev/null 2>&1 & # cairn-hook\n", encoding="utf-8")  # old line
    python_hook = "#!/usr/bin/env python3\nprint('not sh')\n"
    (hooks_dir / "post-checkout").write_text(python_hook, encoding="utf-8")
    proj = Project.discover(repo)
    assert hooks.install_git_hooks(proj) == ["post-commit", "post-merge", "post-rewrite"]
    line = hooks.hook_line()
    commit = (hooks_dir / "post-commit").read_bytes()
    # Cairn's line goes right after the shebang (an `exit` further down can't skip it), in the file's CRLF style
    assert commit == b"#!/bin/sh\r\n" + line.encode() + b"\r\n" + user_bytes[len(b"#!/bin/sh\r\n"):]
    assert (hooks_dir / "post-merge").read_text(encoding="utf-8") == f"#!/bin/sh\n{line}\n"  # upgraded, not duplicated
    assert (hooks_dir / "post-checkout").read_text(encoding="utf-8") == python_hook  # another interpreter: untouched
    hooks.remove_git_hooks(proj)
    assert (hooks_dir / "post-commit").read_bytes() == user_bytes  # byte-exact
    assert not (hooks_dir / "post-merge").exists()


def test_with_hook_line_places_and_replaces():
    line = "LINE # cairn-hook"
    assert hooks.with_hook_line(None, line) == "#!/bin/sh\nLINE # cairn-hook\n"
    assert hooks.with_hook_line("#!/usr/bin/env bash\nset -e\n", line) == "#!/usr/bin/env bash\nLINE # cairn-hook\nset -e\n"
    assert hooks.with_hook_line("#!/bin/sh", line) == "#!/bin/sh\nLINE # cairn-hook\n"
    assert hooks.with_hook_line("echo no shebang\n", line) == "LINE # cairn-hook\necho no shebang\n"
    assert hooks.with_hook_line("#!C:/Program Files/Git/bin/sh.exe\n", line).count("cairn-hook") == 1
    assert hooks.with_hook_line("#!/usr/bin/env node\n", line) is None


def test_git_itself_runs_the_installed_hook(repo, tmp_path, monkeypatch):
    """The whole chain for real, on this OS (Git for Windows' sh on the Windows CI runner): git runs the hook,
    the hook runs the interpreter at a path with spaces and non-ASCII, and `-m cairn hook git` reaches Python."""
    fake = tmp_path / "fake site"
    (fake / "cairn").mkdir(parents=True)
    (fake / "cairn" / "__init__.py").write_text("", encoding="utf-8")
    (fake / "cairn" / "__main__.py").write_text(
        "import json, os, sys\n"
        "out = os.environ['CAIRN_FAKE_OUT']\n"
        "open(out + '.tmp', 'w', encoding='utf-8').write(json.dumps(sys.argv[1:]))\n"
        "os.replace(out + '.tmp', out)\n", encoding="utf-8")
    python = sys.executable
    if os.name != "nt":  # a spaced, non-ASCII interpreter path (symlinks need privileges on Windows)
        link_dir = tmp_path / "Py Thon – ü $x"
        link_dir.mkdir()
        (link_dir / "python3").symlink_to(sys.executable)
        python = str(link_dir / "python3")
    monkeypatch.setattr(sys, "executable", python)
    hooks_dir = tmp_path / "My Hooks – ü"
    _set_hooks_path(repo, hooks_dir)
    assert "post-commit" in hooks.install_git_hooks(Project.discover(repo))
    out = tmp_path / "ran.json"
    env = {**os.environ, "PYTHONPATH": str(fake), "CAIRN_FAKE_OUT": str(out)}
    started = time.time()
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "hook check"], env=env, check=True,
                   capture_output=True, timeout=60)
    assert time.time() - started < 30
    assert json.loads(wait_for(out, 30)) == ["hook", "git"]


# ---- agent hook commands -----------------------------------------------------------------------------------
def test_claude_code_commands_on_windows(windows, monkeypatch):
    # no 8.3 name: Git Bash (the default) form, forward slashes
    assert agents._hook_cmd("session-start") == '"C:/Program Files/Python 3.12/python.exe" -m cairn hook session-start'
    # a short name exists: unquoted, so Git Bash and the PowerShell fallback read it alike
    monkeypatch.setattr(shellcmd, "short_path", lambda p: WIN_SHORT)
    assert agents._hook_cmd("session-start") == "C:/PROGRA~1/PYTHON~1.12/python.exe -m cairn hook session-start"
    monkeypatch.setattr(sys, "executable", r"C:\Users\Zoë\venv\Scripts\python.exe")
    assert agents._hook_cmd("statusline") == "C:/Users/Zoë/venv/Scripts/python.exe -m cairn hook statusline"
    monkeypatch.setattr(shutil, "which", lambda *a, **k: r"C:\venv\Scripts\cairn.exe")
    assert agents._hook_cmd("session-start") == "cairn hook session-start"


def test_claude_code_commands_without_git_bash_use_powershell(windows, monkeypatch):
    """No Git Bash: Claude Code runs commands through PowerShell, so a path that needs quoting (spaces, no 8.3
    name) gets PowerShell's form; an unquoted form still works for both."""
    monkeypatch.setattr(shellcmd, "has_git_bash", lambda: False)
    assert agents._hook_cmd("session-start") == r"& 'C:\Program Files\Python 3.12\python.exe' -m cairn hook session-start"
    assert ig.hook_command("claude-code", "context", python=WIN_PY).startswith(r"& 'C:\Program Files\Python 3.12\python")
    monkeypatch.setattr(shellcmd, "short_path", lambda p: WIN_SHORT)
    assert agents._hook_cmd("session-start") == "C:/PROGRA~1/PYTHON~1.12/python.exe -m cairn hook session-start"


def test_git_bash_detection(monkeypatch, tmp_path):
    monkeypatch.setattr(shellcmd, "is_windows", lambda: False)
    assert shellcmd.has_git_bash() is False
    monkeypatch.setattr(shellcmd, "is_windows", lambda: True)
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH", raising=False)
    root = tmp_path / "Git"
    (root / "cmd").mkdir(parents=True)
    (root / "cmd" / "git.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(shellcmd.shutil, "which", lambda name: str(root / "cmd" / "git.exe"))
    assert shellcmd.has_git_bash() is False
    (root / "bin").mkdir()
    (root / "bin" / "bash.exe").write_text("", encoding="utf-8")
    assert shellcmd.has_git_bash() is True
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(tmp_path / "elsewhere" / "bash.exe"))
    assert shellcmd.has_git_bash() is False  # the override wins, and must exist
    monkeypatch.setattr(shellcmd.shutil, "which", lambda name: None)
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH")
    assert shellcmd.has_git_bash() is False


def test_uninstall_still_recognises_every_form():
    for cmd in ('"C:/Program Files/Python 3.12/python.exe" -m cairn hook session-start',
                "C:/PROGRA~1/PYTHON~1.12/python.exe -m cairn statusline", "cairn hook session-start",
                '"/opt/py env/bin/python3" -m cairn statusline'):
        assert agents._is_ours({"command": cmd}), cmd
    assert not agents._is_ours({"command": "C:/tools/my-cairn-helper.exe statusline --x"})


@not_windows
@pytest.mark.parametrize("short", [None, WIN_SHORT])
@pytest.mark.parametrize("shell", POSIX_SHELLS)
def test_claude_code_commands_run_under_sh(monkeypatch, tmp_path, shell, short):
    monkeypatch.setattr(sys, "executable", WIN_PY)
    with as_windows(monkeypatch, short):
        cmd = agents._hook_cmd("session-start")
    shim_at(tmp_path, short or WIN_PY)
    script = tmp_path / "cmd.sh"
    script.write_text(cmd + "\n", encoding="utf-8")
    out = tmp_path / "args.txt"
    assert run_sh(shell, script, tmp_path, out).returncode == 0
    assert wait_for(out).splitlines() == ["-m", "cairn", "hook", "session-start"]


def test_session_capture_commands_on_windows(windows, monkeypatch):
    claude = ig.claude_code_hooks(WIN_PY)["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert claude == '"C:/Program Files/Python 3.12/python.exe" -m cairn.capture --platform claude-code session-init'
    cursor = ig.cursor_hooks_json(WIN_PY)["hooks"]["beforeSubmitPrompt"][0]["command"]
    assert cursor == r"& 'C:\Program Files\Python 3.12\python.exe' -m cairn.capture --platform cursor session-init"
    windsurf = ig._windsurf_entry("pre_user_prompt", WIN_PY, None)["command"]
    assert windsurf.startswith(r"& 'C:\Program Files\Python 3.12\python.exe' -m cairn.capture --platform windsurf ")
    gemini, _ = ig._named_hook_groups(ig.GEMINI_CLI_PLATFORM, ig.GEMINI_CLI_EVENTS, WIN_PY, "powershell")
    assert all(g[0]["hooks"][0]["command"].startswith(r"& 'C:\Program Files\Python 3.12\python.exe' -m cairn.capture")
               for g in gemini.values())
    codex = ig.codex_hooks(WIN_PY)["hooks"]
    assert all(g["hooks"][0]["command"].startswith(r"& 'C:\Program Files\Python 3.12\python.exe' -m cairn.capture "
                                                   "--platform codex") for gs in codex.values() for g in gs)
    bare, warnings = ig._named_hook_groups(ig.ANTIGRAVITY_PLATFORM, ig.ANTIGRAVITY_EVENTS, WIN_PY, "bare")
    assert warnings and "spaces" in warnings[0]
    monkeypatch.setattr(shellcmd, "short_path", lambda p: WIN_SHORT)
    assert ig.hook_command("codex", "summarize", python=WIN_PY, style="portable") == \
        "C:/PROGRA~1/PYTHON~1.12/python.exe -m cairn.capture --platform codex summarize"
    assert ig.hook_command("antigravity", "summarize", python=WIN_PY, style="bare") == \
        "C:/PROGRA~1/PYTHON~1.12/python.exe -m cairn.capture --platform antigravity summarize"
    assert ig._space_free(WIN_PY) == (WIN_SHORT, None)
    assert ig.hook_command("cursor", "summarize", python="C:\\Users\\O\u2019Brien\\python.exe", style="powershell").startswith("& 'C:\\Users\\O\u2019\u2019Brien\\python.exe'")
    for cmd in (claude, cursor, windsurf, ig.hook_command("codex", "summarize", python=WIN_PY, style="portable")):
        assert ig.is_cairn_hook({"command": cmd})
    with pytest.raises(ValueError):
        ig.hook_command("codex", "summarize", python=WIN_PY, style="fish")


def test_gemini_install_writes_powershell_commands_on_windows(tmp_path, windows_fs):
    rep = ig.install_gemini_cli(home=tmp_path)
    assert not rep.get("errors") and rep.get("written")
    settings = json.loads((tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]
    assert commands and all(c.startswith(r"& 'C:\Program Files\Python 3.12\python.exe' -m cairn.capture")
                            for c in commands)


@not_windows
def test_commands_elsewhere_keep_their_posix_shape(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(sys, "executable", "/opt/py env/bin/python3")
    assert agents._hook_cmd("session-start") == '"/opt/py env/bin/python3" -m cairn hook session-start'
    for style in ("quoted", "powershell", "portable"):
        assert ig.hook_command("codex", "context", python="/opt/py env/bin/python3", style=style) == \
            '"/opt/py env/bin/python3" -m cairn.capture --platform codex context'


# ---- cairn up / cairn down ---------------------------------------------------------------------------------
def test_server_spawn_flags(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    assert daemon._spawn_kwargs() == {"creationflags": daemon.DETACHED_PROCESS | daemon.CREATE_NEW_PROCESS_GROUP}
    monkeypatch.setattr(os, "name", "posix")
    assert daemon._spawn_kwargs() == {"start_new_session": True}


def _fake_run(calls: list, stdout: str = "", missing: tuple = (), exc: Exception | None = None):
    def run(argv, **kw):
        calls.append((argv, kw))
        if argv[0] in missing:
            raise FileNotFoundError(argv[0])
        if exc:
            raise exc
        return subprocess.CompletedProcess(argv, 0, stdout, "")
    return run


def test_windows_process_check_uses_powershell_cim(monkeypatch):
    calls: list = []
    monkeypatch.setattr(daemon.subprocess, "run", _fake_run(
        calls, '"C:\\Program Files\\Python 3.12\\python.exe" -m cairn serve --port 4747\r\n'))
    monkeypatch.setattr(os, "name", "nt")
    assert daemon._is_cairn_server(4321) is True
    argv, kw = calls[0]
    assert argv[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
    assert argv[4] == "(Get-CimInstance Win32_Process -Filter 'ProcessId=4321').CommandLine"
    assert kw["creationflags"] == daemon.CREATE_NO_WINDOW and kw["timeout"] >= 10
    assert not any(tool in " ".join(argv) for tool in ("wmic", "tasklist", " ps "))


def test_windows_process_check_is_conservative(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    calls: list = []
    monkeypatch.setattr(daemon.subprocess, "run", _fake_run(calls, "notepad.exe notes.txt\r\n"))
    assert daemon._is_cairn_server(7) is False
    monkeypatch.setattr(daemon.subprocess, "run", _fake_run(calls, ""))  # another user's process: no CommandLine
    assert daemon._is_cairn_server(7) is False
    calls.clear()
    monkeypatch.setattr(daemon.subprocess, "run", _fake_run(calls, "python -m cairn serve", missing=("powershell.exe",)))
    assert daemon._is_cairn_server(7) is True and [c[0][0] for c in calls] == ["powershell.exe", "pwsh.exe"]
    monkeypatch.setattr(daemon.subprocess, "run", _fake_run(calls, missing=("powershell.exe", "pwsh.exe")))
    assert daemon._is_cairn_server(7) is False
    monkeypatch.setattr(daemon.subprocess, "run", _fake_run(calls, exc=subprocess.TimeoutExpired("powershell", 20)))
    assert daemon._is_cairn_server(7) is False
    assert daemon._is_cairn_server(0) is False


def test_cairn_down_survives_windows_kill_semantics(tmp_path, monkeypatch):
    """On Windows os.kill(pid, SIGTERM) is TerminateProcess, and a process that just exited raises a plain
    OSError (WinError 87), not ProcessLookupError."""
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    (tmp_path / "server.json").write_text(json.dumps({"pid": 4321, "port": 1}), encoding="utf-8")
    monkeypatch.setattr(daemon, "_is_cairn_server", lambda pid: True)
    sent = []

    def gone(pid, sig):
        sent.append((pid, sig))
        raise OSError(22, "The parameter is incorrect")
    monkeypatch.setattr(daemon.os, "kill", gone)
    assert daemon.stop() is False and sent == [(4321, signal.SIGTERM)]
    assert not (tmp_path / "server.json").exists()


def test_cairn_down_stops_a_real_server_process(tmp_path, monkeypatch):
    """For real on this OS (PowerShell CIM + TerminateProcess on the Windows CI runner)."""
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "cairn", "serve"])
    try:
        (tmp_path / "server.json").write_text(json.dumps({"pid": proc.pid, "port": 1}), encoding="utf-8")
        assert daemon._is_cairn_server(proc.pid)
        assert daemon.stop() is True
        assert proc.wait(timeout=30) is not None
    finally:
        if proc.poll() is None:
            proc.kill()


# ---- sync lock on Windows: msvcrt.locking stands in for flock (flock is POSIX-only) -----------------------
class _StubMsvcrt:
    """Stand-in for the msvcrt module: one holder of byte 0 at a time; a second LK_NBLCK on the region
    raises OSError exactly like the real thing when another handle holds it."""
    LK_NBLCK, LK_UNLCK = "NBLCK", "UNLCK"

    def __init__(self):
        self.calls: list[tuple[int, str, int]] = []
        self.held = False

    def locking(self, fd: int, mode: str, nbytes: int) -> None:
        self.calls.append((fd, mode, nbytes))
        if mode == self.LK_NBLCK:
            if self.held:
                raise OSError(13, "Permission denied")  # EACCES: what a contended region raises
            self.held = True
        else:
            self.held = False


def _stub_msvcrt(monkeypatch):
    import types
    stub = _StubMsvcrt()
    monkeypatch.setitem(sys.modules, "msvcrt",
                        types.SimpleNamespace(LK_NBLCK=stub.LK_NBLCK, LK_UNLCK=stub.LK_UNLCK, locking=stub.locking))
    return stub


def test_windows_sync_lock_logic_with_a_stubbed_msvcrt(monkeypatch, tmp_path):
    """The msvcrt path cannot execute on POSIX, so its logic is exercised by stubbing the module
    `sync._windows_lock` imports: lock byte 0 non-blocking, treat OSError as contention, unlock on release."""
    stub = _stub_msvcrt(monkeypatch)
    lock = tmp_path / "sync.lock"
    holder = open(lock, "w", encoding="utf-8")
    contender = open(lock, "w", encoding="utf-8")
    try:
        release = sync._windows_lock(holder, 0)
        assert release is not None
        assert stub.calls == [(holder.fileno(), "NBLCK", 1)]  # byte 0 of the lock file, non-blocking
        stub.held = True  # as if another process held the region now
        assert sync._windows_lock(contender, 0) is None  # contended, no wait configured: skip at once
        assert stub.held  # the contender never unlocked on the holder's behalf
        release()  # what locked()'s finally does
        assert stub.calls[-1] == (holder.fileno(), "UNLCK", 1)
    finally:
        contender.close()
        holder.close()


def test_windows_sync_lock_waits_for_release_with_a_stubbed_msvcrt(monkeypatch, tmp_path):
    import threading
    stub = _stub_msvcrt(monkeypatch)
    lock = tmp_path / "sync.lock"
    holder = open(lock, "w", encoding="utf-8")
    contender = open(lock, "w", encoding="utf-8")
    timer = threading.Timer(0.3, setattr, args=(stub, "held", False))
    try:
        assert sync._windows_lock(holder, 0) is not None
        timer.start()
        started = time.monotonic()
        release = sync._windows_lock(contender, 5)  # contended: retries until the holder lets go
        assert release is not None
        assert time.monotonic() - started >= 0.2
        release()
    finally:
        timer.join()
        contender.close()
        holder.close()


@on_windows
def test_real_windows_sync_lock_excludes_a_second_holder(tmp_path):
    """For real on the Windows CI runner: a second handle in this process cannot take byte 0."""
    lock = tmp_path / "sync.lock"
    with sync.locked(lock) as first:
        assert first is True
        with sync.locked(lock) as second:
            assert second is False


# ---- real Windows (CI) -------------------------------------------------------------------------------------
@on_windows
def test_real_windows_shells_run_each_form(tmp_path):
    """Each generated form, through the real shells, with an interpreter path that has spaces and non-ASCII
    (a directory junction to this Python's home)."""
    link = tmp_path / "Py Thon – ü"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), sys.base_prefix], check=True, capture_output=True)
    python = str(link / Path(sys.executable).name)
    candidates = [shutil.which("sh")]
    git = shutil.which("git")
    if git:  # Git for Windows: <root>/cmd/git.exe, with sh in <root>/bin or <root>/usr/bin
        candidates += [str(Path(git).resolve().parents[1] / d / "sh.exe") for d in ("bin", "usr/bin")]
    sh = next((c for c in candidates if c and os.path.exists(c)), None)
    assert sh, "Git for Windows' sh not found"

    def via_sh(cmd: str) -> subprocess.CompletedProcess:
        script = tmp_path / "cmd.sh"  # a file: sh's own command-line parsing never sees the quotes
        script.write_bytes((cmd + "\n").encode("utf-8"))
        return subprocess.run([sh, str(script)], capture_output=True, text=True, timeout=60, check=False, encoding="utf-8", errors="replace")

    def via_powershell(cmd: str) -> subprocess.CompletedProcess:
        return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
                              capture_output=True, text=True, timeout=60, check=False, encoding="utf-8", errors="replace")

    runs = [("posix", via_sh), ("gitbash", via_sh), ("powershell", via_powershell), ("unknown", via_powershell)]
    gitbash = shellcmd.python_prefix(python, "gitbash")
    if not gitbash.startswith('"'):  # unquoted or PowerShell form: must work when Claude Code uses PowerShell
        runs.append(("gitbash", via_powershell))
    for shell_name, runner in runs:
        cmd = f"{shellcmd.python_prefix(python, shell_name)} -m platform"
        res = runner(cmd)
        assert res.returncode == 0 and "Windows" in res.stdout, (shell_name, runner.__name__, cmd, res.stderr)


# ---- CI --------------------------------------------------------------------------------------------------------
def test_ci_runs_the_suite_on_all_three_systems():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["test"]
    assert set(job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "macos-latest", "windows-latest"}
    assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.12", "3.13"] and job["strategy"]["fail-fast"] is False
    steps = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert 'pip install -e ".[dev]"' in steps and "pytest" in steps
    import tomllib
    extras = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert any(d.startswith("pytest") for d in extras["dev"])
