"""Observer health ledger and quota breaker, kept in the store's ``kv`` table (standard library only).

Consecutive model failures mark the observer unhealthy and a warning is appended to every
session-start context, so an outage is never silent. A spent allowance arms a cooldown: queued work
is kept, and only one probe request is allowed through per window instead of one per tool call.
"""
from __future__ import annotations

import json
import re
import time

UNHEALTHY_FAILURE_THRESHOLD = 3
QUOTA_RECHECK_COOLDOWN_MS = 30 * 60_000
MAX_ERROR_MESSAGE_LENGTH = 600
_KEY = "observer.health"
_QUOTA = "observer.quota"


def _now() -> int:
    return int(time.time() * 1000)


def scrub(message: str) -> str:
    s = re.sub(r"(?i)(sk-[a-z0-9_-]{8,}|bearer\s+[\w.~+/-]+=*|api[_-]?key\s*[=:]\s*\S+)", "[redacted]", message or "")
    s = " ".join(s.split())
    return s[:MAX_ERROR_MESSAGE_LENGTH] + ("…" if len(s) > MAX_ERROR_MESSAGE_LENGTH else "")


def read(store) -> dict | None:
    raw = store.get_kv(_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _write(store, state: dict) -> None:
    store.set_kv(_KEY, json.dumps(state))


def record_failure(store, provider: str, message: str, *, kind: str | None = None, action: str | None = None,
                   url: str | None = None) -> dict:
    state = read(store) or {"consecutiveFailures": 0}
    now = _now()
    if not state.get("consecutiveFailures"):
        state["failingSinceAt"] = now
    state.update(consecutiveFailures=int(state.get("consecutiveFailures") or 0) + 1, lastErrorAt=now,
                 lastErrorProvider=provider, lastErrorMessage=scrub(message), lastErrorKind=kind,
                 lastErrorAction=action, lastErrorUrl=url)
    _write(store, state)
    return state


def record_success(store) -> None:
    state = read(store)
    if state and state.get("consecutiveFailures"):
        state.update(consecutiveFailures=0, failingSinceAt=None, lastSuccessAt=_now())
        _write(store, state)
    elif state is None:
        _write(store, {"consecutiveFailures": 0, "lastSuccessAt": _now()})
    else:
        state["lastSuccessAt"] = _now()
        _write(store, state)


def is_unhealthy(state: dict | None) -> bool:
    return bool(state) and int(state.get("consecutiveFailures") or 0) >= UNHEALTHY_FAILURE_THRESHOLD


def is_quota_failure(state: dict) -> bool:
    return state.get("lastErrorKind") in ("quota_exhausted", "rate_limit")


# ---- quota breaker ------------------------------------------------------------------------------------
def quota_state(store, provider: str) -> dict | None:
    try:
        return (json.loads(store.get_kv(_QUOTA) or "{}") or {}).get(provider)
    except ValueError:
        return None


def record_quota_exhausted(store, provider: str, message: str, window: str | None = None) -> None:
    try:
        all_ = json.loads(store.get_kv(_QUOTA) or "{}") or {}
    except ValueError:
        all_ = {}
    all_[provider] = {"armedAtMs": _now(), "message": scrub(message), "window": window, "probeInFlightSinceMs": None}
    store.set_kv(_QUOTA, json.dumps(all_))


def clear_quota(store, provider: str) -> None:
    try:
        all_ = json.loads(store.get_kv(_QUOTA) or "{}") or {}
    except ValueError:
        all_ = {}
    if all_.pop(provider, None) is not None:
        store.set_kv(_QUOTA, json.dumps(all_))


def quota_cooldown_active(store, provider: str) -> bool:
    st = quota_state(store, provider)
    return bool(st) and _now() - int(st.get("armedAtMs") or 0) < QUOTA_RECHECK_COOLDOWN_MS


def admit_quota_probe(store, provider: str) -> bool:
    """True when a request may be sent: no cooldown, or the window elapsed (one probe goes through)."""
    st = quota_state(store, provider)
    if not st:
        return True
    return _now() - int(st.get("armedAtMs") or 0) >= QUOTA_RECHECK_COOLDOWN_MS


# ---- rendering ----------------------------------------------------------------------------------------
def describe_duration(ms: int) -> str:
    mins = max(0, ms) // 60_000
    if mins < 1:
        return "less than a minute"
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    hours = mins // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"


def render_warning(state: dict, now_ms: int | None = None) -> str:
    now_ms = now_ms or _now()
    since = state.get("failingSinceAt") or state.get("lastErrorAt")
    since_text = (f"for {describe_duration(now_ms - since)} (since "
                  f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(since / 1000))})") if since else \
        "for an unknown amount of time"
    provider = state.get("lastErrorProvider") or "unknown provider"
    count = int(state.get("consecutiveFailures") or 0)
    latest = state.get("lastErrorMessage") or "unknown"
    extra = [f"What to do: {state['lastErrorAction']}"] if state.get("lastErrorAction") else []
    extra += [f"Link: {state['lastErrorUrl']}"] if state.get("lastErrorUrl") else []
    if is_quota_failure(state):
        return "\n".join([
            "⚠️ Heads up: Cairn can't save session memories right now.", "",
            f"The session recorder's model allowance on {provider} is used up {since_text}.", "",
            f"Latest error: {latest}", *extra, "",
            "Queued work is kept and will be recorded once the allowance resets or another model is configured",
            "(`[models] provider` in .cairn/config.toml).", "",
            "(Assistant: tell the user about this at the start of your first reply, quoting the error above.)"])
    return "\n".join([
        "⚠️ Heads up: Cairn can't save session memories right now.", "",
        f"The session recorder (model: {provider}) has failed {count} time{'s' if count != 1 else ''} in a row"
        f" {since_text}.", "",
        f"Latest error: {latest}", *extra, "",
        "Queued work is kept. To retry now run: cairn sessions worker --once", "If it keeps failing run: cairn doctor",
        "", "(Assistant: tell the user about this at the start of your first reply, quoting the error above.)"])


def render_quota_notice(store, provider: str) -> str | None:
    st = quota_state(store, provider)
    if not st or not quota_cooldown_active(store, provider):
        return None
    left = QUOTA_RECHECK_COOLDOWN_MS - (_now() - int(st.get("armedAtMs") or 0))
    return (f"⏸ Cairn's session recorder is paused: the model allowance on {provider} is exhausted "
            f"({st.get('message') or 'quota reached'}). Queued work is kept; the next attempt is in "
            f"{describe_duration(left)}.")


def warning_text(store, provider: str | None = None) -> str:
    """The health warning to append to injected context, or ''."""
    try:
        state = read(store)
    except Exception:  # a read-only or damaged store never blocks context
        return ""
    if is_unhealthy(state):
        return render_warning(state)
    if provider:
        notice = render_quota_notice(store, provider)
        if notice:
            return notice
    return ""
