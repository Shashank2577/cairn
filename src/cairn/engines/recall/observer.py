"""The observer: a model that watches a session's tool events and writes observations and summaries.

Each session keeps one running observer conversation (persisted in ``observer_conversations``): the
framing prompt for the current user request (init for the first prompt, continuation after) and the
exchanges so far. Every queued tool event becomes one observation prompt; the reply is parsed and
stored with the files the tool calls actually touched. A conversation that outgrows its budget is
retired and a fresh one starts, briefed with the session-start context (the observations already
written), so continuity rides on the memory itself.

The model is reached through ``cairn.router.Router`` (stateless), so the conversation is replayed on
every call: the framing prompt as the system prompt, prior exchanges as a cached prefix, and the new
event as the prompt.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import health, prompts
from .fallback import relative
from .modes import Mode
from .parser import (
    classify_error,
    classify_observer_output,
    is_auth_failure,
    is_context_overflow,
    is_quota_limited,
    is_transport_failure,
    parse_agent_xml,
    preview_output,
)
from .store import Store

log = logging.getLogger("cairn.recall")

TASK_OBSERVE = "recall_observe"
TASK_SUMMARIZE = "recall_summarize"
TASK_COMPRESS = "recall_compress"
MAX_CONSECUTIVE_RECYCLES = 2
OVERFLOW_EXHAUSTED_COOLDOWN_MS = 10 * 60_000
FIELD_OPTIMIZE_TIMEOUT_S = 30
SIMPLE_TOOLS = {"Read", "Glob", "Grep", "LS", "ListMcpResourcesTool"}
READ_TOOL_NAMES = {"Read"}
WRITE_TOOL_NAMES = {"Edit", "MultiEdit", "Write", "NotebookEdit", "write_file"}
PATCH_TOOL_NAMES = {"apply_patch"}


def _estimate(text: str) -> int:
    return max(0, len(text or "") // 4)


class ObserverLLM:
    """Replays an observer conversation through the stateless router."""

    def __init__(self, router):
        self.router = router

    @property
    def available(self) -> bool:
        return bool(self.router is not None and getattr(self.router, "available", False))

    @property
    def provider(self) -> str:
        return str(getattr(self.router, "provider", None) or "model")

    def model_name(self, task: str) -> str | None:
        try:
            return str(self.router.model(self.router.tier_for(task)))
        except Exception:  # noqa: BLE001 - the model label is informational
            return None

    def chat(self, system: str, exchanges: list[dict], prompt: str, *, task: str, tier: str | None = None,
             max_tokens: int = 4000) -> str:
        chat = getattr(self.router, "chat", None)
        if callable(chat):  # a router that takes messages natively
            messages = []
            for ex in exchanges:
                messages += [{"role": "user", "content": ex["user"]}, {"role": "assistant", "content": ex["assistant"]}]
            messages.append({"role": "user", "content": prompt})
            return chat(task, messages, system=system, max_tokens=max_tokens, tier=tier)
        cached = render_exchanges(exchanges)
        kwargs: dict[str, Any] = {"system": system, "max_tokens": max_tokens}
        if cached:
            kwargs["cached_context"] = cached
        if tier:
            kwargs["tier"] = tier
        try:
            return self.router.complete(task, prompt, **kwargs)
        except TypeError:  # a minimal router without cached_context/tier support
            return self.router.complete(task, (cached + "\n\n" + prompt) if cached else prompt, system=system,
                                        max_tokens=max_tokens)

    def compress(self, text: str, budget: int) -> str | None:
        """One bounded condensing call for an oversized tool payload (never retried)."""
        result: dict[str, Any] = {}

        def run() -> None:
            try:
                result["text"] = self.router.complete(TASK_COMPRESS, prompts.build_field_compression_prompt(text, budget),
                                                      max_tokens=max(256, budget // 3))
            except Exception as exc:  # noqa: BLE001 - compression falls back to truncation
                result["error"] = exc
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(FIELD_OPTIMIZE_TIMEOUT_S)
        return result.get("text") if not t.is_alive() else None


def render_exchanges(exchanges: list[dict]) -> str:
    if not exchanges:
        return ""
    parts = ["OBSERVER CONVERSATION SO FAR (already processed — do not re-record it; continue from here):"]
    for ex in exchanges:
        parts.append(f"<turn role=\"user\">\n{ex['user']}\n</turn>")
        parts.append(f"<turn role=\"assistant\">\n{ex['assistant']}\n</turn>")
    return "\n".join(parts)


REPLAY_USER_CHARS = 1_500  # of each replayed prompt; the observer's own answers are replayed whole


def replay_window(exchanges: list[dict], window: int, budget_chars: int) -> list[dict]:
    """The most recent exchanges that fit ``budget_chars``: what the observer recorded so far, with each
    earlier prompt cut to its opening (the raw tool events were already turned into those answers)."""
    recent = exchanges[-window:] if window > 0 else exchanges
    out: list[dict] = []
    used = 0
    for ex in reversed(recent):
        user = ex["user"] if len(ex["user"]) <= REPLAY_USER_CHARS else ex["user"][:REPLAY_USER_CHARS] + "\n[…]"
        size = len(user) + len(ex["assistant"])
        if out and used + size > budget_chars:
            break
        out.append({**ex, "user": user})
        used += size
    return out[::-1]


def field_chars(budget_chars: int, events: int) -> int:
    """Characters for each <parameters>/<outcome> field so a batch of ``events`` fits ``budget_chars``."""
    return max(1_200, min(prompts.OBS_PROMPT_FIELD_MAX_CHARS, budget_chars // max(1, 2 * events)))


def conversation_chars(conv: dict) -> int:
    return len(conv.get("system") or "") + sum(len(e["user"]) + len(e["assistant"]) for e in conv.get("exchanges", []))


# ---- oversized fields ---------------------------------------------------------------------------------------
def optimize_field(value: Any, llm: ObserverLLM | None, max_chars: int = prompts.OBS_PROMPT_FIELD_MAX_CHARS) -> Any:
    raw = json.dumps(value, indent=2, ensure_ascii=False, default=str) if value is not None else "null"
    if len(raw) <= max_chars or llm is None or not llm.available:
        return value
    budget = int(max_chars * prompts.FIELD_OPTIMIZE_TARGET_RATIO)
    condensed = (llm.compress(raw, budget) or "").strip()
    if not condensed or len(condensed) > max_chars:
        return value  # the head/tail truncation in the prompt still applies
    return f'<condensed original_size_chars="{len(raw)}" reason="oversize">\n{condensed}\n</condensed>'


def compact_edit_output(tool_input: Any, tool_output: Any, max_chars: int) -> Any:
    """For a verified Edit, drop the redundant full source and patch lines (observer view only)."""
    try:
        inp = json.loads(tool_input) if isinstance(tool_input, str) else tool_input
        out = json.loads(tool_output) if isinstance(tool_output, str) else tool_output
        if not isinstance(inp, dict) or not isinstance(out, dict) or out.get("userModified") is not False:
            return tool_output
        if not all(isinstance(inp.get(k), str) for k in ("file_path", "old_string", "new_string")) \
                or not inp["file_path"] or not inp["old_string"]:
            return tool_output
        if out.get("filePath") != inp["file_path"] or out.get("oldString") != inp["old_string"] \
                or out.get("newString") != inp["new_string"]:
            return tool_output
        allowed = {"filePath", "oldString", "newString", "originalFile", "structuredPatch", "userModified", "replaceAll"}
        if set(out) - allowed or not isinstance(out.get("structuredPatch"), list) or not out["structuredPatch"]:
            return tool_output
        if out.get("originalFile") is not None and not isinstance(out.get("originalFile"), str):
            return tool_output
        if out.get("replaceAll") is not None and out["replaceAll"] != inp.get("replace_all", False):
            return tool_output
        if len(json.dumps(tool_output if not isinstance(tool_output, str) else out, indent=2)) <= max_chars:
            return tool_output
        for h in out["structuredPatch"]:
            if not isinstance(h, dict) or set(h) - {"oldStart", "oldLines", "newStart", "newLines", "lines"}:
                return tool_output
            lines = h.get("lines")
            if not isinstance(lines, list) or not all(isinstance(ln, str) and (ln[:1] in (" ", "+", "-")
                                                                                 or ln == "\\ No newline at end of file")
                                                        for ln in lines):
                return tool_output
            before = "\n".join(ln[1:] for ln in lines if ln[:1] in (" ", "-"))
            after = "\n".join(ln[1:] for ln in lines if ln[:1] in (" ", "+"))
            replaced = before.replace(inp["old_string"], inp["new_string"]) if inp.get("replace_all") else \
                before.replace(inp["old_string"], inp["new_string"], 1)
            if inp["old_string"] not in before or replaced != after:
                return tool_output
        view = {**out, "originalFile": None if out.get("originalFile") is None else
                f"[omitted {len(out['originalFile'])} source characters]",
                "structuredPatch": [{**{k: v for k, v in h.items() if k != "lines"}, "omitted_lines": len(h["lines"])}
                                    for h in out["structuredPatch"]],
                "observer_note": "Redundant full-source and patch lines omitted; exact native oldString/newString and"
                                 " hunk locations retained. Raw tool payload unchanged."}
        return view if len(json.dumps(view, indent=2)) <= max_chars else tool_output
    except (TypeError, ValueError, KeyError, AttributeError):
        return tool_output


# ---- file evidence ----------------------------------------------------------------------------------------------
def _obj(value: Any) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip()[:1] in ("{", "["):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _patch_files(patch: str) -> list[str]:
    files = []
    for line in patch.splitlines():
        t = line.strip()
        for marker in ("*** Update File: ", "*** Add File: ", "*** Delete File: ", "*** Move to: "):
            if t.startswith(marker):
                files.append(t[len(marker):].strip())
        if t.startswith("+++ "):
            fp = t[4:].removeprefix("b/").strip()
            if fp and fp != "/dev/null":
                files.append(fp)
    return list(dict.fromkeys(files))


def file_evidence(messages: list[dict]) -> tuple[list[str], list[str]]:
    """Files the claimed tool calls actually read and modified (authoritative over the model's lists)."""
    read: list[str] = []
    modified: list[str] = []

    def push(lst: list[str], v: Any) -> None:
        if isinstance(v, str) and v.strip() and v.strip() not in lst:
            lst.append(v.strip())
    for m in messages:
        if m.get("message_type") != "observation":
            continue
        tool = str(m.get("tool_name") or "")
        inp = _obj(m.get("tool_input"))
        if tool in READ_TOOL_NAMES and inp:
            for f in ("file_path", "filePath", "notebook_path", "notebookPath", "filePaths"):
                vals = inp.get(f)
                for v in vals if isinstance(vals, list) else [vals]:
                    push(read, v)
        if tool in WRITE_TOOL_NAMES and inp:
            for f in ("file_path", "filePath", "notebook_path", "notebookPath", "path", "filePaths"):
                vals = inp.get(f)
                for v in vals if isinstance(vals, list) else [vals]:
                    push(modified, v)
            for edit in inp.get("edits") or []:
                if isinstance(edit, dict):
                    for f in ("file_path", "filePath", "notebook_path", "notebookPath", "path"):
                        push(modified, edit.get(f))
        if tool in PATCH_TOOL_NAMES:
            patches = []
            if inp:
                if isinstance(inp.get("patch"), str):
                    patches.append(inp["patch"])
                for e in inp.get("edits") or []:
                    if isinstance(e, str):
                        patches.append(e)
                    elif isinstance(e, dict) and isinstance(e.get("patch"), str):
                        patches.append(e["patch"])
            elif isinstance(m.get("tool_input"), str):
                patches.append(m["tool_input"])
            for p in patches:
                for f in _patch_files(p):
                    push(modified, f)
    return read, modified


class Outcome:
    STORED = "stored"
    DROPPED = "dropped"          # the model declined (idle / prose): batch confirmed
    PRESERVED = "preserved"      # a refusal or failure: batch returned to the queue
    RECYCLED = "recycled"        # the conversation overflowed and was retired
    UNAVAILABLE = "unavailable"  # no usable model: caller derives records instead


class SessionObserver:
    """Processes one session's queued messages with the model (one worker thread at a time)."""

    def __init__(self, root: Path, store: Store, llm: ObserverLLM, mode: Mode, settings: dict, *,
                 prior_context: Callable[[], str] | None = None, on_stored: Callable[[dict], None] | None = None):
        self.root = Path(root)
        self.store = store
        self.llm = llm
        self.mode = mode
        self.settings = settings
        self.prior_context = prior_context or (lambda: "")
        self.on_stored = on_stored

    # ---- conversation state ------------------------------------------------------------------
    def load(self, sid: int) -> dict:
        c = self.store.get_conversation(sid)
        history = c["history"] if isinstance(c["history"], dict) else {}
        return {"system": history.get("system", ""), "prompt_number": history.get("prompt_number"),
                "exchanges": history.get("exchanges", []), "consecutive_overflows": c["consecutive_overflows"],
                "paused_until_epoch": c["paused_until_epoch"]}

    def save(self, sid: int, conv: dict) -> None:
        window = int(self.settings.get("observer_replay_exchanges") or 0)
        if window > 0:
            conv["exchanges"] = conv["exchanges"][-window:]
        self.store.save_conversation(sid, {"system": conv["system"], "prompt_number": conv["prompt_number"],
                                           "exchanges": conv["exchanges"]},
                                     consecutive_overflows=conv.get("consecutive_overflows", 0),
                                     paused_until_epoch=conv.get("paused_until_epoch"))

    def frame(self, session: dict, conv: dict, prompt_number: int | None, fresh: bool) -> None:
        """(Re)write the framing prompt when a new user request starts or a fresh generation begins."""
        number = prompt_number or 1
        if not fresh and conv["system"] and conv["prompt_number"] == number:
            return
        csid = session["content_session_id"]
        user_prompt = self.store.get_user_prompt(csid, number, session["id"]) \
            or self.store.get_latest_prompt_text(csid, session["id"]) or session.get("user_prompt") or ""
        user_prompt = user_prompt.removeprefix("/")
        prior = self.prior_context() if (fresh or not conv["system"]) else ""
        if number <= 1:
            conv["system"] = prompts.build_init_prompt(session["project"], csid, user_prompt, self.mode, prior)
        else:
            conv["system"] = prompts.build_continuation_prompt(user_prompt, number, csid, self.mode, prior)
        conv["prompt_number"] = number

    def recycle(self, sid: int, conv: dict, claimed: list[int], detail: str) -> str:
        attempts = int(conv.get("consecutive_overflows") or 0) + 1
        conv["consecutive_overflows"] = attempts
        self.store.reset_to_pending(claimed)
        conv["exchanges"] = []
        conv["system"] = ""
        if attempts > MAX_CONSECUTIVE_RECYCLES:
            conv["paused_until_epoch"] = int(time.time() * 1000) + OVERFLOW_EXHAUSTED_COOLDOWN_MS
            log.error("observer conversation still does not fit after %s recycles; pausing session %s (%s)",
                      MAX_CONSECUTIVE_RECYCLES, sid, detail)
        else:
            log.info("retiring observer conversation for session %s: %s", sid, detail)
        self.save(sid, conv)
        return Outcome.RECYCLED

    # ---- one batch ---------------------------------------------------------------------------
    def process(self, session: dict, messages: list[dict]) -> str:
        sid = session["id"]
        claimed = [m["id"] for m in messages]
        conv = self.load(sid)
        if conv["paused_until_epoch"] and conv["paused_until_epoch"] > time.time() * 1000:
            self.store.reset_to_pending(claimed)
            return Outcome.PRESERVED
        if conv["paused_until_epoch"]:
            conv["paused_until_epoch"] = None
            conv["consecutive_overflows"] = 0
        provider = self.llm.provider
        if not health.admit_quota_probe(self.store, provider):
            self.store.reset_to_pending(claimed)
            return Outcome.PRESERVED
        max_chars = int(self.settings.get("observer_max_conversation_chars") or 400_000)
        if conv["exchanges"] and conversation_chars(conv) >= max_chars:
            return self.recycle(sid, conv, claimed, f"conversation reached {conversation_chars(conv)} chars")
        is_summary = messages[0]["message_type"] == "summarize"
        number = messages[-1].get("prompt_number")
        self.frame(session, conv, number, fresh=not conv["exchanges"] and not conv["system"])
        if is_summary:
            m = messages[0]
            prompt = prompts.build_summary_prompt(m.get("last_assistant_message") or "", self.mode)
            task, tier, max_tokens = TASK_SUMMARIZE, None, 2000
        else:
            blocks = []
            per_field = field_chars(int(self.settings.get("observer_prompt_chars") or 48_000), len(messages))
            # condensing oversized fields costs a model call each: only for ordinary batches, not a catch-up
            condense = self.llm if len(messages) <= int(self.settings.get("observe_batch") or 1) else None
            for m in messages:
                ti = optimize_field(prompts.strip_image_payloads_from_field(m.get("tool_input")), condense)
                raw_out = prompts.strip_image_payloads_from_field(m.get("tool_response"))
                if m.get("tool_name") == "Edit":
                    raw_out = compact_edit_output(ti, raw_out, per_field)
                to = optimize_field(raw_out, condense)
                blocks.append(prompts.build_observation_prompt(str(m.get("tool_name")), ti, to,
                                                               int(m.get("created_at_epoch") or time.time() * 1000),
                                                               m.get("cwd"), per_field))
            prompt = "\n\n".join(blocks)
            task = TASK_OBSERVE
            tier = "fast" if self.settings.get("tier_routing") and all(
                m.get("tool_name") in SIMPLE_TOOLS for m in messages) else None
            max_tokens = 4000
        replay = replay_window(conv["exchanges"], int(self.settings.get("observer_replay_exchanges") or 0),
                               int(self.settings.get("observer_replay_chars") or 24_000))
        try:
            text = self.llm.chat(conv["system"], replay, prompt, task=task, tier=tier, max_tokens=max_tokens)
        except Exception as exc:  # noqa: BLE001 - every model failure is classified, never raised
            return self._failed(session, conv, messages, exc)
        discovery = _estimate(conv["system"]) + sum(_estimate(e["user"]) + _estimate(e["assistant"])
                                                    for e in replay) + _estimate(prompt) + _estimate(text)
        return self._respond(session, conv, messages, prompt, text or "", discovery)

    def _failed(self, session: dict, conv: dict, messages: list[dict], exc: BaseException) -> str:
        kind = classify_error(exc)
        msg = str(exc)
        claimed = [m["id"] for m in messages]
        if kind == "setup_required":
            self.store.reset_to_pending(claimed)
            return Outcome.UNAVAILABLE
        if kind == "unrecoverable" and (is_context_overflow(msg) or "too long" in msg.lower()):
            return self.recycle(session["id"], conv, claimed, f"refused as too long: {preview_output(msg)}")
        health.record_failure(self.store, self.llm.provider, str(exc), kind=kind,
                              action="Run /login in Claude Code" if kind == "auth_invalid" else None)
        if kind in ("quota_exhausted", "rate_limit"):
            health.record_quota_exhausted(self.store, self.llm.provider, str(exc))
        self.store.reset_to_pending(claimed, count_retry=True)
        log.warning("observer call failed (%s) for session %s: %s", kind, session["id"], preview_output(str(exc)))
        return Outcome.PRESERVED

    def _respond(self, session: dict, conv: dict, messages: list[dict], prompt: str, text: str,
                 discovery: int) -> str:
        sid = session["id"]
        claimed = [m["id"] for m in messages]
        provider = self.llm.provider
        rejection = bool(text) and (is_context_overflow(text) or is_quota_limited(text) or is_auth_failure(text)
                                    or is_transport_failure(text))
        parsed = parse_agent_xml(text, self.mode)
        if not parsed["valid"]:
            if is_context_overflow(text):
                return self.recycle(sid, conv, claimed, f"refused as too long: {preview_output(text)}")
            if is_quota_limited(text):
                health.record_quota_exhausted(self.store, provider, text)
                health.record_failure(self.store, provider, text, kind="quota_exhausted")
                self.store.reset_to_pending(claimed)
                return Outcome.PRESERVED
            if is_auth_failure(text):
                health.record_failure(self.store, provider, text, kind="auth_invalid", action="Run /login")
                self.store.reset_to_pending(claimed, count_retry=True)
                return Outcome.PRESERVED
            if is_transport_failure(text):
                health.record_failure(self.store, provider, text, kind="transient")
                self.store.reset_to_pending(claimed, count_retry=True)
                return Outcome.PRESERVED
            # The model read the batch and declined: confirm it (re-queueing a skip only loops).
            log.info("observer returned non-XML %s for session %s: %s", classify_observer_output(text), sid,
                     preview_output(text))
            if not rejection:
                conv["exchanges"].append({"user": prompt, "assistant": text})
            conv["consecutive_overflows"] = 0
            self.save(sid, conv)
            self.store.confirm(claimed)
            return Outcome.DROPPED
        conv["exchanges"].append({"user": prompt, "assistant": text})
        conv["consecutive_overflows"] = 0
        read, modified = file_evidence(messages)
        root = str(self.root)
        rel = lambda p: relative(p, root, messages[0].get("cwd") or root)
        read, modified = [rel(p) for p in read], [rel(p) for p in modified]
        observations = []
        for o in parsed["observations"]:
            model_read = [rel(p) for p in o.get("files_read") or []]
            observations.append({**o, "files_read": list(dict.fromkeys(read + model_read)), "files_modified": modified,
                                 "agent_type": messages[-1].get("agent_type"), "agent_id": messages[-1].get("agent_id")})
        summary = parsed.get("summary")
        summary_row = None
        if summary and not summary.get("skipped"):
            s_read = list(dict.fromkeys(read + [f for o in observations for f in o["files_read"]]))
            s_edit = list(dict.fromkeys(modified + [f for o in observations for f in o["files_modified"]]))
            if messages[0]["message_type"] == "summarize":
                turn = self.store.db.execute(
                    "SELECT files_read, files_modified FROM observations WHERE memory_session_id=? AND prompt_number IS ?",
                    (session["memory_session_id"], messages[0].get("prompt_number"))).fetchall()
                for r in turn:
                    s_read += [f for f in json.loads(r["files_read"] or "[]") if f not in s_read]
                    s_edit += [f for f in json.loads(r["files_modified"] or "[]") if f not in s_edit]
            summary_row = {"request": summary.get("request") or "", "investigated": summary.get("investigated") or "",
                           "learned": summary.get("learned") or "", "completed": summary.get("completed") or "",
                           "next_steps": summary.get("next_steps") or "", "notes": summary.get("notes"),
                           "files_read": s_read, "files_edited": s_edit}
        memory_id = self.store.ensure_memory_session_id(sid)
        earliest = min(int(m.get("created_at_epoch") or 0) for m in messages) or None
        model_name = self.llm.model_name(TASK_SUMMARIZE if messages[0]["message_type"] == "summarize" else TASK_OBSERVE)
        res = self.store.store_observations(memory_id, session["project"], observations, summary_row,
                                            messages[-1].get("prompt_number"), discovery, earliest, model_name)
        tool_ids = [m["tool_use_id"] for m in messages if m.get("tool_use_id")]
        if tool_ids and res["observation_ids"]:
            try:
                self.store.link_tool_uses_to_observation(session["content_session_id"], tool_ids,
                                                         res["observation_ids"][0], memory_id)
            except Exception as exc:  # noqa: BLE001 - a failed back-link costs nothing else
                log.warning("tool_uses link failed: %s", exc)
        health.record_success(self.store)
        health.clear_quota(self.store, provider)
        self.save(sid, conv)
        self.store.confirm(claimed)
        if self.on_stored:
            self.on_stored({"session": session, "observation_ids": list(dict.fromkeys(res["observation_ids"])),
                            "summary_id": res["summary_id"], "observations": observations,
                            "project": session["project"]})
        return Outcome.STORED
