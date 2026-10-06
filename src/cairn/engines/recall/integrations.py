"""Agent integrations for Recall: per-agent hook installers, MCP config writers and the bundled skills.

Every agent gets the hook configuration it natively loads, and every hook command runs Cairn's hook
entry: ``"<python>" -m cairn.capture --platform <platform> <event>``. Installs are idempotent merges:
the entries Cairn adds are marked (their command runs ``-m cairn.capture``, or the hook ``name`` is
``cairn``; the MCP server key is ``cairn``; Markdown context sits between ``<cairn-context>`` tags), so
uninstall removes exactly those, other entries are preserved, and a config file that cannot be parsed
is never overwritten. Standard library only.

Agents: claude-code (entries for ``.claude/settings.json``, merged by the caller), cursor, codex
(a local plugin marketplace), windsurf, opencode (a JS plugin), antigravity (``agy``), gemini-cli, and
the MCP-only IDEs copilot-cli, roo-code, warp and goose. ``cairn init`` uses the per-repository
installers at the end of this module (``install_project`` / ``uninstall_project``).
"""
from __future__ import annotations

import argparse
import contextlib
import contextvars
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .folders import (  # noqa: F401
    CURSOR_PLACEHOLDER,
    cursor_registry_path,
    inject_context_into_markdown_file,
    read_cursor_registry,
    register_cursor_project,
    to_bmp_safe,
    unregister_cursor_project,
)
from .folders import update_cursor_context_for_project as _folders_update_cursor_context
from ... import shellcmd
from .projects import find_store_root, project_context
from .settings import cairn_home

HOOK_MODULE = "cairn.capture"
MARK = "cairn"  # the hook name / MCP server key Cairn owns
CAPTURE_EVENTS = ("context", "session-init", "observation", "file-context", "summarize", "session-end",
                  "user-message", "file-edit")
CONTEXT_TAG_OPEN = "<cairn-context>"
CONTEXT_TAG_CLOSE = "</cairn-context>"
PLACEHOLDER_CONTEXT = """# Cairn: Cross-Session Memory

*No context yet. Complete your first session and context will appear here.*

Use Cairn's MCP search tools (recall_search, recall_timeline, get_observations) for manual memory queries."""
DEFAULT_MCP_COMMAND = "cairn"
DEFAULT_MCP_ARGS = ("mcp",)


class IntegrationError(RuntimeError):
    """An install/uninstall step that cannot proceed."""


class CorruptConfigError(IntegrationError):
    """A config file exists but cannot be parsed; it is left untouched."""


# ---- small helpers -----------------------------------------------------------------------------------------
def _home(home: Path | str | None) -> Path:
    return Path(home) if home else Path.home()


def _root(root: Path | str | None) -> Path:
    return Path(root) if root else Path.cwd()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _report(agent: str) -> dict:
    return {"agent": agent, "ok": True, "written": [], "removed": [], "notes": []}


def _fail(report: dict, error: str) -> dict:
    report["ok"] = False
    report["error"] = error
    return report


def _strip_bom(text: str) -> str:
    return text.removeprefix("\ufeff")


