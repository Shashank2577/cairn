"""Transcript ingestion: a schema-driven JSONL watcher and Claude Code transcript replay.

Two capture paths that do not depend on an agent's hooks having fired:

* **Watched transcripts.** A JSON config (``$CAIRN_HOME/transcript-watch.json`` unless the
  ``transcripts_config_path`` setting names another) lists *watches* -- a file, a folder or a glob such as
  ``~/.codex/sessions/**/*.jsonl`` -- and *schemas* that say which JSONL entries are prompts, tool calls,
  tool results, assistant messages or session ends and where their fields live (match rules and field
  specs). A polling tailer reads appended bytes, keeps per-file offsets (and the in-flight session state) in
  a state file, and hands each entry to :class:`TranscriptEventProcessor`, which calls the same ingest
  functions the hooks use. The repository an event belongs to is decided by its cwd: the nearest folder
  holding ``.cairn/``. Events whose cwd is in no Cairn repository are skipped.
* **Claude Code replay** (the recovery path). :func:`ingest_claude_transcript` re-reads a Claude Code
  session file (``~/.claude/projects/<dashed-cwd>/<session>.jsonl``) and replays its prompts, tool
  calls and turn ends through the ingest functions, idempotently, so sessions whose hooks missed events
  (or ran before Cairn was installed) are recovered.

Standard library only.
"""
from __future__ import annotations

import copy
import glob
import json
import logging
import os
import re
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ingest
from .context import claude_config_dir, cwd_to_dashed, inject_context
from .platforms import normalize_platform_source
from .projects import expand_home, find_store_root, project_context, should_track
from .settings import cairn_home
from .settings import load as load_settings
from .store import Store
from .tags import (
    SYSTEM_REMINDER_RE,
    is_internal_protocol_payload,
    normalize_stored_prompt_text,
    strip_memory_tags,
)
from .transcript_parser import content_to_text

log = logging.getLogger("cairn.recall")

CONFIG_FILENAME = "transcript-watch.json"
STATE_FILENAME = "transcript-watch-state.json"
ACTIONS = ("session_init", "session_context", "user_message", "assistant_message", "tool_use", "tool_result",
           "observation", "file_edit", "session_end")
SETTINGS_TTL_S = 5.0                 # a long-running watcher picks up settings changes this quickly
MAX_PERSISTED_SESSIONS = 500         # in-flight session states kept in the state file
READ_CHUNK_BYTES = 8 * 1024 * 1024   # a large backlog is read (and its offset advanced) in chunks

_GLOB_CHARS = re.compile(r"[*?[\]{}()]")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_AGENT_ID_IN_PATH = re.compile(
    r"agent-transcripts[/\\]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:[/\\]|$)", re.IGNORECASE)


# ---- the built-in schema -----------------------------------------------------------------------------------
CODEX_SCHEMA: dict = {
    "name": "codex",
    "version": "0.3",
    "description": "Schema for Codex session JSONL files under ~/.codex/sessions.",
    "events": [
        {"name": "session-meta", "match": {"path": "type", "equals": "session_meta"}, "action": "session_context",
         "fields": {"sessionId": "payload.id", "cwd": "payload.cwd"}},
        {"name": "turn-context", "match": {"path": "type", "equals": "turn_context"}, "action": "session_context",
         "fields": {"cwd": "payload.cwd"}},
        {"name": "user-message", "match": {"path": "payload.type", "equals": "user_message"},
         "action": "session_init", "fields": {"prompt": "payload.message"}},
        {"name": "assistant-message", "match": {"path": "payload.type", "equals": "agent_message"},
         "action": "assistant_message", "fields": {"message": "payload.message"}},
        {"name": "tool-use",
         "match": {"path": "payload.type", "in": ["function_call", "custom_tool_call", "web_search_call"]},
         "action": "tool_use",
         "fields": {"toolId": "payload.call_id",
                    "toolName": {"coalesce": ["payload.name", "payload.type"]},
                    "toolInput": {"coalesce": ["payload.arguments", "payload.input", "payload.command",
                                               "payload.action"]}}},
        {"name": "exec-command-end",
         "match": {"path": "payload.type", "in": ["exec_command_end", "exec_command_output"]},
         "action": "observation",
         "fields": {"toolUseId": "payload.call_id",
                    "toolName": {"value": "exec_command"},
                    "toolInput": {"coalesce": ["payload.command", "payload.input"]},
                    "toolResponse": {"coalesce": ["payload.aggregated_output", "payload.output", "payload.stdout",
                                                  "payload.stderr"]}}},
        {"name": "tool-result",
         "match": {"path": "payload.type", "in": ["function_call_output", "custom_tool_call_output"]},
         "action": "tool_result", "fields": {"toolId": "payload.call_id", "toolResponse": "payload.output"}},
        {"name": "session-end",
         "match": {"path": "payload.type", "in": ["turn_aborted", "turn_completed", "task_complete"]},
         "action": "session_end"},
    ],
}
BUILTIN_SCHEMAS: dict[str, dict] = {"codex": CODEX_SCHEMA}
CODEX_WATCH_PATH = "~/.codex/sessions/**/*.jsonl"


# ---- config ------------------------------------------------------------------------------------------------
class ConfigNotFoundError(FileNotFoundError):
    """The transcript watch config file does not exist."""


def default_config_path() -> Path:
    return cairn_home() / CONFIG_FILENAME


def default_state_path() -> Path:
    return cairn_home() / STATE_FILENAME


def sample_config() -> dict:
    """What ``init`` writes: the built-in Codex schema and a Codex watch that starts at the end of files."""
    return {"version": 1, "schemas": {"codex": copy.deepcopy(CODEX_SCHEMA)},
            "watches": [{"name": "codex", "path": CODEX_WATCH_PATH, "schema": "codex", "startAtEnd": True}],
            "stateFile": str(default_state_path())}


def expand_home_path(value: str | Path | None) -> str:
    """Expand a leading ``~`` (the current user only; ``~alice/`` is left alone) or ``$CAIRN_HOME``."""
    if not value:
        return "" if value is None else str(value)
    text = str(value)
    for var in ("${CAIRN_HOME}", "$CAIRN_HOME"):
        if text == var or text.startswith((var + "/", var + "\\")):
            return str(cairn_home()) + text[len(var):]
    return expand_home(text)


def resolve_config_path(config_path: str | Path | None = None, settings: dict | None = None) -> Path:
    """An explicit path, else the ``transcripts_config_path`` setting, else ``$CAIRN_HOME/transcript-watch.json``."""
    chosen = config_path or (settings or {}).get("transcripts_config_path") or default_config_path()
    return Path(expand_home_path(str(chosen)))


def load_transcript_watch_config(path: str | Path | None = None) -> dict:
    resolved = Path(expand_home_path(str(path or default_config_path())))
    if not resolved.exists():
        raise ConfigNotFoundError(f"Transcript watch config not found: {resolved}")
    try:
        parsed = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid transcript watch config: {resolved} ({exc})") from exc
    if not isinstance(parsed, dict) or not parsed.get("version") or not isinstance(parsed.get("watches"), list):
        raise ValueError(f"Invalid transcript watch config: {resolved}")
    if not parsed.get("stateFile"):
        parsed["stateFile"] = str(default_state_path())
    return parsed


def write_sample_config(path: str | Path | None = None) -> Path:
    resolved = Path(expand_home_path(str(path or default_config_path())))
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(sample_config(), indent=2), encoding="utf-8")
    return resolved


def resolve_schema(watch: dict, config: dict) -> dict | None:
    """A watch's schema: inline, named in the config's ``schemas``, or a built-in one."""
    schema = watch.get("schema")
    if isinstance(schema, str):
        named = (config.get("schemas") or {}).get(schema)
        return named if isinstance(named, dict) else BUILTIN_SCHEMAS.get(schema)
    return schema if isinstance(schema, dict) else None


