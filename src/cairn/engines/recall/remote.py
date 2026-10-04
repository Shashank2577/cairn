"""Session capture from other machines: the outbox and push on the agent's side, the receiving end on
a team server (standard library only).

Client. When ``.cairn/config.toml`` has ``[team] server`` and ``[team] project``, every hook event the
local store accepts is also written to ``remote_outbox``, cleaned first: private blocks removed,
secrets masked, long fields trimmed, the summary text taken from the local transcript (a server
never reads client files). ``push()`` sends the rows past a cursor to
``POST {server}/api/p/{project}/sessions/events`` in batches, with ``CAIRN_TOKEN`` as the bearer
token. The cursor moves only when the server acknowledged a batch, and each event carries
``<client id>:<outbox id>``, so a batch sent twice is recorded once. The Stop/SessionEnd hooks start a
push in the background; after a failure the next pushes wait (backoff) and retry from the cursor.

Server. ``ingest_events()`` validates a batch and records each event through the same hook handlers
as local capture (privacy, redaction, skip rules, the server's own ``[recall]`` settings), into the
server's store, under the server's project name, at the time the event happened.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
import tomllib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import schema
from .projects import git_branch
from .settings import config_path
from .store import Store
from .tags import redact, strip_memory_tags

log = logging.getLogger("cairn.recall")

TOKEN_ENV = "CAIRN_TOKEN"
REMOTE_EVENTS = ("session-init", "observation", "summarize", "session-end", "file-edit")
MAX_BATCH = 500                        # events the server takes per request
MAX_EVENT_BYTES = 512 * 1024           # one event's payload, as JSON
PUSH_BATCH = 200                       # events the client sends per request
MAX_REQUEST_BYTES = 4 * 1024 * 1024    # the client's cap on one request body
FIELD_MAX_CHARS = 20_000               # longer text is trimmed (the observer reads 16k per field)
RECEIVED_TTL_MS = 30 * 24 * 3600 * 1000
BACKOFF_BASE_MS = 30_000
BACKOFF_MAX_MS = 30 * 60 * 1000
RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}
PRIVATE_PROMPT = "<private></private>"  # a wholly private prompt: recorded empty on the server too

CURSOR_KEY = "remote.push_cursor"
CLIENT_KEY = "remote.client_id"
LAST_KEY = "remote.last_push"
FAILURES_KEY = "remote.failures"
RETRY_KEY = "remote.retry_after"

_COMMON = ("session_id", "cwd", "agent_id", "agent_type", "branch")
_EVENT_FIELDS = {
    "session-init": ("prompt",),
    "observation": ("tool_name", "tool_input", "tool_response", "tool_use_id"),
    "summarize": ("last_assistant_message", "observed_model", "stop_hook_active"),
    "session-end": ("reason",),
    "file-edit": ("file_path", "edits"),
}
_CONTENT = {"prompt", "tool_input", "tool_response", "last_assistant_message", "edits"}
_TEXT = {"prompt", "last_assistant_message"}
_STR_LIMITS = {"session_id": 256, "cwd": 4096, "agent_id": 128, "agent_type": 128, "branch": 256, "tool_name": 256,
               "tool_use_id": 256, "observed_model": 128, "reason": 64, "file_path": 4096}
_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


# ---- configuration ---------------------------------------------------------------------------------------
def team_config(root: Path | str) -> dict | None:
    """``{"server", "project"}`` from ``[team]`` in ``.cairn/config.toml``, or None when not connected."""
    try:
        team = tomllib.loads(config_path(Path(root)).read_text(encoding="utf-8")).get("team") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return None
    server, project = team.get("server"), team.get("project")
    if not isinstance(server, str) or not re.match(r"^https?://\S+$", server.strip()):
        return None
    if not isinstance(project, (str, int)) or not str(project).strip():
        return None
    env = team.get("token_env")
    env = env.strip() if isinstance(env, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*", env.strip()) else TOKEN_ENV
    return {"server": server.strip().rstrip("/"), "project": str(project).strip(), "token_env": env}


def token_env(cfg: dict | None) -> str:
    """The environment variable holding the API token (`[team] token_env`, default CAIRN_TOKEN)."""
    return (cfg or {}).get("token_env") or TOKEN_ENV


def events_url(cfg: dict) -> str:
    return f"{cfg['server']}/api/p/{quote(cfg['project'], safe='')}/sessions/events"


# ---- cleaning (both ends) ------------------------------------------------------------------------------------
def _trim(text: str, limit: int = FIELD_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.6), int(limit * 0.3)
    return f"{text[:head]}\n[... {len(text) - head - tail} characters trimmed ...]\n{text[-tail:]}"


def _clean(value: Any, depth: int = 0) -> Any:
    """Private blocks removed, secrets masked and long strings trimmed, everywhere in a JSON value."""
    if isinstance(value, str):
        return _trim(redact(strip_memory_tags(value)))
    if depth > 12:
        return None
    if isinstance(value, list):
        return [_clean(v, depth + 1) for v in value]
    if isinstance(value, dict):
        return {str(k): _clean(v, depth + 1) for k, v in value.items()}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _trim(str(value))


def _fit(payload: dict) -> dict:
    """Keep one event under the server's size limit by flattening its biggest fields to trimmed text."""
    for key in ("tool_response", "tool_input", "edits", "last_assistant_message", "prompt"):
        if len(json.dumps(payload, ensure_ascii=False).encode()) <= MAX_EVENT_BYTES - 4096:
            break
        if key in payload:
            text = payload[key] if isinstance(payload[key], str) else json.dumps(payload[key], ensure_ascii=False)
            payload[key] = _trim(text, 8_000)
    return payload


