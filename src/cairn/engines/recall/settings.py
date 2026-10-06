"""Recall settings: the ``[recall]`` section of ``.cairn/config.toml`` (standard library only).

Every key has a default; ``CAIRN_RECALL_<KEY>`` in the environment wins over the file. The legacy
``[sessions] capture = false`` switch is still honoured. ``save()`` rewrites only the ``[recall]``
section, leaving the rest of the file (and its comments) untouched.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import tomllib

SECTION = "recall"

DEFAULTS: dict[str, Any] = {
    # capture
    "capture": True,                     # record agent sessions at all
    "skip_tools": "ListMcpResourcesTool,SlashCommand,Skill,TodoWrite,AskUserQuestion",
    "excluded_projects": "",             # comma-separated globs of project paths never recorded
    "mode": "code",                      # observation vocabulary (modes/<id>.json; parent--override inherits)
    "runtime": "local",                  # local | server (team server: advertises server-only tools)
    # worker / model
    "worker_spawn": True,                # Stop/SessionEnd hooks start a detached worker to drain the queue
    "worker_model": True,                # False: that worker derives records without a model (a connected
                                         # team server does the model work, so it is not paid for twice)
    "tier_routing": True,                # batches of read-only tools go to the fast tier
    "observe_batch": 5,                  # tool events per model call (1 = one call per event)
    "observe_catch_up": 20,              # up to this many per call while a session is far behind (0 = off)
    "observer_replay_exchanges": 8,      # recent observer exchanges replayed per call (0 = the whole conversation)
    "observer_replay_chars": 24_000,     # at most this much of them is replayed (earlier prompts are cut short)
    "observer_prompt_chars": 48_000,     # tool events per call are trimmed to fit this, however many there are
    "observer_max_conversation_chars": 400_000,  # retire an observer conversation past this size
    "max_retries": 3,                    # model failures before a queued event falls back to a derived record
    "max_concurrent_sessions": 2,        # sessions a hosted worker processes in parallel
    "vectors": True,                     # keep a local vector index for semantic search
    # SessionStart context injection
    "context_observations": 50,
    "context_full_count": 0,
    "context_full_field": "narrative",   # narrative | facts
    "context_session_count": 10,
    "context_show_last_summary": True,
    "context_show_last_message": False,
    "context_show_read_tokens": False,
    "context_show_work_tokens": False,
    "context_show_savings_amount": False,
    "context_show_savings_percent": True,
    "context_observation_types": "",     # comma-separated narrowing of the mode's types ("" = all)
    "context_observation_concepts": "",  # comma-separated narrowing of the mode's concepts ("" = all)
    "context_show_terminal_output": True,
    "welcome_hint": True,
    "semantic_inject": False,            # add relevant past observations on every prompt
    "semantic_inject_limit": 5,
    # folder context files
    "folder_context": False,             # keep a <cairn-context> timeline in CLAUDE.md of touched folders
    "folder_use_local_md": False,        # write CLAUDE.local.md instead of CLAUDE.md
    "folder_md_exclude": "[]",           # JSON list of folders never written
    "folder_md_skeleton_denylist": "[]", # JSON list of globs: skip folders whose file would be empty
    # transcripts
    "transcripts_enabled": True,
    "transcripts_config_path": "",       # default: $CAIRN_HOME/transcript-watch.json
    "codex_transcript_ingestion": False,
}

ENV_PREFIX = "CAIRN_RECALL_"


def cairn_home() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn")


def config_path(root: Path) -> Path:
    return Path(root) / ".cairn" / "config.toml"


def _coerce(value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(str(value).strip())
        except ValueError:
            return default
    if isinstance(value, (list, dict)):
        return json.dumps(value)  # a TOML array for a JSON-list setting (e.g. folder_md_exclude)
    return value if isinstance(value, str) else ("" if value is None else str(value))


def load(root: Path | str | None) -> dict[str, Any]:
    """Effective settings for the repository at ``root`` (defaults when there is none)."""
    out = dict(DEFAULTS)
    layers: list[dict] = []  # the committed config, then this checkout's untracked overrides (as Project reads them)
    if root is not None:
        for path in (config_path(Path(root)), config_path(Path(root)).with_name("config.local.toml")):
            try:
                layers.append(tomllib.loads(path.read_text(encoding="utf-8")))
            except (OSError, tomllib.TOMLDecodeError):
                continue
    for data in layers:
        capture = data.get("sessions", {}).get("capture")
        if isinstance(capture, bool):
            out["capture"] = capture
        for k, v in (data.get(SECTION) or {}).items():
            if k in DEFAULTS:
                out[k] = _coerce(v, DEFAULTS[k])
    for k in DEFAULTS:
        env = os.environ.get(ENV_PREFIX + k.upper())
        if env is not None:
            out[k] = _coerce(env, DEFAULTS[k])
    return out


def csv(value: str) -> list[str]:
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return json.dumps(str(v))  # a JSON string literal is a valid TOML basic string


_HEADER = re.compile(r"^\s*\[([^\]]+)\]\s*(#.*)?$")


def save(root: Path | str, updates: dict[str, Any]) -> dict[str, Any]:
    """Persist ``updates`` (unknown keys are rejected) into the ``[recall]`` section; returns settings."""
    unknown = sorted(k for k in updates if k not in DEFAULTS)
    if unknown:
        raise KeyError(f"unknown recall settings: {', '.join(unknown)}")
    path = config_path(Path(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    clean = {k: _coerce(v, DEFAULTS[k]) for k, v in updates.items()}
    start = next((i for i, ln in enumerate(lines) if (m := _HEADER.match(ln)) and m.group(1).strip() == SECTION), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{SECTION}]")
        lines += [f"{k} = {_toml_value(v)}" for k, v in clean.items()]
    else:
        end = next((i for i in range(start + 1, len(lines)) if _HEADER.match(lines[i])), len(lines))
        body, seen = lines[start + 1:end], set()
        for i, ln in enumerate(body):
            key = ln.split("=", 1)[0].strip() if "=" in ln and not ln.lstrip().startswith("#") else None
            if key in clean:
                comment = ""
                if "#" in ln.split("=", 1)[1] and not ln.split("=", 1)[1].strip().startswith(('"', "'")):
                    comment = "  #" + ln.split("#", 1)[1]
                body[i] = f"{key} = {_toml_value(clean[key])}{comment}"
                seen.add(key)
        insert_at = len(body)
        while insert_at > 0 and not body[insert_at - 1].strip():
            insert_at -= 1
        body[insert_at:insert_at] = [f"{k} = {_toml_value(v)}" for k, v in clean.items() if k not in seen]
        lines[start + 1:end] = body
    text = "\n".join(lines) + "\n"
    tomllib.loads(text)  # never write a file we cannot read back
    path.write_text(text, encoding="utf-8")
    return load(root)
