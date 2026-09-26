"""Context files written into the repository: a ``<cairn-context>`` activity timeline in the CLAUDE.md
(or CLAUDE.local.md) of each folder agents work in, the same block in AGENTS.md and other markdown
context files, the Cursor rules file refreshed after every session summary, and the
``cairn sessions claude-md generate|clean`` commands.

Only the text between the tags is ever rewritten; everything around the block belongs to the user.
Every code point written into these auto-loaded files is in the Basic Multilingual Plane: an agent that
truncates its context in the middle of a surrogate pair would otherwise send an invalid request on
every turn, and the bad bytes survive a context reset because they live in the file. Standard library only.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import posixpath
import re
import sqlite3
import subprocess
from collections.abc import Iterable
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import schema, sqlsearch
from .context import inject_context
from .fmt import format_time, group_by_date
from .modes import Mode, load_mode
from .projects import expand_home, find_store_root, matches_any_glob, project_context
from .settings import cairn_home
from .settings import load as load_settings
from .store import Store, parse_list

log = logging.getLogger("cairn.recall")

CONTEXT_TAG_OPEN = "<cairn-context>"
CONTEXT_TAG_CLOSE = "</cairn-context>"
CLAUDE_MD_FILENAME = "CLAUDE.md"
CLAUDE_LOCAL_MD_FILENAME = "CLAUDE.local.md"
DEFAULT_OBSERVATION_LIMIT = 50

# Never written into, wherever they appear below the repository root (.cairn holds the store itself).
EXCLUDED_UNSAFE_DIRECTORIES = frozenset({"res", ".git", "build", "node_modules", "__pycache__", ".cairn"})
# Skipped when walking the repository for folders / context files.
IGNORED_WALK_DIRS = frozenset({"node_modules", ".git", ".next", "dist", "build", ".cache", "__pycache__", ".venv",
                               "venv", ".idea", ".vscode", "coverage", ".cairn", ".open-next", ".turbo"})


# ---- BMP-safe text --------------------------------------------------------------------------------------
ASTRAL_FALLBACKS = {
    "\U0001F534": "●",  # bugfix
    "\U0001F7E3": "◆",  # feature
    "\U0001F504": "↻",  # refactor
    "\U0001F535": "○",  # discovery
    "\U0001F6A8": "⚠",  # security alert
    "\U0001F510": "⚷",  # security note
    "\U0001F92B": "⊘",  # sensitive
    "\U0001F6E0": "⚒",  # tool / build
    "\U0001F50D": "⌕",  # search / discovery
    "\U0001F3AF": "◎",  # session
    "\U0001F4AC": "”",  # prompt
    "\U0001F9E0": "◈",  # decision (timeline legend)
}
FALLBACK_BULLET = "•"


def to_bmp_safe(text: str) -> str:
    """``text`` with only code points <= U+FFFF: known type markers map to distinct BMP glyphs, any other
    astral character becomes a neutral bullet, lone surrogates are dropped."""
    if not text:
        return text
    out = []
    for ch in text:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            continue
        out.append(ch if cp <= 0xFFFF else ASTRAL_FALLBACKS.get(ch, FALLBACK_BULLET))
    return "".join(out)


# ---- the tagged block -------------------------------------------------------------------------------------
def replace_tagged_content(existing: str, new: str) -> str:
    """``existing`` with its ``<cairn-context>`` block replaced by ``new`` (appended when there is no
    complete block; the whole file when ``existing`` is empty)."""
    block = f"{CONTEXT_TAG_OPEN}\n{new}\n{CONTEXT_TAG_CLOSE}"
    if not existing:
        return block
    start, end = existing.find(CONTEXT_TAG_OPEN), existing.find(CONTEXT_TAG_CLOSE)
    if start != -1 and end != -1:
        return existing[:start] + block + existing[end + len(CONTEXT_TAG_CLOSE):]
    return f"{existing}\n\n{block}"


def _in_git_dir(path: str | Path) -> bool:
    return ".git" in Path(os.path.abspath(path)).parts


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def get_target_filename(settings: dict | None = None) -> str:
    s = settings if settings is not None else load_settings(None)
    return CLAUDE_LOCAL_MD_FILENAME if s.get("folder_use_local_md") else CLAUDE_MD_FILENAME


def write_claude_md_to_folder(folder_path: str | Path, new_content: str, target_filename: str | None = None,
                              settings: dict | None = None) -> Path | None:
    """Write ``new_content`` into the tagged block of the folder's context file. Never creates folders and
    never writes inside ``.git``; returns the file written, or None."""
    if _in_git_dir(folder_path):
        return None
    folder = Path(folder_path)
    if not folder.exists():
        log.debug("folder context: skipping non-existent folder %s", folder)
        return None
    target = folder / (target_filename or get_target_filename(settings))
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    _atomic_write(target, replace_tagged_content(existing, to_bmp_safe(new_content)))
    return target


def write_agents_md(agents_path: str | Path, context: str) -> None:
    """Replace (or append) the ``<cairn-context>`` block of an AGENTS.md with ``context``."""
    if not agents_path or not str(agents_path).strip():
        return
    path = Path(agents_path)
    if _in_git_dir(path):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    final = replace_tagged_content(existing, f"# Memory Context\n\n{context}")
    try:
        _atomic_write(path, final)
    except OSError as exc:
        log.error("failed to write AGENTS.md %s: %s", path, exc)


def inject_context_into_markdown_file(file_path: str | Path, context_content: str,
                                      header_line: str | None = None) -> None:
    """Put ``context_content`` into the file's ``<cairn-context>`` block, creating the file (and its
    folder, headed by ``header_line`` when given) if needed."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wrapped = f"{CONTEXT_TAG_OPEN}\n{to_bmp_safe(context_content)}\n{CONTEXT_TAG_CLOSE}"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        start, end = existing.find(CONTEXT_TAG_OPEN), existing.find(CONTEXT_TAG_CLOSE)
        if start != -1 and end != -1:
            existing = existing[:start] + wrapped + existing[end + len(CONTEXT_TAG_CLOSE):]
        else:
            existing = existing.rstrip() + "\n\n" + wrapped + "\n"
        path.write_text(existing, encoding="utf-8")
    else:
        path.write_text(f"{header_line}\n\n{wrapped}\n" if header_line else wrapped + "\n", encoding="utf-8")


