"""Non-JSONL session readers for the transcript watcher: Gemini CLI, Cline and OpenCode.

The JSONL tailer in :mod:`.transcripts` only understands append-only line files. These agents keep sessions
as whole-file JSON (Gemini CLI, Cline) or in a SQLite database (OpenCode), so each gets a reader that turns
what is new since the last poll into *normalized entries* (``kind`` = user | assistant | reasoning | tool |
tool_result | end). :data:`NORMALIZED_SCHEMA` maps those entries onto the processor's actions, so the
existing :class:`~.transcripts.TranscriptEventProcessor` (and through it the same ingest functions the hooks
use) does all the recording; the repository is still decided by the entry's cwd.

A watch selects a reader with ``"source"``::

    {"name": "gemini", "source": "gemini", "startAtEnd": true}          # default locations
    {"name": "cline", "source": "cline", "path": "~/somewhere/**/*.json"}  # optional path override
    {"name": "opencode", "source": "opencode", "path": "/data/opencode.db"}

Per-watch progress lives in the watch state file under ``sources[<watch name>]``; every reader is
idempotent across restarts (message counters, tool-call flags, a time cursor plus a done-set). The formats
are tolerant by design: missing, corrupt or locked inputs are skipped and retried on the next poll.
Standard library only.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("cairn.recall")

SOURCES = ("gemini", "cline", "opencode")
REASONING_CLIP = 6000
TOOL_OUTPUT_CLIP = 4000
TEXT_CLIP = 60000
OPENCODE_OVERLAP_MS = 10 * 60 * 1000
OPENCODE_MAX_PENDING = 500

NORMALIZED_SCHEMA: dict = {
    "name": "sources",
    "version": "1",
    "sessionIdPath": "sessionId",
    "cwdPath": "cwd",
    "eventTypePath": "kind",
    "events": [
        {"name": "user", "match": {"path": "kind", "equals": "user"}, "action": "session_init",
         "fields": {"prompt": "text"}},
        {"name": "assistant", "match": {"path": "kind", "equals": "assistant"}, "action": "assistant_message",
         "fields": {"message": "text"}},
        {"name": "reasoning", "match": {"path": "kind", "equals": "reasoning"}, "action": "observation",
         "fields": {"toolName": {"value": "reasoning"}, "toolInput": {"value": {}}, "toolResponse": "text"}},
        {"name": "tool", "match": {"path": "kind", "equals": "tool"}, "action": "tool_use",
         "fields": {"toolId": "toolId", "toolName": "toolName", "toolInput": "toolInput",
                    "toolResponse": "toolResponse"}},
        {"name": "tool-result", "match": {"path": "kind", "equals": "tool_result"}, "action": "tool_result",
         "fields": {"toolId": "toolId", "toolName": "toolName", "toolInput": "toolInput",
                    "toolResponse": "toolResponse"}},
        {"name": "end", "match": {"path": "kind", "equals": "end"}, "action": "session_end"},
    ],
}


# ---- small helpers -----------------------------------------------------------------------------------------
def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def text_of(value: Any) -> str:
    """Plain text of a string, a ``{text}`` part, or a list of parts/blocks (non-text parts are ignored)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(t for t in (text_of(v) for v in value) if t)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if "content" in value:
            return text_of(value["content"])
    return ""


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def _read_json(path: str) -> Any:
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _files(patterns: list[str]) -> list[str]:
    found: set[str] = set()
    for pat in patterns:
        for match in glob.glob(pat.replace("\\", "/"), recursive=True, include_hidden=True):
            if os.path.isfile(match):
                found.add(os.path.abspath(match))
    return sorted(found)


def _override_patterns(path: str, default_glob: str) -> list[str]:
    path = _expand(path)
    if os.path.isdir(path):
        return [os.path.join(path, "**", default_glob)]
    return [path]


