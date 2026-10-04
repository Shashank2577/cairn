"""Agent hook handlers (standard library only; run in the agent's hook process).

Events (``cairn.capture <event>``):
  context        SessionStart      inject the recent-work timeline (or a first-run hint)
  session-init   UserPromptSubmit  store the numbered prompt; optionally inject related past work
  observation    PostToolUse       queue the tool event for the observer
  file-context   PreToolUse(Read)  inject this file's prior observations before it is read
  summarize      Stop              queue a progress summary of the prompt that finished
  session-end    SessionEnd        mark the session completed
  user-message   (manual)          render the timeline as a user-visible banner
  file-edit      editor write      queue an editor-reported write as a tool event

Handlers only write to ``.cairn/sessions.db`` and return a result; the heavy work is done by the
worker, which the Stop/SessionEnd hooks start in the background. A hook never fails the agent. When
the repository is connected to a team server, accepted events are also put in the outbox and the
Stop/SessionEnd hooks start a push (``remote.py``); the server records them with ``dispatch()``.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ... import filelock
from . import ingest, remote, schema
from .adapters import AdapterRejectedInput, format_output, normalize, parse_stdin
from .context import inject_context, viewer_url
from .fmt import (
    compact_time,
    format_date,
    format_time,
    header_datetime,
    parse_json_array,
)
from .modes import load_mode
from .platforms import normalize_platform_source
from .projects import declared_branch, find_store_root, project_context, should_track
from .settings import load as load_settings
from .store import Store
from .tags import is_internal_protocol_payload, strip_memory_tags
from .transcript_parser import extract_last_assistant_model, extract_last_assistant_turn

log = logging.getLogger("cairn.recall")

LEGACY_EVENTS = {"prompt": "session-init", "tool": "observation", "stop": "summarize"}
EVENTS = ("context", "session-init", "observation", "summarize", "session-end", "user-message", "file-edit",
          "file-context")
QUIET = {"continue": True, "suppressOutput": True}
FILE_READ_GATE_MIN_BYTES = 1_500
FETCH_LOOKAHEAD_LIMIT = 40
DISPLAY_LIMIT = 15
MAX_FILE_CONTEXT_PATHS = 10


def _empty_context() -> dict:
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ""}}


def no_op_result(event: str) -> dict:
    return {**QUIET, **(_empty_context() if event == "context" else {})}


# (store root, project name) while recording events that came from another machine (see remote_target)
_TARGET: contextvars.ContextVar[tuple[Path, str] | None] = contextvars.ContextVar("recall_hook_target",
                                                                                  default=None)


@contextmanager
def remote_target(root: Path | str, project: str, branch: str | None = None) -> Iterator[None]:
    """Record into ``root``'s store under ``project``: the event's cwd belongs to another machine, so
    nothing is looked up from it on this disk."""
    token = _TARGET.set((Path(root), project))
    try:
        with declared_branch(branch):
            yield
    finally:
        _TARGET.reset(token)


def _root_for(inp: dict) -> Path | None:
    target = _TARGET.get()
    return target[0] if target else find_store_root(inp.get("cwd"))


def _project_for(cwd: str | None) -> str | None:
    target = _TARGET.get()
    if target:
        return target[1]
    return project_context(cwd).primary if cwd else None


CLAUDE_PLATFORMS = ("claude-code", "claude")


def _project_brief(root: Path) -> str:
    """The Cairn brief (map hubs, active spec, principles, team knowledge); empty when unavailable."""
    try:
        from cairn.agents import project_brief
        return project_brief(root).strip()
    except Exception as exc:  # noqa: BLE001 - the timeline still goes out without it
        log.warning("brief unavailable: %s", exc)
        return ""


def _open(root: Path, cwd: str | None) -> Store:
    return Store.open(root, project_name=project_context(str(root)).primary)


# ---- handlers ------------------------------------------------------------------------------------------------
def handle_context(inp: dict) -> dict:
    cwd = inp.get("cwd") or os.getcwd()
    root = _root_for(inp)
    if root is None:
        return _empty_context()
    settings = load_settings(root)
    if not should_track(cwd, settings):
        return _empty_context()
    ctx = project_context(cwd)
    platform = inp.get("platform")
    # every agent sees the work of every agent in this repository
    text = inject_context(root, ctx.all_projects, settings=settings, session_id=inp.get("session_id")).strip()
    if platform not in CLAUDE_PLATFORMS:
        # Claude Code gets the Cairn brief from its own SessionStart hook; other agents get both in one block
        text = "\n\n".join(part for part in (_project_brief(root), text) if part)
    result: dict[str, Any] = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    if settings.get("context_show_terminal_output") and platform != "codex":
        colored = ""
        if platform in CLAUDE_PLATFORMS:
            colored = inject_context(root, ctx.all_projects, for_human=True, settings=settings,
                                     session_id=inp.get("session_id")).strip()
        display = colored or (text if platform in ("antigravity-cli", "antigravity") else "")
        if display:
            result["systemMessage"] = f"{display}\n\nView session memory live @ {viewer_url(root)}"
    return result


def handle_session_init(inp: dict) -> dict:
    sid = inp.get("session_id")
    if not sid:
        return QUIET
    cwd = inp.get("cwd") or os.getcwd()
    root = _root_for(inp)
    if root is None:
        return QUIET
    settings = load_settings(root)
    if not should_track(cwd, settings):
        return QUIET
    raw = inp.get("prompt")
    if isinstance(raw, str) and is_internal_protocol_payload(raw):
        return QUIET
    prompt = raw if isinstance(raw, str) and raw.strip() else ingest.MEDIA_PROMPT
    project = _project_for(cwd)
    with _open(root, cwd) as store:
        res = ingest.session_init(store, content_session_id=str(sid), project=project, prompt=prompt,
                                  platform_source=inp.get("platform"), cwd=cwd)
        if res.get("skipped"):
            # a private prompt is forwarded (as an empty one) so both stores number the turns alike
            return {**QUIET, "_recorded": 0, "_push": res.get("reason") == "private"}
        extra = ""
        if settings.get("semantic_inject") and _TARGET.get() is None and len(prompt) >= 20 \
                and prompt != ingest.MEDIA_PROMPT:
            extra = semantic_context(store, prompt, project, int(settings.get("semantic_inject_limit") or 5),
                                     inp.get("platform"))
    out: dict[str, Any] = {**QUIET, "_recorded": 1, "_push": True}
    if extra:
        out["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit", "additionalContext": extra}
    return out


def semantic_context(store: Store, query: str, project: str, limit: int, platform: str | None = None) -> str:
    """Relevant past observations for a prompt (keyword search here; the hook cannot load a model)."""
    from . import sqlsearch
    limit = min(max(limit, 1), 20)
    try:
        rows = sqlsearch.search_observations(store.db, query, limit=limit, project=project, loose=True,
                                             platform_source=normalize_platform_source(platform) if platform else None)
    except Exception:  # noqa: BLE001 - injection is best effort
        return ""
    if not rows:
        return ""
    lines = ["## Relevant Past Work (semantic match)\n"]
    for o in rows[:limit]:
        lines.append(f"### {o.get('title') or 'Observation'} ({(o.get('created_at') or '')[:10]})")
        if o.get("narrative"):
            lines.append(o["narrative"])
        lines.append("")
    return "\n".join(lines)


def handle_observation(inp: dict) -> dict:
    tool = inp.get("tool_name")
    cwd = inp.get("cwd")
    if not tool or not cwd or not inp.get("session_id"):
        return QUIET
    root = _root_for(inp)
    if root is None:
        return QUIET
    settings = load_settings(root)
    if not should_track(cwd, settings):
        return QUIET
    with _open(root, cwd) as store:
        res = ingest.ingest_observation(store, settings, content_session_id=str(inp["session_id"]), tool_name=str(tool),
                                        tool_input=inp.get("tool_input"), tool_response=inp.get("tool_response"),
                                        cwd=cwd, platform_source=inp.get("platform"), agent_id=inp.get("agent_id"),
                                        agent_type=inp.get("agent_type"), tool_use_id=inp.get("tool_use_id"),
                                        project=_project_for(cwd))
    queued = res.get("status") == "queued"
    return {**QUIET, "_recorded": 1 if queued else 0, "_push": queued}


def handle_summarize(inp: dict) -> dict:
    cwd = inp.get("cwd")
    root = _root_for(inp)
    if root is None:
        return QUIET
    settings = load_settings(root)
    if cwd and not should_track(cwd, settings):
        return QUIET
    if inp.get("stop_hook_active") is True or inp.get("agent_id"):
        return QUIET
    sid = inp.get("session_id")
    if not sid:
        return QUIET
    transcript = inp.get("transcript_path")
    if inp.get("last_assistant_message") is not None:
        message = strip_memory_tags(inp["last_assistant_message"])
        model = inp.get("observed_model") or (extract_last_assistant_model(transcript) if transcript else None)
    else:
        if not transcript:
            return {**QUIET, "_spawn": True}
        text, model = extract_last_assistant_turn(transcript, True)
        message = strip_memory_tags(text)
    if not message or not message.strip():
        return {**QUIET, "_spawn": True}
    inp["last_assistant_message"], inp["observed_model"] = message, model  # what a push forwards
    with _open(root, cwd) as store:
        res = ingest.queue_summarize(store, content_session_id=str(sid), last_assistant_message=message,
                                     platform_source=inp.get("platform"), observed_model=model, cwd=cwd,
                                     project=_project_for(cwd))
    queued = res.get("status") == "queued"
    return {**QUIET, "_recorded": 1 if queued else 0, "_push": queued, "_spawn": True}


def handle_session_end(inp: dict) -> dict:
    sid = inp.get("session_id")
    root = _root_for(inp)
    if not sid or root is None:
        return QUIET
    with _open(root, inp.get("cwd")) as store:
        res = ingest.session_end(store, content_session_id=str(sid), platform_source=inp.get("platform"))
    return {**QUIET, "_push": res.get("status") == "accepted", "_spawn": True}


def handle_user_message(inp: dict) -> dict:
    cwd = inp.get("cwd") or os.getcwd()
    root = _root_for(inp)
    if root is None:
        return {}
    ctx = project_context(cwd)
    colored = inp.get("platform") in ("claude-code", "claude")
    output = inject_context(root, [ctx.primary], platform_source=inp.get("platform"), for_human=colored)
    banner = ("\n\n\U0001F4DD Cairn session memory loaded\n\n" + output +
              "\n\n\U0001F4A1 Wrap any message with <private> ... </private> to keep it out of memory.\n"
              f"\n\U0001F4FA Watch live in the browser {viewer_url(root)}/\n")
    return {"systemMessage": banner}


def handle_file_edit(inp: dict) -> dict:
    fp, cwd, sid = inp.get("file_path"), inp.get("cwd"), inp.get("session_id")
    if not fp or not cwd or not sid:
        return QUIET
    root = _root_for(inp)
    if root is None:
        return QUIET
    settings = load_settings(root)
    if not should_track(cwd, settings):
        return QUIET
    with _open(root, cwd) as store:
        res = ingest.file_edit(store, settings, content_session_id=str(sid), file_path=fp, edits=inp.get("edits"),
                               cwd=cwd, platform_source=inp.get("platform"), project=_project_for(cwd))
    queued = res.get("status") == "queued"
    return {**QUIET, "_recorded": 1 if queued else 0, "_push": queued}


def _dedupe_and_score(observations: list[dict], target: str, limit: int) -> list[dict]:
    seen, by_session = set(), []
    for o in observations:
        key = o.get("memory_session_id") or f"no-session-{o['id']}"
        if key not in seen:
            seen.add(key)
            by_session.append(o)
    norm = target.replace("\\", "/")
    scored = []
    for o in by_session:
        read, mod = parse_json_array(o.get("files_read")), parse_json_array(o.get("files_modified"))
        total = len(read) + len(mod)
        score = (2 if any(str(f).replace("\\", "/") == norm for f in mod) else 0) + \
            (2 if total <= 3 else 1 if total <= 8 else 0)
        scored.append((score, o))
    scored.sort(key=lambda s: -s[0])
    return [o for _, o in scored[:limit]]


def format_file_timeline(observations: list[dict], file_path: str, mode_id: str | None = None) -> str:
    mode = load_mode(mode_id)
    safe = file_path.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    lines = [f"Current: {header_datetime()}",
             "This file has prior observations \u2014 supplementary context follows. The Read result below is the"
             " full requested section.",
             "- **Need details on a past observation?** get_observations([IDs]) \u2014 ~300 tokens each.",
             f'- **Need a structural map first?** smart_outline("{safe}") \u2014 line numbers only, cheaper than'
             " re-reading."]
    days: dict[str, list[dict]] = {}
    for o in observations:
        days.setdefault(format_date(o["created_at_epoch"]), []).append(o)
    for day, obs in sorted(days.items(), key=lambda kv: min(o["created_at_epoch"] for o in kv[1])):
        lines.append(f"### {day}")
        for o in sorted(obs, key=lambda o: o["created_at_epoch"]):
            title = " ".join((o.get("title") or "Untitled").split())[:160]
            lines.append(f"{o['id']} {compact_time(format_time(o['created_at_epoch']))} "
                         f"{mode.type_icon(o['type'])} {title}")
    return "\n".join(lines)


def _file_timeline(store: Store, inp: dict, file_path: str, settings: dict) -> str | None:
    cwd = inp.get("cwd") or os.getcwd()
    abs_path = os.path.normpath(os.path.join(cwd, file_path))
    mtime_ms = 0
    try:
        st = os.stat(abs_path)
        if not os.path.isfile(abs_path) or st.st_size < FILE_READ_GATE_MIN_BYTES:
            return None
        mtime_ms = int(st.st_mtime * 1000)
    except FileNotFoundError:
        return None
    except OSError:
        pass
    rel = os.path.relpath(abs_path, cwd).replace(os.sep, "/")
    cands = list(dict.fromkeys([abs_path.replace(os.sep, "/"), rel]))
    root_rel = None
    root = _root_for(inp)
    if root:
        try:
            root_rel = Path(abs_path).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            root_rel = None
    if root_rel:
        cands.append(root_rel)
    obs = store.get_observations_by_file(cands, projects=project_context(cwd).all_projects or None,
                                         limit=FETCH_LOOKAHEAD_LIMIT)
    if not obs:
        return None
    newest = max(o["created_at_epoch"] for o in obs)
    if mtime_ms and mtime_ms >= newest:
        return None  # the file changed after the last observation: its history may be stale
    shown = _dedupe_and_score(obs, root_rel or rel, DISPLAY_LIMIT)
    if not store.claim_file_context_injection(str(inp.get("session_id") or ""), abs_path, newest):
        return None
    return format_file_timeline(shown, file_path, settings.get("mode"))


def handle_file_context(inp: dict) -> dict:
    if inp.get("agent_id"):
        return QUIET
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    fps = [p for p in ti.get("filePaths") or [] if isinstance(p, str)][:MAX_FILE_CONTEXT_PATHS] \
        if isinstance(ti.get("filePaths"), list) else []
    fp = ti.get("file_path")
    cands = fps or ([fp] if isinstance(fp, str) and fp else [])
    if not cands:
        return QUIET
    root = _root_for(inp)
    if root is None:
        return QUIET
    settings = load_settings(root)
    if inp.get("cwd") and not should_track(inp["cwd"], settings):
        return QUIET
    timelines = []
    with _open(root, inp.get("cwd")) as store:
        for c in cands:
            try:
                t = _file_timeline(store, inp, c, settings)
            except Exception:  # noqa: BLE001 - one path failing never blocks the read
                t = None
            if t:
                timelines.append(t)
    if not timelines:
        return QUIET
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n\n---\n\n".join(timelines),
                                   "permissionDecision": "allow"}}


HANDLERS = {"context": handle_context, "session-init": handle_session_init, "observation": handle_observation,
            "summarize": handle_summarize, "session-end": handle_session_end, "user-message": handle_user_message,
            "file-edit": handle_file_edit, "file-context": handle_file_context}


def statusline_counts(cwd: str | None = None) -> dict:
    """``{observations, prompts, project}`` for a status line (read-only, standard library only)."""
    project = project_context(cwd or os.getcwd()).primary
    root = find_store_root(cwd or os.getcwd())
    if root is None or not schema.store_path(root).exists():
        return {"observations": 0, "prompts": 0, "project": project}
    try:
        db = schema.connect(schema.store_path(root), readonly=True)
    except Exception as exc:  # noqa: BLE001
        return {"observations": 0, "prompts": 0, "project": project, "error": str(exc)}
    try:
        obs = db.execute("SELECT COUNT(*) FROM observations WHERE project = ? OR merged_into_project = ?",
                         (project, project)).fetchone()[0]
        prs = db.execute("SELECT COUNT(*) FROM user_prompts up JOIN sdk_sessions s ON s.id = up.session_db_id"
                         " WHERE s.project = ?", (project,)).fetchone()[0]
        return {"observations": int(obs), "prompts": int(prs), "project": project}
    finally:
        db.close()


# ---- the detached worker ------------------------------------------------------------------------------------
def worker_lock_path(root: Path) -> Path:
    return Path(root) / ".cairn" / "recall" / "worker.lock"


def _lock_held(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with open(path, "a", encoding="utf-8") as fh:
            if not filelock.try_lock(fh):
                return True
            filelock.unlock(fh)
    except OSError:
        return False
    return False


def worker_running(root: Path) -> bool:
    """True when another process holds the worker lock (a hosted or detached worker is draining)."""
    return _lock_held(worker_lock_path(root))


def _spawn(root: Path, module: str, args: list[str], log_name: str) -> bool:
    """Start ``python -m <module> <args>`` detached, logging to ``.cairn/recall/<log_name>``."""
    log_dir = root / ".cairn" / "recall"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = open(log_dir / log_name, "a", encoding="utf-8")
    except OSError:
        return False
    kwargs: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000008}
    try:
        subprocess.Popen([sys.executable, "-m", module, *args], cwd=str(root), stdin=subprocess.DEVNULL, stdout=fh,
                         stderr=fh, **kwargs)
        return True
    except OSError:
        return False
    finally:
        fh.close()


def spawn_worker(root: Path, model: bool = True) -> bool:
    """Start ``python -m cairn.engines.recall.worker --once`` detached; never waits on it."""
    root = Path(root)
    if os.environ.get("CAIRN_RECALL_NO_SPAWN") or worker_running(root):
        return False
    args = ["--once", "--root", str(root)] + ([] if model else ["--no-model"])
    return _spawn(root, "cairn.engines.recall.worker", args, "worker.log")


def spawn_push(root: Path) -> bool:
    """Start ``python -m cairn.engines.recall.remote`` detached when a team server is configured and
    events are waiting for it (and it is not backing off after a failure)."""
    root = Path(root)
    if os.environ.get("CAIRN_RECALL_NO_SPAWN") or not remote.push_due(root) or _lock_held(remote.push_lock_path(root)):
        return False
    return _spawn(root, "cairn.engines.recall.remote", ["--root", str(root)], "push.log")


# ---- entry points -------------------------------------------------------------------------------------------------
def dispatch(event: str, inp: dict) -> dict:
    """Handle an already normalized input (the server records pushed events this way, inside
    ``remote_target``); returns the raw handler result."""
    handler = HANDLERS.get(LEGACY_EVENTS.get(event, event))
    return handler(inp) if handler else QUIET


def run(event: str, payload: Any, platform: str = "claude-code") -> tuple[dict, dict]:
    """Normalize, handle; returns (the raw handler result, the platform-formatted output)."""
    event = LEGACY_EVENTS.get(event, event)
    handler = HANDLERS.get(event)
    if handler is None:
        return QUIET, {}
    if platform in CLAUDE_PLATFORMS and isinstance(payload, dict) and "cursor_version" in payload:
        # Cursor also runs the hooks in .claude/settings.json; its own hooks.json entries record the session
        res = no_op_result(event)
        return res, format_output(platform, res)
    try:
        inp = normalize(platform, payload if isinstance(payload, dict) else {})
    except AdapterRejectedInput:
        res = no_op_result(event)
        return res, format_output(platform, res)
    res = handler(inp)
    if res.get("_push") and _TARGET.get() is None:
        root = _root_for(inp)
        if root is not None:
            remote.record_outbox(root, event, inp)
    public = {k: v for k, v in res.items() if not k.startswith("_")}
    return res, format_output(platform, public)


def record(kind: str, payload: dict, platform: str = "claude-code") -> int:
    """Programmatic capture (no output, no worker spawn): how many queue/prompt rows were written."""
    if os.environ.get("CAIRN_INTERNAL"):
        return 0
    try:
        res, _ = run(kind, payload, platform)
    except Exception:  # noqa: BLE001 - capture never raises into its caller
        return 0
    return int(res.get("_recorded") or 0)


def main(argv: list[str] | None = None, stdin_text: str | None = None) -> int:
    """``python -m cairn.capture [--platform P] <event>`` (also ``hook <platform> <event>``)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    platform = "claude-code"
    if argv and argv[0] == "hook" and len(argv) >= 3:
        platform, argv = argv[1], argv[2:]
    if "--platform" in argv:
        i = argv.index("--platform")
        if i + 1 < len(argv):
            platform = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
    event = argv[0] if argv else ""
    try:
        raw_text = sys.stdin.read() if stdin_text is None else stdin_text
    except (OSError, ValueError):
        raw_text = ""
    try:
        payload = parse_stdin(raw_text)
    except ValueError:
        payload = None
    if os.environ.get("CAIRN_INTERNAL"):
        payload = None  # Cairn's own model calls are never recorded
    try:
        if payload is None:
            res = no_op_result(LEGACY_EVENTS.get(event, event))
            out = format_output(platform, res) if LEGACY_EVENTS.get(event, event) == "context" else {}
        else:
            res, out = run(event, payload, platform)
            if res.get("_spawn"):
                root = find_store_root((payload or {}).get("cwd") or os.getcwd())
                if root is not None:
                    settings = load_settings(root)
                    if settings.get("worker_spawn"):
                        spawn_worker(root, model=bool(settings.get("worker_model", True)))
                    spawn_push(root)
    except Exception:  # noqa: BLE001 - a hook must never fail the agent it observes
        out = {}
    if out:
        sys.stdout.write(json.dumps(out))
        sys.stdout.flush()
    return 0