def outbox_payload(event: str, inp: dict) -> dict:
    """What leaves the machine for one accepted event (a normalized hook input)."""
    out: dict[str, Any] = {}
    for key in _COMMON + _EVENT_FIELDS.get(event, ()):
        value = inp.get(key)
        if value is None:
            continue
        if key == "prompt" and isinstance(value, str):
            cleaned = _trim(redact(strip_memory_tags(value)))
            out[key] = cleaned if cleaned.strip() or not value.strip() else PRIVATE_PROMPT
        elif key in _CONTENT:
            out[key] = _clean(value)
        else:
            out[key] = str(value) if key in _STR_LIMITS else value
    if "branch" not in out and isinstance(inp.get("cwd"), str):
        branch = git_branch(inp["cwd"])
        if branch:
            out["branch"] = branch
    return _fit(out)


# ---- client: outbox and push -----------------------------------------------------------------------------------
def record_outbox(root: Path | str, event: str, inp: dict) -> bool:
    """Queue an accepted hook event for the team server (only when one is configured; never raises)."""
    try:
        if event not in REMOTE_EVENTS or team_config(root) is None:
            return False
        payload = outbox_payload(event, inp)
        with Store.open(root) as st:
            st.db.execute("INSERT INTO remote_outbox(event, platform, payload, created_at_epoch) VALUES(?,?,?,?)",
                          (event, str(inp.get("platform") or "claude-code"), json.dumps(payload, ensure_ascii=False),
                           schema.now_ms()))
        return True
    except Exception as exc:  # noqa: BLE001 - capture never fails because of the team server
        log.warning("team outbox write failed: %s", exc)
        return False


def push_lock_path(root: Path | str) -> Path:
    return Path(root) / ".cairn" / "recall" / "push.lock"