# ---- whole-file message engine (Gemini CLI, Cline) ---------------------------------------------------------
# A parsed message is a list of events: ("user"|"assistant"|"reasoning", text),
# ("tool", id, name, input, result-or-None) and ("tool_result", id, result).
def _sync_messages(st: dict, sid: str, cwd: str, messages: list[list[tuple]], complete: bool) -> list[dict]:
    """Normalized entries for what is new in ``messages``; ``st`` (count + tool flags) is updated in place.

    Text events are de-duplicated by message index (messages only ever append); tool events by their id and
    a flag: ``u`` (use emitted, no result yet) or ``r`` (result emitted)."""
    done = int(st.get("n") or 0)
    tools: dict[str, str] = st.setdefault("tools", {})
    base = {"sessionId": sid, "cwd": cwd}
    out: list[dict] = []
    for index, events in enumerate(messages):
        for ev in events:
            kind = ev[0]
            if kind in ("user", "assistant", "reasoning"):
                if index >= done and ev[1].strip():
                    limit = REASONING_CLIP if kind == "reasoning" else TEXT_CLIP
                    out.append({**base, "kind": kind, "text": clip(ev[1], limit)})
            elif kind == "tool":
                _, tid, name, tinput, result = ev
                flag = tools.get(tid)
                if flag == "r" or (flag == "u" and result is None):
                    continue
                entry = {**base, "kind": "tool_result" if flag == "u" else "tool", "toolId": tid,
                         "toolName": name, "toolInput": tinput}
                if result is not None:
                    entry["toolResponse"] = clip(stringify(result), TOOL_OUTPUT_CLIP)
                tools[tid] = "r" if result is not None else "u"
                out.append(entry)
            elif kind == "tool_result":
                _, tid, result = ev
                if tools.get(tid) == "r":
                    continue
                tools[tid] = "r"
                out.append({**base, "kind": "tool_result", "toolId": tid,
                            "toolResponse": clip(stringify(result), TOOL_OUTPUT_CLIP)})
    fresh = len(messages) > done
    st["n"] = max(done, len(messages))
    if out and fresh and complete:
        out.append({**base, "kind": "end"})
    return out


def _poll_files(files: list[tuple[str, Any]], bucket: dict, skip_existing: bool) -> list[dict]:
    """``files`` = [(path, parser)]; a parser returns (sid, cwd, messages, complete) or None (retry later)."""
    state: dict[str, dict] = bucket.setdefault("files", {})
    entries: list[dict] = []
    for path, parser in files:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        st = state.get(path)
        sig = [stat.st_size, stat.st_mtime_ns]
        if st is not None and st.get("sig") == sig:
            continue
        try:
            parsed = parser(path)
        except Exception as exc:  # noqa: BLE001 - one bad file never stops the others
            log.debug("failed to parse session file %s: %s", path, exc)
            continue
        if parsed is None:
            continue
        sid, cwd, messages, complete = parsed
        first = st is None
        st = st or {}
        new = _sync_messages(st, sid, cwd, messages, complete)
        st["sig"] = sig
        state[path] = st
        if not (first and skip_existing):
            entries += new
    for path in [p for p in state if not os.path.exists(p)]:
        state.pop(path, None)
    return entries


# ---- Gemini CLI --------------------------------------------------------------------------------------------
def gemini_roots() -> list[str]:
    roots: list[str] = []
    home = os.environ.get("GEMINI_CLI_HOME")
    if home:
        roots.append(os.path.join(_expand(home), ".gemini", "tmp"))
    roots.append(os.path.join(os.path.expanduser("~"), ".gemini", "tmp"))
    return list(dict.fromkeys(roots))


def _gemini_cwd(chat_file: str) -> str | None:
    project_dir = os.path.dirname(os.path.dirname(chat_file))
    try:
        text = Path(project_dir, ".project_root").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text.splitlines()[0].strip() if text else None


def _gemini_result(call: dict) -> Any:
    for key in ("result", "resultDisplay"):
        value = call.get(key)
        if value in (None, "", []):
            continue
        if isinstance(value, list):   # [{functionResponse: {response: {output|error}}}]
            texts = []
            for part in value:
                resp = (part.get("functionResponse") or {}).get("response") if isinstance(part, dict) else None
                if isinstance(resp, dict):
                    texts.append(stringify(resp.get("output", resp.get("error", resp))))
                elif part is not None:
                    texts.append(text_of(part) or stringify(part))
            if any(texts):
                return "\n".join(texts)
            continue
        if isinstance(value, dict) and isinstance(value.get("fileDiff"), str):
            return value["fileDiff"]
        return value
    status = call.get("status")
    return str(status) if status in ("error", "cancelled", "success") else None