def read_json(path: Path | str, default: Any = None, *, empty_ok: bool = False) -> Any:
    """Parse a JSON config (a UTF-8 BOM is tolerated). Missing file -> a copy of ``default``; an
    unparseable file raises ``CorruptConfigError`` so the caller never overwrites it. ``empty_ok``
    treats a zero-byte/blank file as ``default`` (hosts that create empty placeholder files)."""
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default)
    try:
        text = _strip_bom(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CorruptConfigError(f"Cannot read {path}: {exc}") from exc
    if empty_ok and not text.strip():
        return copy.deepcopy(default)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CorruptConfigError(f"Corrupt JSON in {path}, refusing to overwrite") from exc


def write_json(path: Path | str, data: Any) -> None:
    """Write JSON (2-space indent, trailing newline) atomically: temp file + rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---- hook commands -----------------------------------------------------------------------------------------
def _space_free(path: str) -> tuple[str, str | None]:
    """A path usable in a command a host splits on spaces (no shell): Windows 8.3 short path when the
    path has a space; otherwise the path itself plus a warning."""
    if " " not in path:
        return path, None
    short = shellcmd.short_path(path)
    if short and " " not in short:
        return short, None
    return path, (f"hook path contains spaces: {path}; this agent splits commands on spaces, so hooks may fail "
                  "to launch. Use a Python/venv path without spaces.")


# The shell each style's host reads its hook commands with on Windows (on macOS/Linux, every host uses sh).
_STYLE_SHELL = {"quoted": "gitbash", "powershell": "powershell", "portable": "unknown"}


# Hooks installed for every repository (`cairn global install`) carry ``--scope user``: they stand aside in a
# repository whose own config wires the same agent, so set-up repositories never record an event twice.
_SCOPE: contextvars.ContextVar[str] = contextvars.ContextVar("cairn_hook_scope", default="project")


@contextlib.contextmanager
def user_scope():
    """Spell every hook command written inside this block for user-level (all repositories) config."""
    token = _SCOPE.set("user")
    try:
        yield
    finally:
        _SCOPE.reset(token)


def hook_command(platform: str, event: str, *, python: str | None = None, style: str = "quoted") -> str:
    """The command an agent runs for one hook event: ``<python> -m cairn.capture --platform P E``, spelled for
    the shell the host reads it with.

    On macOS/Linux every style except ``bare`` is ``"<python>" ...`` (POSIX-quoted). On Windows:
    ``quoted`` (Claude Code: Git Bash, or PowerShell without it): an unquoted forward-slash path that both read
    alike (8.3 short name if it has spaces), else quoted for whichever of the two this machine will use;
    ``powershell`` (Cursor, Windsurf, Gemini CLI): ``& '<python>' ...``, PowerShell's call operator on a literal;
    ``portable`` (Codex, whose shell is the session's, usually PowerShell): the unquoted form, else PowerShell's;
    ``bare`` (hosts that split on spaces without a shell and keep quotes literally): unquoted, forward slashes.
    """
    if event not in CAPTURE_EVENTS:
        raise ValueError(f"unknown capture event: {event}")
    py = python or sys.executable
    scope = " --scope user" if _SCOPE.get() == "user" else ""
    tail = f"-m {HOOK_MODULE} --platform {platform}{scope} {event}"
    if style == "bare":
        return f"{_space_free(py)[0].replace(chr(92), '/')} {tail}"
    if style not in _STYLE_SHELL:
        raise ValueError(f"unknown hook command style: {style}")
    return f"{shellcmd.python_prefix(py, _STYLE_SHELL[style])} {tail}"


def is_cairn_hook(entry: Any) -> bool:
    """True for a hook entry Cairn added (named ``cairn`` or running ``-m cairn.capture``)."""
    if not isinstance(entry, dict):
        return False
    return entry.get("name") == MARK or f"-m {HOOK_MODULE}" in str(entry.get("command") or "")


def _without_cairn_groups(groups: Any) -> tuple[list, int]:
    """Drop Cairn's hooks from ``[{matcher, hooks: [...]}]`` groups; a group left empty goes too."""
    if not isinstance(groups, list):
        return [], 0
    kept, removed = [], 0
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            kept.append(group)
            continue
        hooks = [h for h in group["hooks"] if not is_cairn_hook(h)]
        removed += len(group["hooks"]) - len(hooks)
        if hooks:
            kept.append({**group, "hooks": hooks})
        elif not group["hooks"]:
            kept.append(group)
    return kept, removed


def merge_hook_groups(existing: dict | None, new: dict) -> dict:
    """Merge ``event -> [{matcher, hooks}]`` maps (Claude Code / Gemini / Antigravity shape): Cairn's
    earlier entries are replaced, every other entry is kept. Idempotent."""
    out = dict(existing or {})
    for event, groups in new.items():
        kept, _ = _without_cairn_groups(out.get(event, []))
        out[event] = kept + copy.deepcopy(groups)
    return out


def remove_hook_groups(existing: dict | None) -> tuple[dict, int]:
    """Remove Cairn's entries from an ``event -> [{matcher, hooks}]`` map; empty events are dropped."""
    out, total = {}, 0
    for event, groups in (existing or {}).items():
        if not isinstance(groups, list):
            out[event] = groups
            continue
        kept, removed = _without_cairn_groups(groups)
        total += removed
        if kept or not removed:
            out[event] = kept if removed else groups
    return out, total


def _cairn_events(hooks: dict | None) -> list[str]:
    found = []
    for event, groups in (hooks or {}).items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) and "hooks" in group else [group]
            if isinstance(entries, list) and any(is_cairn_hook(h) for h in entries):
                found.append(event)
                break
    return found


# ---- markdown context blocks -------------------------------------------------------------------------------
_CONTEXT_BLOCK = re.compile(r"\n?<cairn-context>[\s\S]*?</cairn-context>\n?")


def remove_context_block(path: Path | str, *, delete_if_empty: bool = True, header_line: str | None = None) -> bool:
    """Remove the ``<cairn-context>`` block; the file goes too when nothing (or only ``header_line``) is left."""
    path = Path(path)
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    if not _CONTEXT_BLOCK.search(content):
        return False
    content = _CONTEXT_BLOCK.sub("\n", content, count=1)
    rest = content.strip()
    if delete_if_empty and (not rest or (header_line and rest == header_line.strip())):
        path.unlink()
    else:
        path.write_text(rest + "\n", encoding="utf-8")
    return True


def has_context_block(path: Path | str) -> bool:
    path = Path(path)
    try:
        return path.exists() and CONTEXT_TAG_OPEN in path.read_text(encoding="utf-8")
    except OSError:
        return False


def fetch_project_context(workspace: Path | str, project: str | None = None) -> str | None:
    """The startup context for a workspace's project (what the context hook would inject), or None when
    the workspace has no session store yet or the context is empty."""
    root = find_store_root(str(workspace))
    if root is None or not (root / ".cairn" / "sessions.db").exists():
        return None
    try:
        from .context import generate_context
        text = generate_context(root, cwd=str(workspace), projects=[project] if project else None)
    except Exception:  # noqa: BLE001 - context for an agent file is best effort
        return None
    return text if text and text.strip() else None


# ---- MCP config writers ------------------------------------------------------------------------------------
def mcp_server_entry(command: str = DEFAULT_MCP_COMMAND, args: Iterable[str] = DEFAULT_MCP_ARGS) -> dict:
    return {"command": command, "args": list(args)}


def write_mcp_json_config(path: Path | str, command: str = DEFAULT_MCP_COMMAND,
                          args: Iterable[str] = DEFAULT_MCP_ARGS, servers_key: str = "mcpServers",
                          *, empty_ok: bool = False, extra: dict | None = None) -> bool:
    """Add/refresh ``<servers_key>.cairn`` in a JSON MCP config, keeping every other key. Returns True
    when the file changed."""
    path = Path(path)
    config = read_json(path, {}, empty_ok=empty_ok)
    if not isinstance(config, dict):
        raise CorruptConfigError(f"{path} is not a JSON object, refusing to overwrite")
    servers = config.get(servers_key)
    if not isinstance(servers, dict):
        servers = {}
    entry = {**(extra or {}), **mcp_server_entry(command, args)}
    if servers.get(MARK) == entry and config.get(servers_key) is servers:
        return False
    config[servers_key] = {**servers, MARK: entry}
    write_json(path, config)
    return True


def remove_mcp_json_config(path: Path | str, servers_key: str = "mcpServers") -> bool:
    """Remove ``<servers_key>.cairn`` (and an emptied servers map). A corrupt file raises."""
    path = Path(path)
    if not path.exists():
        return False
    config = read_json(path, {}, empty_ok=True)
    servers = config.get(servers_key) if isinstance(config, dict) else None
    if not isinstance(servers, dict) or MARK not in servers:
        return False
    servers = {k: v for k, v in servers.items() if k != MARK}
    if servers:
        config[servers_key] = servers
    else:
        config.pop(servers_key)
    write_json(path, config)
    return True


def mcp_json_has_cairn(path: Path | str, servers_key: str = "mcpServers") -> bool:
    try:
        config = read_json(path, {}, empty_ok=True)
    except CorruptConfigError:
        return False
    return isinstance(config, dict) and isinstance(config.get(servers_key), dict) and MARK in config[servers_key]


# ---- Claude Code -------------------------------------------------------------------------------------------
# (native event, matcher, Cairn event, timeout seconds, async) -- the plugin's hooks.json, minus its
# Setup (runtime version check) and SessionStart "worker start" entries (no daemon here).
CLAUDE_CODE_HOOKS = (
    ("SessionStart", "startup|resume|clear|compact", "context", 60, False),
    ("UserPromptSubmit", None, "session-init", 60, False),
    ("PostToolUse", "*", "observation", 120, True),
    ("PreToolUse", "Read", "file-context", 60, True),
    ("Stop", None, "summarize", 120, True),
    ("SessionEnd", None, "session-end", None, True),
)


def claude_code_hooks(python: str | None = None, scope: str = "project") -> dict:
    """The ``hooks`` entries for ``.claude/settings.json`` (merge with ``merge_hook_groups``). ``scope="user"``
    spells them for ``~/.claude/settings.json``: they stand aside in a repository that wires its own."""
    hooks: dict[str, list] = {}
    for event, matcher, cairn_event, timeout, is_async in CLAUDE_CODE_HOOKS:
        with user_scope() if scope == "user" else contextlib.nullcontext():
            command = hook_command("claude-code", cairn_event, python=python)
        entry: dict[str, Any] = {"type": "command", "command": command}
        if timeout:
            entry["timeout"] = timeout
        if is_async:
            entry["async"] = True
        hooks.setdefault(event, []).append({"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]})
    return hooks


# ---- Cursor ------------------------------------------------------------------------------------------------
# sessionStart returns the memory as ``additional_context`` (the IDE and cursor-agent, also under -p);
# beforeSubmitPrompt and stop fire only in interactive sessions.
CURSOR_HOOKS = (("sessionStart", "context"), ("beforeSubmitPrompt", "session-init"), ("postToolUse", "observation"),
                ("stop", "summarize"), ("sessionEnd", "session-end"))
CURSOR_TARGETS = ("project", "user", "enterprise")
CURSOR_CONTEXT_FILE = "cairn-context.mdc"
_CURSOR_FRONTMATTER = ('---\nalwaysApply: true\n'
                       'description: "Cairn session memory from past sessions (auto-updated)"\n---\n')


def cursor_target_dir(target: str = "project", *, root: Path | str | None = None,
                      home: Path | str | None = None) -> Path | None:
    if target == "project":
        return _root(root) / ".cursor"
    if target == "user":
        return _home(home) / ".cursor"
    if target == "enterprise":
        if sys.platform == "darwin":
            return Path("/Library/Application Support/Cursor")
        if sys.platform.startswith("linux"):
            return Path("/etc/cursor")
        if os.name == "nt":
            return Path(os.environ.get("ProgramData") or "C:\\ProgramData") / "Cursor"
    return None


def _cursor_command(event: str, python: str | None) -> str:
    return hook_command("cursor", event, python=python, style="powershell")  # PowerShell on Windows


def cursor_hooks_json(python: str | None = None, events: Iterable[str] | None = None) -> dict:
    """Cairn's ``hooks.json`` entries (only the Cursor ``events`` given, when given)."""
    wanted = set(events) if events is not None else None
    hooks: dict[str, list] = {}
    for event, cairn_event in CURSOR_HOOKS:
        if wanted is None or event in wanted:
            hooks.setdefault(event, []).append({"command": _cursor_command(cairn_event, python)})
    return {"version": 1, "hooks": hooks}


def update_cursor_context_for_project(project: str) -> bool:
    """Refresh ``.cursor/rules/cairn-context.mdc`` of a registered project (call after a summary is stored)."""
    return _folders_update_cursor_context(None, project)



def install_cursor(root: Path | str | None = None, *, target: str = "project", home: Path | str | None = None,
                   python: str | None = None, events: Iterable[str] | None = None) -> dict:
    """Merge Cairn's hooks (or only ``events``) into ``<target>/hooks.json``; a project install also retires the
    context rule earlier versions generated."""
    rep = _report("cursor")
    target_dir = cursor_target_dir(target, root=root, home=home)
    if target_dir is None:
        return _fail(rep, f"Invalid target: {target}. Use: project, user, or enterprise")
    path = target_dir / "hooks.json"
    try:
        existing = read_json(path, {"version": 1, "hooks": {}})
    except CorruptConfigError as exc:
        return _fail(rep, str(exc))
    if not isinstance(existing, dict):
        return _fail(rep, f"{path} is not a JSON object, refusing to overwrite")
    hooks = existing.get("hooks") if isinstance(existing.get("hooks"), dict) else {}
    merged = {ev: [h for h in entries if not is_cairn_hook(h)] if isinstance(entries, list) else entries
              for ev, entries in hooks.items()}
    for event, entries in cursor_hooks_json(python, events)["hooks"].items():
        merged[event] = [*(merged.get(event) or []), *entries]
    new = {**existing, "version": existing.get("version", 1), "hooks": merged}
    try:
        if new != existing or not path.exists():
            write_json(path, new)
            rep["written"].append(str(path))
    except OSError as exc:
        hint = " (enterprise installation may require admin privileges)" if target == "enterprise" else ""
        return _fail(rep, f"{exc}{hint}")
    if target == "project":
        # memory reaches Cursor through its sessionStart hook now: retire the rule file earlier versions generated
        workspace = _root(root)
        legacy = workspace / ".cursor" / "rules" / CURSOR_CONTEXT_FILE
        if _generated_cursor_rule(legacy):
            legacy.unlink()
            rep["removed"].append(str(legacy))
        unregister_cursor_project(project_context(str(workspace)).primary)
    return rep


def _generated_cursor_rule(path: Path) -> bool:
    try:
        return path.exists() and path.read_text(encoding="utf-8").startswith(CURSOR_PLACEHOLDER.split("\n#", 1)[0])
    except OSError:
        return False


def uninstall_cursor(root: Path | str | None = None, *, target: str = "project",
                     home: Path | str | None = None) -> dict:
    rep = _report("cursor")
    target_dir = cursor_target_dir(target, root=root, home=home)
    if target_dir is None:
        return _fail(rep, f"Invalid target: {target}")
    path = target_dir / "hooks.json"
    if path.exists():
        try:
            data = read_json(path, {})
        except CorruptConfigError as exc:
            rep["notes"].append(f"{exc}; left intact")
            data = None
        if isinstance(data, dict) and isinstance(data.get("hooks"), dict):
            hooks, removed = {}, 0
            for event, entries in data["hooks"].items():
                if not isinstance(entries, list):
                    hooks[event] = entries
                    continue
                kept = [h for h in entries if not is_cairn_hook(h)]
                removed += len(entries) - len(kept)
                if kept:
                    hooks[event] = kept
            if removed:
                if hooks or set(data) - {"version", "hooks"}:
                    write_json(path, {**data, "hooks": hooks})
                else:
                    path.unlink()
                rep["removed"].append(str(path))
    if target == "project":
        workspace = _root(root)
        ctx_file = workspace / ".cursor" / "rules" / CURSOR_CONTEXT_FILE
        if _generated_cursor_rule(ctx_file):
            ctx_file.unlink()
            rep["removed"].append(str(ctx_file))
        unregister_cursor_project(project_context(str(workspace)).primary)
    return rep


def cursor_status(root: Path | str | None = None, *, home: Path | str | None = None) -> dict:
    locations = [("project", cursor_target_dir("project", root=root)), ("user", cursor_target_dir("user", home=home))]
    enterprise = cursor_target_dir("enterprise")
    if enterprise is not None and os.name != "nt":
        locations.append(("enterprise", enterprise))
    out = []
    for name, directory in locations:
        path = directory / "hooks.json"
        row: dict[str, Any] = {"name": name, "config": str(path), "exists": path.exists(), "installed": False,
                               "events": []}
        if path.exists():
            try:
                data = read_json(path, {})
                row["events"] = [ev for ev, entries in (data.get("hooks") or {}).items()
                                 if isinstance(entries, list) and any(is_cairn_hook(h) for h in entries)]
                row["installed"] = bool(row["events"])
            except (CorruptConfigError, AttributeError):
                row["error"] = "Unable to parse hooks.json"
        if name == "project":
            row["context"] = (directory / "rules" / CURSOR_CONTEXT_FILE).exists()
        out.append(row)
    return {"agent": "cursor", "installed": any(r["installed"] for r in out), "locations": out}


def configure_cursor_mcp(root: Path | str | None = None, *, target: str = "project", home: Path | str | None = None,
                         command: str = DEFAULT_MCP_COMMAND, args: Iterable[str] = DEFAULT_MCP_ARGS) -> dict:
    rep = _report("cursor-mcp")
    target_dir = cursor_target_dir(target, root=root, home=home)
    if target_dir is None:
        return _fail(rep, f"Invalid target: {target}. Use: project or user")
    path = target_dir / "mcp.json"
    try:
        if write_mcp_json_config(path, command, args):
            rep["written"].append(str(path))
    except CorruptConfigError as exc:
        return _fail(rep, str(exc))
    return rep


def remove_cursor_mcp(root: Path | str | None = None, *, target: str = "project",
                      home: Path | str | None = None) -> dict:
    rep = _report("cursor-mcp")
    target_dir = cursor_target_dir(target, root=root, home=home)
    if target_dir is None:
        return _fail(rep, f"Invalid target: {target}")
    try:
        if remove_mcp_json_config(target_dir / "mcp.json"):
            rep["removed"].append(str(target_dir / "mcp.json"))
    except CorruptConfigError as exc:
        return _fail(rep, str(exc))
    return rep


# ---- Windsurf ----------------------------------------------------------------------------------------------
WINDSURF_EVENTS = {"pre_user_prompt": "session-init", "post_write_code": "file-edit",
                   "post_run_command": "observation", "post_mcp_tool_use": "observation",
                   "post_cascade_response": "observation"}
WINDSURF_CONTEXT_CHAR_LIMIT = 6000
WINDSURF_CONTEXT_FILE = "cairn-context.md"


def windsurf_hooks_path(home: Path | str | None = None) -> Path:
    return _home(home) / ".codeium" / "windsurf" / "hooks.json"


def windsurf_registry_path() -> Path:
    return cairn_home() / "windsurf-projects.json"


def read_windsurf_registry() -> dict:
    try:
        data = read_json(windsurf_registry_path(), {})
    except CorruptConfigError:
        return {}
    return data if isinstance(data, dict) else {}


def register_windsurf_project(workspace: Path | str) -> None:
    registry = read_windsurf_registry()
    registry[str(workspace)] = {"installedAt": _now_iso()}
    write_json(windsurf_registry_path(), registry)


def unregister_windsurf_project(workspace: Path | str) -> None:
    registry = read_windsurf_registry()
    if str(workspace) in registry:
        del registry[str(workspace)]
        write_json(windsurf_registry_path(), registry)


def write_windsurf_context_file(workspace: Path | str, context: str) -> Path:
    path = Path(workspace) / ".windsurf" / "rules" / WINDSURF_CONTEXT_FILE
    content = f"""# Memory Context from Past Sessions

The following context is from Cairn's session memory, which tracks your coding sessions.

{to_bmp_safe(context)}

---
*Auto-updated by Cairn after each session. Use MCP search tools for detailed queries.*
"""
    if len(content) > WINDSURF_CONTEXT_CHAR_LIMIT:
        content = (content[:WINDSURF_CONTEXT_CHAR_LIMIT - 50]
                   + "\n\n*[Truncated \u2014 use MCP search for full history]*\n")
    _write_text_atomic(path, content)
    return path


def update_windsurf_context(workspace: Path | str) -> bool:
    """Refresh a registered workspace's ``.windsurf/rules/cairn-context.md`` from its session memory."""
    if str(workspace) not in read_windsurf_registry():
        return False
    context = fetch_project_context(workspace, project_context(str(workspace)).primary)
    if not context:
        return False
    write_windsurf_context_file(workspace, context)
    return True


def _windsurf_entry(event: str, python: str | None, working_directory: str | None) -> dict:
    entry: dict[str, Any] = {"command": hook_command("windsurf", WINDSURF_EVENTS.get(event, "observation"),
                                                     python=python, style="powershell"), "show_output": False}
    if working_directory:
        entry["working_directory"] = working_directory
    return entry


def install_windsurf(root: Path | str | None = None, *, home: Path | str | None = None, python: str | None = None,
                     working_directory: str | None = None) -> dict:
    """Merge Cairn's entries into the user-level ``~/.codeium/windsurf/hooks.json`` and seed the workspace's
    ``.windsurf/rules/cairn-context.md``."""
    rep = _report("windsurf")
    path = windsurf_hooks_path(home)
    try:
        config = read_json(path, {"hooks": {}})
    except CorruptConfigError as exc:
        return _fail(rep, str(exc))
    if not isinstance(config, dict):
        return _fail(rep, f"{path} is not a JSON object, refusing to overwrite")
    before = copy.deepcopy(config)
    hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
    for event in WINDSURF_EVENTS:
        kept = [h for h in hooks.get(event) or [] if not is_cairn_hook(h)]
        hooks[event] = [*kept, _windsurf_entry(event, python, working_directory)]
    config["hooks"] = hooks
    if config != before or not path.exists():
        write_json(path, config)
        rep["written"].append(str(path))
    workspace = _root(root)
    rules = workspace / ".windsurf" / "rules" / WINDSURF_CONTEXT_FILE
    context = fetch_project_context(workspace, project_context(str(workspace)).primary)
    if context:
        write_windsurf_context_file(workspace, context)
        rep["notes"].append("generated initial context from existing memory")
    elif not rules.exists():
        _write_text_atomic(rules, "# Memory Context from Past Sessions\n\n*No context yet. Complete your first "
                                  "session and context will appear here.*\n\nUse Cairn's MCP search tools for "
                                  "manual memory queries.\n")
        rep["notes"].append("created placeholder context file (populates after the first session)")
    rep["written"].append(str(rules))
    register_windsurf_project(workspace)
    return rep


def uninstall_windsurf(root: Path | str | None = None, *, home: Path | str | None = None) -> dict:
    rep = _report("windsurf")
    path = windsurf_hooks_path(home)
    if path.exists():
        try:
            config = read_json(path, {})
            hooks = dict(config.get("hooks") or {})
            removed = 0
            for event in WINDSURF_EVENTS:
                entries = hooks.get(event)
                if isinstance(entries, list) and entries:
                    kept = [h for h in entries if not is_cairn_hook(h)]
                    removed += len(entries) - len(kept)
                    if kept:
                        hooks[event] = kept
                    else:
                        del hooks[event]
            if removed:
                if hooks or set(config) - {"hooks"}:
                    write_json(path, {**config, "hooks": hooks})
                else:
                    path.unlink()
                rep["removed"].append(str(path))
        except (CorruptConfigError, AttributeError) as exc:
            rep["notes"].append(f"could not parse hooks.json ({exc}); left intact to preserve other hooks")
    workspace = _root(root)
    ctx_file = workspace / ".windsurf" / "rules" / WINDSURF_CONTEXT_FILE
    if ctx_file.exists():
        ctx_file.unlink()
        rep["removed"].append(str(ctx_file))
    unregister_windsurf_project(workspace)
    return rep


def windsurf_status(root: Path | str | None = None, *, home: Path | str | None = None) -> dict:
    path = windsurf_hooks_path(home)
    out: dict[str, Any] = {"agent": "windsurf", "config": str(path), "exists": path.exists(), "installed": False,
                           "events": [], "total_events": len(WINDSURF_EVENTS)}
    if path.exists():
        try:
            hooks = read_json(path, {}).get("hooks") or {}
            out["events"] = [ev for ev in WINDSURF_EVENTS if any(is_cairn_hook(h) for h in hooks.get(ev) or [])]
            out["installed"] = bool(out["events"])
        except (CorruptConfigError, AttributeError):
            out["error"] = "Unable to parse hooks.json"
    out["context"] = (_root(root) / ".windsurf" / "rules" / WINDSURF_CONTEXT_FILE).exists()
    return out


# ---- Codex CLI ---------------------------------------------------------------------------------------------
CODEX_MARKETPLACE_NAME = "cairn-local"
CODEX_PLUGIN_NAME = "cairn"
CODEX_PLUGIN_ID = f"{CODEX_PLUGIN_NAME}@{CODEX_MARKETPLACE_NAME}"
MIN_CODEX_MARKETPLACE_VERSION = "0.128.0"
REQUIRED_MARKETPLACE_FILES = (
    Path(".agents") / "plugins" / "marketplace.json",
    Path("plugin") / ".codex-plugin" / "plugin.json",
    Path("plugin") / ".mcp.json",
    Path("plugin") / "hooks" / "codex-hooks.json",
    Path("plugin") / "skills" / "cairn-recall-search" / "SKILL.md",
)
# (native event, matcher, Cairn event, timeout seconds) -- the plugin's codex-hooks.json
CODEX_HOOKS = (
    ("SessionStart", "startup|resume|clear|compact", "context", 20),
    ("UserPromptSubmit", None, "session-init", 20),
    ("PreToolUse", r"^Bash$|^mcp__.+__(read|view|cat)(_file|_files)?$", "file-context", 30),
    ("PostToolUse", ".*", "observation", 120),
    ("Stop", None, "summarize", 60),
)
_WINDOWS_CODEX_EXTENSIONS = {".cmd", ".exe", ".bat", ".com"}
MACOS_CODEX_BUNDLE_PATHS = ("/Applications/ChatGPT.app/Contents/Resources/codex",
                            "/Applications/Codex.app/Contents/Resources/codex")
CodexRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def codex_dir(home: Path | str | None = None) -> Path:
    return _home(home) / ".codex"


def codex_marketplace_root() -> Path:
    return cairn_home() / "codex-marketplace"


def codex_hooks(python: str | None = None) -> dict:
    hooks: dict[str, list] = {}
    for event, matcher, cairn_event, timeout in CODEX_HOOKS:
        entry = {"type": "command", "command": hook_command("codex", cairn_event, python=python, style="portable"),
                 "timeout": timeout}
        hooks.setdefault(event, []).append({"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]})
    return {"hooks": hooks}


def write_codex_marketplace(root: Path | str | None = None, *, python: str | None = None,
                            mcp_command: str = DEFAULT_MCP_COMMAND, mcp_args: Iterable[str] = DEFAULT_MCP_ARGS,
                            mcp_server_name: str = MARK) -> Path:
    """Write the local Codex plugin marketplace (manifest, hooks, MCP declaration, skills)."""
    from cairn import __version__
    root = Path(root) if root else codex_marketplace_root()
    plugin = root / "plugin"
    write_json(root / ".agents" / "plugins" / "marketplace.json", {
        "name": CODEX_MARKETPLACE_NAME, "interface": {"displayName": "Cairn (local)"},
        "plugins": [{"name": CODEX_PLUGIN_NAME, "source": {"source": "local", "path": "./plugin"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                     "category": "Productivity"}]})
    write_json(plugin / ".codex-plugin" / "plugin.json", {
        "name": CODEX_PLUGIN_NAME, "version": __version__,
        "description": "Cairn session memory: capture coding-session activity and inject relevant past work",
        "license": "Apache-2.0", "keywords": ["memory", "mcp", "sessions", "context"],
        "skills": "./skills/", "mcpServers": "./.mcp.json", "hooks": "./hooks/codex-hooks.json",
        "interface": {
            "displayName": "Cairn",
            "shortDescription": "Persistent memory and context across coding sessions.",
            "longDescription": "Cairn captures coding-session activity, compresses it into reusable observations, "
                               "and injects relevant context back into future sessions.",
            "category": "Productivity", "capabilities": ["Interactive", "Write"],
            "defaultPrompt": ["Find what I already learned about this codebase before I start a new task.",
                              "Show recent observations related to the files I am editing right now.",
                              "Summarize the last session and inject the most relevant context into this one."]}})
    write_json(plugin / ".mcp.json", {"mcpServers": {mcp_server_name: {"type": "stdio",
                                                                        **mcp_server_entry(mcp_command, mcp_args)}}})
    write_json(plugin / "hooks" / "codex-hooks.json", codex_hooks(python))
    install_skills(plugin / "skills")
    return root


def missing_marketplace_files(root: Path | str) -> list[str]:
    return [str(p) for p in REQUIRED_MARKETPLACE_FILES if not (Path(root) / p).exists()]


def set_toml_boolean_in_table(content: str, header: str, key: str, enabled: bool) -> str:
    """Set ``key = true|false`` inside ``header``'s table, adding the table when missing (text edit, so
    comments and formatting elsewhere are kept)."""
    line = f"{key} = {'true' if enabled else 'false'}"
    lines = content.split("\n")
    idx = next((i for i, ln in enumerate(lines) if ln.strip() == header), -1)
    if idx == -1:
        trimmed = content.rstrip()
        return f"{trimmed}{chr(10) * 2 if trimmed else ''}{header}\n{line}\n"
    end = idx + 1
    while end < len(lines) and not re.match(r"^\s*\[", lines[end]):
        end += 1
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    key_idx = next((i for i in range(idx + 1, end) if pattern.match(lines[i])), -1)
    if key_idx == -1:
        lines.insert(idx + 1, line)
    else:
        lines[key_idx] = line
    return "\n".join(lines)


def set_toml_plugin_enabled(content: str, plugin_id: str, enabled: bool) -> str:
    escaped = plugin_id.replace("\\", "\\\\").replace('"', '\\"')
    return set_toml_boolean_in_table(content, f'[plugins."{escaped}"]', "enabled", enabled)


def set_toml_feature_enabled(content: str, feature: str, enabled: bool) -> str:
    return set_toml_boolean_in_table(content, "[features]", feature, enabled)


def _toml_header(line: str) -> str | None:
    m = re.match(r"^\[([^\]]+)\]\s*$", line.strip())
    return re.sub(r"\s+", "", m.group(1)).replace('"', "") if m else None


def remove_codex_mcp_server_block(content: str, name: str = MARK, owner_marker: str = MARK) -> str:
    """Drop a user-level ``[mcp_servers.<name>]`` table (and its child tables) that Cairn owns (its text
    mentions ``owner_marker``), so Codex uses the plugin-managed MCP declaration instead of two copies."""
    blocks: list[tuple[str | None, list[str]]] = []
    header: str | None = None
    current: list[str] = []
    for line in content.split("\n"):
        h = _toml_header(line)
        if h is not None:
            blocks.append((header, current))
            header, current = h, [line]
        else:
            current.append(line)
    blocks.append((header, current))
    target = f"mcp_servers.{name}"
    if not any(h == target and owner_marker in "\n".join(text) for h, text in blocks):
        return content
    kept = [text for h, text in blocks if not (h == target or (h or "").startswith(target + "."))]
    out = "\n".join("\n".join(t) for t in kept)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"^\n+", "", out))


def _codex_is_executable(candidate: str) -> bool:
    return os.path.isfile(candidate) and os.access(candidate, os.X_OK)


def _usable_codex_bundle(candidate: str) -> bool:
    try:
        return subprocess.run([candidate, "--version"], capture_output=True, timeout=5, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def resolve_codex_command(platform: str | None = None, which: Callable[[str], str | None] = shutil.which,
                          bundle_ok: Callable[[str], bool] = _usable_codex_bundle) -> str | None:
    """The Codex CLI to run: ``codex`` on PATH (Windows prefers a .cmd/.exe shim; macOS also checks the
    app bundles). None when it cannot be found."""
    platform = platform or sys.platform
    found = which("codex")
    if platform == "win32":
        return found
    if found:
        return found
    if platform == "darwin":
        return next((c for c in MACOS_CODEX_BUNDLE_PATHS if bundle_ok(c)), None)
    return None


def _codex_invocation(command: str, args: list[str]) -> list[str]:
    if os.name == "nt" and Path(command).suffix.lower() in (".cmd", ".bat"):
        return ["cmd.exe", "/d", "/s", "/c", command, *args]
    return [command, *args]


def default_codex_runner() -> CodexRunner | None:
    command = resolve_codex_command()
    if not command:
        return None

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(_codex_invocation(command, args), capture_output=True, text=True, timeout=120,
                              check=False, encoding="utf-8", errors="replace")
    return run


def _run_codex(runner: CodexRunner, args: list[str]) -> str:
    res = runner(args)
    out = ((res.stdout or "") + ("\n" + res.stderr if res.stderr else "")).strip()
    if res.returncode != 0:
        raise IntegrationError(f"codex {' '.join(args)} failed with exit code {res.returncode}"
                               f"{': ' + (res.stderr or '').strip() if res.stderr else ''}")
    return out


def _semver_tuple(v: str) -> tuple[int, int, int]:
    a, b, c = (int(x) for x in v.split("."))
    return a, b, c


def _check_codex_version(runner: CodexRunner, rep: dict) -> None:
    res = runner(["--version"])
    output = f"{res.stdout or ''}\n{res.stderr or ''}".strip()
    if res.returncode != 0:
        rep["notes"].append("could not determine the Codex CLI version; plugin marketplace support requires "
                            f"{MIN_CODEX_MARKETPLACE_VERSION} or newer")
        return
    m = re.search(r"\d+\.\d+\.\d+", output)
    if not m:
        rep["notes"].append(f'could not parse the Codex CLI version from "{output or "<empty>"}"; plugin '
                            f"marketplace support requires {MIN_CODEX_MARKETPLACE_VERSION} or newer")
        return
    if _semver_tuple(m.group(0)) < _semver_tuple(MIN_CODEX_MARKETPLACE_VERSION):
        raise IntegrationError(f"Codex CLI {m.group(0)} is too old for plugin marketplace support. Update Codex CLI "
                               f"to {MIN_CODEX_MARKETPLACE_VERSION} or newer.")


def _register_codex_marketplace(runner: CodexRunner, root: Path, rep: dict) -> None:
    try:
        _run_codex(runner, ["plugin", "marketplace", "add", str(root)])
        return
    except IntegrationError as exc:
        msg = str(exc)
        if (f"marketplace '{CODEX_MARKETPLACE_NAME}' is already added from a different source" not in msg
                and f"marketplace `{CODEX_MARKETPLACE_NAME}` is already added from a different source" not in msg):
            raise
    rep["notes"].append(f"Codex marketplace {CODEX_MARKETPLACE_NAME} was registered from another source; "
                        f"replaced it with {root}")
    _run_codex(runner, ["plugin", "marketplace", "remove", CODEX_MARKETPLACE_NAME])
    _run_codex(runner, ["plugin", "marketplace", "add", str(root)])


def write_codex_plugin_config(enabled: bool, home: Path | str | None = None, *,
                              replace_user_mcp: str | None = MARK) -> bool:
    """Enable (hooks feature + plugin) or disable the plugin in ``~/.codex/config.toml``."""
    path = codex_dir(home) / "config.toml"
    if not enabled and not path.exists():
        return False
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    new = current
    if enabled:
        new = set_toml_feature_enabled(new, "hooks", True)
        if replace_user_mcp:
            new = remove_codex_mcp_server_block(new, replace_user_mcp)
    new = set_toml_plugin_enabled(new, CODEX_PLUGIN_ID, enabled)
    if new == current:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return True


def remove_codex_agents_md_context(home: Path | str | None = None) -> bool:
    """Strip a legacy global ``<cairn-context>`` block from ``~/.codex/AGENTS.md`` (the plugin's hooks inject
    context now). Returns True when something was removed."""
    path = codex_dir(home) / "AGENTS.md"
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    start, end = content.find(CONTEXT_TAG_OPEN), content.find(CONTEXT_TAG_CLOSE)
    if start == -1 or end == -1:
        return False
    before = content[:start].rstrip("\n")
    after = content[end + len(CONTEXT_TAG_CLOSE):].lstrip("\n")
    final = (before + ("\n\n" + after if after else "")).strip()
    path.write_text(final + "\n" if final else "", encoding="utf-8")
    return True


def _is_legacy_codex_agents_context(context: dict, agents_md: Path) -> bool:
    if context.get("mode") != "agents":
        return False
    update_on = context.get("updateOn")
    if not (isinstance(update_on, list) and len(update_on) == 2 and "session_start" in update_on
            and "session_end" in update_on):
        return False
    if context.get("path") is None:
        return True
    p = context["path"]
    return isinstance(p, str) and Path(os.path.expanduser(p)).resolve() == agents_md.resolve()


def disable_codex_transcript_agents_context(transcripts_config: Path | str | None = None,
                                            home: Path | str | None = None) -> bool:
    """Drop a legacy "write AGENTS.md on session start/end" context from the codex transcript watch."""
    path = Path(transcripts_config) if transcripts_config else cairn_home() / "transcript-watch.json"
    if not path.exists():
        return False
    try:
        parsed = read_json(path, {})
    except CorruptConfigError:
        return False
    if not isinstance(parsed, dict) or not isinstance(parsed.get("watches"), list):
        return False
    agents_md = codex_dir(home) / "AGENTS.md"
    changed = False
    for watch in parsed["watches"]:
        if not isinstance(watch, dict) or not (watch.get("name") == "codex" or watch.get("schema") == "codex"):
            continue
        if isinstance(watch.get("context"), dict) and _is_legacy_codex_agents_context(watch["context"], agents_md):
            del watch["context"]
            changed = True
    if changed:
        write_json(path, parsed)
    return changed


def install_codex(home: Path | str | None = None, *, python: str | None = None,
                  mcp_command: str = DEFAULT_MCP_COMMAND, mcp_args: Iterable[str] = DEFAULT_MCP_ARGS,
                  marketplace_root: Path | str | None = None, runner: CodexRunner | None = None,
                  transcripts_config: Path | str | None = None, replace_user_mcp: str | None = MARK) -> dict:
    """Register Cairn as a native Codex plugin: write the local marketplace, add it with ``codex plugin
    marketplace add``, enable hooks + the plugin in ``~/.codex/config.toml`` and ``codex plugin add`` it."""
    rep = _report("codex")
    runner = runner or default_codex_runner()
    if runner is None:
        return _fail(rep, "Codex CLI was not found on PATH. Install Codex, then run the install again.")
    try:
        _check_codex_version(runner, rep)
        root = write_codex_marketplace(marketplace_root, python=python, mcp_command=mcp_command, mcp_args=mcp_args)
        missing = missing_marketplace_files(root)
        if missing:
            raise IntegrationError(f"Codex marketplace root {root} is missing required files: {', '.join(missing)}")
        rep["written"].append(str(root))
        _register_codex_marketplace(runner, root, rep)
        if write_codex_plugin_config(True, home, replace_user_mcp=replace_user_mcp):
            rep["written"].append(str(codex_dir(home) / "config.toml"))
        _run_codex(runner, ["plugin", "add", CODEX_PLUGIN_ID])
    except (IntegrationError, OSError, subprocess.SubprocessError) as exc:
        return _fail(rep, str(exc))
    if remove_codex_agents_md_context(home):
        rep["notes"].append("removed legacy global context from ~/.codex/AGENTS.md")
    if disable_codex_transcript_agents_context(transcripts_config, home):
        rep["notes"].append("disabled legacy codex transcript AGENTS.md context")
    rep["notes"].append("open Codex CLI and trust the Cairn hooks when prompted; restart sessions opened earlier")
    return rep


def uninstall_codex(home: Path | str | None = None, *, marketplace_root: Path | str | None = None,
                    runner: CodexRunner | None = None, transcripts_config: Path | str | None = None) -> dict:
    rep = _report("codex")
    errors = []
    try:
        if write_codex_plugin_config(False, home):
            rep["removed"].append(f"{codex_dir(home) / 'config.toml'} (plugin disabled)")
    except OSError as exc:
        errors.append(f"Codex plugin config update failed: {exc}")
    runner = runner or default_codex_runner()
    if runner is None:
        rep["notes"].append("Codex CLI not found; skipped marketplace removal")
    else:
        try:
            _run_codex(runner, ["plugin", "marketplace", "remove", CODEX_MARKETPLACE_NAME])
        except (IntegrationError, OSError, subprocess.SubprocessError) as exc:
            errors.append(f"Codex marketplace removal failed: {exc}")
    root = Path(marketplace_root) if marketplace_root else codex_marketplace_root()
    if (root / ".agents" / "plugins" / "marketplace.json").exists():
        shutil.rmtree(root, ignore_errors=True)
        rep["removed"].append(str(root))
    try:
        if remove_codex_agents_md_context(home):
            rep["removed"].append(str(codex_dir(home) / "AGENTS.md") + " (legacy context)")
        disable_codex_transcript_agents_context(transcripts_config, home)
    except OSError as exc:
        errors.append(f"legacy context cleanup failed: {exc}")
    return _fail(rep, "; ".join(errors)) if errors else rep


def codex_status(home: Path | str | None = None, *, marketplace_root: Path | str | None = None) -> dict:
    path = codex_dir(home) / "config.toml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    header = f'[plugins."{CODEX_PLUGIN_ID}"]'
    enabled = False
    lines = text.split("\n")
    if header in (ln.strip() for ln in lines):
        i = next(i for i, ln in enumerate(lines) if ln.strip() == header) + 1
        while i < len(lines) and not re.match(r"^\s*\[", lines[i]):
            if re.match(r"^\s*enabled\s*=\s*true\b", lines[i]):
                enabled = True
            i += 1
    root = Path(marketplace_root) if marketplace_root else codex_marketplace_root()
    return {"agent": "codex", "config": str(path), "plugin_enabled": enabled,
            "hooks_feature": bool(re.search(r"(?m)^\s*hooks\s*=\s*true\b", text)),
            "marketplace": str(root), "marketplace_complete": not missing_marketplace_files(root),
            "installed": enabled and not missing_marketplace_files(root)}


# ---- OpenCode ----------------------------------------------------------------------------------------------
OPENCODE_PLUGIN_FILE = "cairn.js"
OPENCODE_PLUGIN_REF = f"./plugins/{OPENCODE_PLUGIN_FILE}"
OPENCODE_CONTEXT_HEADER = "# Cairn Memory Context"
_OPENCODE_MARK = "// generated by cairn: session memory plugin for OpenCode"

_OPENCODE_PLUGIN = r"""// generated by cairn: session memory plugin for OpenCode (removed by uninstall)
//
// OpenCode loads this file from .opencode/plugins/ (or ~/.config/opencode/plugins/). Hooks used:
//   - "experimental.chat.system.transform" (input, output) - every model request: adds this repository's
//                                                          memory (the Cairn brief and the session timeline,
//                                                          fetched once per session) to the system prompt
//   - "chat.message"                    (input, output) - a new message; the user's prompt starts a turn
//   - "tool.execute.after"              (input, output) - after every tool run
//   - "event"                           ({ event })     - bus events: message.part.updated (the latest
//                                                          text, kept for the summary), session.idle (the
//                                                          turn is done: summarize), session.deleted
//   - "experimental.session.compacting" (input)         - the session compacts: summarize first
// Both run Cairn's hook entry with a JSON payload on stdin:
//   <python> -m cairn.capture --platform opencode <event>
// OpenCode treats every export of this module as a plugin function: export nothing else.
import { spawn, execFile } from "node:child_process";

const PYTHON = __CAIRN_PYTHON__;
const CAPTURE = __CAIRN_CAPTURE__;
const PLATFORM = "opencode";
const SCOPE = __CAIRN_SCOPE__;  // ["--scope", "user"] when installed for every repository
const MAX_TOOL_RESPONSE_LENGTH = 1000;
const MAX_TRACKED_SESSIONS = 1000;

// Tool argument schemas come from OpenCode's plugin helper (or zod) when available; without either the
// search tool is simply not registered and capture still works.
let schema = null;
try {
  schema = (await import("@opencode-ai/plugin")).tool.schema;
} catch {
  try {
    schema = (await import("zod")).z;
  } catch {
    schema = null;
  }
}

function capture(event, payload, cwd) {
  if (!CAPTURE) return;
  try {
    const child = spawn(PYTHON, ["-m", "cairn.capture", "--platform", PLATFORM, ...SCOPE, event], {
      cwd: cwd || process.cwd(),
      stdio: ["pipe", "ignore", "ignore"],
      windowsHide: true,
    });
    child.on("error", (error) => console.warn(`[cairn] capture ${event} failed: ${error.message}`));
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify({ ...payload, cwd: cwd || process.cwd() }));
  } catch (error) {
    console.warn(`[cairn] capture ${event} failed: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function runSearch(query, cwd) {
  return new Promise((resolve) => {
    execFile(
      PYTHON,
      [
        "-m", "cairn.engines.recall.integrations", "search",
        "--cwd", cwd || process.cwd(), "--limit", "10", "--", query,
      ],
      { timeout: 30000, windowsHide: true, maxBuffer: 4 * 1024 * 1024 },
      (error, stdout) => {
        if (error) {
          resolve(`Cairn session memory is not available: ${error.message}`);
          return;
        }
        const text = String(stdout || "").trim();
        resolve(text || `No results found for "${query}".`);
      },
    );
  });
}

// One Cairn session per OpenCode session (its ids are unique, so the mapping survives restarts).
const sessionKey = (openCodeSessionId) => `opencode-${openCodeSessionId}`;
const latestTextBySessionId = new Map();
const memoryBySessionId = new Map();

// This repository's memory, as the session-start hooks of other agents receive it ("" when unavailable).
function loadMemory(sessionID, cwd) {
  return new Promise((resolve) => {
    try {
      const child = execFile(
        PYTHON,
        ["-m", "cairn.capture", "--platform", PLATFORM, ...SCOPE, "context"],
        { cwd: cwd || process.cwd(), timeout: 30000, windowsHide: true, maxBuffer: 4 * 1024 * 1024 },
        (error, stdout) => {
          if (error) {
            resolve("");
            return;
          }
          try {
            resolve(JSON.parse(String(stdout || "{}"))?.hookSpecificOutput?.additionalContext || "");
          } catch {
            resolve("");
          }
        },
      );
      child.stdin?.on("error", () => {});
      child.stdin?.end(JSON.stringify({ session_id: sessionKey(sessionID), cwd: cwd || process.cwd() }));
    } catch {
      resolve("");
    }
  });
}

function rememberText(sessionID, text) {
  latestTextBySessionId.delete(sessionID);
  while (latestTextBySessionId.size >= MAX_TRACKED_SESSIONS) {
    latestTextBySessionId.delete(latestTextBySessionId.keys().next().value);
  }
  latestTextBySessionId.set(sessionID, text);
}

function textOf(parts) {
  return (parts || [])
    .filter((part) => part?.type === "text" && typeof part.text === "string" && !part.synthetic)
    .map((part) => part.text)
    .join("\n")
    .trim();
}

function truncate(text) {
  return text.length > MAX_TOOL_RESPONSE_LENGTH ? text.slice(0, MAX_TOOL_RESPONSE_LENGTH) : text;
}

function summarize(sessionID, cwd) {
  capture(
    "summarize",
    { session_id: sessionKey(sessionID), last_assistant_message: latestTextBySessionId.get(sessionID) || "" },
    cwd,
  );
}

export const CairnPlugin = async (ctx) => {
  const cwd = ctx.directory;

  const hooks = {
    // Every model request of a session carries the memory it started with.
    "experimental.chat.system.transform": async (input, output) => {
      const sessionID = input?.sessionID;
      if (!sessionID || !Array.isArray(output?.system)) return;
      if (!memoryBySessionId.has(sessionID)) {
        while (memoryBySessionId.size >= MAX_TRACKED_SESSIONS) {
          memoryBySessionId.delete(memoryBySessionId.keys().next().value);
        }
        memoryBySessionId.set(sessionID, loadMemory(sessionID, cwd));
      }
      const memory = await memoryBySessionId.get(sessionID);
      if (memory) output.system.push(memory);
    },

    // The user's prompt opens a turn (assistant messages only update the text kept for the summary).
    "chat.message": async (input, output) => {
      const sessionID = input?.sessionID || output?.message?.sessionID;
      const text = textOf(output?.parts);
      if (!sessionID || !text) return;
      if (output?.message?.role === "assistant") {
        rememberText(sessionID, text);
        return;
      }
      capture("session-init", { session_id: sessionKey(sessionID), prompt: text }, cwd);
    },

    // Every tool execution becomes an observation (the primary capture path).
    "tool.execute.after": async (input, output) => {
      if (!input?.sessionID) return;
      const result = typeof output?.output === "string" ? output.output : JSON.stringify(output?.output ?? "");
      capture(
        "observation",
        {
          session_id: sessionKey(input.sessionID),
          tool_name: input.tool,
          tool_input: input.args || output?.args || {},
          tool_response: truncate(result),
          tool_use_id: input.callID,
        },
        cwd,
      );
    },

    // Summarize when a session compacts (OpenCode's real compaction hook).
    "experimental.session.compacting": async (input) => {
      if (input?.sessionID) summarize(input.sessionID, cwd);
    },

    event: async ({ event }) => {
      const props = event?.properties || {};
      const sessionID = props.sessionID || props.part?.sessionID || props.info?.id;
      if (!sessionID) return;
      switch (event.type) {
        case "message.part.updated":
          // the last text of a finished turn is the assistant's answer
          if (props.part?.type === "text" && !props.part.synthetic && props.part.text) {
            rememberText(sessionID, props.part.text);
          }
          break;
        case "session.idle":
          summarize(sessionID, cwd);
          break;
        case "session.deleted":
          capture("session-end", { session_id: sessionKey(sessionID) }, cwd);
          latestTextBySessionId.delete(sessionID);
          memoryBySessionId.delete(sessionID);
          break;
        default:
          break;
      }
    },
  };

  if (schema) {
    hooks.tool = {
      cairn_search: {
        description: "Search Cairn session memory for past observations, sessions, and context",
        args: { query: schema.string().describe("Search query for memory observations") },
        async execute(args) {
          const query = String(args?.query || "");
          if (!query) return "Please provide a search query.";
          return runSearch(query, cwd);
        },
      },
    };
  }
  return hooks;
};
"""


def opencode_config_dir(home: Path | str | None = None) -> Path:
    env = os.environ.get("OPENCODE_CONFIG_DIR")
    return Path(env) if env else _home(home) / ".config" / "opencode"


def opencode_plugin_path(home: Path | str | None = None) -> Path:
    return opencode_config_dir(home) / "plugins" / OPENCODE_PLUGIN_FILE


def opencode_plugin_source(python: str | None = None, *, capture: bool = True) -> str:
    return (_OPENCODE_PLUGIN.replace("__CAIRN_PYTHON__", json.dumps(python or sys.executable))
            .replace("__CAIRN_CAPTURE__", "true" if capture else "false")
            .replace("__CAIRN_SCOPE__", '["--scope", "user"]' if _SCOPE.get() == "user" else "[]"))


def _plugin_entries(config: dict) -> list:
    plugin = config.get("plugin")
    if isinstance(plugin, list):
        return list(plugin)
    return [] if plugin is None else [plugin]


def add_opencode_plugin_reference(config: dict) -> dict:
    entries = _plugin_entries(config)
    return config if OPENCODE_PLUGIN_REF in entries else {**config, "plugin": [*entries, OPENCODE_PLUGIN_REF]}


def remove_opencode_plugin_reference(config: dict) -> dict:
    entries = _plugin_entries(config)
    if OPENCODE_PLUGIN_REF not in entries:
        return config
    kept = [p for p in entries if p != OPENCODE_PLUGIN_REF]
    out = {k: v for k, v in config.items() if k != "plugin"}
    return {**out, "plugin": kept} if kept else out


def install_opencode(home: Path | str | None = None, *, python: str | None = None,
                     root: Path | str | None = None) -> dict:
    """Write the OpenCode plugin, register it in ``opencode.json`` and seed ``AGENTS.md`` context."""
    rep = _report("opencode")
    cfg_dir = opencode_config_dir(home)
    config_path = cfg_dir / "opencode.json"
    try:
        config = read_json(config_path, {"$schema": "https://opencode.ai/config.json"})
    except CorruptConfigError as exc:
        return _fail(rep, f"Failed to register OpenCode plugin in config: {exc}")
    if not isinstance(config, dict):
        return _fail(rep, f"{config_path} is not a JSON object, refusing to overwrite")
    plugin_path = opencode_plugin_path(home)
    if plugin_path.exists() and _OPENCODE_MARK not in plugin_path.read_text(encoding="utf-8", errors="replace"):
        return _fail(rep, f"{plugin_path} exists and was not written by Cairn; refusing to overwrite")
    source = opencode_plugin_source(python)
    if not plugin_path.exists() or plugin_path.read_text(encoding="utf-8") != source:
        _write_text_atomic(plugin_path, source)
        rep["written"].append(str(plugin_path))
    updated = add_opencode_plugin_reference(config)
    if updated is not config or not config_path.exists():
        write_json(config_path, updated)
        rep["written"].append(str(config_path))
    context = fetch_project_context(root, project_context(str(root)).primary) if root else None
    agents_md = cfg_dir / "AGENTS.md"
    if context or not has_context_block(agents_md):
        inject_context_into_markdown_file(agents_md, context or "# Memory Context from Past Sessions\n\n*No context "
                                          "yet. Complete your first session and context will appear here.*\n\nUse "
                                          "Cairn search tools for manual memory queries.", OPENCODE_CONTEXT_HEADER)
        rep["written"].append(str(agents_md))
    return rep


def uninstall_opencode(home: Path | str | None = None) -> dict:
    rep = _report("opencode")
    errors = []
    plugin_path = opencode_plugin_path(home)
    if plugin_path.exists():
        if _OPENCODE_MARK in plugin_path.read_text(encoding="utf-8", errors="replace"):
            plugin_path.unlink()
            rep["removed"].append(str(plugin_path))
        else:
            rep["notes"].append(f"{plugin_path} was not written by Cairn; left in place")
    config_path = opencode_config_dir(home) / "opencode.json"
    if config_path.exists():
        try:
            config = read_json(config_path, {})
            updated = remove_opencode_plugin_reference(config) if isinstance(config, dict) else config
            if updated is not config:
                write_json(config_path, updated)
                rep["removed"].append(f"{config_path} (plugin reference)")
        except CorruptConfigError as exc:
            errors.append(f"Failed to deregister OpenCode plugin from config: {exc}")
    agents_md = opencode_config_dir(home) / "AGENTS.md"
    if remove_context_block(agents_md, header_line=OPENCODE_CONTEXT_HEADER):
        rep["removed"].append(f"{agents_md} (context)")
    return _fail(rep, "; ".join(errors)) if errors else rep


def opencode_status(home: Path | str | None = None) -> dict:
    cfg_dir = opencode_config_dir(home)
    plugin_path = opencode_plugin_path(home)
    registered = False
    try:
        config = read_json(cfg_dir / "opencode.json", {})
        registered = isinstance(config, dict) and OPENCODE_PLUGIN_REF in _plugin_entries(config)
    except CorruptConfigError:
        pass
    agents_md = cfg_dir / "AGENTS.md"
    return {"agent": "opencode", "config_dir": str(cfg_dir), "config_dir_exists": cfg_dir.exists(),
            "plugin": str(plugin_path), "plugin_installed": plugin_path.exists(), "registered": registered,
            "agents_md": str(agents_md), "agents_md_exists": agents_md.exists(),
            "context": has_context_block(agents_md), "installed": plugin_path.exists() and registered}


# ---- Antigravity CLI (agy) ---------------------------------------------------------------------------------
# agy loads hooks from ~/.gemini/config/hooks.json and fires these five events.
ANTIGRAVITY_EVENTS = {"PreInvocation": "context", "PreToolUse": "observation", "PostToolUse": "observation",
                      "PostInvocation": "observation", "Stop": "summarize"}
ANTIGRAVITY_PLATFORM = "antigravity"
HOOK_TIMEOUT_MS = 10000
ANTIGRAVITY_CONTEXT_FILE = "cairn-context.md"


def gemini_dir(home: Path | str | None = None) -> Path:
    return _home(home) / ".gemini"


def antigravity_hooks_path(home: Path | str | None = None) -> Path:
    return gemini_dir(home) / "config" / "hooks.json"


def antigravity_mcp_paths(home: Path | str | None = None) -> list[Path]:
    # both locations exist in the wild; write both until one is known to be the only one agy reads
    return [gemini_dir(home) / "antigravity" / "mcp_config.json", gemini_dir(home) / "config" / "mcp_config.json"]


def antigravity_rules_path(home: Path | str | None = None) -> Path:
    return _home(home) / ".agents" / "rules" / ANTIGRAVITY_CONTEXT_FILE


def _named_hook_groups(platform: str, events: dict[str, str], python: str | None, style: str) -> tuple[dict, list]:
    groups: dict[str, list] = {}
    warnings: list[str] = []
    if style == "bare":
        warning = _space_free(python or sys.executable)[1]
        if warning:
            warnings.append(warning)
    for native, cairn_event in events.items():
        groups[native] = [{"matcher": "*", "hooks": [{"name": MARK, "type": "command", "timeout": HOOK_TIMEOUT_MS,
                                                      "command": hook_command(platform, cairn_event, python=python,
                                                                              style=style)}]}]
    return groups, warnings


def _setup_gemini_md_context(home: Path | str | None) -> bool:
    path = gemini_dir(home) / "GEMINI.md"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if CONTEXT_TAG_OPEN in content:
        return False
    placeholder = (f"{CONTEXT_TAG_OPEN}\n# Memory Context from Past Sessions\n\n*No context yet. Complete your first "
                   f"session and context will appear here.*\n{CONTEXT_TAG_CLOSE}")
    sep = "\n\n" if content and not content.endswith("\n") else ("\n" if content else "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + sep + placeholder + "\n", encoding="utf-8")
    return True


def install_antigravity(home: Path | str | None = None, *, python: str | None = None,
                        mcp_command: str = DEFAULT_MCP_COMMAND, mcp_args: Iterable[str] = DEFAULT_MCP_ARGS) -> dict:
    """Merge Cairn's hooks into ``~/.gemini/config/hooks.json``, register the MCP server in both agy MCP
    configs and seed the GEMINI.md and ``~/.agents/rules`` context."""
    rep = _report("antigravity")
    path = antigravity_hooks_path(home)
    try:
        existing = read_json(path, {}, empty_ok=True)
        if not isinstance(existing, dict):
            raise CorruptConfigError(f"{path} is not a JSON object, refusing to overwrite user hooks")
        groups, warnings = _named_hook_groups(ANTIGRAVITY_PLATFORM, ANTIGRAVITY_EVENTS, python, "bare")
        rep["notes"] += warnings
        merged = merge_hook_groups(existing, groups)
        if merged != existing or not path.exists():
            write_json(path, merged)
            rep["written"].append(str(path))
        if _setup_gemini_md_context(home):
            rep["written"].append(str(gemini_dir(home) / "GEMINI.md"))
        for mcp_path in antigravity_mcp_paths(home):
            if write_mcp_json_config(mcp_path, mcp_command, mcp_args, empty_ok=True):
                rep["written"].append(str(mcp_path))
        rules = antigravity_rules_path(home)
        if not has_context_block(rules):
            inject_context_into_markdown_file(rules, PLACEHOLDER_CONTEXT)
            rep["written"].append(str(rules))
    except CorruptConfigError as exc:
        return _fail(rep, str(exc))
    return rep


def uninstall_antigravity(home: Path | str | None = None) -> dict:
    rep = _report("antigravity")
    path = antigravity_hooks_path(home)
    if path.exists():
        try:
            config = read_json(path, {}, empty_ok=True)
            cleaned, removed = remove_hook_groups(config)
            if removed:
                if cleaned:
                    write_json(path, cleaned)
                else:
                    path.unlink()
                rep["removed"].append(f"{path} ({removed} hooks)")
        except CorruptConfigError as exc:
            rep["notes"].append(f"skipping hooks.json cleanup: {exc}")
    for mcp_path in antigravity_mcp_paths(home):
        try:
            if remove_mcp_json_config(mcp_path):
                rep["removed"].append(f"{mcp_path} (cairn entry)")
        except CorruptConfigError as exc:
            rep["notes"].append(f"skipping {mcp_path}: {exc}")
    if remove_context_block(antigravity_rules_path(home)):
        rep["removed"].append(str(antigravity_rules_path(home)))
    # GEMINI.md is shared with the Gemini CLI integration: keep the block while that is installed
    if not gemini_cli_status(home)["installed"] and remove_context_block(gemini_dir(home) / "GEMINI.md"):
        rep["removed"].append(f"{gemini_dir(home) / 'GEMINI.md'} (context)")
    return rep


def antigravity_status(home: Path | str | None = None) -> dict:
    path = antigravity_hooks_path(home)
    out: dict[str, Any] = {"agent": "antigravity", "config": str(path), "exists": path.exists(), "events": [],
                           "total_events": len(ANTIGRAVITY_EVENTS)}
    if path.exists():
        try:
            out["events"] = _cairn_events(read_json(path, {}, empty_ok=True))
        except CorruptConfigError as exc:
            out["error"] = str(exc)
    out["installed"] = bool(out["events"])
    out["context"] = has_context_block(gemini_dir(home) / "GEMINI.md")
    out["mcp"] = {str(p): mcp_json_has_cairn(p) for p in antigravity_mcp_paths(home)}
    out["rules"] = has_context_block(antigravity_rules_path(home))
    return out


# ---- Gemini CLI --------------------------------------------------------------------------------------------
# Gemini CLI reads hooks from ~/.gemini/settings.json; its payloads use Claude Code's field names
# (session_id, cwd, prompt, tool_name, tool_input, tool_response, transcript_path).
GEMINI_CLI_EVENTS = {"SessionStart": "context", "BeforeAgent": "session-init", "AfterTool": "observation",
                     "AfterAgent": "summarize", "SessionEnd": "session-end"}
GEMINI_CLI_PLATFORM = "gemini"


def gemini_settings_path(home: Path | str | None = None) -> Path:
    return gemini_dir(home) / "settings.json"


def install_gemini_cli(home: Path | str | None = None, *, python: str | None = None) -> dict:
    """Merge Cairn's hooks into ``~/.gemini/settings.json`` and seed the GEMINI.md context section."""
    rep = _report("gemini-cli")
    path = gemini_settings_path(home)
    try:
        settings = read_json(path, {})
    except CorruptConfigError as exc:
        return _fail(rep, f"{exc} (user settings)")
    if not isinstance(settings, dict):
        return _fail(rep, f"{path} is not a JSON object, refusing to overwrite user settings")
    groups, _ = _named_hook_groups(GEMINI_CLI_PLATFORM, GEMINI_CLI_EVENTS, python, "powershell")  # PowerShell on Windows
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    merged = {**settings, "hooks": merge_hook_groups(hooks, groups)}
    if merged != settings or not path.exists():
        write_json(path, merged)
        rep["written"].append(str(path))
    if _setup_gemini_md_context(home):
        rep["written"].append(str(gemini_dir(home) / "GEMINI.md"))
    return rep


def uninstall_gemini_cli(home: Path | str | None = None) -> dict:
    rep = _report("gemini-cli")
    path = gemini_settings_path(home)
    if path.exists():
        try:
            settings = read_json(path, {})
        except CorruptConfigError as exc:
            return _fail(rep, str(exc))
        if isinstance(settings, dict) and isinstance(settings.get("hooks"), dict):
            cleaned, removed = remove_hook_groups(settings["hooks"])
            if removed:
                new = {k: v for k, v in settings.items() if k != "hooks"}
                if cleaned:
                    new["hooks"] = cleaned
                write_json(path, new)
                rep["removed"].append(f"{path} ({removed} hooks)")
    if not antigravity_status(home)["installed"] and remove_context_block(gemini_dir(home) / "GEMINI.md"):
        rep["removed"].append(f"{gemini_dir(home) / 'GEMINI.md'} (context)")
    return rep


def gemini_cli_status(home: Path | str | None = None) -> dict:
    path = gemini_settings_path(home)
    out: dict[str, Any] = {"agent": "gemini-cli", "config": str(path), "exists": path.exists(), "events": [],
                           "total_events": len(GEMINI_CLI_EVENTS)}
    if path.exists():
        try:
            settings = read_json(path, {})
            out["events"] = _cairn_events(settings.get("hooks") if isinstance(settings, dict) else None)
        except CorruptConfigError as exc:
            out["error"] = str(exc)
    out["installed"] = bool(out["events"])
    out["context"] = has_context_block(gemini_dir(home) / "GEMINI.md")
    return out


# ---- per-repository wiring (``cairn init`` / ``cairn agents install``) -------------------------------------
# Each agent's hooks go into the repository's own config. What the installed CLIs do with them:
#   Gemini CLI 0.38   .gemini/settings.json; runs under -p too; SessionStart ``additionalContext`` is injected
#   Codex 0.144       .codex/hooks.json; runs once the project is trusted and each hook approved (/hooks);
#                     SessionStart ``additionalContext`` is injected; no SessionEnd
#   Cursor            .cursor/hooks.json (IDE and cursor-agent); sessionStart ``additional_context`` is injected;
#                     prompt and stop hooks fire only interactively
#   OpenCode          .opencode/plugins/*.js, loaded automatically; the plugin adds the memory to the system prompt
#   Copilot CLI 1.0   .github/hooks/*.json; interactive sessions of a trusted folder only; output ignored (its
#                     memory comes from an untracked instructions file, see cairn.agents)
# The session-start hooks run the ``context`` event, which returns the Cairn brief and the timeline.
CODEX_PROJECT_HOOKS = (("SessionStart", "startup|resume|clear|compact", "context", 30),
                       ("UserPromptSubmit", None, "session-init", 20), ("PostToolUse", None, "observation", 60),
                       ("Stop", None, "summarize", 60))
COPILOT_HOOKS = (("userPromptSubmitted", "session-init"), ("postToolUse", "observation"),
                 ("agentStop", "summarize"), ("sessionEnd", "session-end"))
COPILOT_HOOKS_FILE = Path(".github") / "hooks" / "cairn.json"
OPENCODE_PROJECT_PLUGIN = Path(".opencode") / "plugins" / OPENCODE_PLUGIN_FILE
GEMINI_CONTEXT_TIMEOUT_MS = 30000  # SessionStart renders the Cairn brief as well as the timeline
PROJECT_AGENTS = ("claude", "gemini", "codex", "cursor", "opencode", "copilot")


def _merge_hooks_file(path: Path, groups: dict, rep: dict) -> dict:
    """Merge ``event -> [{matcher?, hooks}]`` groups into the ``hooks`` map of a JSON config."""
    try:
        data = read_json(path, {}, empty_ok=True)
    except CorruptConfigError as exc:
        return _fail(rep, str(exc))
    if not isinstance(data, dict):
        return _fail(rep, f"{path} is not a JSON object, refusing to overwrite")
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    merged = {**data, "hooks": merge_hook_groups(hooks, groups)}
    if merged != data or not path.exists():
        write_json(path, merged)
        rep["written"].append(str(path))
    return rep


def _unmerge_hooks_file(path: Path, rep: dict) -> dict:
    """Remove Cairn's entries from a JSON config's ``hooks`` map; the file goes when nothing else is left."""
    if not path.exists():
        return rep
    try:
        data = read_json(path, {}, empty_ok=True)
    except CorruptConfigError as exc:
        rep["notes"].append(f"{exc}; left intact")
        return rep
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return rep
    cleaned, removed = remove_hook_groups(data["hooks"])
    if not removed:
        return rep
    rest = {k: v for k, v in data.items() if k != "hooks"}
    if cleaned:
        rest["hooks"] = cleaned
    if rest:
        write_json(path, rest)
    else:
        path.unlink()
    rep["removed"].append(str(path))
    return rep


def _hooks_file_events(path: Path) -> list[str]:
    try:
        data = read_json(path, {}, empty_ok=True)
    except CorruptConfigError:
        return []
    return _cairn_events(data.get("hooks") if isinstance(data, dict) else None)


def gemini_project_hooks(python: str | None = None, *, capture: bool = True) -> dict:
    """``.gemini/settings.json`` hooks: SessionStart context, plus the capture events when ``capture``."""
    events = GEMINI_CLI_EVENTS if capture else {"SessionStart": GEMINI_CLI_EVENTS["SessionStart"]}
    groups, _ = _named_hook_groups(GEMINI_CLI_PLATFORM, events, python, "powershell")
    for group in groups["SessionStart"]:
        for hook in group["hooks"]:
            hook["timeout"] = GEMINI_CONTEXT_TIMEOUT_MS
    return groups


def codex_project_hooks(python: str | None = None, *, capture: bool = True) -> dict:
    """``.codex/hooks.json`` hooks: SessionStart memory, plus the capture events when ``capture``."""
    hooks = {}
    for event, matcher, cairn_event, timeout in CODEX_PROJECT_HOOKS:
        if capture or cairn_event == "context":
            entry = {"type": "command", "timeout": timeout,
                     "command": hook_command("codex", cairn_event, python=python, style="portable")}
            hooks[event] = [{"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]}]
    return hooks


def codex_hooks_approved(root: Path | str, home: Path | str | None = None) -> bool | None:
    """Whether Codex has approved every Cairn hook in ``<root>/.codex/hooks.json``. Codex records each approval
    under ``[hooks.state."<file>:<event>:<group>:<handler>"]`` in ``~/.codex/config.toml`` (read here, never
    written). None when the repository has no Cairn hooks for Codex."""
    import tomllib
    hooks_file = _root(root).resolve() / ".codex" / "hooks.json"
    ours = _hooks_file_events(hooks_file)
    if not ours:
        return None
    try:
        state = tomllib.loads((codex_dir(home) / "config.toml").read_text(encoding="utf-8")).get("hooks", {})
    except (OSError, tomllib.TOMLDecodeError):
        return False
    approved = {key[len(f"{hooks_file}:"):].split(":")[0] for key in (state.get("state") or {})
                if key.startswith(f"{hooks_file}:")}
    return all(re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower() in approved for event in ours)


def copilot_hooks_json(python: str | None = None) -> dict:
    """``.github/hooks/cairn.json`` for GitHub Copilot CLI (bash on macOS/Linux, PowerShell on Windows)."""
    return {"version": 1, "hooks": {event: [{
        "type": "command", "timeoutSec": 30,
        "bash": hook_command("copilot", cairn_event, python=python, style="quoted"),
        "powershell": hook_command("copilot", cairn_event, python=python, style="powershell")}]
        for event, cairn_event in COPILOT_HOOKS}}


def _ours(path: Path, marker: str) -> bool:
    try:
        return path.exists() and marker in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _write_owned(path: Path, text: str, marker: str, rep: dict) -> dict:
    """Write a file Cairn owns outright; a same-named file of the user's is left alone."""
    if path.exists() and not _ours(path, marker):
        return _fail(rep, f"{path} exists and was not written by Cairn; left in place")
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        _write_text_atomic(path, text)
        rep["written"].append(str(path))
    return rep


def _remove_owned(path: Path, marker: str, rep: dict) -> dict:
    if _ours(path, marker):
        path.unlink()
        rep["removed"].append(str(path))
    return rep


def install_project(agent: str, root: Path | str, *, python: str | None = None, capture: bool = True) -> dict:
    """Wire one agent's hooks into the repository at ``root`` (see the table above). Without ``capture`` only
    the session-start memory is wired."""
    root = _root(root)
    if agent == "gemini":
        return _merge_hooks_file(root / ".gemini" / "settings.json", gemini_project_hooks(python, capture=capture),
                                 _report(agent))
    if agent == "codex":
        rep = _merge_hooks_file(root / ".codex" / "hooks.json", codex_project_hooks(python, capture=capture),
                                _report(agent))
        if rep["ok"] and not codex_hooks_approved(root):
            rep["notes"].append("Codex: open codex here and approve Cairn's hooks once under /hooks")
        return rep
    if agent == "cursor":
        return install_cursor(root, target="project", python=python, events=None if capture else ("sessionStart",))
    if agent == "opencode":
        return _write_owned(root / OPENCODE_PROJECT_PLUGIN, opencode_plugin_source(python, capture=capture),
                            _OPENCODE_MARK, _report(agent))
    if agent == "copilot" and capture:
        text = json.dumps(copilot_hooks_json(python), indent=2) + "\n"
        return _write_owned(root / COPILOT_HOOKS_FILE, text, f"-m {HOOK_MODULE}", _report(agent))
    return _report(agent)  # claude is wired by cairn.agents; the rest have no per-repository hooks


def uninstall_project(agent: str, root: Path | str) -> dict:
    root = _root(root)
    if agent == "gemini":
        return _unmerge_hooks_file(root / ".gemini" / "settings.json", _report(agent))
    if agent == "codex":
        return _unmerge_hooks_file(root / ".codex" / "hooks.json", _report(agent))
    if agent == "cursor":
        return uninstall_cursor(root, target="project")
    if agent == "opencode":
        return _remove_owned(root / OPENCODE_PROJECT_PLUGIN, _OPENCODE_MARK, _report(agent))
    if agent == "copilot":
        return _remove_owned(root / COPILOT_HOOKS_FILE, f"-m {HOOK_MODULE}", _report(agent))
    return _report(agent)


def project_status(root: Path | str) -> dict[str, dict]:
    """Per agent: ``capture`` (its hooks record sessions here) and ``start_hook`` (a SessionStart hook
    injects memory)."""
    root = _root(root)
    claude = _hooks_file_events(root / ".claude" / "settings.json")
    gemini = _hooks_file_events(root / ".gemini" / "settings.json")
    codex = _hooks_file_events(root / ".codex" / "hooks.json")
    cursor = next((r for r in cursor_status(root)["locations"] if r["name"] == "project"), {}).get("events", [])
    plugin = root / OPENCODE_PROJECT_PLUGIN
    opencode = _ours(plugin, _OPENCODE_MARK)
    return {
        "claude": {"capture": any(e != "SessionStart" for e in claude), "start_hook": "SessionStart" in claude},
        "gemini": {"capture": any(e != "SessionStart" for e in gemini), "start_hook": "SessionStart" in gemini},
        "codex": {"capture": any(e != "SessionStart" for e in codex), "start_hook": "SessionStart" in codex},
        "cursor": {"capture": any(e != "sessionStart" for e in cursor), "start_hook": "sessionStart" in cursor},
        "opencode": {"capture": opencode and "const CAPTURE = true;" in plugin.read_text(encoding="utf-8"),
                     "start_hook": opencode},
        "copilot": {"capture": _ours(root / COPILOT_HOOKS_FILE, f"-m {HOOK_MODULE}"), "start_hook": False},
    }


# ---- MCP-only IDEs -----------------------------------------------------------------------------------------
# ide -> (label, config base ("home"|"root"), config path, servers key, context base, context path)
MCP_IDES: dict[str, tuple[str, str, str, str, str | None, str | None]] = {
    "copilot-cli": ("Copilot CLI", "home", ".github/copilot/mcp.json", "servers", "root",
                    ".github/copilot-instructions.md"),
    "roo-code": ("Roo Code", "root", ".roo/mcp.json", "mcpServers", "root", ".roo/rules/cairn-context.md"),
    "warp": ("Warp", "home", ".warp/mcp.json", "mcpServers", "root", "WARP.md"),
    "goose": ("Goose", "home", ".config/goose/config.yaml", "mcpServers", None, None),
}


def _mcp_ide_paths(ide: str, root: Path | str | None, home: Path | str | None) -> tuple[Path, Path | None]:
    if ide not in MCP_IDES:
        raise ValueError(f"unknown MCP IDE: {ide} (known: {', '.join(MCP_IDES)})")
    _, cfg_base, cfg_rel, _, ctx_base, ctx_rel = MCP_IDES[ide]
    base = {"home": _home(home), "root": _root(root)}
    return base[cfg_base] / cfg_rel, (base[ctx_base] / ctx_rel if ctx_base and ctx_rel else None)


_YAML_PLAIN = re.compile(r"^[A-Za-z0-9_./\\~+=-][A-Za-z0-9_ ./\\~+=:-]*$")


def _yaml_scalar(value: str) -> str:
    return value if _YAML_PLAIN.match(value) and ": " not in value and not value.endswith(" ") else json.dumps(value)


def goose_entry_yaml(command: str, args: Iterable[str], with_header: bool = False) -> str:
    args = list(args)
    lines = ["mcpServers:"] if with_header else []
    lines += [f"  {MARK}:", f"    command: {_yaml_scalar(command)}"]
    lines += ["    args:", *(f"      - {_yaml_scalar(a)}" for a in args)] if args else ["    args: []"]
    return "\n".join(lines)


_GOOSE_BLOCK = re.compile(rf"( {{2}}{MARK}:\n(?:.*\n)*?(?= {{2}}\S|\n\n|^\S|$))", re.MULTILINE)
_GOOSE_HAS = re.compile(rf"^ {{2}}{MARK}:\s*$", re.MULTILINE)


def merge_goose_yaml(text: str | None, command: str, args: Iterable[str]) -> str:
    """Add/replace the ``cairn`` server under ``mcpServers:`` in Goose's YAML config (a text edit, so the
    rest of the file, comments included, is untouched)."""
    if text is None:
        return goose_entry_yaml(command, args, True) + "\n"
    if _GOOSE_HAS.search(text) and "mcpServers:" in text:
        if not _GOOSE_BLOCK.search(text + ("" if text.endswith("\n") else "\n")):
            raise IntegrationError("found mcpServers/cairn markers but could not locate a replaceable cairn block")
        body = text if text.endswith("\n") else text + "\n"
        return _GOOSE_BLOCK.sub(lambda _m: goose_entry_yaml(command, args) + "\n", body, count=1)
    if "mcpServers:" in text:
        i = text.index("mcpServers:") + len("mcpServers:")
        return text[:i] + "\n" + goose_entry_yaml(command, args) + text[i:]
    return text.rstrip() + "\n\n" + goose_entry_yaml(command, args, True) + "\n"


def remove_goose_yaml(text: str) -> str:
    body = text if text.endswith("\n") else text + "\n"
    if not _GOOSE_HAS.search(body):
        return text
    lines = _GOOSE_BLOCK.sub("", body, count=1).split("\n")
    for i, line in enumerate(lines):  # drop an mcpServers header left without children
        if line.rstrip() == "mcpServers:":
            nxt = next((ln for ln in lines[i + 1:] if ln.strip()), None)
            if nxt is None or not nxt[0].isspace():
                del lines[i]
            break
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out + "\n" if out else ""


def install_mcp_ide(ide: str, *, root: Path | str | None = None, home: Path | str | None = None,
                    command: str = DEFAULT_MCP_COMMAND, args: Iterable[str] = DEFAULT_MCP_ARGS) -> dict:
    """MCP-only integration: register ``cairn`` as an MCP server for ``ide`` and seed its context file."""
    rep = _report(ide)
    try:
        config_path, context_path = _mcp_ide_paths(ide, root, home)
    except ValueError as exc:
        return _fail(rep, str(exc))
    args = list(args)
    try:
        if ide == "goose":
            text = config_path.read_text(encoding="utf-8") if config_path.exists() else None
            new = merge_goose_yaml(text, command, args)
            if new != text:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(new, encoding="utf-8")
                rep["written"].append(str(config_path))
        elif ide == "warp" and not config_path.parent.exists():
            rep["notes"].append("~/.warp/ not found; MCP may need to be configured via the Warp Drive UI")
        elif write_mcp_json_config(config_path, command, args, MCP_IDES[ide][3]):
            rep["written"].append(str(config_path))
    except (IntegrationError, OSError) as exc:
        return _fail(rep, str(exc))
    if context_path is not None:
        inject_context_into_markdown_file(context_path, PLACEHOLDER_CONTEXT)
        rep["written"].append(str(context_path))
    rep["notes"].append(f"MCP-only integration: search tools and context; transcript capture is not available for "
                        f"{MCP_IDES[ide][0]}. Restart it to pick up the MCP server.")
    return rep


def uninstall_mcp_ide(ide: str, *, root: Path | str | None = None, home: Path | str | None = None) -> dict:
    rep = _report(ide)
    try:
        config_path, context_path = _mcp_ide_paths(ide, root, home)
    except ValueError as exc:
        return _fail(rep, str(exc))
    try:
        if ide == "goose":
            if config_path.exists():
                text = config_path.read_text(encoding="utf-8")
                new = remove_goose_yaml(text)
                if new != text:
                    if new:
                        config_path.write_text(new, encoding="utf-8")
                    else:
                        config_path.unlink()
                    rep["removed"].append(str(config_path))
        elif remove_mcp_json_config(config_path, MCP_IDES[ide][3]):
            rep["removed"].append(str(config_path))
    except (CorruptConfigError, OSError) as exc:
        return _fail(rep, str(exc))
    if context_path is not None and remove_context_block(context_path):
        rep["removed"].append(str(context_path))
    return rep


def mcp_ide_status(ide: str, *, root: Path | str | None = None, home: Path | str | None = None) -> dict:
    config_path, context_path = _mcp_ide_paths(ide, root, home)
    if ide == "goose":
        registered = config_path.exists() and bool(_GOOSE_HAS.search(config_path.read_text(encoding="utf-8")))
    else:
        registered = mcp_json_has_cairn(config_path, MCP_IDES[ide][3])
    return {"agent": ide, "label": MCP_IDES[ide][0], "config": str(config_path), "installed": registered,
            "context": has_context_block(context_path) if context_path else None}


# ---- hook event map (for docs / the integration note) ------------------------------------------------------
def _event_map(rows: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for native, ours in rows:
        out.setdefault(native, []).append(ours)
    return out


HOOK_EVENTS: dict[str, dict[str, list[str]]] = {
    "claude-code": _event_map((ev if ev != "PreToolUse" else f"{ev}({m})", ours)
                              for ev, m, ours, _, _ in CLAUDE_CODE_HOOKS),
    "codex": _event_map((ev, ours) for ev, _, ours, _ in CODEX_HOOKS),
    "cursor": _event_map(CURSOR_HOOKS),
    "windsurf": _event_map(WINDSURF_EVENTS.items()),
    "antigravity": _event_map(ANTIGRAVITY_EVENTS.items()),
    "gemini-cli": _event_map(GEMINI_CLI_EVENTS.items()),
    "opencode": {"chat.message (user)": ["session-init"], "tool.execute.after": ["observation"],
                 "experimental.session.compacting": ["summarize"], "event: session.idle": ["summarize"],
                 "event: session.deleted": ["session-end"]},
    "copilot": _event_map(COPILOT_HOOKS),
}
PLATFORM_FLAGS = {"claude-code": "claude-code", "codex": "codex", "cursor": "cursor", "windsurf": "windsurf",
                  "antigravity": ANTIGRAVITY_PLATFORM, "gemini-cli": GEMINI_CLI_PLATFORM, "opencode": "opencode",
                  "copilot": "copilot"}


# ---- skills ------------------------------------------------------------------------------------------------
SKILL_MARKER = ".cairn-skill"


def skills_dir() -> Path:
    """The bundled product skills (``<name>/SKILL.md`` plus the files each one references)."""
    return Path(__file__).resolve().parent / "skills"


def _frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\n([\s\S]*?)\n---", text)
    out: dict[str, str] = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if km and km.group(2):
            out[km.group(1)] = km.group(2).strip().strip('"')
    return out


def list_skills() -> list[dict]:
    """``[{name, description, path, files}]`` for every bundled skill."""
    out = []
    for d in sorted(p for p in skills_dir().iterdir() if (p / "SKILL.md").is_file()):
        meta = _frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))
        files = sorted(f.relative_to(d).as_posix() for f in d.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
        out.append({"name": meta.get("name", d.name), "description": meta.get("description", ""), "path": str(d),
                    "files": files})
    return out


def install_skills(dest: Path | str, names: Iterable[str] | None = None) -> list[str]:
    """Copy bundled skills into ``dest`` (e.g. ``<repo>/.claude/skills``). A same-named folder that Cairn did
    not install is left alone; Cairn's own copies are refreshed. Returns the installed names."""
    dest = Path(dest)
    wanted = set(names) if names is not None else None
    installed = []
    for skill in list_skills():
        name = Path(skill["path"]).name
        if wanted is not None and name not in wanted:
            continue
        target = dest / name
        if target.exists() and not (target / SKILL_MARKER).exists():
            continue
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill["path"], target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (target / SKILL_MARKER).write_text("installed by cairn; removed by uninstall\n", encoding="utf-8")
        installed.append(name)
    return installed


def uninstall_skills(dest: Path | str) -> list[str]:
    dest = Path(dest)
    removed = []
    for skill in list_skills():
        target = dest / Path(skill["path"]).name
        if (target / SKILL_MARKER).exists():
            shutil.rmtree(target)
            removed.append(target.name)
    return removed


# ---- search (the OpenCode plugin's cairn_search tool) ------------------------------------------------------
def search_text(cwd: Path | str, query: str, limit: int = 10) -> str:
    """Keyword search of the session store holding ``cwd``, as an index an agent can read."""
    from . import schema, sqlsearch
    from .fmt import estimate_tokens, format_datetime
    from .modes import load_mode
    from .settings import load as load_settings
    root = find_store_root(str(cwd))
    if root is None or not schema.store_path(root).exists():
        return f'No results found for "{query}".'
    try:
        db = schema.connect(schema.store_path(root), readonly=True)
    except Exception:  # noqa: BLE001 - a missing/locked store is "no results", never a crash in the agent
        return f'No results found for "{query}".'
    try:
        rows = sqlsearch.search_observations(db, query, limit=limit, loose=True)
    except Exception:  # noqa: BLE001
        rows = []
    finally:
        db.close()
    if not rows:
        return f'No results found for "{query}".'
    mode = load_mode(load_settings(root).get("mode"))
    lines = [f'Found {len(rows)} observation(s) matching "{query}":', "", "| ID | Time | T | Title | Read |",
             "|----|------|---|-------|------|"]
    for o in rows:
        body = " ".join(str(o.get(k) or "") for k in ("title", "subtitle", "narrative", "facts"))
        title = " ".join(str(o.get("title") or "Untitled").split())
        lines.append(f"| #{o['id']} | {format_datetime(o['created_at_epoch'])} | {mode.type_icon(o['type'])} | "
                     f"{title} | ~{estimate_tokens(body)} |")
    lines += ["", "Use cairn_session_observations(ids=[...]) for full details."]
    return "\n".join(lines)


# ---- dispatch ----------------------------------------------------------------------------------------------
AGENTS = ("cursor", "codex", "windsurf", "opencode", "antigravity", "gemini-cli", *MCP_IDES)


def install(agent: str, *, root: Path | str | None = None, home: Path | str | None = None,
            python: str | None = None, mcp_command: str = DEFAULT_MCP_COMMAND,
            mcp_args: Iterable[str] = DEFAULT_MCP_ARGS, **kw: Any) -> dict:
    """Install one agent integration by name (see ``AGENTS``)."""
    if agent == "cursor":
        return install_cursor(root, target=kw.get("target", "project"), home=home, python=python)
    if agent == "codex":
        return install_codex(home, python=python, mcp_command=mcp_command, mcp_args=mcp_args, runner=kw.get("runner"))
    if agent == "windsurf":
        return install_windsurf(root, home=home, python=python)
    if agent == "opencode":
        return install_opencode(home, python=python, root=root)
    if agent == "antigravity":
        return install_antigravity(home, python=python, mcp_command=mcp_command, mcp_args=mcp_args)
    if agent == "gemini-cli":
        return install_gemini_cli(home, python=python)
    if agent in MCP_IDES:
        return install_mcp_ide(agent, root=root, home=home, command=mcp_command, args=mcp_args)
    return _fail(_report(agent), f"unknown agent: {agent} (known: {', '.join(AGENTS)})")


def uninstall(agent: str, *, root: Path | str | None = None, home: Path | str | None = None, **kw: Any) -> dict:
    if agent == "cursor":
        return uninstall_cursor(root, target=kw.get("target", "project"), home=home)
    if agent == "codex":
        return uninstall_codex(home, runner=kw.get("runner"))
    if agent == "windsurf":
        return uninstall_windsurf(root, home=home)
    if agent == "opencode":
        return uninstall_opencode(home)
    if agent == "antigravity":
        return uninstall_antigravity(home)
    if agent == "gemini-cli":
        return uninstall_gemini_cli(home)
    if agent in MCP_IDES:
        return uninstall_mcp_ide(agent, root=root, home=home)
    return _fail(_report(agent), f"unknown agent: {agent} (known: {', '.join(AGENTS)})")


def status(agent: str, *, root: Path | str | None = None, home: Path | str | None = None) -> dict:
    if agent == "cursor":
        return cursor_status(root, home=home)
    if agent == "codex":
        return codex_status(home)
    if agent == "windsurf":
        return windsurf_status(root, home=home)
    if agent == "opencode":
        return opencode_status(home)
    if agent == "antigravity":
        return antigravity_status(home)
    if agent == "gemini-cli":
        return gemini_cli_status(home)
    if agent in MCP_IDES:
        return mcp_ide_status(agent, root=root, home=home)
    return {"agent": agent, "installed": False, "error": f"unknown agent: {agent}"}


def main(argv: list[str] | None = None) -> int:
    """``python -m cairn.engines.recall.integrations search|install|uninstall|status ...``"""
    p = argparse.ArgumentParser(prog="cairn-recall-integrations")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="keyword search of the session memory for a directory")
    s.add_argument("query")
    s.add_argument("--cwd", default=os.getcwd())
    s.add_argument("--limit", type=int, default=10)
    for name in ("install", "uninstall", "status"):
        a = sub.add_parser(name)
        a.add_argument("agent", choices=AGENTS)
        a.add_argument("--root", default=None)
        a.add_argument("--target", default="project", choices=CURSOR_TARGETS)
    args = p.parse_args(argv)
    if args.cmd == "search":
        sys.stdout.write(search_text(args.cwd, args.query, args.limit) + "\n")
        return 0
    if args.cmd == "status":
        res = status(args.agent, root=args.root)
    elif args.cmd == "install":
        res = install(args.agent, root=args.root, target=args.target)
    else:
        res = uninstall(args.agent, root=args.root, target=args.target)
    sys.stdout.write(json.dumps(res, indent=2) + "\n")
    return 0 if res.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