def validate_config(config: dict) -> list[str]:
    """Problems that would make a watch ingest nothing (empty when the config is usable)."""
    problems: list[str] = []
    for name, schema in (config.get("schemas") or {}).items():
        if not isinstance(schema, dict) or not isinstance(schema.get("events"), list):
            problems.append(f"schema {name!r}: missing events list")
            continue
        for i, event in enumerate(schema["events"]):
            label = f"schema {name!r} event {event.get('name', i) if isinstance(event, dict) else i!r}"
            if not isinstance(event, dict):
                problems.append(f"{label}: not an object")
                continue
            if event.get("action") not in ACTIONS:
                problems.append(f"{label}: unknown action {event.get('action')!r}")
            rx = (event.get("match") or {}).get("regex") if isinstance(event.get("match"), dict) else None
            if rx:
                try:
                    re.compile(str(rx))
                except re.error as exc:
                    problems.append(f"{label}: invalid regex ({exc})")
    for i, watch in enumerate(config.get("watches") or []):
        if not isinstance(watch, dict):
            problems.append(f"watch {i}: not an object")
            continue
        label = f"watch {watch.get('name', i)!r}"
        if not watch.get("path"):
            problems.append(f"{label}: missing path")
        if resolve_schema(watch, config) is None:
            problems.append(f"{label}: schema {watch.get('schema')!r} not found")
    return problems


def _schema_name(watch: dict) -> str | None:
    schema = watch.get("schema")
    if isinstance(schema, str):
        return schema
    return schema.get("name") if isinstance(schema, dict) else None


def is_native_hook_backed_codex_watch(watch: dict) -> bool:
    """The canonical Codex sessions watch, which Codex's own hooks already cover."""
    if not (watch.get("name") == "codex" or _schema_name(watch) == "codex") or not watch.get("path"):
        return False
    normalized = expand_home_path(str(watch["path"])).replace("\\", "/")
    sessions_root = str(Path.home() / ".codex" / "sessions").replace("\\", "/")
    return normalized == f"{sessions_root}/**/*.jsonl"


def should_suppress_native_codex_agents_context(watch: dict) -> bool:
    """Codex hooks inject session context themselves; the transcript watch must not also write AGENTS.md."""
    name = _schema_name(watch)
    canonical = watch.get("name") == "codex" and (not name or name == "codex")
    ctx = watch.get("context")
    mode = ctx.get("mode") if isinstance(ctx, dict) else None
    return mode == "agents" and canonical and is_native_hook_backed_codex_watch(watch)


def filter_native_hook_backed_codex_watches(config: dict,
                                            allow_codex_transcript_ingestion: bool) -> tuple[dict, int]:
    """Drop the native Codex watch unless ``codex_transcript_ingestion`` opts in; returns (config, removed)."""
    if allow_codex_transcript_ingestion:
        return config, 0
    watches = [w for w in config.get("watches") or []
               if not (isinstance(w, dict) and is_native_hook_backed_codex_watch(w))]
    return {**config, "watches": watches}, len(config.get("watches") or []) - len(watches)


# ---- watch state -------------------------------------------------------------------------------------------
def load_watch_state(state_path: str | Path) -> dict:
    path = Path(state_path)
    try:
        if not path.exists():
            return {"offsets": {}}
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("offsets"), dict):
            return {"offsets": {}}
        return parsed
    except (OSError, ValueError) as exc:
        log.warning("failed to load transcript watch state %s, starting fresh: %s", path, exc)
        return {"offsets": {}}