def parse_gemini(path: str) -> tuple[str, str, list[list[tuple]], bool] | None:
    data = _read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        return None
    cwd = _gemini_cwd(path)
    sid = data.get("sessionId") if isinstance(data.get("sessionId"), str) else None
    if not cwd or not sid:
        return None
    messages: list[list[tuple]] = []
    last_final = False
    for msg in data["messages"]:
        events: list[tuple] = []
        last_final = False
        if isinstance(msg, dict):
            kind = msg.get("type")
            text = text_of(msg.get("content")) or text_of(msg.get("displayContent"))
            if kind == "user":
                events.append(("user", text))
            elif kind == "gemini":
                thoughts = msg.get("thoughts")
                if isinstance(thoughts, list):
                    parts = [f"{t.get('subject', '')}: {t.get('description', '')}".strip(": ").strip()
                             for t in thoughts if isinstance(t, dict)]
                    events.append(("reasoning", "\n".join(p for p in parts if p)))
                calls = [c for c in msg.get("toolCalls") or [] if isinstance(c, dict)]
                for i, call in enumerate(calls):
                    tid = str(call.get("id") or f"{msg.get('id') or len(messages)}:{i}")
                    events.append(("tool", tid, str(call.get("name") or "tool"), call.get("args") or {},
                                   _gemini_result(call)))
                events.append(("assistant", text))
                last_final = bool(text.strip()) and not calls
        messages.append(events)
    return sid, cwd, messages, last_final


def poll_gemini(watch: dict, bucket: dict, skip_existing: bool) -> list[dict]:
    if watch.get("path"):
        patterns = _override_patterns(str(watch["path"]), "*.json")
    else:
        patterns = [os.path.join(root, "*", "chats", "*.json") for root in gemini_roots()]
    return _poll_files([(f, parse_gemini) for f in _files(patterns)], bucket, skip_existing)


# ---- Cline -------------------------------------------------------------------------------------------------
_USER_INPUT_RE = re.compile(r"<user_input\b[^>]*>(.*?)</user_input>", re.DOTALL)
_WRAPPER_RE = re.compile(r"<(task|feedback|answer)>\s*(.*?)\s*</\1>", re.DOTALL)
_ENV_DETAILS_RE = re.compile(r"<environment_details>.*?</environment_details>", re.DOTALL)
_CLINE_CWD_RE = re.compile(r"Current Working Directory \(([^)\n]+)\)")
_CWD_KEYS = ("cwd", "workspace", "workspacePath", "workspace_path", "cwdOnTaskInitialization")


def cline_patterns() -> list[str]:
    home = os.path.expanduser("~")
    patterns = [os.path.join(home, ".cline", "data", "sessions", "*", "*.messages.json")]
    bases = [os.path.join(home, ".config"), os.path.join(home, "Library", "Application Support")]
    if os.environ.get("APPDATA"):
        bases.append(os.environ["APPDATA"])
    if sys.platform.startswith("linux") and os.environ.get("XDG_CONFIG_HOME"):
        bases.append(os.environ["XDG_CONFIG_HOME"])
    for base in dict.fromkeys(bases):
        for app in ("Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf"):
            patterns.append(os.path.join(base, app, "User", "globalStorage", "saoudrizwan.claude-dev", "tasks",
                                         "*", "api_conversation_history.json"))
    return patterns


def _cline_unwrap_user(text: str) -> str:
    inputs = _USER_INPUT_RE.findall(text)
    if inputs:
        return "\n".join(i.strip() for i in inputs if i.strip())
    text = _ENV_DETAILS_RE.sub("", text)
    wrapped = _WRAPPER_RE.findall(text)
    if wrapped:
        return "\n".join(w[1] for w in wrapped if w[1].strip())
    return text.strip()


def _cline_session(path: str) -> tuple[str, dict]:
    """(session id, metadata dict) from the file layout and any sibling summary/metadata JSON."""
    folder, name = os.path.dirname(path), os.path.basename(path)
    sid = name[: -len(".messages.json")] if name.endswith(".messages.json") else os.path.basename(folder)
    meta: dict = {}
    for cand in (f"{sid}.json", "task_metadata.json", "metadata.json"):
        data = _read_json(os.path.join(folder, cand))
        if isinstance(data, dict):
            meta = {**data, **meta}
    return sid, meta


