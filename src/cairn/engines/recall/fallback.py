"""Deterministic records for when no model is available (or a queued event kept failing).

One observation per prompt turn, derived from the tool events: files read and changed, commands run,
searches made; one summary per Stop, derived from the prompt, the turn's files and the agent's final
answer. They are marked ``{"derived": true}`` in ``metadata`` and are merged as later events of the
same turn arrive, so capture is never empty and never duplicated. Standard library only.
"""
from __future__ import annotations

import json
import os
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