# ---- timeline text ----------------------------------------------------------------------------------------
TABLE_HEADER = "| ID | Time | T | Title | Read | Work |\n|-----|------|---|-------|------|------|"
SESSION_ICON = "\U0001F3AF"
_DATE_LINE = re.compile(r"^###\s+(.+)$")
_ROW = re.compile(r"^\|\s*(#[S]?\d+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|")
_CLOCK = re.compile(r"(\d+):(\d+)\s*(AM|PM)", re.IGNORECASE)


def _observation_index(obs: dict, mode: Mode) -> str:
    size = sum(len(obs.get(k) or "") for k in ("title", "subtitle", "narrative", "facts"))
    work = int(obs.get("discovery_tokens") or 0)
    work_display = f"{mode.work_emoji(obs.get('type') or '')} {work}" if work > 0 else "-"
    return (f"| #{obs['id']} | {format_time(obs['created_at_epoch'])} | {mode.type_icon(obs.get('type') or '')} |"
            f" {obs.get('title') or 'Untitled'} | ~{-(-size // 4)} | {work_display} |")


def _session_index(session: dict) -> str:
    title = session.get("request") or f"Session {(session.get('memory_session_id') or '')[:8] or 'unknown'}"
    return f"| #S{session['id']} | {format_time(session['created_at_epoch'])} | {SESSION_ICON} | {title} | - | - |"


def file_timeline_text(file_path: str, observations: list[dict], sessions: list[dict], mode: Mode) -> str:
    """The by-file search rendering: newest first, grouped by day, one table row per record."""
    total = len(observations) + len(sessions)
    if total == 0:
        return f'No results found for file "{file_path}"'
    combined = ([("observation", o) for o in observations] + [("session", s) for s in sessions])
    combined.sort(key=lambda item: item[1]["created_at_epoch"], reverse=True)
    lines = [f'Found {total} result(s) for file "{file_path}"', ""]
    for day, items in group_by_date(combined, lambda item: item[1]["created_at"]):
        lines += [f"### {day}", "", TABLE_HEADER]
        lines += [_observation_index(d, mode) if kind == "observation" else _session_index(d) for kind, d in items]
        lines.append("")
    return "\n".join(lines)