def parse_cline(path: str) -> tuple[str, str, list[list[tuple]], bool] | None:
    data = _read_json(path)
    if isinstance(data, dict):
        data = data.get("messages")
    if not isinstance(data, list):
        return None
    sid, meta = _cline_session(path)
    if isinstance(meta.get("id"), str) and "api_conversation_history" not in path:
        sid = meta["id"]
    cwd = next((str(meta[k]) for k in _CWD_KEYS if isinstance(meta.get(k), str) and meta[k]), None)
    messages: list[list[tuple]] = []
    last_final = False
    for msg in data:
        events: list[tuple] = []
        last_final = False
        if not isinstance(msg, dict):
            messages.append(events)
            continue
        role, content = msg.get("role"), msg.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (
            [b for b in content if isinstance(b, dict)] if isinstance(content, list) else [])
        for block in blocks:
            btype = block.get("type")
            if btype == "text":
                raw = block.get("text") or ""
                if cwd is None:
                    found = _CLINE_CWD_RE.search(raw)
                    cwd = found.group(1).strip() if found else None
                if role == "user":
                    events.append(("user", _cline_unwrap_user(raw)))
                elif role == "assistant":
                    events.append(("assistant", raw))
                    last_final = bool(raw.strip())
            elif btype in ("thinking", "reasoning") and role == "assistant":
                events.append(("reasoning", block.get("thinking") or block.get("text") or ""))
            elif btype == "tool_use":
                events.append(("tool", str(block.get("id") or f"{len(messages)}:{len(events)}"),
                               str(block.get("name") or "tool"), block.get("input") or {}, None))
                last_final = False
            elif btype == "tool_result":
                events.append(("tool_result", str(block.get("tool_use_id") or ""), text_of(block.get("content"))))
        messages.append(events)
    if not cwd:
        return None
    ended = meta.get("ended_at") or meta.get("endedAt")
    return sid, cwd, messages, last_final or bool(ended)


def poll_cline(watch: dict, bucket: dict, skip_existing: bool) -> list[dict]:
    patterns = _override_patterns(str(watch["path"]), "*.json") if watch.get("path") else cline_patterns()
    files = [(f, parse_cline) for f in _files(patterns)]
    return _poll_files(files, bucket, skip_existing)


# ---- OpenCode ----------------------------------------------------------------------------------------------
def opencode_db_path() -> str:
    env = os.environ.get("OPENCODE_DB")
    if env:
        return _expand(env)
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(_expand(data_home), "opencode", "opencode.db")


def _open_readonly(path: str) -> sqlite3.Connection:
    # mode=ro (not immutable=1) so the write-ahead log is still read.
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _json_obj(raw: Any) -> dict:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _opencode_tool_output(state: dict) -> Any:
    out = state.get("output")
    if out in (None, ""):
        out = state.get("error")
    return out if out is not None else ""


