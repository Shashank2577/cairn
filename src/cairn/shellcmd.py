"""How Cairn spells "run this Python" for the shell that will read the command, on every OS.

Hosts read the commands Cairn writes in different ways:

* POSIX ``sh``: git hooks (Git for Windows runs them through its bundled sh too), and on macOS/Linux every
  agent's hook commands;
* ``gitbash``: Claude Code's hooks and status line on Windows, run through Git Bash, or through PowerShell when
  Git Bash isn't installed (checked when the command is written);
* ``powershell``: Cursor, Windsurf and Gemini CLI hooks on Windows;
* ``unknown``: a host whose Windows shell is the user's session shell (Codex: usually PowerShell).

On Windows a path is written with forward slashes for sh (Git Bash treats unquoted backslashes as escapes); as a
single-quoted literal behind PowerShell's call operator for PowerShell; and, where one text must work under
several shells, unquoted with forward slashes, using the 8.3 short name when the path has spaces. Standard
library only, so every engine can import it.
"""
from __future__ import annotations

import os
import re
import shutil

SHELLS = ("posix", "gitbash", "powershell", "unknown")

# A Windows path every shell (sh, PowerShell) reads as one word without quotes: drive, then word characters
# (Unicode letters and digits included), dots, dashes, plus signs and tildes.
_NEUTRAL = re.compile(r"[A-Za-z]:(?:/[\w.+~-]+)+")
# PowerShell ends a single-quoted string at any of these, so each is doubled inside one.
_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def is_windows() -> bool:
    return os.name == "nt"


def _one_line(text: str) -> str:
    if any(c in text for c in "\r\n\0"):
        raise ValueError(f"cannot put {text!r} on one command line")
    return text


def posix_quote(text: str) -> str:
    """Double-quoted for POSIX sh: ``$``, backtick, ``"`` and ``\\`` are escaped; nothing else is special."""
    return '"' + re.sub(r'([$`"\\])', r"\\\1", _one_line(text)) + '"'


def powershell_quote(text: str) -> str:
    """A PowerShell literal string: single-quoted with every kind of single quote doubled, so nothing expands."""
    return "'" + "".join(c * 2 if c in _PS_QUOTES else c for c in _one_line(text)) + "'"


def forward_slashes(path: str) -> str:
    """Windows separators as forward slashes (Git Bash, PowerShell and CreateProcess all accept them)."""
    return path.replace("\\", "/") if is_windows() else path


def short_path(path: str) -> str | None:
    """The 8.3 short form of an existing Windows path (``C:\\PROGRA~1\\...``), or None when there is none
    (not Windows, 8.3 names disabled on the volume, or the path doesn't exist)."""
    if not is_windows():
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))  # type: ignore[attr-defined]
    except (OSError, AttributeError, ValueError):
        return None
    return buf.value if 0 < n < len(buf) else None


def has_git_bash() -> bool:
    """Whether Claude Code will find Git Bash on this Windows machine: its ``CLAUDE_CODE_GIT_BASH_PATH`` override,
    or ``bash.exe`` in the Git for Windows installation that provides ``git``. Without it, Claude Code runs
    commands through PowerShell."""
    if not is_windows():
        return False
    override = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if override:
        return os.path.isfile(override)
    git = shutil.which("git")
    if not git:
        return False
    root = os.path.dirname(os.path.dirname(os.path.realpath(git)))  # <root>/cmd/git.exe or <root>/bin/git.exe
    return any(os.path.isfile(os.path.join(root, *rel, "bash.exe")) for rel in (("bin",), ("usr", "bin")))


def neutral_path(path: str) -> str | None:
    """A Windows path in a form sh and PowerShell both read unquoted (forward slashes; the short name when the
    long one has spaces or other special characters), or None when it has no such form."""
    for candidate in (path, short_path(path)):
        if candidate and _NEUTRAL.fullmatch(fwd := candidate.replace("\\", "/")):
            return fwd
    return None


def python_prefix(python: str, shell: str) -> str:
    """The start of a command line that runs ``python`` when ``shell`` reads it; append ``-m module args``
    (plain words only). On macOS and Linux every shell here is sh, so it is always POSIX-quoted there."""
    if shell not in SHELLS:
        raise ValueError(f"unknown shell {shell!r}; expected one of {', '.join(SHELLS)}")
    if not is_windows():
        return posix_quote(python)
    if shell == "posix":
        return posix_quote(forward_slashes(python))
    if shell == "powershell":
        return "& " + powershell_quote(python)
    neutral = neutral_path(python)
    if neutral:
        return neutral
    # No unquoted form: quote for the shell the host will actually use. Claude Code uses Git Bash when it is
    # installed and PowerShell when it isn't; the other hosts use PowerShell.
    if shell == "gitbash" and has_git_bash():
        return posix_quote(forward_slashes(python))
    return "& " + powershell_quote(python)