@contextmanager
def _push_lock(root: Path) -> Iterator[bool]:
    """Yields False when another push holds the lock."""
    path = push_lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # no advisory locks here; a concurrent push is harmless (the server dedupes)
        yield True
        return
    with open(path, "a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def push_due(root: Path | str) -> bool:
    """A team server is configured, events are waiting and no failure backoff is running."""
    if team_config(root) is None or not schema.store_path(root).exists():
        return False
    try:
        with Store.open(root) as st:
            waiting = st.db.execute("SELECT EXISTS(SELECT 1 FROM remote_outbox)").fetchone()[0]
            retry_after = int(st.get_kv(RETRY_KEY, "0") or 0)
    except sqlite3.Error:
        return False
    return bool(waiting) and retry_after <= schema.now_ms()


def _client_id(st: Store) -> str:
    cid = st.get_kv(CLIENT_KEY)
    if not cid:
        cid = uuid.uuid4().hex
        st.set_kv(CLIENT_KEY, cid)
    return cid


def _post(url: str, token: str, body: dict, attempts: int, backoff: float,
          timeout: float) -> tuple[int | None, dict, str | None]:
    """POST JSON; retries transient failures. Returns (HTTP status or None, response object, error)."""
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # never replay the token to another location
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    data = json.dumps(body, ensure_ascii=False).encode()
    status: int | None = None
    error: str | None = None
    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(backoff * (2 ** (attempt - 1)))
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "cairn-sessions-push"})
        try:
            with opener.open(req, timeout=timeout) as resp:
                status, raw = resp.status, resp.read()
            try:
                parsed = json.loads(raw or b"null")
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict):
                return status, {}, "the server's reply is not a JSON object (is [team] server the team server?)"
            return status, parsed, None
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                detail = ""
            error = f"HTTP {exc.code}" + (f": {detail}" if detail else "")
            if exc.code not in RETRY_STATUSES:
                return status, {}, error
        except (OSError, ValueError) as exc:  # URLError, timeouts, refused connections
            status, error = None, f"cannot reach the team server: {getattr(exc, 'reason', exc)}"
    return status, {}, error


def _finish(st: Store, result: dict) -> dict:
    now = schema.now_ms()
    st.set_kv(LAST_KEY, json.dumps({"at": now, **{k: v for k, v in result.items() if k != "rejections"}}))
    if result.get("ok"):
        st.del_kv(FAILURES_KEY)
        st.del_kv(RETRY_KEY)
    else:
        failures = int(st.get_kv(FAILURES_KEY, "0") or 0) + 1
        st.set_kv(FAILURES_KEY, str(failures))
        st.set_kv(RETRY_KEY, str(now + min(BACKOFF_BASE_MS * 2 ** (failures - 1), BACKOFF_MAX_MS)))
    return result


def _pending(st: Store, cursor: int) -> int:
    return int(st.db.execute("SELECT COUNT(*) FROM remote_outbox WHERE id > ?", (cursor,)).fetchone()[0])


def push(root: Path | str, *, token: str | None = None, batch: int = PUSH_BATCH, attempts: int = 3,
         backoff: float = 1.0, timeout: float = 20.0) -> dict:
    """Send every not-yet-pushed outbox event to the team server. Never raises for server trouble:
    the result says what happened (``ok``, counts, ``error``) and the next push resumes at the cursor."""
    root = Path(root)
    cfg = team_config(root)
    if cfg is None:
        return {"ok": False, "error": "no team server configured ([team] server and project in .cairn/config.toml)"}
    token = token or os.environ.get(token_env(cfg), "").strip()
    with _push_lock(root) as held:
        if not held:
            return {"ok": True, "busy": True, "pushed": 0}
        with Store.open(root) as st:
            if not token:
                return _finish(st, {"ok": False, "error": f"{token_env(cfg)} is not set", "pushed": 0,
                                    "pending": _pending(st, int(st.get_kv(CURSOR_KEY, "0") or 0))})
            return _finish(st, _push(st, events_url(cfg), token, batch, attempts, backoff, timeout))