def save_watch_state(state_path: str | Path, state: dict) -> None:
    path = Path(state_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("failed to save transcript watch state %s: %s", path, exc)


# ---- field specs and match rules ---------------------------------------------------------------------------
class _Missing:
    """An absent value (distinct from JSON ``null``)."""
    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING: Any = _Missing()
_PATH_TOKEN = re.compile(r"([^[\]]+)|\[(\d+)\]")


def parse_path(path: str) -> list[str | int]:
    """``$.a.b[0].c`` -> ``["a", "b", 0, "c"]`` (a leading ``$`` or ``$.`` is dropped)."""
    cleaned = re.sub(r"^\$\.?", "", path.strip())
    if not cleaned:
        return []
    tokens: list[str | int] = []
    for part in cleaned.split("."):
        for m in _PATH_TOKEN.finditer(part):
            if m.group(1):
                tokens.append(m.group(1))
            elif m.group(2):
                tokens.append(int(m.group(2)))
    return tokens


def get_value_by_path(value: Any, path: str) -> Any:
    """The value at ``path`` inside a parsed JSON entry, or ``MISSING``."""
    if not path:
        return MISSING
    current = value
    for token in parse_path(path):
        if current is None or current is MISSING:
            return MISSING
        if isinstance(current, dict):
            current = current.get(token if isinstance(token, str) else str(token), MISSING)
        elif isinstance(current, (list, str)):
            index = token if isinstance(token, int) else (int(token) if token.isdigit() else None)
            current = current[index] if index is not None and 0 <= index < len(current) else MISSING
        else:
            return MISSING
    return current


def _is_empty(value: Any) -> bool:
    return value is MISSING or value is None or value == ""


def _session_view(session: Any) -> dict | None:
    if session is None:
        return None
    return session.view() if hasattr(session, "view") else session


def _resolve_from_context(path: str, ctx: dict) -> Any:
    watch, schema, session = ctx.get("watch") or {}, ctx.get("schema") or {}, _session_view(ctx.get("session"))
    if path.startswith("$watch."):
        return watch.get(path[len("$watch."):], MISSING)
    if path.startswith("$schema."):
        return schema.get(path[len("$schema."):], MISSING)
    if path.startswith("$session."):
        return session.get(path[len("$session."):], MISSING) if session is not None else MISSING
    if path == "$cwd":
        return watch.get("workspace", MISSING)
    if path == "$project":
        return watch.get("project", MISSING)
    return MISSING


def resolve_field_spec(spec: Any, entry: Any, ctx: dict) -> Any:
    """A field spec: a path string, or ``{"path", "value", "coalesce": [specs], "default"}``.

    Paths starting ``$watch.``, ``$schema.``, ``$session.``, ``$cwd`` or ``$project`` read the watch, the
    schema or the session being built; when those are absent the path is read from the entry instead.
    """
    if spec is None or spec is MISSING:
        return MISSING
    if isinstance(spec, str):
        from_ctx = _resolve_from_context(spec, ctx)
        return from_ctx if from_ctx is not MISSING else get_value_by_path(entry, spec)
    if not isinstance(spec, dict):
        return MISSING
    if isinstance(spec.get("coalesce"), list):
        for candidate in spec["coalesce"]:
            value = resolve_field_spec(candidate, entry, ctx)
            if not _is_empty(value):
                return value
    if spec.get("path"):
        from_ctx = _resolve_from_context(str(spec["path"]), ctx)
        if from_ctx is not MISSING:
            return from_ctx
        value = get_value_by_path(entry, str(spec["path"]))
        if not _is_empty(value):
            return value
    if "value" in spec:
        return spec["value"]
    if "default" in spec:
        return spec["default"]
    return MISSING


def resolve_fields(fields: dict | None, entry: Any, ctx: dict) -> dict[str, Any]:
    return {key: resolve_field_spec(spec, entry, ctx) for key, spec in (fields or {}).items()}


def _js_string(value: Any) -> str:
    """How JavaScript's ``String(value ?? '')`` renders a JSON value (regex rules test this form)."""
    if value is None or value is MISSING:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, list):
        return ",".join("" if v is None else _js_string(v) for v in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


def _strict_equal(a: Any, b: Any) -> bool:
    """JavaScript ``===`` between two JSON values (objects and arrays are never identical)."""
    if a is MISSING or b is MISSING:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


def matches_rule(entry: Any, rule: dict | None, schema: dict) -> bool:
    """Every operator on the rule must pass (logical AND). The rule reads ``rule.path``, else the schema's
    ``eventTypePath``, else ``type``."""
    if not rule:
        return True
    path = rule.get("path") or schema.get("eventTypePath") or "type"
    value = get_value_by_path(entry, path)
    absent = _is_empty(value)
    if "exists" in rule:
        if rule["exists"] and absent:
            return False
        if not rule["exists"] and not absent:
            return False
    if "equals" in rule and not _strict_equal(value, rule["equals"]):
        return False
    if "not_equals" in rule and _strict_equal(value, rule["not_equals"]):
        return False
    if isinstance(rule.get("in"), list) and not any(_strict_equal(value, v) for v in rule["in"]):
        return False
    if isinstance(rule.get("not_in"), list) and any(_strict_equal(value, v) for v in rule["not_in"]):
        return False
    if "contains" in rule and (not isinstance(value, str) or _js_string(rule["contains"]) not in value):
        return False
    if "not_contains" in rule and isinstance(value, str) and _js_string(rule["not_contains"]) in value:
        return False
    if rule.get("regex"):
        try:
            if not re.search(str(rule["regex"]), _js_string(value)):
                return False
        except re.error:
            log.debug("invalid regex in transcript match rule: %s", rule["regex"])
            return False
    return True


# ---- event processing --------------------------------------------------------------------------------------
def resolve_watch_agent_id(watch: dict) -> str | None:
    """The explicit ``agentId`` of a watch, else the ``agent-transcripts/<uuid>/`` segment of its path."""
    explicit = watch.get("agentId").strip() if isinstance(watch.get("agentId"), str) else ""
    if explicit and explicit != "*":
        return explicit
    m = _AGENT_ID_IN_PATH.search(str(watch.get("path") or ""))
    return m.group(1) if m else None


def extract_session_id_from_path(file_path: str) -> str | None:
    m = _UUID.search(file_path)
    return m.group(0) if m else None


def parse_apply_patch_files(patch: str) -> list[str]:
    """Files touched by an ``apply_patch`` envelope or a unified diff, in order, without duplicates."""
    files: list[str] = []
    for line in patch.split("\n"):
        t = line.strip()
        for marker in ("*** Update File: ", "*** Add File: ", "*** Delete File: ", "*** Move to: "):
            if t.startswith(marker):
                files.append(t[len(marker):].strip())
                break
        else:
            if t.startswith("+++ "):
                p = re.sub(r"^b/", "", t[4:]).strip()
                if p and p != "/dev/null":
                    files.append(p)
    return list(dict.fromkeys(files))


def _maybe_parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed or not trimmed.startswith(("{", "[")):
        return value
    try:
        return json.loads(trimmed)
    except ValueError:
        log.debug("transcript field looked like JSON but did not parse: %s", trimmed[:120])
        return value


def _defined(value: Any) -> Any:
    return None if value is MISSING else value


def _event_or_schema_spec(event: dict, schema: dict, field_name: str, schema_path_key: str) -> Any:
    """The event's own field spec for ``field_name``, else ``{"path": schema[schema_path_key]}`` when set."""
    fields = event.get("fields") if isinstance(event.get("fields"), dict) else {}
    if field_name in fields:
        return fields[field_name]
    return {"path": schema[schema_path_key]} if schema.get(schema_path_key) else None


@dataclass
class SessionState:
    """What the processor remembers about one transcript session between entries."""
    session_id: str
    platform_source: str
    cwd: str | None = None
    project: str | None = None
    last_user_message: str | None = None
    last_assistant_message: str | None = None
    pending_tools: dict[str, dict] = field(default_factory=dict)

    def view(self) -> dict:
        """The session as ``$session.<key>`` field specs see it."""
        out: dict[str, Any] = {"sessionId": self.session_id, "platformSource": self.platform_source}
        for key, value in (("cwd", self.cwd), ("project", self.project), ("lastUserMessage", self.last_user_message),
                           ("lastAssistantMessage", self.last_assistant_message)):
            if value is not None:
                out[key] = value
        if self.pending_tools:
            out["pendingTools"] = self.pending_tools
        return out

    def to_dict(self) -> dict:
        return {"sessionId": self.session_id, "platformSource": self.platform_source, "cwd": self.cwd,
                "project": self.project, "lastUserMessage": self.last_user_message,
                "lastAssistantMessage": self.last_assistant_message, "pendingTools": self.pending_tools}

    @classmethod
    def from_dict(cls, raw: Any) -> SessionState | None:
        if not isinstance(raw, dict) or not isinstance(raw.get("sessionId"), str):
            return None
        pending = raw.get("pendingTools") if isinstance(raw.get("pendingTools"), dict) else {}
        return cls(raw["sessionId"], str(raw.get("platformSource") or normalize_platform_source(None)),
                   raw.get("cwd"), raw.get("project"), raw.get("lastUserMessage"), raw.get("lastAssistantMessage"),
                   {k: v for k, v in pending.items() if isinstance(v, dict)})


class _RepoCache:
    """Open stores and settings per repository root (a processor lives on one thread)."""

    def __init__(self):
        self.stores: dict[Path, Store] = {}
        self.settings_by_root: dict[Path, tuple[float, dict]] = {}

    def settings(self, root: Path) -> dict:
        hit = self.settings_by_root.get(root)
        if hit and time.monotonic() - hit[0] < SETTINGS_TTL_S:
            return hit[1]
        loaded = load_settings(root)
        self.settings_by_root[root] = (time.monotonic(), loaded)
        return loaded

    def store(self, root: Path) -> Store:
        if root not in self.stores:
            self.stores[root] = Store.open(root, project_name=project_context(str(root)).primary)
        return self.stores[root]

    def close(self) -> None:
        for store in self.stores.values():
            store.close()
        self.stores.clear()


class TranscriptEventProcessor:
    """Turns matched transcript entries into prompts, tool observations, file edits and summaries."""

    def __init__(self, sessions: dict | None = None):
        self.sessions: dict[str, SessionState] = {}
        for key, raw in (sessions or {}).items():
            state = SessionState.from_dict(raw)
            if state is not None:
                self.sessions[key] = state
        self.counts: Counter = Counter()
        self.spawn_roots: set[Path] = set()   # repositories that queued a summary or ended a session
        self._repos = _RepoCache()

    def close(self) -> None:
        self._repos.close()

    def export_sessions(self) -> dict:
        keys = list(self.sessions)[-MAX_PERSISTED_SESSIONS:]
        return {k: self.sessions[k].to_dict() for k in keys}

    # -- entry points --
    def process_entry(self, entry: Any, watch: dict, schema: dict, session_id_override: str | None = None) -> None:
        for event in schema.get("events") or []:
            if not isinstance(event, dict) or not matches_rule(entry, event.get("match"), schema):
                continue
            self.handle_event(entry, watch, schema, event, session_id_override)

    def handle_event(self, entry: Any, watch: dict, schema: dict, event: dict,
                     session_id_override: str | None = None) -> None:
        session_id = self._resolve_session_id(entry, watch, schema, event, session_id_override)
        if not session_id:
            log.debug("skipping transcript event without a session id: %s (%s)", event.get("name"), watch.get("name"))
            self.counts["skipped_no_session"] += 1
            return
        self.counts["events"] += 1
        session = self._get_or_create_session(watch, session_id)
        cwd = self._resolve_cwd(entry, watch, schema, event, session)
        if cwd:
            session.cwd = cwd
        project = self._resolve_project(entry, watch, schema, event, session)
        if project:
            session.project = project
        fields = resolve_fields(event.get("fields"), entry, {"watch": watch, "schema": schema, "session": session})
        action = event.get("action")
        if action == "session_context":
            self._apply_session_context(session, fields)
        elif action == "session_init":
            self._handle_session_init(session, fields)
            ctx = watch.get("context")
            update_on = ctx.get("updateOn") if isinstance(ctx, dict) else None
            if isinstance(update_on, list) and "session_start" in update_on:
                self.update_context(session, watch)
        elif action == "user_message":
            if isinstance(fields.get("message"), str):
                session.last_user_message = fields["message"]
            if isinstance(fields.get("prompt"), str):
                session.last_user_message = fields["prompt"]
        elif action == "assistant_message":
            if isinstance(fields.get("message"), str):
                session.last_assistant_message = fields["message"]
        elif action == "tool_use":
            self._handle_tool_use(session, watch, fields)
        elif action == "tool_result":
            self._handle_tool_result(session, watch, fields)
        elif action == "observation":
            self._send_observation(session, watch, fields)
        elif action == "file_edit":
            self._send_file_edit(session, fields)
        elif action == "session_end":
            self._handle_session_end(session, watch)

    # -- session resolution --
    @staticmethod
    def _session_key(watch: dict, session_id: str) -> str:
        return f"{watch.get('name')}:{session_id}"

    def _get_or_create_session(self, watch: dict, session_id: str) -> SessionState:
        key = self._session_key(watch, session_id)
        session = self.sessions.get(key)
        if session is None:
            session = SessionState(session_id, normalize_platform_source(watch.get("name")))
            self.sessions[key] = session
        return session

    @staticmethod
    def _resolve_session_id(entry: Any, watch: dict, schema: dict, event: dict,
                            session_id_override: str | None) -> str | None:
        spec = _event_or_schema_spec(event, schema, "sessionId", "sessionIdPath")
        resolved = resolve_field_spec(spec, entry, {"watch": watch, "schema": schema})
        if isinstance(resolved, str) and resolved.strip():
            return resolved
        if isinstance(resolved, (int, float)) and not isinstance(resolved, bool):
            return _js_string(resolved)
        if session_id_override and session_id_override.strip():
            return session_id_override
        return None

    @staticmethod
    def _resolve_cwd(entry: Any, watch: dict, schema: dict, event: dict, session: SessionState) -> str | None:
        spec = _event_or_schema_spec(event, schema, "cwd", "cwdPath")
        resolved = resolve_field_spec(spec, entry, {"watch": watch, "schema": schema, "session": session})
        if isinstance(resolved, str) and resolved.strip():
            return resolved
        if watch.get("workspace"):
            return str(watch["workspace"])
        return session.cwd

    @staticmethod
    def _resolve_project(entry: Any, watch: dict, schema: dict, event: dict, session: SessionState) -> str | None:
        spec = _event_or_schema_spec(event, schema, "project", "projectPath")
        resolved = resolve_field_spec(spec, entry, {"watch": watch, "schema": schema, "session": session})
        if isinstance(resolved, str) and resolved.strip():
            return resolved
        if watch.get("project"):
            return str(watch["project"])
        if session.cwd:
            return project_context(session.cwd).primary
        return session.project

    # -- the repository an event lands in --
    def _target(self, session: SessionState) -> tuple[Path, Store, dict] | None:
        """The repository for the session's cwd, or None (counted) when the event cannot be recorded."""
        if not session.cwd:
            self.counts["skipped_no_repo"] += 1
            return None
        root = find_store_root(session.cwd)
        if root is None:
            self.counts["skipped_no_repo"] += 1
            return None
        settings = self._repos.settings(root)
        if not should_track(session.cwd, settings):
            self.counts["skipped_untracked"] += 1
            return None
        return root, self._repos.store(root), settings

    # -- actions --
    @staticmethod
    def _apply_session_context(session: SessionState, fields: dict) -> None:
        if isinstance(fields.get("cwd"), str) and fields["cwd"]:
            session.cwd = fields["cwd"]
        if isinstance(fields.get("project"), str) and fields["project"]:
            session.project = fields["project"]

    def _handle_session_init(self, session: SessionState, fields: dict) -> None:
        prompt = fields.get("prompt") if isinstance(fields.get("prompt"), str) else ""
        if prompt:
            session.last_user_message = prompt
        target = self._target(session)
        if target is None:
            return
        _, store, _ = target
        if prompt and is_internal_protocol_payload(prompt):
            self.counts["prompts_skipped"] += 1
            return
        res = ingest.session_init(store, content_session_id=session.session_id,
                                  project=session.project or project_context(session.cwd).primary,
                                  prompt=prompt if prompt.strip() else ingest.MEDIA_PROMPT,
                                  platform_source=session.platform_source, cwd=session.cwd)
        self.counts["prompts_skipped" if res.get("skipped") else "prompts"] += 1

    def _handle_tool_use(self, session: SessionState, watch: dict, fields: dict) -> None:
        tool_id = fields.get("toolId") if isinstance(fields.get("toolId"), str) else None
        tool_name = fields.get("toolName") if isinstance(fields.get("toolName"), str) else None
        tool_input = _maybe_parse_json(fields.get("toolInput", MISSING))
        tool_response = _maybe_parse_json(fields.get("toolResponse", MISSING))
        if tool_name == "apply_patch" and isinstance(tool_input, str):
            for file_path in parse_apply_patch_files(tool_input):
                self._send_file_edit(session, {"filePath": file_path,
                                               "edits": [{"type": "apply_patch", "patch": tool_input}]})
        if tool_name and tool_response is not MISSING:
            self._send_observation(session, watch, {"toolName": tool_name, "toolInput": tool_input,
                                                    "toolResponse": tool_response, "toolUseId": tool_id})
        elif tool_name and tool_id:
            session.pending_tools[tool_id] = {"toolName": tool_name, "toolInput": _defined(tool_input)}

    def _handle_tool_result(self, session: SessionState, watch: dict, fields: dict) -> None:
        tool_id = fields.get("toolId") if isinstance(fields.get("toolId"), str) else None
        tool_name = fields.get("toolName") if isinstance(fields.get("toolName"), str) else None
        tool_response = _maybe_parse_json(fields.get("toolResponse", MISSING))
        tool_input = _maybe_parse_json(fields.get("toolInput", MISSING))
        if tool_id and tool_id in session.pending_tools:
            pending = session.pending_tools.pop(tool_id)
            tool_name = tool_name or pending.get("toolName")
            if tool_input is MISSING:
                tool_input = pending.get("toolInput")
        if tool_name:
            self._send_observation(session, watch, {"toolName": tool_name, "toolInput": tool_input,
                                                    "toolResponse": tool_response, "toolUseId": tool_id})
        else:
            log.debug("dropping tool_result with no resolvable tool name (session %s, tool %s)",
                      session.session_id, tool_id)
            self.counts["tool_results_unpaired"] += 1

    def _send_observation(self, session: SessionState, watch: dict, fields: dict) -> None:
        tool_name = fields.get("toolName")
        if not isinstance(tool_name, str) or not tool_name:
            return
        target = self._target(session)
        if target is None:
            return
        _, store, settings = target
        tool_use_id = fields.get("toolUseId") if isinstance(fields.get("toolUseId"), str) else None
        res = ingest.ingest_observation(
            store, settings, content_session_id=session.session_id, tool_name=tool_name,
            tool_input=_defined(_maybe_parse_json(fields.get("toolInput", MISSING))),
            tool_response=_defined(_maybe_parse_json(fields.get("toolResponse", MISSING))), cwd=session.cwd,
            platform_source=session.platform_source, agent_id=resolve_watch_agent_id(watch), tool_use_id=tool_use_id,
            project=session.project)
        if not res.get("ok"):
            raise RuntimeError(f"ingest_observation failed: {res.get('reason')}")
        self.counts["observations" if res.get("status") == "queued" else "observations_skipped"] += 1

    def _send_file_edit(self, session: SessionState, fields: dict) -> None:
        file_path = fields.get("filePath")
        if not isinstance(file_path, str) or not file_path:
            return
        target = self._target(session)
        if target is None:
            return
        _, store, settings = target
        res = ingest.file_edit(store, settings, content_session_id=session.session_id, file_path=file_path,
                               edits=fields.get("edits") if isinstance(fields.get("edits"), list) else None,
                               cwd=session.cwd, platform_source=session.platform_source)
        self.counts["file_edits" if res.get("status") == "queued" else "file_edits_skipped"] += 1

    def _handle_session_end(self, session: SessionState, watch: dict) -> None:
        target = self._target(session)
        if target is not None:
            root, store, _ = target
            # Like the Stop hook: summarize the turn from its last assistant message, then close the session.
            res = ingest.queue_summarize(store, content_session_id=session.session_id,
                                         last_assistant_message=session.last_assistant_message or "",
                                         platform_source=session.platform_source, cwd=session.cwd,
                                         project=session.project)
            self.counts["summaries" if res.get("status") == "queued" else "summaries_skipped"] += 1
            if ingest.session_end(store, content_session_id=session.session_id,
                                  platform_source=session.platform_source).get("status") == "accepted":
                self.counts["session_ends"] += 1
            self.spawn_roots.add(root)
        self.update_context(session, watch)
        session.pending_tools.clear()
        self.sessions.pop(self._session_key(watch, session.session_id), None)

    def update_context(self, session: SessionState, watch: dict) -> bool:
        """``context: {"mode": "agents"}``: write the session-start context into an AGENTS.md."""
        ctx = watch.get("context")
        if not isinstance(ctx, dict) or ctx.get("mode") != "agents":
            return False
        if should_suppress_native_codex_agents_context(watch):
            return False
        cwd = session.cwd or watch.get("workspace")
        if not cwd:
            return False
        root = find_store_root(cwd)
        if root is None:
            return False
        agents_path = expand_home_path(str(ctx.get("path") or f"{cwd}/AGENTS.md"))
        resolved = Path(agents_path).resolve()
        allowed = [Path(expand_home_path(str(cwd))).resolve(), cairn_home().resolve()]
        if not any(resolved == r or r in resolved.parents for r in allowed):
            log.warning("rejected AGENTS.md path outside the workspace and CAIRN_HOME: %s -> %s", ctx.get("path"),
                        resolved)
            return False
        try:
            content = inject_context(root, project_context(str(cwd)).all_projects,
                                     platform_source=session.platform_source,
                                     settings=self._repos.settings(root)).strip()
        except Exception as exc:  # noqa: BLE001 - context refresh never stops ingestion
            log.warning("failed to build AGENTS.md context: %s", exc)
            return False
        if not content:
            return False
        try:
            from .folders import write_agents_md
        except ImportError as exc:
            log.warning("AGENTS.md context unavailable: %s", exc)
            return False
        write_agents_md(agents_path, content)
        self.counts["context_updates"] += 1
        return True


# ---- polling watcher ---------------------------------------------------------------------------------------
def _has_glob(path: str) -> bool:
    return bool(_GLOB_CHARS.search(path))


def _split_top_level(body: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return parts


def expand_braces(pattern: str) -> list[str]:
    """``a/{b,c}/*.jsonl`` -> ``["a/b/*.jsonl", "a/c/*.jsonl"]`` (nested groups supported)."""
    depth, start = 0, -1
    for i, ch in enumerate(pattern):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                out: list[str] = []
                for part in _split_top_level(pattern[start + 1:i]):
                    out += expand_braces(pattern[:start] + part + pattern[i + 1:])
                return out
    return [pattern]


def _scan_glob(pattern: str) -> list[str]:
    found: set[str] = set()
    for pat in expand_braces(pattern.replace("\\", "/")):
        for match in glob.glob(pat, recursive=True, include_hidden=True):
            if os.path.isfile(match):
                found.add(os.path.abspath(match))
    return sorted(found)


def resolve_watch_files(input_path: str) -> list[str]:
    """The JSONL files a watch path covers: a glob, every ``*.jsonl`` under a folder, or one file."""
    if not input_path:
        return []
    if _has_glob(input_path):
        return _scan_glob(input_path)
    if os.path.isdir(input_path):
        return _scan_glob(os.path.join(input_path, "**", "*.jsonl"))
    if os.path.exists(input_path):
        return [os.path.abspath(input_path)]
    return []


def _end_offset(path: str, size: int) -> int:
    """The offset just past the last complete line (``startAtEnd`` never starts inside a line)."""
    try:
        with open(path, "rb") as fh:
            pos = size
            while pos > 0:
                step = min(65536, pos)
                pos -= step
                fh.seek(pos)
                block = fh.read(step)
                idx = block.rfind(b"\n")
                if idx >= 0:
                    return pos + idx + 1
    except OSError:
        return 0
    return 0


@dataclass
class _Tail:
    path: str
    watch: dict
    schema: dict
    session_override: str | None
    seen: tuple[int, float] | None = None   # (size, mtime) at the last read: unchanged files are not reopened


class TranscriptWatcher:
    """Polls the watched files and feeds new complete lines to the processor.

    Offsets (and the file identity, to notice rotation) are kept per absolute path in the state file,
    advancing only past complete lines; a shrunk or replaced file is read again from the start.
    """

    def __init__(self, config: dict, state_path: str | Path, processor: TranscriptEventProcessor | None = None):
        self.config = config
        self.state_path = Path(expand_home_path(str(state_path)))
        self.state = load_watch_state(self.state_path)
        self.processor = processor or TranscriptEventProcessor(self.state.get("sessions"))
        self.tails: dict[str, _Tail] = {}
        self._scanned = False
        self._dirty = False
        self._warned: set[str] = set()

    def scan(self) -> None:
        """Pick up files that newly match a watch (``startAtEnd`` applies to files present at the first scan)."""
        initial = not self._scanned
        for watch in self.config.get("watches") or []:
            if not isinstance(watch, dict):
                continue
            schema = resolve_schema(watch, self.config)
            if schema is None:
                if str(watch.get("name")) not in self._warned:
                    log.warning("missing schema for transcript watch %s", watch.get("name"))
                    self._warned.add(str(watch.get("name")))
                continue
            for file_path in resolve_watch_files(expand_home_path(str(watch.get("path") or ""))):
                if file_path not in self.tails:
                    self._add_tail(file_path, watch, schema, initial)
        self._scanned = True

    def _add_tail(self, file_path: str, watch: dict, schema: dict, initial: bool) -> None:
        offsets = self.state.setdefault("offsets", {})
        if file_path not in offsets and watch.get("startAtEnd") and initial:
            try:
                offsets[file_path] = _end_offset(file_path, os.stat(file_path).st_size)
            except OSError:
                offsets[file_path] = 0
            self._dirty = True
        self.tails[file_path] = _Tail(file_path, watch, schema, extract_session_id_from_path(file_path))
        log.info("watching transcript file %s (watch %s, schema %s)", file_path, watch.get("name"), schema.get("name"))

    def _read_new(self, tail: _Tail) -> int:
        try:
            st = os.stat(tail.path)
        except OSError:
            self.tails.pop(tail.path, None)   # gone; picked up again (from its saved offset) if it reappears
            return 0
        offsets = self.state.setdefault("offsets", {})
        identities = self.state.setdefault("inodes", {})
        identity = f"{st.st_dev}:{st.st_ino}"
        offset = int(offsets.get(tail.path, 0) or 0)
        if identities.get(tail.path) not in (None, identity):
            offset = 0   # rotated: a new file under the same name
        if identities.get(tail.path) != identity:
            identities[tail.path] = identity
            self._dirty = True
        if st.st_size < offset:
            offset = 0   # truncated
        if offsets.get(tail.path) != offset:
            offsets[tail.path] = offset
            tail.seen = None   # rotated or truncated: read again even if size and mtime look unchanged
            self._dirty = True
        if st.st_size == offset or tail.seen == (st.st_size, st.st_mtime):
            tail.seen = (st.st_size, st.st_mtime)
            return 0
        tail.seen = (st.st_size, st.st_mtime)
        lines = 0
        try:
            with open(tail.path, "rb") as fh:
                fh.seek(offset)
                carry = b""
                remaining = st.st_size - offset
                while remaining > 0:
                    block = fh.read(min(READ_CHUNK_BYTES, remaining))
                    if not block:
                        break
                    remaining -= len(block)
                    buf = carry + block
                    nl = buf.rfind(b"\n")
                    if nl < 0:
                        carry = buf
                        continue
                    for raw in buf[:nl + 1].split(b"\n"):
                        text = raw.decode("utf-8", errors="replace").strip()
                        if text:
                            lines += 1
                            self._handle_line(text, tail)
                    offset += nl + 1   # ``buf`` starts at ``offset``; the rest is carried to the next block
                    carry = buf[nl + 1:]
                    offsets[tail.path] = offset
                    self._dirty = True
        except OSError as exc:
            log.debug("failed to read transcript file %s: %s", tail.path, exc)
        return lines

    def _handle_line(self, line: str, tail: _Tail) -> None:
        try:
            entry = json.loads(line)
            self.processor.process_entry(entry, tail.watch, tail.schema, tail.session_override)
        except Exception as exc:  # noqa: BLE001 - one bad line never stops the tail
            self.processor.counts["errors"] += 1
            log.debug("failed to process transcript line (watch %s, file %s): %s", tail.watch.get("name"),
                      os.path.basename(tail.path), exc)

    def poll(self) -> dict:
        """One tick: discover files, read what was appended, persist offsets. Returns this tick's counts."""
        before = Counter(self.processor.counts)
        self.scan()
        lines = sum(self._read_new(tail) for tail in list(self.tails.values()))
        if lines or self._dirty:
            self.save()
        delta = Counter(self.processor.counts)
        delta.subtract(before)
        return {"files": len(self.tails), "lines": lines, **{k: v for k, v in delta.items() if v}}

    def save(self) -> None:
        self.state["sessions"] = self.processor.export_sessions()
        save_watch_state(self.state_path, self.state)
        self._dirty = False

    def run(self, stop_event: threading.Event | None = None, poll_seconds: float = 1.0,
            kick_worker: bool = True) -> dict:
        stop_event = stop_event or threading.Event()
        total: Counter = Counter()
        try:
            while not stop_event.is_set():
                tick = self.poll()
                total.update({k: v for k, v in tick.items() if k != "files"})
                total["files"] = tick["files"]
                if kick_worker and self.processor.spawn_roots:
                    _kick_workers(self.processor.spawn_roots)
                    self.processor.spawn_roots.clear()
                stop_event.wait(poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
        return dict(total)

    def stop(self) -> None:
        try:
            self.save()
        finally:
            self.processor.close()


def _kick_workers(roots: set[Path]) -> None:
    """Start the detached queue worker for repositories that queued work (a no-op while one runs)."""
    from .hooks import spawn_worker
    for root in list(roots):
        try:
            if load_settings(root).get("worker_spawn"):
                spawn_worker(root)
        except Exception as exc:  # noqa: BLE001 - never let the worker kick fail ingestion
            log.debug("worker spawn failed for %s: %s", root, exc)


def _ambient_settings() -> dict:
    return load_settings(find_store_root(os.getcwd()))


def _prepare(config_path: str | Path | None, settings: dict | None) -> tuple[TranscriptWatcher | None, dict]:
    """What the hosted watcher does on start: honour ``transcripts_enabled`` and ``codex_transcript_ingestion``,
    and stay idle when there is no config or nothing to watch."""
    settings = settings if settings is not None else _ambient_settings()
    if not settings.get("transcripts_enabled", True):
        log.info("transcript watcher disabled (transcripts_enabled = false)")
        return None, {"status": "disabled"}
    path = resolve_config_path(config_path, settings)
    if not path.exists():
        log.info("transcript watch config not found; skipping transcript capture: %s", path)
        return None, {"status": "no_config", "config": str(path)}
    try:
        loaded = load_transcript_watch_config(path)
    except ValueError as exc:
        log.error("failed to start the transcript watcher (continuing without transcript ingestion): %s", exc)
        return None, {"status": "invalid_config", "config": str(path), "error": str(exc)}
    config, removed = filter_native_hook_backed_codex_watches(loaded, bool(settings.get("codex_transcript_ingestion")))
    if removed:
        log.warning("skipped %d Codex transcript watch(es): native Codex hooks are authoritative"
                    " (set codex_transcript_ingestion = true to ingest them anyway)", removed)
    if not config["watches"]:
        log.info("transcript watch config has no active watches: %s", path)
        return None, {"status": "no_watches", "config": str(path), "removed": removed}
    return TranscriptWatcher(config, config.get("stateFile") or default_state_path()), {
        "status": "ok", "config": str(path), "removed": removed}


def run_once(config_path: str | Path | None = None, *, settings: dict | None = None,
             kick_worker: bool = True) -> dict:
    """Process whatever the watched transcripts gained since the last run; returns the counts."""
    watcher, info = _prepare(config_path, settings)
    if watcher is None:
        return info
    try:
        counts = watcher.poll()
    finally:
        watcher.stop()
    if kick_worker and watcher.processor.spawn_roots:
        _kick_workers(watcher.processor.spawn_roots)
    return {**info, **counts}


def watch(config_path: str | Path | None = None, stop_event: threading.Event | None = None,
          poll_seconds: float = 1.0, *, settings: dict | None = None, kick_worker: bool = True) -> dict:
    """Poll the watched transcripts until ``stop_event`` is set (or Ctrl+C); safe to run on a thread."""
    watcher, info = _prepare(config_path, settings)
    if watcher is None:
        return info
    log.info("transcript watcher started: %s", info.get("config"))
    return {**info, **watcher.run(stop_event, poll_seconds, kick_worker)}


# ---- Claude Code transcript replay (the recovery path) -----------------------------------------------------
CLAUDE_PLATFORM = "claude"
_SKIP_FLAGS = ("isSidechain", "isMeta", "isCompactSummary", "isVisibleInTranscriptOnly")
_NON_PROMPT_PREFIXES = ("[Request interrupted by user", "<local-command-stdout>", "<local-command-stderr>",
                        "<local-command-caveat>")
_COMMAND_NAME = re.compile(r"<command-name>\s*([^<]*?)\s*</command-name>")
_COMMAND_ARGS = re.compile(r"<command-args>\s*([\s\S]*?)\s*</command-args>")
REPLAY_COUNT_KEYS = ("prompts", "prompts_existing", "prompts_skipped", "observations", "observations_existing",
                     "observations_skipped", "summaries", "summaries_existing", "summaries_skipped",
                     "tool_results_unpaired", "skipped_lines", "skipped_no_repo", "skipped_untracked", "errors")


def claude_projects_dir() -> Path:
    return claude_config_dir() / "projects"


def claude_project_dir(cwd: str | Path) -> Path:
    """``~/.claude/projects/<cwd with / and . replaced by ->`` (honours ``CLAUDE_CONFIG_DIR``)."""
    return claude_projects_dir() / cwd_to_dashed(str(cwd))


def _truncate_bytes(text: str, max_bytes: int) -> str:
    data = text.encode("utf-8")
    return text if len(data) <= max_bytes else data[:max_bytes].decode("utf-8", errors="ignore")


def stored_prompt_form(prompt: str) -> str:
    """The text ``ingest.session_init`` stores for a prompt ("" for an entirely private one)."""
    text = _truncate_bytes(prompt, ingest.MAX_USER_PROMPT_BYTES) if prompt and prompt.strip() else ingest.MEDIA_PROMPT
    cleaned = strip_memory_tags(text)
    return normalize_stored_prompt_text(cleaned) if cleaned.strip() else ""


def claude_prompt_text(raw: str) -> str | None:
    """A user line's prompt as the UserPromptSubmit hook sees it, or None when the line is not a prompt."""
    stripped = (raw or "").strip()
    if stripped.startswith(_NON_PROMPT_PREFIXES):
        return None
    if stripped.startswith("<command-"):
        name = _COMMAND_NAME.search(stripped)
        if name:
            args = _COMMAND_ARGS.search(stripped)
            tail = args.group(1).strip() if args else ""
            return f"{name.group(1).strip()} {tail}".strip()
    return raw or ""


def _assistant_text(content: Any) -> str:
    text = content_to_text(content) or ""
    return re.sub(r"\n{3,}", "\n\n", SYSTEM_REMINDER_RE.sub("", text)).strip()


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with open(path, "rb") as fh:
        for raw in fh:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


@dataclass
class _Turn:
    has_prompt: bool = False
    number: int | None = None   # the stored prompt number this turn belongs to
    text: str = ""              # its last non-empty assistant text
    model: str | None = None


@dataclass
class _PromptLedger:
    """Prompts already stored for one session in one repository, and which of them this replay matched."""
    known: dict[int, str]
    used: set[int] = field(default_factory=set)
    position: int = 0
    previous: tuple[str, int | None] | None = None


@dataclass
class _ReplaySession:
    session_id: str
    cwd: str | None = None
    pending: dict[str, tuple[str, Any]] = field(default_factory=dict)
    turn: _Turn = field(default_factory=_Turn)


def _summary_marker(content_session_id: str, number: int) -> str:
    return f"transcripts:summary:{CLAUDE_PLATFORM}:{content_session_id}:{number}"


class _ClaudeReplay:
    def __init__(self, path: Path, root: Path | None = None):
        self.path = path
        self.root = root
        self.counts: Counter = Counter()
        self.sessions: dict[str, _ReplaySession] = {}
        self.ledgers: dict[tuple[Path, str], _PromptLedger] = {}
        self.spawn_roots: set[Path] = set()
        self._repos = _RepoCache()

    def close(self) -> None:
        self._repos.close()

    def run(self) -> dict:
        for entry in _iter_jsonl(self.path):
            kind = entry.get("type")
            if kind not in ("user", "assistant"):
                continue
            if any(entry.get(flag) for flag in _SKIP_FLAGS):
                self.counts["skipped_lines"] += 1
                continue
            sid = entry.get("sessionId") if isinstance(entry.get("sessionId"), str) and entry["sessionId"] \
                else self.path.stem
            session = self.sessions.setdefault(sid, _ReplaySession(sid))
            if isinstance(entry.get("cwd"), str) and entry["cwd"].strip():
                session.cwd = entry["cwd"]
            message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
            try:
                if kind == "assistant":
                    self._assistant(session, message)
                else:
                    self._user(session, entry, message.get("content"))
            except Exception as exc:  # noqa: BLE001 - one bad line never stops the replay
                self.counts["errors"] += 1
                log.debug("transcript replay failed on a line of %s: %s", self.path.name, exc)
        for session in self.sessions.values():
            self._finish_turn(session)
        return {"file": str(self.path), "sessions": sorted(self.sessions),
                **{k: int(self.counts.get(k, 0)) for k in REPLAY_COUNT_KEYS}}

    # -- repository --
    def _target(self, session: _ReplaySession) -> tuple[Path, Store, dict, str] | None:
        cwd = session.cwd or (str(self.root) if self.root else None)
        root = self.root or (find_store_root(cwd) if cwd else None)
        if root is None or not cwd:
            self.counts["skipped_no_repo"] += 1
            return None
        settings = self._repos.settings(root)
        if not should_track(cwd, settings):
            self.counts["skipped_untracked"] += 1
            return None
        return root, self._repos.store(root), settings, cwd

    def _ledger(self, root: Path, store: Store, content_session_id: str) -> _PromptLedger:
        key = (root, content_session_id)
        if key not in self.ledgers:
            sid = store.find_session_db_id(content_session_id, CLAUDE_PLATFORM)
            known = {} if sid is None else {int(r[0]): r[1] for r in store.db.execute(
                "SELECT prompt_number, prompt_text FROM user_prompts WHERE session_db_id=?", (sid,))}
            self.ledgers[key] = _PromptLedger(known)
        return self.ledgers[key]

    # -- lines --
    def _assistant(self, session: _ReplaySession, message: dict) -> None:
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str) \
                    and isinstance(block.get("name"), str):
                session.pending[block["id"]] = (block["name"], block.get("input"))
        text = _assistant_text(content)
        if text:
            session.turn.text = text
        if isinstance(message.get("model"), str) and message["model"]:
            session.turn.model = message["model"]

    def _user(self, session: _ReplaySession, entry: dict, content: Any) -> None:
        if isinstance(content, list):
            results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            if results:
                for block in results:
                    self._tool_result(session, entry, block, single=len(results) == 1)
                return
            raw = content_to_text(content) or ""
        elif isinstance(content, str):
            raw = content
        else:
            return
        prompt = claude_prompt_text(raw)
        if prompt is None:
            self.counts["skipped_lines"] += 1
            return
        self._prompt(session, prompt)

    def _prompt(self, session: _ReplaySession, prompt: str) -> None:
        if prompt and is_internal_protocol_payload(prompt):
            self.counts["prompts_skipped"] += 1   # not user work; the turn it starts stays with the current one
            return
        self._finish_turn(session)
        session.turn = _Turn(has_prompt=True)
        target = self._target(session)
        if target is None:
            return
        root, store, _, cwd = target
        text = prompt if prompt.strip() else ingest.MEDIA_PROMPT
        expected = stored_prompt_form(text)
        ledger = self._ledger(root, store, session.session_id)
        ledger.position += 1
        match = self._match(ledger, expected)
        if match is not None:
            ledger.used.add(match)
            session.turn.number = match
            ledger.previous = (expected, match)
            self.counts["prompts_existing"] += 1
            return
        if ledger.previous and ledger.previous[0] == expected and ledger.previous[1] is not None:
            session.turn.number = ledger.previous[1]   # a repeat the store collapsed into the previous prompt
            self.counts["prompts_existing"] += 1
            return
        res = ingest.session_init(store, content_session_id=session.session_id, project=project_context(cwd).primary,
                                  prompt=text, platform_source=CLAUDE_PLATFORM, cwd=cwd)
        number = res.get("promptNumber") if isinstance(res.get("promptNumber"), int) else None
        if res.get("skipped"):
            self.counts["prompts_skipped"] += 1
            if res.get("reason") == "private" and number is not None:
                ledger.known[number] = ""
                ledger.used.add(number)
        elif number is not None:
            ledger.known[number] = expected
            ledger.used.add(number)
            self.counts["prompts"] += 1
        session.turn.number = number
        ledger.previous = (expected, number)

    @staticmethod
    def _match(ledger: _PromptLedger, expected: str) -> int | None:
        """The stored prompt this one already is: same text at the same position, else the first unmatched
        stored prompt with the same text."""
        if ledger.known.get(ledger.position) == expected and ledger.position not in ledger.used:
            return ledger.position
        for number in sorted(ledger.known):
            if number not in ledger.used and ledger.known[number] == expected:
                return number
        return None

    def _tool_result(self, session: _ReplaySession, entry: dict, block: dict, single: bool) -> None:
        tool_use_id = block.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            return
        pending = session.pending.pop(tool_use_id, None)
        if pending is None:
            self.counts["tool_results_unpaired"] += 1
            return
        target = self._target(session)
        if target is None:
            return
        _, store, settings, cwd = target
        if store.db.execute("SELECT 1 FROM tool_uses WHERE content_session_id=? AND tool_use_id=? LIMIT 1",
                            (session.session_id, tool_use_id)).fetchone():
            self.counts["observations_existing"] += 1
            return
        tool_name, tool_input = pending
        response = entry["toolUseResult"] if single and "toolUseResult" in entry else block.get("content")
        res = ingest.ingest_observation(store, settings, content_session_id=session.session_id, tool_name=tool_name,
                                        tool_input=tool_input, tool_response=response, cwd=cwd,
                                        platform_source=CLAUDE_PLATFORM, tool_use_id=tool_use_id)
        if res.get("status") == "queued":
            self.counts["observations"] += 1
        elif res.get("reason") == "duplicate":
            self.counts["observations_existing"] += 1
        else:
            self.counts["observations_skipped"] += 1

    def _finish_turn(self, session: _ReplaySession) -> None:
        """A turn ends at the next real prompt or the end of the file: queue its summary like the Stop hook."""
        turn, session.turn = session.turn, _Turn()
        if not turn.has_prompt or turn.number is None or not turn.text.strip():
            return
        message = strip_memory_tags(turn.text)
        if not message.strip():
            return
        target = self._target(session)
        if target is None:
            return
        root, store, _, cwd = target
        if self._summary_exists(store, session.session_id, turn.number):
            self.counts["summaries_existing"] += 1
            return
        res = ingest.queue_summarize(store, content_session_id=session.session_id, last_assistant_message=message,
                                     platform_source=CLAUDE_PLATFORM, observed_model=turn.model, cwd=cwd,
                                     project=project_context(cwd).primary)
        if res.get("status") == "queued":
            store.set_kv(_summary_marker(session.session_id, turn.number), "1")
            self.counts["summaries"] += 1
            self.spawn_roots.add(root)
        else:
            self.counts["summaries_skipped"] += 1

    @staticmethod
    def _summary_exists(store: Store, content_session_id: str, number: int) -> bool:
        """Already summarized: by an earlier replay, a queued Stop-hook summary, or a stored summary."""
        if store.get_kv(_summary_marker(content_session_id, number)):
            return True
        sid = store.find_session_db_id(content_session_id, CLAUDE_PLATFORM)
        if sid is None:
            return False
        if store.db.execute("SELECT 1 FROM pending_messages WHERE session_db_id=? AND message_type='summarize'"
                            " AND prompt_number=? LIMIT 1", (sid, number)).fetchone():
            return True
        row = store.get_session_by_id(sid)
        memory_id = row.get("memory_session_id") if row else None
        return bool(memory_id and store.db.execute(
            "SELECT 1 FROM session_summaries WHERE memory_session_id=? AND prompt_number=? LIMIT 1",
            (memory_id, number)).fetchone())


def ingest_claude_transcript(path: str | Path, *, root: str | Path | None = None) -> dict:
    """Replay one Claude Code session file through the ingest functions (idempotent; returns counts).

    Real prompts become numbered prompts (a prompt already stored for the session -- same text at the same
    position -- is skipped), each tool_use/tool_result pair becomes a queued observation keyed by its
    ``tool_use_id``, and each turn end queues a summary of the turn's last assistant text. ``root`` forces
    the repository; otherwise each line's cwd decides it and lines outside a Cairn repository are skipped.
    """
    replay = _ClaudeReplay(Path(expand_home_path(str(path))), Path(root) if root else None)
    try:
        return {**replay.run(), "_spawn_roots": sorted(str(r) for r in replay.spawn_roots)}
    finally:
        replay.close()


def _public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def ingest_claude_project(cwd: str | Path, *, root: str | Path | None = None) -> dict:
    """Replay every Claude Code session recorded for ``cwd`` (``<config dir>/projects/<dashed cwd>/*.jsonl``)."""
    absolute = os.path.abspath(expand_home_path(str(cwd)))
    candidates = list(dict.fromkeys([absolute, os.path.realpath(absolute)]))
    dirs = [claude_project_dir(c) for c in candidates if claude_project_dir(c).is_dir()]
    files = sorted({p for d in dirs for p in d.glob("*.jsonl") if p.is_file()},
                   key=lambda p: (p.stat().st_mtime, str(p)))
    totals: Counter = Counter()
    results, spawn = [], set()
    for file_path in files:
        res = ingest_claude_transcript(file_path, root=root)
        spawn.update(res.get("_spawn_roots") or [])
        results.append(_public(res))
        totals.update({k: res.get(k, 0) for k in REPLAY_COUNT_KEYS})
    return {"dirs": [str(d) for d in dirs], "files": len(files), "results": results,
            **{k: int(totals.get(k, 0)) for k in REPLAY_COUNT_KEYS}, "_spawn_roots": sorted(spawn)}


# ---- CLI handlers ------------------------------------------------------------------------------------------
USAGE = "Usage: cairn sessions transcripts <init|watch|validate|replay <path>> [--config <path>]"


def _load_or_create(config_path: Path) -> dict:
    try:
        return load_transcript_watch_config(config_path)
    except ConfigNotFoundError:
        write_sample_config(config_path)
        print(f"Created sample config: {config_path}")
        return load_transcript_watch_config(config_path)


def cli_init(path: str | Path | None = None) -> int:
    """``cairn sessions transcripts init [--config P]``: write the sample config (built-in Codex schema)."""
    resolved = write_sample_config(resolve_config_path(path, _ambient_settings()))
    print(f"Created sample config: {resolved}")
    return 0


def cli_validate(path: str | Path | None = None) -> int:
    """``cairn sessions transcripts validate [--config P]``: check the config (creating the sample if missing)."""
    resolved = resolve_config_path(path, _ambient_settings())
    try:
        config = _load_or_create(resolved)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    problems = validate_config(config)
    if problems:
        print(f"Config has problems: {resolved}", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"Config OK: {resolved}")
    return 0


def cli_watch(path: str | Path | None = None, *, poll_seconds: float = 1.0,
              stop_event: threading.Event | None = None) -> int:
    """``cairn sessions transcripts watch [--config P]``: tail the configured transcripts until Ctrl+C."""
    resolved = resolve_config_path(path, _ambient_settings())
    try:
        config = _load_or_create(resolved)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    watcher = TranscriptWatcher(config, config.get("stateFile") or default_state_path())
    print("Transcript watcher running. Press Ctrl+C to stop.")
    watcher.run(stop_event, poll_seconds, kick_worker=True)
    return 0


def _replay_line(result: dict, files: int) -> str:
    return (f"Replayed {files} transcript(s): {result.get('prompts', 0)} new prompt(s)"
            f" ({result.get('prompts_existing', 0)} already recorded), {result.get('observations', 0)} tool"
            f" observation(s) queued ({result.get('observations_existing', 0)} already recorded),"
            f" {result.get('summaries', 0)} summary request(s) queued; {result.get('skipped_no_repo', 0)} event(s)"
            " skipped outside a Cairn repository.")


def cli_replay(path_or_dir: str | Path) -> int:
    """``cairn sessions transcripts replay <path>``: replay a Claude Code session file, a folder of them, or
    (for a folder holding no ``*.jsonl``) every recorded session of that project folder."""
    target = Path(os.path.abspath(expand_home_path(str(path_or_dir))))
    if target.is_file():
        result, files = ingest_claude_transcript(target), 1
    elif target.is_dir() and any(p.is_file() for p in target.glob("*.jsonl")):
        totals: Counter = Counter()
        spawn: set[str] = set()
        paths = sorted((p for p in target.glob("*.jsonl") if p.is_file()), key=lambda p: (p.stat().st_mtime, str(p)))
        for p in paths:
            res = ingest_claude_transcript(p)
            spawn.update(res.get("_spawn_roots") or [])
            totals.update({k: res.get(k, 0) for k in REPLAY_COUNT_KEYS})
        result, files = {**totals, "_spawn_roots": sorted(spawn)}, len(paths)
    elif target.is_dir():
        result = ingest_claude_project(target)
        files = int(result.get("files") or 0)
        if not files:
            print(f"No Claude Code sessions recorded for {target} (looked in {claude_project_dir(target)})")
            return 0
    else:
        print(f"Not found: {target}", file=sys.stderr)
        return 1
    _kick_workers({Path(r) for r in result.get("_spawn_roots") or []})
    print(_replay_line(result, files))
    return 0


def _arg_value(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    i = args.index(name)
    return args[i + 1] if i + 1 < len(args) else None


def run_transcript_command(subcommand: str | None, args: list[str]) -> int:
    config = _arg_value(args, "--config")
    if subcommand == "init":
        return cli_init(config)
    if subcommand == "watch":
        return cli_watch(config)
    if subcommand == "validate":
        return cli_validate(config)
    if subcommand == "replay":
        positional = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--config")]
        if positional:
            return cli_replay(positional[0])
    print(USAGE)
    return 1


def main(argv: list[str] | None = None) -> int:
    """``python -m cairn.engines.recall.transcripts <init|watch|validate|replay> ...``"""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return run_transcript_command(argv[0] if argv else None, argv[1:])
    except Exception as exc:  # noqa: BLE001 - report, do not trace back at the user
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
