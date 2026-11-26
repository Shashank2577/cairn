"""Deterministic records for when no model is available (or a queued event kept failing).

One observation per prompt turn, derived from the tool events: files read and changed, commands run,
searches made; one summary per Stop, derived from the prompt, the turn's files and the agent's final
answer. They are marked ``{"derived": true}`` in ``metadata`` and are merged as later events of the
same turn arrive, so capture is never empty and never duplicated.

Four kinds of event get an observation of their own (``metadata.event``), each found by fixed rules and
only on explicit signals, so that a search such as "what did we reject" has an answer without a model:
``rejected`` (the user denied a tool call), ``error-fix`` (a call failed, then the same call worked after
the agent changed something), ``retry-loop`` (the same call attempted three or more times in one turn,
at least two of them failing) and ``correction`` (the user told the agent it was wrong). Standard
library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from .modes import Mode, load_mode
from .tags import redact

READ_TOOLS = {"Read": "file_path", "NotebookRead": "notebook_path", "view": "path", "read_file": "path"}
EDIT_TOOLS = {"Edit": "file_path", "MultiEdit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path",
              "write_file": "filePath", "str_replace_editor": "path", "create_file": "path"}
SHELL_TOOLS = {"Bash", "exec_command", "shell", "run_shell_command", "local_shell"}
SEARCH_TOOLS = {"Grep", "Glob", "search_file_content", "glob"}
MAX_LIST = 30
MAX_FACTS = 12
TITLE_MAX = 120


def _pick(mode: Mode, preferred: list[str], ids: list[str]) -> str:
    return next((p for p in preferred if p in ids), ids[0] if ids else preferred[0])


def _concepts(mode: Mode, preferred: list[str]) -> list[str]:
    ids = mode.concept_ids()
    picked = [p for p in preferred if p in ids]
    return picked or (ids[:1] if ids else preferred[:1])


def _load(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def relative(path: str, root: str | None, cwd: str | None) -> str:
    """Repository-relative form of a path when it lies inside the repository."""
    if not path:
        return path
    base = cwd or root or ""
    p = os.path.realpath(path if os.path.isabs(path) else os.path.join(base, path))
    for anchor in (root, cwd):
        if not anchor:
            continue
        a = os.path.realpath(anchor)
        if p.startswith(a + os.sep):
            return os.path.relpath(p, a).replace(os.sep, "/")
    return path


def turn_evidence(messages: list[dict], root: str | None = None) -> dict:
    """What a list of queued tool events did: files read/modified, commands, searches, other tools."""
    ev = {"read": [], "modified": [], "commands": [], "searches": [], "tools": {}}
    for m in messages:
        if m.get("message_type", "observation") != "observation":
            continue
        tool = str(m.get("tool_name") or "")
        if not tool:
            continue
        ev["tools"][tool] = ev["tools"].get(tool, 0) + 1
        inp = _load(m.get("tool_input"))
        inp = inp if isinstance(inp, dict) else {}
        cwd = m.get("cwd") or root
        if tool in READ_TOOLS:
            fp = inp.get(READ_TOOLS[tool]) or inp.get("file_path") or inp.get("path")
            if isinstance(fp, str) and fp:
                rel = relative(fp, root, cwd)
                if rel not in ev["read"]:
                    ev["read"].append(rel)
            for extra in inp.get("filePaths") or []:
                if isinstance(extra, str) and relative(extra, root, cwd) not in ev["read"]:
                    ev["read"].append(relative(extra, root, cwd))
        elif tool in EDIT_TOOLS or tool == "apply_patch":
            paths = []
            fp = inp.get(EDIT_TOOLS.get(tool, "file_path")) or inp.get("file_path") or inp.get("path")
            if isinstance(fp, str) and fp:
                paths.append(fp)
            if tool == "apply_patch":
                patch = inp.get("patch") if isinstance(inp.get("patch"), str) else (
                    inp.get("input") if isinstance(inp.get("input"), str) else "")
                for line in patch.splitlines():
                    for marker in ("*** Update File: ", "*** Add File: ", "*** Delete File: ", "*** Move to: "):
                        if line.strip().startswith(marker):
                            paths.append(line.strip()[len(marker):].strip())
            for p in paths:
                rel = relative(p, root, cwd)
                if rel not in ev["modified"]:
                    ev["modified"].append(rel)
        elif tool in SHELL_TOOLS:
            cmd = inp.get("command") or inp.get("cmd") or inp.get("command_line")
            if isinstance(cmd, list):
                cmd = " ".join(str(c) for c in cmd)
            cmd = redact(" ".join(str(cmd or "").split()))[:500]
            if cmd and cmd not in ev["commands"]:
                ev["commands"].append(cmd)
        elif tool in SEARCH_TOOLS:
            pat = inp.get("pattern") or inp.get("query")
            if isinstance(pat, str) and pat:
                where = inp.get("path")
                ev["searches"].append(redact(pat)[:200] + (f" in {relative(where, root, cwd)}" if isinstance(where, str)
                                                           and where else ""))
    for k in ("read", "modified"):
        ev[k] = ev[k][:MAX_LIST]
    ev["read"] = [f for f in ev["read"] if f not in ev["modified"]] + [f for f in ev["read"] if f in ev["modified"]]
    return ev


def _title(prompt: str, ev: dict) -> str:
    text = " ".join((prompt or "").split())
    if text:
        return text if len(text) <= TITLE_MAX else text[: TITLE_MAX - 3] + "..."
    if ev["modified"]:
        return f"Changed {', '.join(ev['modified'][:3])}"[:TITLE_MAX]
    if ev["commands"]:
        return f"Ran {ev['commands'][0]}"[:TITLE_MAX]
    if ev["read"]:
        return f"Read {', '.join(ev['read'][:3])}"[:TITLE_MAX]
    return "Agent work before the first prompt"


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def observation_from_evidence(prompt: str, ev: dict, mode: Mode | None = None) -> dict | None:
    """A derived observation for one turn, or None when the turn did nothing worth recording."""
    mode = mode or load_mode()
    if not (ev["read"] or ev["modified"] or ev["commands"] or ev["searches"] or ev["tools"]):
        return None
    types = mode.type_ids()
    if ev["modified"]:
        typ = _pick(mode, ["change", "feature"], types)
        concepts = _concepts(mode, ["what-changed"])
    else:
        typ = _pick(mode, ["discovery"], types)
        concepts = _concepts(mode, ["how-it-works"])
    facts = [f"modified `{f}`" for f in ev["modified"][:5]] + [f"ran `{c[:140]}`" for c in ev["commands"][:5]] + \
        [f"read `{f}`" for f in ev["read"][:4]] + [f"searched for `{s[:100]}`" for s in ev["searches"][:3]]
    other = {t: n for t, n in ev["tools"].items()
             if t not in READ_TOOLS and t not in EDIT_TOOLS and t not in SHELL_TOOLS and t not in SEARCH_TOOLS
             and t != "apply_patch"}
    facts += [f"used {t} ({n}x)" for t, n in list(other.items())[:3]]
    parts = []
    if ev["modified"]:
        parts.append(f"changed {', '.join(ev['modified'][:8])}")
    if ev["read"]:
        parts.append(f"read {', '.join(ev['read'][:8])}")
    if ev["commands"]:
        parts.append("ran " + "; ".join(f"`{c[:120]}`" for c in ev["commands"][:5]))
    if ev["searches"]:
        parts.append("searched for " + ", ".join(ev["searches"][:4]))
    ask = " ".join((prompt or "").split())
    narrative = (f"While working on \"{ask[:300]}\", the agent " if ask else "The agent ") + "; ".join(parts) + "."
    subtitle = ", ".join(x for x in (
        f"changed {_count(len(ev['modified']), 'file')}" if ev["modified"] else "",
        f"read {_count(len(ev['read']), 'file')}" if ev["read"] else "",
        f"ran {_count(len(ev['commands']), 'command')}" if ev["commands"] else "",
        f"{_count(len(ev['searches']), 'search')}" if ev["searches"] else "") if x) or "tool activity"
    return {"type": typ, "title": _title(prompt, ev), "subtitle": subtitle[:200], "facts": facts[:MAX_FACTS],
            "narrative": narrative, "concepts": concepts, "files_read": ev["read"], "files_modified": ev["modified"],
            "metadata": json.dumps({"derived": True, "tools": ev["tools"]})}


def merge_evidence(old: dict, new: dict) -> dict:
    out = {"read": list(old.get("read", [])), "modified": list(old.get("modified", [])),
           "commands": list(old.get("commands", [])), "searches": list(old.get("searches", [])),
           "tools": dict(old.get("tools", {}))}
    for k in ("read", "modified", "commands", "searches"):
        for v in new.get(k, []):
            if v not in out[k]:
                out[k].append(v)
    for t, n in new.get("tools", {}).items():
        out["tools"][t] = out["tools"].get(t, 0) + n
    return out


def evidence_from_observation(row: dict) -> dict:
    """Recover the evidence of a stored derived observation (to merge later events into it)."""
    from .store import parse_list
    meta = {}
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except ValueError:
        pass
    facts = parse_list(row.get("facts"))
    return {"read": parse_list(row.get("files_read")), "modified": parse_list(row.get("files_modified")),
            "commands": [f[5:-1] for f in facts if f.startswith("ran `") and f.endswith("`")],
            "searches": [f[15:-1] for f in facts if f.startswith("searched for `") and f.endswith("`")],
            "tools": meta.get("tools") or {}}


def summary_from_turn(prompt: str, ev: dict, last_assistant_message: str | None) -> dict:
    answer = " ".join((last_assistant_message or "").split())
    investigated = []
    if ev["read"]:
        investigated.append(f"Read {', '.join(ev['read'][:10])}")
    if ev["searches"]:
        investigated.append(f"searched for {', '.join(ev['searches'][:5])}")
    completed = []
    if ev["modified"]:
        completed.append(f"Changed {', '.join(ev['modified'][:10])}")
    if ev["commands"]:
        completed.append("ran " + "; ".join(ev["commands"][:5]))
    first_para = (last_assistant_message or "").strip().split("\n\n")[0].strip()
    return {"request": " ".join((prompt or "").split())[:300] or (answer[:120] if answer else "Session work"),
            "investigated": "; ".join(investigated),
            "learned": "",
            "completed": "; ".join(completed) or first_para[:600],
            "next_steps": "",
            "notes": answer[:1000] or None,
            "files_read": ev["read"], "files_edited": ev["modified"]}


def legacy_turn_observation(turn: dict) -> dict | None:
    """Observation for one turn of Cairn's earlier event log (see schema v3)."""
    ev = {"read": turn.get("read", []), "modified": turn.get("modified", []), "commands": turn.get("commands", []),
          "searches": turn.get("searches", []), "tools": {}}
    obs = observation_from_evidence(turn.get("prompt", ""), ev)
    if obs is None and turn.get("prompt"):
        mode = load_mode()
        obs = {"type": _pick(mode, ["discovery"], mode.type_ids()), "title": _title(turn["prompt"], ev),
               "subtitle": "conversation", "facts": [], "narrative": f"The user asked: {turn['prompt'][:300]}",
               "concepts": _concepts(mode, ["how-it-works"]), "files_read": [], "files_modified": []}
    return obs