def _push(st: Store, url: str, token: str, batch: int, attempts: int, backoff: float, timeout: float) -> dict:
    client = _client_id(st)
    cursor = int(st.get_kv(CURSOR_KEY, "0") or 0)
    size = max(1, min(int(batch), MAX_BATCH))
    totals = {"pushed": 0, "accepted": 0, "skipped": 0, "duplicates": 0, "rejected": 0, "requests": 0}
    rejections: list[dict] = []
    error, http_status = None, None
    while True:
        rows = st.db.execute("SELECT * FROM remote_outbox WHERE id > ? ORDER BY id LIMIT ?", (cursor, size)).fetchall()
        if not rows:
            break
        events: list[dict] = []
        nbytes = 0
        for r in rows:
            n = len(r["payload"]) + 200
            if events and nbytes + n > MAX_REQUEST_BYTES:
                break
            events.append({"id": f"{client}:{r['id']}", "platform": r["platform"], "event": r["event"],
                           "ts": r["created_at_epoch"], "payload": json.loads(r["payload"])})
            nbytes += n
        status, body, err = _post(url, token, {"events": events}, attempts, backoff, timeout)
        totals["requests"] += 1
        if status == 413 and len(events) > 1:
            size = max(1, len(events) // 2)
            continue
        if status == 413:  # one event the server will never take: it is dropped, not retried forever
            status, body, err = 200, {"rejected": [{"index": 0, "id": events[0]["id"],
                                                    "reason": "too large for the server"}]}, None
        if err is not None or status is None or not 200 <= status < 300:
            error, http_status = err or f"HTTP {status}", status
            break
        for key in ("accepted", "skipped", "duplicates"):
            totals[key] += int(body.get(key) or 0)
        rejected = body.get("rejected") or []
        totals["rejected"] += len(rejected)
        rejections += rejected[: max(0, 10 - len(rejections))]
        for r in rejected[:10]:
            log.warning("team server rejected %s: %s", r.get("id"), r.get("reason"))
        totals["pushed"] += len(events)
        cursor = int(rows[len(events) - 1]["id"])
        with st.tx():
            st.set_kv(CURSOR_KEY, str(cursor))
            st.db.execute("DELETE FROM remote_outbox WHERE id <= ?", (cursor,))
    result: dict[str, Any] = {"ok": error is None, **totals, "cursor": cursor, "pending": _pending(st, cursor)}
    if rejections:
        result["rejections"] = rejections
    if error is not None:
        result["error"] = error
        result["http_status"] = http_status
    return result


def status(root: Path | str) -> dict:
    """Team sync state for ``cairn sessions push --status``."""
    root = Path(root)
    cfg = team_config(root)
    out: dict[str, Any] = {"configured": cfg is not None,
                           "token": bool(os.environ.get(token_env(cfg), "").strip())}
    if cfg:
        out.update(server=cfg["server"], project=cfg["project"], url=events_url(cfg))
    if not schema.store_path(root).exists():
        return {**out, "pending": 0}
    with Store.open(root) as st:
        cursor = int(st.get_kv(CURSOR_KEY, "0") or 0)
        out.update(pending=_pending(st, cursor), cursor=cursor, failures=int(st.get_kv(FAILURES_KEY, "0") or 0))
        retry_after = int(st.get_kv(RETRY_KEY, "0") or 0)
        if retry_after > schema.now_ms():
            out["retry_after"] = schema.iso(retry_after)
        last = st.get_kv(LAST_KEY)
        if last:
            out["last_push"] = json.loads(last)
    return out


# ---- server: receiving ------------------------------------------------------------------------------------------
class IngestError(ValueError):
    """The request as a whole is unusable; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _event_time(ts: Any) -> int | None:
    """Epoch milliseconds (seconds are accepted); never in the future; None when unusable."""
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or ts <= 0:
        return None
    ms = int(ts * 1000) if ts < 1e11 else int(ts)
    return min(ms, int(time.time() * 1000))


def _event_input(ev: Any) -> tuple[str, dict, int | None, str | None]:
    """(event, normalized hook input, event time, event id) for one pushed event; ValueError when invalid."""
    from .hooks import LEGACY_EVENTS
    if not isinstance(ev, dict):
        raise ValueError("an event must be an object")
    eid = ev.get("id")
    if eid is not None and (not isinstance(eid, str) or not 0 < len(eid) <= 200):
        raise ValueError("id must be a string of at most 200 characters")
    name = ev.get("event")
    event = LEGACY_EVENTS.get(name, name) if isinstance(name, str) else None
    if event not in REMOTE_EVENTS:
        raise ValueError(f"unsupported event {name!r} (expected one of {', '.join(REMOTE_EVENTS)})")
    platform = ev.get("platform") or "claude-code"
    if not isinstance(platform, str) or not _PLATFORM_RE.match(platform):
        raise ValueError("invalid platform")
    payload = ev.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    size = len(json.dumps(payload, ensure_ascii=False, default=str).encode())
    if size > MAX_EVENT_BYTES:
        raise ValueError(f"payload is {size} bytes (limit {MAX_EVENT_BYTES})")
    inp: dict[str, Any] = {"platform": platform}
    if payload.get("session_id") is None and ev.get("session_id") is not None:
        payload = {**payload, "session_id": ev["session_id"]}
    for key in _COMMON + _EVENT_FIELDS[event]:
        value = payload.get(key)
        if value is None:
            continue
        limit = _STR_LIMITS.get(key)
        if (limit or key in _TEXT) and not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        if limit and len(value) > limit:
            raise ValueError(f"{key} is longer than {limit} characters")
        if key == "stop_hook_active" and not isinstance(value, bool):
            raise ValueError("stop_hook_active must be a boolean")
        # the prompt keeps its private blocks for the handler to strip (an all-private prompt is recorded empty)
        inp[key] = redact(value) if key == "prompt" else _clean(value) if key in _CONTENT else value
    if not inp.get("session_id"):
        raise ValueError("session_id is required")
    return event, inp, _event_time(ev.get("ts")), eid


def ingest_events(root: Path | str, events: Any, *, caller: str | None = None) -> dict:
    """Record hook events pushed from other machines into ``root``'s store, exactly as local hooks
    would. ``events`` is a list (or ``{"events": [...]}``) of ``{event, platform, payload, ts?, id?,
    session_id?}``. Raises ``IngestError`` (400/413) for an unusable request; otherwise returns
    ``{received, accepted, skipped, duplicates, rejected: [{index, id, reason}]}``.

    ``caller`` (the authenticated user) scopes everything it sends: session ids become
    ``<caller>:<session id>``, event ids are de-duplicated per caller, and its sessions record
    ``pushed_by = caller``. Without it, ids are used as sent."""
    from .hooks import dispatch, remote_target
    from .projects import project_context
    if isinstance(events, dict):
        events = events.get("events")
    if not isinstance(events, list):
        raise IngestError('expected a list of events (or {"events": [...]})')
    if len(events) > MAX_BATCH:
        raise IngestError(f"{len(events)} events in one request (limit {MAX_BATCH})", 413)
    root = Path(root)
    caller = str(caller) if caller not in (None, "") else None
    project = project_context(str(root)).primary
    out: dict[str, Any] = {"received": len(events), "accepted": 0, "skipped": 0, "duplicates": 0, "rejected": []}
    with Store.open(root, project_name=project) as st:
        st.db.execute("DELETE FROM remote_received WHERE received_at_epoch < ?", (schema.now_ms() - RECEIVED_TTL_MS,))
        for i, ev in enumerate(events):
            try:
                event, inp, ts, eid = _event_input(ev)
            except ValueError as exc:
                out["rejected"].append({"index": i, "id": ev.get("id") if isinstance(ev, dict) else None,
                                        "reason": str(exc)})
                continue
            if caller:
                inp["session_id"] = f"{caller}:{inp['session_id']}"
            key = f"{caller}:{eid}" if caller and eid else eid
            if key and st.db.execute("SELECT 1 FROM remote_received WHERE event_id=?", (key,)).fetchone():
                out["duplicates"] += 1
                continue
            try:
                with schema.clock(ts), remote_target(root, project, inp.get("branch")):
                    res = dispatch(event, inp)
            except Exception as exc:  # noqa: BLE001 - one bad event never loses the rest of the batch
                log.warning("pushed event %s failed: %s", eid or i, exc)
                out["rejected"].append({"index": i, "id": eid, "reason": f"could not be recorded: {exc}"})
                continue
            if key:
                st.db.execute("INSERT OR IGNORE INTO remote_received(event_id, received_at_epoch) VALUES(?, ?)",
                              (key, schema.now_ms()))
            if caller:
                st.db.execute("UPDATE sdk_sessions SET pushed_by=? WHERE content_session_id=? AND pushed_by IS NULL",
                              (caller, inp["session_id"]))
            out["accepted" if res.get("_push") else "skipped"] += 1
    return out


# ---- entry point (the hooks start ``python -m cairn.engines.recall.remote --root <repo>``) -------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m cairn.engines.recall.remote", description="Push captured"
                                 " sessions to the team server.")
    ap.add_argument("--root", required=True)
    args = ap.parse_args(argv)
    res = push(Path(args.root))
    print(json.dumps({"at": schema.iso(), **res}, default=str))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