def poll_opencode(watch: dict, bucket: dict, skip_existing: bool) -> list[dict]:
    path = _expand(str(watch["path"])) if watch.get("path") else opencode_db_path()
    if not os.path.isfile(path):
        return []
    first = "cursor" not in bucket
    cursor = int(bucket.get("cursor") or 0)
    parts: dict[str, list] = bucket.setdefault("parts", {})   # key -> [time_created, flag]
    entries: list[dict] = []
    try:
        conn = _open_readonly(path)
    except sqlite3.Error as exc:
        log.debug("cannot open OpenCode database %s: %s", path, exc)
        return []
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(part)")}
        if not cols:
            return []
        key = "p.id" if "id" in cols else "p.rowid"
        base_sql = (f"SELECT {key} AS k, p.message_id AS message_id, p.time_created AS t, p.data AS data, "
                    "m.data AS mdata, m.session_id AS session_id, s.directory AS directory "
                    "FROM part p JOIN message m ON m.id = p.message_id "
                    "LEFT JOIN session s ON s.id = m.session_id ")
        since = max(0, cursor - OPENCODE_OVERLAP_MS)
        rows = list(conn.execute(base_sql + "WHERE p.time_created >= ? ORDER BY p.time_created, p.rowid",
                                 (since,)))
        pending = [k for k, v in parts.items() if v[1] == "u"]
        seen_keys = {str(r["k"]) for r in rows}
        for i in range(0, len(pending), 200):
            chunk = [k for k in pending[i:i + 200] if k not in seen_keys]
            if chunk:
                rows += list(conn.execute(base_sql + f"WHERE {key} IN ({','.join('?' * len(chunk))})",
                                          tuple(int(k) if key == "p.rowid" else k for k in chunk)))
        ended = bucket.setdefault("ended", {})
        touched: dict[str, tuple[dict, dict, str]] = {}
        for row in rows:
            k, t = str(row["k"]), int(row["t"] or 0)
            cwd, sid = row["directory"], row["session_id"]
            if not cwd or not sid:
                continue
            data, mdata = _json_obj(row["data"]), _json_obj(row["mdata"])
            role, ptype = mdata.get("role"), data.get("type")
            base = {"sessionId": str(sid), "cwd": str(cwd)}
            flag = parts.get(k, [t, None])[1]
            new: list[dict] = []
            if ptype == "text":
                if flag == "d":
                    continue
                if data.get("synthetic") or data.get("ignored"):
                    parts[k] = [t, "d"]
                    continue
                times = data.get("time")
                if role == "assistant" and isinstance(times, dict) and not times.get("end"):
                    continue   # still streaming: pick it up on a later poll
                text = text_of(data.get("text"))
                if text.strip():
                    new.append({**base, "kind": "user" if role == "user" else "assistant",
                                "text": clip(text, TEXT_CLIP)})
                flag = "d"
            elif ptype == "reasoning":
                if flag != "d":
                    text = text_of(data.get("text"))
                    if text.strip():
                        new.append({**base, "kind": "reasoning", "text": clip(text, REASONING_CLIP)})
                    flag = "d"
            elif ptype == "tool":
                tstate = data.get("state") if isinstance(data.get("state"), dict) else {}
                finished = tstate.get("status") in ("completed", "error")
                if flag != "d":
                    entry = {**base, "kind": "tool_result" if flag == "u" else "tool",
                             "toolId": str(data.get("callID") or k), "toolName": str(data.get("tool") or "tool"),
                             "toolInput": tstate.get("input") or {}}
                    if finished:
                        entry["toolResponse"] = clip(stringify(_opencode_tool_output(tstate)), TOOL_OUTPUT_CLIP)
                        new.append(entry)
                        flag = "d"
                    elif flag != "u":
                        new.append(entry)
                        flag = "u"
            else:
                flag = "d"
            parts[k] = [t, flag]
            entries += new
            touched[str(row["message_id"])] = (base, mdata, str(role))
            cursor = max(cursor, t)
        for mid, (base, mdata, role) in touched.items():
            times = mdata.get("time") if isinstance(mdata.get("time"), dict) else {}
            if (role == "assistant" and times.get("completed") and mdata.get("finish") not in (None, "tool-calls")
                    and mid not in ended):
                ended[mid] = cursor
                entries.append({**base, "kind": "end"})
        if first and cursor == 0:
            cursor = 1   # empty database: remember that the first pass happened
        bucket["cursor"] = cursor
        floor = cursor - OPENCODE_OVERLAP_MS * 2
        for k in [k for k, v in parts.items() if v[1] == "d" and v[0] < floor]:
            del parts[k]
        stale = [k for k, v in parts.items() if v[1] == "u"]
        for k in sorted(stale, key=lambda x: parts[x][0])[: max(0, len(stale) - OPENCODE_MAX_PENDING)]:
            del parts[k]
        for k in [k for k, v in ended.items() if v < floor]:
            del ended[k]
    except sqlite3.Error as exc:   # locked, mid-migration, or an unexpected schema: try again next poll
        log.debug("cannot read OpenCode database %s: %s", path, exc)
        return []
    finally:
        conn.close()
    return [] if (first and skip_existing) else entries


# ---- dispatch ----------------------------------------------------------------------------------------------
_POLLERS = {"gemini": poll_gemini, "cline": poll_cline, "opencode": poll_opencode}


def is_source_watch(watch: Any) -> bool:
    return isinstance(watch, dict) and bool(watch.get("source"))


def poll_source(watch: dict, state: dict, initial: bool) -> list[dict]:
    """New normalized entries for a ``source`` watch. ``state`` is the whole watch-state dict; this watch's
    progress is ``state["sources"][<watch name>]``. ``startAtEnd`` (honoured for sessions present at the
    watcher's first scan) records existing content without ingesting it."""
    poller = _POLLERS.get(str(watch.get("source")).lower())
    if poller is None:
        return []
    bucket = state.setdefault("sources", {}).setdefault(str(watch.get("name") or watch.get("source")), {})
    return poller(watch, bucket, bool(watch.get("startAtEnd")) and initial)


def validate_source_watch(watch: dict) -> list[str]:
    if str(watch.get("source")).lower() not in SOURCES:
        return [f"unknown source {watch.get('source')!r} (expected one of {', '.join(SOURCES)})"]
    return []