def _parse_day(text: str) -> datetime | None:
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_timeline_for_claude_md(timeline_text: str) -> str:
    """Re-render a timeline table (``| #id | time | icon | title | tokens |`` rows under ``### <day>``
    headings) as the ``# Recent Activity`` section of a folder context file; '' when it has no rows."""
    observations: list[dict] = []
    last_time = ""
    current: datetime | None = None
    for line in (timeline_text or "").split("\n"):
        day = _DATE_LINE.match(line)
        if day:
            parsed = _parse_day(day.group(1).strip())
            if parsed is not None:
                current = parsed
            continue
        m = _ROW.match(line)
        if not m:
            continue
        oid, time_str, icon, title, tokens = m.groups()
        if time_str.strip() in ("″", '"'):
            time = last_time
        else:
            time = time_str.strip()
            last_time = time
        base = current or datetime.now()
        clock = _CLOCK.search(time)
        if clock:
            hours, minutes = int(clock.group(1)), int(clock.group(2))
            pm = clock.group(3).upper() == "PM"
            if pm and hours != 12:
                hours += 12
            if not pm and hours == 12:
                hours = 0
            base = base.replace(hour=hours % 24, minute=minutes % 60, second=0, microsecond=0)
        observations.append({"id": oid.strip(), "time": time, "icon": icon.strip(), "title": title.strip(),
                             "tokens": tokens.strip(), "epoch": int(base.timestamp() * 1000)})
    if not observations:
        return ""
    lines = ["# Recent Activity", ""]
    for day, items in group_by_date(observations, lambda o: o["epoch"]):
        lines += [f"### {day}", "", "| ID | Time | T | Title | Read |", "|----|------|---|-------|------|"]
        previous = ""
        for o in items:
            shown = '"' if o["time"] == previous else o["time"]
            lines.append(f"| {o['id']} | {shown} | {o['icon']} | {o['title']} | {o['tokens']} |")
            previous = o["time"]
        lines.append("")
    return "\n".join(lines).strip()


# ---- repository paths -------------------------------------------------------------------------------------
def _root_forms(root: str | Path) -> tuple[str, ...]:
    a = os.path.normpath(os.path.abspath(str(root)))
    r = os.path.realpath(a)
    return (a,) if a == r else (a, r)