# ---- richer events (rejected approaches, error-then-fix, retry loops, user corrections) --------------------
EVENT_KINDS = ("rejected", "error-fix", "retry-loop", "correction")
RETRY_MIN_CALLS = 3
RETRY_MIN_FAILURES = 2
_EVENT_LABEL = {"rejected": "Rejected by the user", "error-fix": "Fixed after an error", "retry-loop": "Retry loop",
                "correction": "User correction"}
_EVENT_SUBTITLE = {"rejected": "rejected approach", "error-fix": "error then fix", "retry-loop": "retry loop",
                   "correction": "user correction"}

# A tool call the person refused. Specific on purpose: the shell's own "Permission denied" is an error, not a refusal.
_DENIED = re.compile(r"(?:user|you) (?:doesn't|does not|didn't|did not) want to proceed|tool use was rejected"
                     r"|(?:rejected|denied|declined) by (?:the )?user|user (?:rejected|denied|declined)"
                     r"|permission to use \S+ has been denied|(?:blocked|denied) by (?:a |the )?(?:hook|permission)",
                     re.I)
_EXIT_LINE = re.compile(r"^\s*(?:process )?exit(?:ed)? code:?\s*(-?\d+)", re.I)
_ERROR_LINE = re.compile(r"^\s*(?:<tool_use_error>|error:|traceback \(most recent call last\))", re.I)
_EXIT_KEYS = ("exit_code", "exitCode", "returncode", "return_code")
_FLAG = re.compile(r"^[-+]")

# Opens a prompt that tells the agent it was wrong. Anchored to the start of the message (a "no" or "don't"
# in the middle of a request is usually part of the request), minus the polite forms that are not corrections.
_CORRECTION_START = re.compile(r"^\W*(?:no\b|nope\b|wrong\b|stop\b|don't\b|do not\b|dont\b|never\b|that's (?:not|wrong)"
                               r"|that is (?:not|wrong)|that isn't|not (?:that|what|like)|revert\b|undo\b"
                               r"|why did you|you (?:shouldn't|should not|broke|forgot|missed|misunderstood))", re.I)
_CORRECTION_ANYWHERE = re.compile(r"\bnot what i (?:asked|wanted|meant|said)\b|\bthat'?s wrong\b|\bi (?:told|asked) you (?:to|not)\b"
                                  r"|\byou (?:broke|deleted|removed|changed) (?:the |my |it)\b|\bwrong (?:file|approach|function)\b",
                                  re.I)
_NOT_A_CORRECTION = re.compile(r"^\W*(?:no (?:problem|worries|rush|need|hurry|thanks)|nope,? (?:that'?s|all) (?:fine|good|ok)"
                               r"|don'?t forget|do not forget|don'?t hesitate|don'?t worry|never mind|stop by|"
                               r"no,? (?:that'?s|it'?s) (?:fine|good|great|perfect|ok|okay)|stop (?:and|to) (?:think|explain))",
                               re.I)