def _within(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _repo_relative(path: str, roots: tuple[str, ...]) -> str | None:
    """``path`` (absolute, or relative to the repository root) as a normalised repository-relative POSIX
    path ('' for the root itself); None when it lies outside the repository."""
    if not os.path.isabs(path):
        rel = os.path.normpath(path)
        if rel == os.pardir or rel.startswith(os.pardir + os.sep):
            return None
        return "" if rel == os.curdir else rel.replace(os.sep, "/")
    for cand in dict.fromkeys((os.path.normpath(path), os.path.realpath(path))):
        for root in roots:
            if _within(cand, root):
                rel = os.path.relpath(cand, root)
                return "" if rel == os.curdir else rel.replace(os.sep, "/")
    return None


def _valid_file_path(file_path: str, roots: tuple[str, ...]) -> str | None:
    """The repository-relative form of a recorded file path, or None when it is not a path worth a
    context file: empty, home-relative, a URL, outside the repository, containing spaces or '#', or with
    a repeated segment (``src/src/``, a common hallucination)."""
    if not isinstance(file_path, str) or not file_path.strip():
        return None
    if file_path.startswith(("~", "http://", "https://")):
        return None
    rel = _repo_relative(file_path, roots)
    if rel is None or " " in rel or "#" in rel:
        return None
    segments = [s for s in rel.split("/") if s not in ("", ".", "..")]
    if any(a == b for a, b in pairwise(segments)):
        return None
    return rel


def _folder_abs(roots: tuple[str, ...], rel_folder: str) -> str:
    return os.path.join(roots[0], *rel_folder.split("/")) if rel_folder else roots[0]


def _str_list(value: Any, name: str) -> list[str]:
    """A JSON list setting as a list of strings (a TOML array arrives as its Python repr)."""
    if isinstance(value, (list, tuple)):
        items: Any = list(value)
    else:
        text = str(value or "").strip() or "[]"
        try:
            items = json.loads(text)
        except ValueError:
            try:
                items = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                log.warning("folder context: could not parse %s setting", name)
                return []
    return [v for v in items if isinstance(v, str)] if isinstance(items, list) else []


def _exclusions(settings: dict, roots: tuple[str, ...]) -> list[str]:
    """``folder_md_exclude`` entries as repository-relative folders (relative entries are resolved
    against the repository root; entries outside it cannot match)."""
    out = []
    for raw in _str_list(settings.get("folder_md_exclude"), "folder_md_exclude"):
        p = expand_home(raw.strip())
        if not p:
            continue
        rel = _repo_relative(p if os.path.isabs(p) else os.path.join(roots[0], p), roots)
        if rel is not None:
            out.append(rel)
    return out


def _is_excluded(rel_folder: str, exclusions: list[str]) -> bool:
    return any(ex == "" or rel_folder == ex or rel_folder.startswith(ex + "/") for ex in exclusions)


def _skip_reason(rel_folder: str, roots: tuple[str, ...], exclusions: list[str]) -> str | None:
    folder = _folder_abs(roots, rel_folder)
    if rel_folder == "" or os.path.exists(os.path.join(folder, ".git")):
        return "project root"
    if any(seg in EXCLUDED_UNSAFE_DIRECTORIES for seg in rel_folder.split("/")):
        return "unsafe directory"
    if exclusions and _is_excluded(rel_folder, exclusions):
        return "excluded folder"
    if not _within(os.path.realpath(folder), os.path.realpath(roots[0])):
        return "path escapes project root"
    return None


def _direct_child(file_path: str, rel_folder: str, roots: tuple[str, ...]) -> bool:
    rel = _repo_relative(file_path, roots) if isinstance(file_path, str) and file_path else None
    return rel is not None and posixpath.dirname(rel) == rel_folder


def _touches_folder(row: dict, columns: Iterable[str], rel_folder: str, roots: tuple[str, ...]) -> bool:
    return any(_direct_child(f, rel_folder, roots) for c in columns for f in parse_list(row.get(c)))


def folder_activity(db: sqlite3.Connection, root: str | Path, rel_folder: str, project: str | None,
                    limit: int = DEFAULT_OBSERVATION_LIMIT) -> dict:
    """Observations and session summaries that read or changed a file directly inside ``rel_folder``
    (a repository-relative folder), newest first. Recorded paths may be absolute or repository-relative."""
    roots = _root_forms(root)
    found = sqlsearch.find_by_file(db, rel_folder, limit=limit, is_folder=True, project=project or None)
    return {"observations": [o for o in found["observations"]
                             if _touches_folder(o, ("files_modified", "files_read"), rel_folder, roots)],
            "sessions": [s for s in found["sessions"]
                         if _touches_folder(s, ("files_edited", "files_read"), rel_folder, roots)]}


def _limit(settings: dict) -> int:
    try:
        return int(settings.get("context_observations") or 0) or DEFAULT_OBSERVATION_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_OBSERVATION_LIMIT


# ---- automatic folder timelines ---------------------------------------------------------------------------
def update_folder_claude_md_files(root: str | Path, file_paths: Iterable[str], project: str,
                                  settings: dict | None = None, store: Store | None = None) -> list[Path]:
    """After observations are stored: refresh the ``<cairn-context>`` timeline of every folder that
    directly holds one of ``file_paths`` (absolute or repository-relative). Returns the files written.

    A folder is skipped when it is the repository root (or holds a nested ``.git``), sits in an unsafe or
    excluded directory, or its own context file was among ``file_paths`` (the agent is editing it). A
    folder without recorded activity gets no new file; an existing file then has its block emptied,
    unless the folder matches ``folder_md_skeleton_denylist``, in which case it is left alone."""
    settings = settings if settings is not None else load_settings(root)
    if not settings.get("folder_context"):
        return []
    roots = _root_forms(root)
    limit = _limit(settings)
    filename = get_target_filename(settings)
    exclusions = _exclusions(settings, roots)
    denylist = _str_list(settings.get("folder_md_skeleton_denylist"), "folder_md_skeleton_denylist")
    paths = [p for p in (file_paths or []) if isinstance(p, str) and p]

    active = set()
    for p in paths:
        if posixpath.basename(p.replace("\\", "/")) in (CLAUDE_MD_FILENAME, CLAUDE_LOCAL_MD_FILENAME):
            rel = _repo_relative(p, roots)
            if rel is not None:
                active.add(posixpath.dirname(rel))

    folders: dict[str, None] = {}
    for p in paths:
        rel = _valid_file_path(p, roots)
        if rel is None:
            log.debug("folder context: skipping invalid file path %s", p)
            continue
        rel_folder = posixpath.dirname(rel)
        reason = _skip_reason(rel_folder, roots, exclusions)
        if reason is None and rel_folder in active:
            reason = "active context file"
        if reason:
            log.debug("folder context: skipping %s (%s)", rel_folder or ".", reason)
            continue
        folders[rel_folder] = None
    if not folders:
        return []

    own = store is None
    if own:
        try:
            store = Store.open(root, readonly=True)
        except (FileNotFoundError, sqlite3.Error) as exc:
            log.debug("folder context: no store to read (%s)", exc)
            return []
    mode = load_mode(settings.get("mode"))
    written: list[Path] = []
    try:
        for rel_folder in folders:
            folder = _folder_abs(roots, rel_folder)
            try:
                found = folder_activity(store.db, roots[0], rel_folder, project, limit)
            except sqlite3.Error as exc:
                log.error("folder context: failed to read the timeline for %s: %s", folder, exc)
                continue
            formatted = format_timeline_for_claude_md(
                file_timeline_text(folder, found["observations"], found["sessions"], mode))
            target = Path(folder) / filename
            empty = formatted.strip() == "" or "*No recent activity*" in formatted
            if empty and (matches_any_glob(folder, denylist) or matches_any_glob(rel_folder, denylist)):
                log.debug("folder context: skeleton %s suppressed in deny-listed %s", filename, folder)
                continue
            if empty and not target.exists():
                continue
            try:
                out = write_claude_md_to_folder(folder, formatted, filename)
            except OSError as exc:
                log.warning("folder context: could not write %s: %s", target, exc)
                continue
            if out is not None:
                written.append(out)
    finally:
        if own:
            store.close()
    return written


# ---- Cursor rules file ------------------------------------------------------------------------------------
CURSOR_CONTEXT_FILENAME = "cairn-context.mdc"
_CURSOR_FRONTMATTER = '---\nalwaysApply: true\ndescription: "Cairn context from past sessions (auto-updated)"\n---\n'
CURSOR_PLACEHOLDER = (_CURSOR_FRONTMATTER + "\n# Memory Context from Past Sessions\n\n"
                      "*No context yet. Complete your first session and context will appear here.*\n\n"
                      "Use Cairn's MCP search tools for manual memory queries.\n")


def cursor_registry_path() -> Path:
    return cairn_home() / "cursor-projects.json"


def cursor_context_path(workspace_path: str | Path) -> Path:
    return Path(workspace_path) / ".cursor" / "rules" / CURSOR_CONTEXT_FILENAME


def read_cursor_registry(registry_file: str | Path | None = None) -> dict:
    """Projects whose Cursor rules file is refreshed after each session: ``{project: {workspacePath,
    installedAt}}``."""
    path = Path(registry_file) if registry_file else cursor_registry_path()
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.error("failed to read the Cursor registry %s, using an empty one: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def write_cursor_registry(registry: dict, registry_file: str | Path | None = None) -> None:
    path = Path(registry_file) if registry_file else cursor_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def register_cursor_project(project_name: str, workspace_path: str | Path,
                            registry_file: str | Path | None = None) -> None:
    registry = read_cursor_registry(registry_file)
    registry[project_name] = {"workspacePath": str(workspace_path), "installedAt": schema.iso()}
    write_cursor_registry(registry, registry_file)
    log.info("registered %s for Cursor context updates (%s)", project_name, workspace_path)


def unregister_cursor_project(project_name: str, registry_file: str | Path | None = None) -> None:
    registry = read_cursor_registry(registry_file)
    if project_name in registry:
        del registry[project_name]
        write_cursor_registry(registry, registry_file)
        log.info("unregistered %s from Cursor context updates", project_name)


def write_context_file(workspace_path: str | Path, context: str) -> Path:
    """Write the always-applied Cursor rule holding ``context``."""
    target = cursor_context_path(workspace_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, (
        f"{_CURSOR_FRONTMATTER}\n# Memory Context from Past Sessions\n\n"
        "The following context is from Cairn, a persistent memory system that tracks your coding sessions.\n\n"
        f"{to_bmp_safe(context)}\n\n---\n"
        "*Updated after last session. Use Cairn's MCP search tools for more detailed queries.*\n"))
    return target


def configure_cursor_mcp(mcp_json_path: str | Path, command: str | None = None,
                         args: list[str] | None = None) -> None:
    """Register Cairn's MCP server in a Cursor ``mcp.json`` (an unreadable file is replaced)."""
    path = Path(mcp_json_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    config: dict = {"mcpServers": {}}
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                config = {"mcpServers": {}}
            if not isinstance(config.get("mcpServers"), dict):
                config["mcpServers"] = {}
        except (OSError, ValueError) as exc:
            log.error("failed to read the MCP config %s, starting fresh: %s", path, exc)
            config = {"mcpServers": {}}
    if command is None:
        from cairn.agents import (
            command as mcp_command,  # the same launcher every agent integration uses
        )
        command, args = mcp_command()
    config["mcpServers"]["cairn"] = {"command": command, "args": list(args or [])}
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def update_cursor_context_for_project(root: str | Path | None, project: str, settings: dict | None = None) -> bool:
    """After a session summary: rewrite the Cursor rules file of the workspace registered for ``project``
    with the project's injected context. A project that was never set up for Cursor is left alone.
    ``root`` is the repository holding the store (default: found from the workspace)."""
    entry = read_cursor_registry().get(project)
    workspace = entry.get("workspacePath") if isinstance(entry, dict) else None
    if not workspace:
        return False
    try:
        store_root = root or find_store_root(workspace)
        context = inject_context(store_root, [project], settings=settings)
        if not context or not context.strip():
            return False
        write_context_file(workspace, context)
        log.debug("updated the Cursor context file for %s in %s", project, workspace)
        return True
    except Exception as exc:  # noqa: BLE001 - a context refresh never fails the summary that triggered it
        log.error("failed to update the Cursor context file for %s: %s", project, exc)
        return False


def setup_cursor_project_context(workspace_root: str | Path, project: str | None = None, *,
                                 root: str | Path | None = None, settings: dict | None = None) -> bool:
    """On a project-level Cursor install: write the rules file from existing memory (or a placeholder) and
    register the project for refreshes. Returns whether real context was written."""
    workspace = Path(workspace_root)
    cursor_context_path(workspace).parent.mkdir(parents=True, exist_ok=True)
    project = project or project_context(str(workspace)).primary
    generated = False
    try:
        store_root = root or find_store_root(workspace)
        if store_root and schema.store_path(store_root).exists():
            context = inject_context(store_root, [project], settings=settings)
            if context and context.strip():
                write_context_file(workspace, context)
                generated = True
    except Exception as exc:  # noqa: BLE001 - the placeholder below covers any failure
        log.debug("initial Cursor context unavailable: %s", exc)
    if not generated:
        cursor_context_path(workspace).write_text(CURSOR_PLACEHOLDER, encoding="utf-8")
    register_cursor_project(project, str(workspace))
    return generated


def remove_cursor_project_context(workspace_root: str | Path, project: str | None = None) -> list[str]:
    """On a project-level Cursor uninstall: delete the rules file and stop refreshing it."""
    removed = []
    target = cursor_context_path(workspace_root)
    if target.exists():
        target.unlink()
        removed.append(str(target))
    unregister_cursor_project(project or project_context(str(workspace_root)).primary)
    return removed


# ---- cairn sessions claude-md generate | clean --------------------------------------------------------------
TYPE_ICONS = {"bugfix": "●", "feature": "◆", "refactor": "↻", "change": "✓", "discovery": "○", "decision": "⚖",
              "session": "◎", "prompt": "”"}
GENERATED_NOTE = "<!-- This section is auto-generated by Cairn. Edit content outside the tags. -->"
_BLOCK = re.compile(re.escape(CONTEXT_TAG_OPEN) + r"[\s\S]*?" + re.escape(CONTEXT_TAG_CLOSE))


def _walk_directories(directory: Path, folders: set[Path], depth: int = 0) -> None:
    if depth > 10:
        return
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        if entry.name in IGNORED_WALK_DIRS or (entry.name.startswith(".") and entry.name != ".claude"):
            continue
        folders.add(Path(entry.path))
        _walk_directories(Path(entry.path), folders, depth + 1)


def tracked_folders(root: str | Path) -> set[Path]:
    """Every folder below ``root`` that holds a git-tracked file (a directory walk outside git)."""
    base = Path(os.path.abspath(root))
    folders: set[Path] = set()
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=base, capture_output=True, text=True, check=True,
                             timeout=120).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("git ls-files failed, walking directories instead: %s", exc)
        _walk_directories(base, folders)
        return folders
    for name in filter(None, out.split("\0")):
        d = (base / name).parent
        while len(str(d)) > len(str(base)) and _within(str(d), str(base)):
            folders.add(d)
            d = d.parent
    return folders


def _type_icon(type_: str) -> str:
    return TYPE_ICONS.get(type_, "•")


def _relevant_file(obs: dict, rel_folder: str, roots: tuple[str, ...]) -> str:
    for column in ("files_modified", "files_read"):
        for f in parse_list(obs.get(column)):
            if _direct_child(f, rel_folder, roots):
                return posixpath.basename(f.replace("\\", "/"))
    return "General"


def format_observations_for_claude_md(observations: list[dict], rel_folder: str, root: str | Path) -> str:
    """The generated ``# Recent Activity`` section: per day, per file, one row per observation."""
    roots = _root_forms(root)
    lines = ["# Recent Activity", "", GENERATED_NOTE, ""]
    if not observations:
        lines.append("*No recent activity*")
        return "\n".join(lines)
    for day, day_obs in group_by_date(observations, lambda o: o["created_at"]):
        lines += [f"### {day}", ""]
        by_file: dict[str, list[dict]] = {}
        for o in day_obs:
            by_file.setdefault(_relevant_file(o, rel_folder, roots), []).append(o)
        for name, file_obs in by_file.items():
            lines += [f"**{name}**", "| ID | Time | T | Title | Read |", "|----|------|---|-------|------|"]
            previous = ""
            for o in file_obs:
                time = format_time(o["created_at_epoch"])
                shown = '"' if time == previous else time
                size = sum(len(o.get(k) or "") for k in ("title", "subtitle", "narrative", "facts"))
                lines.append(f"| #{o['id']} | {shown} | {_type_icon(o.get('type') or '')} |"
                             f" {o.get('title') or 'Untitled'} | ~{-(-size // 4)} |")
                previous = time
            lines.append("")
    return "\n".join(lines).strip()


def _regenerate_folder(db: sqlite3.Connection, rel_folder: str, roots: tuple[str, ...], project: str,
                       dry_run: bool, limit: int, filename: str, exclusions: list[str]) -> dict:
    folder = _folder_abs(roots, rel_folder)
    if not os.path.isdir(folder):
        return {"success": False, "count": 0, "error": "Folder no longer exists"}
    reason = _skip_reason(rel_folder, roots, exclusions)
    if reason:
        return {"success": False, "count": 0, "skipped": True, "error": reason}
    observations = folder_activity(db, roots[0], rel_folder, project, limit)["observations"][:limit]
    if not observations:
        return {"success": False, "count": 0, "skipped": True, "error": "No observations for folder"}
    if dry_run:
        return {"success": True, "count": len(observations)}
    try:
        write_claude_md_to_folder(folder, format_observations_for_claude_md(observations, rel_folder, roots[0]),
                                  filename)
    except OSError as exc:
        log.warning("failed to regenerate %s: %s", rel_folder, exc)
        return {"success": False, "count": 0, "error": str(exc)}
    return {"success": True, "count": len(observations)}


def generate_claude_md(root: str | Path, dry_run: bool = False, project: str | None = None) -> int:
    """Write the activity timeline into the context file of every repository folder with observations.
    Prints a report; returns the exit code."""
    roots = _root_forms(root)
    settings = load_settings(roots[0])
    limit = _limit(settings)
    filename = get_target_filename(settings)
    project = project or project_context(roots[0]).primary
    exclusions = _exclusions(settings, roots)
    print(f"Generating {filename} timelines for {project}{' (dry run)' if dry_run else ''}")
    folders = tracked_folders(roots[0])
    if not folders:
        print("No folders found in project")
        return 0
    print(f"Found {len(folders)} folders in project")
    if not schema.store_path(roots[0]).exists():
        print("No session store found, no observations to process")
        return 0
    try:
        store = Store.open(roots[0], readonly=True)
    except (OSError, sqlite3.Error) as exc:
        print(f"Could not open the session store: {exc}")
        return 1
    written = skipped = errors = 0
    try:
        for folder in sorted(folders):
            rel_folder = os.path.relpath(folder, roots[0]).replace(os.sep, "/")
            result = _regenerate_folder(store.db, rel_folder, roots, project, dry_run, limit, filename, exclusions)
            target = posixpath.join(rel_folder, filename)
            if result["success"]:
                written += 1
                print(f"  {'would write' if dry_run else 'wrote'} {target} ({result['count']} observations)")
            elif result.get("skipped"):
                skipped += 1
            else:
                errors += 1
                print(f"  error {rel_folder}: {result['error']}")
    except sqlite3.Error as exc:
        print(f"Fatal error during generation: {exc}")
        return 1
    finally:
        store.close()
    print(f"Done: {len(folders)} folders, {written} with observations, {skipped} without, {errors} errors"
          f"{' (dry run, nothing written)' if dry_run else ''}")
    return 0


def _clean_single_file(path: Path, dry_run: bool) -> str:
    stripped = _BLOCK.sub("", path.read_text(encoding="utf-8")).strip()
    if stripped == "":
        if not dry_run:
            path.unlink()
        return "deleted"
    if not dry_run:
        path.write_text(stripped, encoding="utf-8")
    return "cleaned"


def clean_claude_md(root: str | Path, dry_run: bool = False) -> int:
    """Remove every ``<cairn-context>`` block from the repository's CLAUDE.md / CLAUDE.local.md files,
    deleting files left empty. Prints a report; returns the exit code."""
    base = Path(os.path.abspath(root))
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_WALK_DIRS)
        for name in (CLAUDE_MD_FILENAME, CLAUDE_LOCAL_MD_FILENAME):
            if name not in filenames:
                continue
            p = Path(dirpath) / name
            try:
                if CONTEXT_TAG_OPEN in p.read_text(encoding="utf-8"):
                    files.append(p)
            except (OSError, UnicodeDecodeError):
                continue
    if not files:
        print("No context files with auto-generated content found")
        return 0
    print(f"Found {len(files)} context files with auto-generated content{' (dry run)' if dry_run else ''}")
    counts = {"deleted": 0, "cleaned": 0, "errors": 0}
    for p in files:
        rel = os.path.relpath(p, base)
        try:
            result = _clean_single_file(p, dry_run)
        except (OSError, UnicodeDecodeError) as exc:
            counts["errors"] += 1
            print(f"  error {rel}: {exc}")
            continue
        counts[result] += 1
        verb = {("deleted", True): "would delete (empty)", ("deleted", False): "deleted (empty)",
                ("cleaned", True): "would clean", ("cleaned", False): "cleaned"}[(result, dry_run)]
        print(f"  {verb} {rel}")
    print(f"Done: {counts['deleted']} deleted, {counts['cleaned']} cleaned, {counts['errors']} errors"
          f"{' (dry run, nothing changed)' if dry_run else ''}")
    return 0