CORRECTION_MAX_CHARS = 600


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


_BARE_EXIT = re.compile(r"^\s*(?:process )?exit(?:ed)? code:?\s*-?\d+\s*$", re.I)


def _first_line(text: str, width: int = 160) -> str:
    """The first line worth reading: not blank, and not a bare ``Exit code N`` (the next line says what failed)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    line = next((ln for ln in lines if not _BARE_EXIT.match(ln)), lines[0] if lines else "")
    return redact(" ".join(line.split()))[:width]


def tool_denied(response: Any) -> bool:
    """Whether the person refused this tool call (an explicit refusal, never an ordinary error)."""
    resp = _load(response)
    if isinstance(resp, dict):
        hs = resp.get("hookSpecificOutput") if isinstance(resp.get("hookSpecificOutput"), dict) else resp
        if hs.get("permissionDecision") == "deny" or resp.get("denied") is True:
            return True
        probe = " ".join(_text_of(resp.get(k)) for k in ("error", "message", "content", "reason") if resp.get(k))
        return bool(_DENIED.search(probe))
    return isinstance(resp, str) and bool(_DENIED.search(resp[:600]))


def tool_error(response: Any) -> str | None:
    """The first line of the error when this tool call failed, else None. Only explicit signals count: an error
    flag, an ``error`` field, a non-zero exit code, or a result that opens with ``Error:``/``Exit code N``. The
    word "error" inside ordinary output (a grep for it, a log line, a passing test's name) is not a failure."""
    resp = _load(response)
    if isinstance(resp, dict):
        meta = resp.get("metadata") if isinstance(resp.get("metadata"), dict) else {}
        for src in (resp, meta):
            for key in _EXIT_KEYS:
                code = src.get(key)
                if isinstance(code, int) and not isinstance(code, bool) and code != 0:
                    return _first_line(_text_of(resp.get("stderr") or resp.get("output") or resp.get("stdout")))\
                        or f"exit code {code}"
        flagged = resp.get("is_error") is True or resp.get("isError") is True
        err = resp.get("error")
        if err not in (None, "", False, [], {}) or flagged:
            return _first_line(_text_of(err or resp.get("content") or resp.get("stderr") or resp.get("message"))) \
                or "error"
        return None
    if isinstance(resp, str):
        head = resp[:400]
        m = _EXIT_LINE.match(head)
        if m and int(m.group(1)) != 0:
            return _first_line(resp[m.end():]) or f"exit code {m.group(1)}"
        if _ERROR_LINE.match(head):
            return _first_line(head)
    return None


def call_target(tool: str, inp: dict, root: str | None, cwd: str | None) -> str:
    """What a call acts on, short enough for a title: the file, the command or the search pattern."""
    if tool in READ_TOOLS or tool in EDIT_TOOLS:
        fp = inp.get(READ_TOOLS.get(tool) or EDIT_TOOLS.get(tool, "file_path")) or inp.get("file_path") or inp.get("path")
        return relative(fp, root, cwd) if isinstance(fp, str) and fp else ""
    if tool in SHELL_TOOLS:
        cmd = inp.get("command") or inp.get("cmd") or inp.get("command_line")
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        return redact(" ".join(str(cmd or "").split()))[:200]
    if tool in SEARCH_TOOLS:
        pat = inp.get("pattern") or inp.get("query")
        return redact(str(pat))[:120] if pat else ""
    for key in ("url", "path", "query", "name"):
        if isinstance(inp.get(key), str) and inp[key]:
            return redact(inp[key])[:160]
    return ""


def call_signature(tool: str, target: str) -> str:
    """Two attempts at the same thing share a signature: the same file, the same search, or the same program and
    subcommand (``pytest tests/a.py`` and ``pytest tests/a.py -x`` match; ``pytest tests/b.py`` does not)."""
    if tool in SHELL_TOOLS:
        words = [w for w in target.split() if not _FLAG.match(w) and "=" not in w.split("/")[0]][:3]
        return f"{tool}:{' '.join(words)}"
    return f"{tool}:{target}"


def _digest(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()[:16]


def _classify(calls: list[dict], root: str | None) -> list[dict]:
    out = []
    seen: dict[str, int] = {}
    for c in calls:
        if c.get("message_type", "observation") != "observation":
            continue
        tool = str(c.get("tool_name") or "")
        if not tool or tool.startswith("mcp__cairn__"):
            continue
        inp = _load(c.get("tool_input"))
        inp = inp if isinstance(inp, dict) else {}
        target = call_target(tool, inp, root, c.get("cwd") or root)
        sig = call_signature(tool, target)
        resp = c.get("tool_response")
        denied = tool_denied(resp)
        err = None if denied else tool_error(resp)
        ordinal = seen[sig] = seen.get(sig, 0) + 1
        out.append({"tool": tool, "target": target, "sig": sig, "denied": denied, "error": err,
                    "edit": tool in EDIT_TOOLS or tool == "apply_patch", "ok": not denied and err is None,
                    "id": str(c.get("tool_use_id") or "") or f"{sig}#{ordinal}"})
    return out


def _event(kind: str, key: str, title: str, narrative: str, facts: list[str], concepts: list[str], *,
           files_read: list[str] | None = None, files_modified: list[str] | None = None) -> dict:
    return {"event": kind, "key": _digest(kind, key), "title": title[:TITLE_MAX], "narrative": narrative,
            "facts": facts[:MAX_FACTS], "concepts": concepts, "files_read": files_read or [],
            "files_modified": files_modified or []}


def _label(c: dict) -> str:
    return f"{c['tool']} `{c['target'][:100]}`" if c["target"] else c["tool"]


def turn_events(calls: list[dict], root: str | None = None) -> list[dict]:
    """The rejected approaches, error-then-fix pairs and retry loops in one turn's tool calls (in order)."""
    cs = _classify(calls, root)
    events: list[dict] = []
    for c in cs:
        if c["denied"]:
            files = [c["target"]] if c["target"] and (c["tool"] in READ_TOOLS or c["edit"]) else []
            events.append(_event(
                "rejected", c["id"], f"{_EVENT_LABEL['rejected']}: {_label(c)}",
                f"The user denied this tool call: {_label(c)}. It was a rejected approach; do not repeat it as it was.",
                [f"denied {_label(c)}"], ["gotcha", "trade-off"], files_read=files))
    paired: set[int] = set()
    for i, bad in enumerate(cs):
        if bad["error"] is None:
            continue
        fixer = next((j for j in range(i + 1, len(cs)) if cs[j]["sig"] == bad["sig"] and cs[j]["ok"]), None)
        if fixer is None:
            continue
        between = cs[i + 1:fixer]
        edited = [x["target"] for x in between if x["edit"] and x["ok"] and x["target"]]
        changed_cmd = cs[fixer]["target"] != bad["target"]
        if not (edited or changed_cmd):
            continue  # it passed on a plain retry: nothing was fixed, so there is nothing to learn
        paired.add(i)
        how = f"after changing {', '.join(dict.fromkeys(edited))}" if edited else "with a changed command"
        events.append(_event(
            "error-fix", bad["id"] + "|" + cs[fixer]["id"], f"{_EVENT_LABEL['error-fix']}: {_label(bad)}",
            f"{_label(bad)} failed with \"{bad['error']}\" and then worked {how}.",
            [f"error: {bad['error']}", f"fixed {how}"], ["problem-solution"],
            files_modified=list(dict.fromkeys(edited))))
    by_sig: dict[str, list[dict]] = {}
    for c in cs:
        by_sig.setdefault(c["sig"], []).append(c)
    for sig, group in by_sig.items():
        failures = [c for c in group if c["error"] is not None]
        first = group[0]
        if (len(group) >= RETRY_MIN_CALLS and len(failures) >= RETRY_MIN_FAILURES
                and first["tool"] not in READ_TOOLS and first["tool"] not in SEARCH_TOOLS):
            events.append(_event(
                "retry-loop", sig + "|" + first["id"], f"{_EVENT_LABEL['retry-loop']}: {_label(first)}",
                f"{_label(first)} was attempted {len(group)} times in one turn and failed {len(failures)} times"
                f" (last error: \"{failures[-1]['error']}\"). Approaches that loop like this need a different"
                " angle, not another retry.",
                [f"{len(group)} attempts, {len(failures)} failed", f"error: {failures[-1]['error']}"], ["gotcha"]))
    return events


def correction_event(prompt: str | None, prompt_number: int | None, files: list[str] | None = None) -> dict | None:
    """An event when this prompt tells the agent it was wrong. The first prompt of a session cannot correct
    anything, and long prompts are requests, not corrections."""
    text = " ".join((prompt or "").split())
    if not text or (prompt_number or 0) < 2 or len(text) > CORRECTION_MAX_CHARS or _NOT_A_CORRECTION.match(text):
        return None
    if not (_CORRECTION_START.match(text) or _CORRECTION_ANYWHERE.search(text)):
        return None
    clean = redact(text)
    return _event("correction", f"{prompt_number}|{clean}", f"{_EVENT_LABEL['correction']}: {clean}",
                  f"The user corrected the agent: \"{clean[:400]}\"."
                  + (f" The agent had just been working on {', '.join(files[:6])}." if files else ""),
                  [f"user said: {clean[:200]}"], ["gotcha"], files_read=list(files or [])[:6])


def event_observation(ev: dict, mode: Mode | None = None) -> dict:
    """The observation a detected event is stored as (typed from the mode's own vocabulary, so every view and
    every search already knows how to show it)."""
    mode = mode or load_mode()
    ids = mode.type_ids()
    typ = {"rejected": _pick(mode, ["decision", "discovery"], ids), "correction": _pick(mode, ["decision", "discovery"], ids),
           "error-fix": _pick(mode, ["bugfix", "change"], ids)}.get(ev["event"], _pick(mode, ["discovery"], ids))
    return {"type": typ, "title": ev["title"], "subtitle": _EVENT_SUBTITLE[ev["event"]], "facts": ev["facts"],
            "narrative": f"{_EVENT_SUBTITLE[ev['event']].capitalize()}. {ev['narrative']}",
            "concepts": _concepts(mode, ev["concepts"]), "files_read": ev["files_read"],
            "files_modified": ev["files_modified"],
            "metadata": json.dumps({"derived": True, "event": ev["event"], "key": ev["key"]})}
