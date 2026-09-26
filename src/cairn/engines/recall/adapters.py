"""Per-agent hook adapters: turn each agent's hook payload into one normalized input, and a handler
result back into the output shape that agent expects. Standard library only.

Platforms: claude-code (also Gemini CLI, which uses the same field names), codex, cursor, windsurf,
antigravity(-cli), copilot (GitHub Copilot CLI) and raw.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from .projects import hook_project_path
from .transcript_parser import extract_last_message


class AdapterRejectedInput(Exception):
    def __init__(self, reason: str):
        super().__init__(f"adapter rejected input: {reason}")
        self.reason = reason


def _valid_cwd(cwd: Any) -> bool:
    return isinstance(cwd, str) and len(cwd) > 0


def _s(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _agent_field(v: Any) -> str | None:
    return v if isinstance(v, str) and 0 < len(v) <= 128 else None


def _bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if v == "true":
        return True
    if v == "false":
        return False
    return None


# ---- claude code ----------------------------------------------------------------------------------------
def claude_code_normalize(r: dict) -> dict:
    cwd = r.get("cwd") or os.getcwd()
    if not _valid_cwd(cwd):
        raise AdapterRejectedInput("invalid_cwd")
    cwd = hook_project_path(cwd)
    if not _valid_cwd(cwd):
        raise AdapterRejectedInput("invalid_cwd")
    return {"session_id": r.get("session_id") or r.get("id") or r.get("sessionId"), "cwd": cwd,
            "prompt": r.get("prompt"), "tool_name": r.get("tool_name"), "tool_input": r.get("tool_input"),
            "tool_response": r.get("tool_response"),
            "tool_use_id": r.get("tool_use_id") if isinstance(r.get("tool_use_id"), str) else None,
            "transcript_path": r.get("transcript_path"),
            "reason": r.get("reason") if isinstance(r.get("reason"), str) else None,
            "agent_id": _agent_field(r.get("agent_id")), "agent_type": _agent_field(r.get("agent_type")),
            "stop_hook_active": _bool(r.get("stop_hook_active")),
            "session_source": r.get("source") if r.get("source") in ("startup", "resume", "clear", "compact") else None,
            # Gemini CLI's AfterAgent carries the final answer as prompt_response
            "last_assistant_message": _s(r.get("last_assistant_message")) or _s(r.get("prompt_response"))}


def claude_code_format(result: dict) -> dict:
    out: dict[str, Any] = {}
    if result.get("hookSpecificOutput"):
        out["hookSpecificOutput"] = result["hookSpecificOutput"]
    if result.get("systemMessage"):
        out["systemMessage"] = result["systemMessage"]
    return out


# ---- codex ------------------------------------------------------------------------------------------------
_CODEX_EVENTS = {"PreToolUse", "PermissionRequest", "PostToolUse", "SessionStart", "UserPromptSubmit", "Stop"}
_READ_COMMANDS = {"cat", "head", "tail", "less", "more", "bat", "view", "nl", "tac"}
_FLAG_VALUES = {"head": {"-n", "-c", "--lines", "--bytes"}, "tail": {"-n", "-c", "--lines", "--bytes"}}
MAX_FILE_PATHS = 10


def _existing_file(candidate: str, cwd: str) -> bool:
    p = candidate if os.path.isabs(candidate) else os.path.join(cwd, candidate)
    try:
        return os.path.isfile(p)
    except (OSError, ValueError):
        return False


def _dedupe_cap(paths: list[str]) -> list[str]:
    return list(dict.fromkeys(paths))[:MAX_FILE_PATHS]


def extract_file_paths(tool_name: str, tool_input: Any, cwd: str) -> list[str]:
    """Files a Codex tool call is about to read (shell read commands and MCP read tools)."""
    if tool_name == "Bash":
        command = (tool_input or {}).get("command") if isinstance(tool_input, dict) else None
        if isinstance(command, list):
            command = " ".join(p for p in command if isinstance(p, str)) or None
        if not isinstance(command, str):
            return []
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            return []  # an unparseable command yields no paths; never block the tool call
        segments, cur = [], []
        for t in tokens:
            if t and all(c in ";&|<>()" for c in t):
                if cur:
                    segments.append(cur)
                cur = []
            else:
                cur.append(t)
        if cur:
            segments.append(cur)
        paths: list[str] = []
        for seg in segments:
            idx = next((i for i, t in enumerate(seg) if t and not t.startswith(("-", "+"))), -1)
            if idx == -1:
                continue
            argv0 = os.path.basename(seg[idx])
            if argv0 not in _READ_COMMANDS:
                continue
            skip = False
            for tok in seg[idx + 1:]:
                if skip:
                    skip = False
                    continue
                if tok.startswith(("-", "+")):
                    vals = _FLAG_VALUES.get(argv0, set())
                    skip = (tok in vals or ("=" in tok and tok.split("=", 1)[0] in vals)) and "=" not in tok
                    continue
                if _existing_file(tok, cwd):
                    paths.append(tok)
        return _dedupe_cap(paths)
    if tool_name.startswith("mcp__") and re.match(r"^mcp__.+__(read|view|cat)(?:_file|_files)?$", tool_name):
        inp = tool_input if isinstance(tool_input, dict) else {}
        cands = [inp["path"]] if isinstance(inp.get("path"), str) else []
        cands += [p for p in inp.get("paths") or [] if isinstance(p, str)]
        return _dedupe_cap([c for c in cands if _existing_file(c, cwd)])
    return []


def codex_normalize(r: dict) -> dict:
    cwd = r.get("cwd") if isinstance(r.get("cwd"), str) and r.get("cwd") else os.getcwd()
    if not _valid_cwd(cwd):
        raise AdapterRejectedInput("invalid_cwd")
    event = r.get("hook_event_name") if r.get("hook_event_name") in _CODEX_EVENTS else None
    tool_name = _s(r.get("tool_name"))
    tool_input = dict(r["tool_input"]) if isinstance(r.get("tool_input"), dict) else r.get("tool_input")
    if event == "PreToolUse" and tool_name:
        fps = extract_file_paths(tool_name, tool_input, cwd)
        if fps and isinstance(tool_input, dict):
            tool_input = {**tool_input, "filePaths": fps}
    sid = _s(r.get("session_id"))
    if not sid:
        raise AdapterRejectedInput("missing_session_id")
    src = r.get("source")
    return {"session_id": sid, "cwd": cwd, "prompt": _s(r.get("prompt")), "tool_name": tool_name,
            "tool_input": tool_input, "tool_response": r.get("tool_response"),
            "tool_use_id": _s(r.get("tool_use_id")) or _s(r.get("call_id")),
            "transcript_path": _s(r.get("transcript_path")), "last_assistant_message": _s(r.get("last_assistant_message")),
            "turn_id": _s(r.get("turn_id")), "stop_hook_active": _bool(r.get("stop_hook_active")),
            "permission_mode": _s(r.get("permission_mode")), "model": _s(r.get("model")),
            "session_source": src if src in ("startup", "resume", "clear") else None}


def codex_format(result: dict) -> dict:
    out: dict[str, Any] = {}
    if result.get("continue") is not None:
        out["continue"] = result["continue"]
    if result.get("systemMessage"):
        out["systemMessage"] = result["systemMessage"]
    if result.get("decision") == "block":
        out["decision"] = "block"
    if result.get("reason"):
        out["reason"] = result["reason"]
    hs = result.get("hookSpecificOutput")
    ev = hs.get("hookEventName") if isinstance(hs, dict) else None
    if not hs or ev not in _CODEX_EVENTS or ev == "Stop":
        return out
    spec: dict[str, Any] = {"hookEventName": ev}
    if isinstance(hs.get("additionalContext"), str):
        spec["additionalContext"] = hs["additionalContext"]
    if ev == "PreToolUse":
        if hs.get("permissionDecision") == "deny":
            spec["permissionDecision"] = "deny"
            if hs.get("permissionDecisionReason"):
                spec["permissionDecisionReason"] = hs["permissionDecisionReason"]
        if hs.get("updatedInput"):
            spec["updatedInput"] = hs["updatedInput"]
    out["hookSpecificOutput"] = spec
    return out


# ---- cursor ------------------------------------------------------------------------------------------------
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def derive_cursor_transcript_path(cwd: str | None, session_id: str | None) -> str | None:
    if not cwd or not session_id or not _SAFE_ID.match(session_id):
        return None
    slug = re.sub(r"[/.]", "-", re.sub(r"^/", "", cwd))
    p = Path.home() / ".cursor" / "projects" / slug / "agent-transcripts" / session_id / f"{session_id}.jsonl"
    return str(p) if p.exists() else None


def cursor_normalize(r: dict) -> dict:
    is_shell = bool(r.get("command")) and not r.get("tool_name")
    roots = r.get("workspace_roots")
    cwd = (roots[0] if isinstance(roots, list) and roots else None) or r.get("cwd") or os.getcwd()
    if not _valid_cwd(cwd):
        raise AdapterRejectedInput("invalid_cwd")
    sid = r.get("conversation_id") or r.get("generation_id") or r.get("id")
    tu = r.get("tool_use_id") if isinstance(r.get("tool_use_id"), str) else (
        r.get("tool_call_id") if isinstance(r.get("tool_call_id"), str) else None)
    return {"session_id": sid, "cwd": cwd, "prompt": r.get("prompt") or r.get("query") or r.get("input") or r.get("message"),
            "tool_name": "Bash" if is_shell else r.get("tool_name"),
            "tool_input": {"command": r.get("command")} if is_shell else r.get("tool_input"),
            "tool_response": {"output": r.get("output")} if is_shell else (
                r.get("tool_output") if r.get("tool_output") is not None else r.get("result_json")),
            "tool_use_id": tu, "transcript_path": derive_cursor_transcript_path(cwd, sid),
            "file_path": r.get("file_path"), "edits": r.get("edits")}


def continue_format(result: dict) -> dict:
    return {"continue": result.get("continue", True)}


def cursor_format(result: dict) -> dict:
    """sessionStart hands the memory back as ``additional_context``; other events print nothing unless they
    block (Cursor validates each event's output, and most accept no ``continue``)."""
    hs = result.get("hookSpecificOutput")
    if isinstance(hs, dict) and hs.get("hookEventName") == "SessionStart":
        return {"additional_context": hs.get("additionalContext") or ""}
    return {"continue": False} if result.get("continue") is False else {}


# ---- windsurf ----------------------------------------------------------------------------------------------
def windsurf_normalize(r: dict) -> dict:
    info = r.get("tool_info") or {}
    action = r.get("agent_action_name") or ""
    cwd = info.get("cwd") or os.getcwd()
    if not _valid_cwd(cwd):
        raise AdapterRejectedInput("invalid_cwd")
    base = {"session_id": r.get("trajectory_id") or r.get("execution_id"), "cwd": cwd, "platform": "windsurf"}
    if action == "pre_user_prompt":
        return {**base, "prompt": info.get("user_prompt")}
    if action == "post_write_code":
        return {**base, "tool_name": "Write", "file_path": info.get("file_path"), "edits": info.get("edits"),
                "tool_input": {"file_path": info.get("file_path"), "edits": info.get("edits")}}
    if action == "post_run_command":
        return {**base, "cwd": info.get("cwd") or cwd, "tool_name": "Bash",
                "tool_input": {"command": info.get("command_line")}}
    if action == "post_mcp_tool_use":
        return {**base, "tool_name": info.get("mcp_tool_name") or "mcp_tool", "tool_input": info.get("mcp_tool_arguments"),
                "tool_response": info.get("mcp_result")}
    if action == "post_cascade_response":
        return {**base, "tool_name": "cascade_response", "tool_response": info.get("response")}
    return base


# ---- antigravity -------------------------------------------------------------------------------------------
_last_antigravity_raw: dict = {}


def unwrap_agy_input(text: str) -> str:
    m = re.search(r"<USER_REQUEST>\s*([\s\S]*?)\s*</USER_REQUEST>", text or "")
    if m:
        return m.group(1).strip()
    text = re.sub(r"<ADDITIONAL_METADATA>[\s\S]*?</ADDITIONAL_METADATA>", "", text or "")
    return re.sub(r"<USER_SETTINGS_CHANGE>[\s\S]*?</USER_SETTINGS_CHANGE>", "", text).strip()


def antigravity_normalize(r: dict) -> dict:
    global _last_antigravity_raw
    _last_antigravity_raw = r
    wp = r.get("workspacePaths")
    cwd = (wp[0] if isinstance(wp, list) and wp else None) or r.get("cwd") or os.environ.get("GEMINI_CWD") \
        or os.environ.get("GEMINI_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if not _valid_cwd(cwd):
        raise AdapterRejectedInput("invalid_cwd")
    sid = r.get("conversationId") or r.get("session_id") or os.environ.get("GEMINI_SESSION_ID")
    transcript = r.get("transcriptPath") or r.get("transcript_path")
    tool_call = r.get("toolCall") if isinstance(r.get("toolCall"), dict) else None
    has_error_key = "error" in r
    invocation = "invocationNum" in r and not tool_call
    tool_name = (tool_call or {}).get("name") or r.get("tool_name")
    tool_input = (tool_call or {}).get("args") if tool_call else r.get("tool_input")
    tool_response = r.get("tool_response")
    tprompt = unwrap_agy_input(extract_last_message(transcript, "user")) if transcript else ""
    prompt = r.get("prompt") or (tprompt or None)
    if invocation:
        response = extract_last_message(transcript, "assistant") if transcript else ""
        tool_name = "AntigravityProvider"
        tool_input = {"prompt": prompt or "User Query"}
        tool_response = {"response": response or "Completed"}
    if tool_call and not has_error_key and tool_name and not tool_response:
        tool_response = {"_preExecution": True}
    if tool_call and has_error_key and tool_name and not tool_response:
        tool_response = {"error": r["error"]} if isinstance(r.get("error"), str) and r["error"] else {"status": "completed"}
    return {"session_id": sid, "cwd": cwd, "prompt": prompt, "tool_name": tool_name, "tool_input": tool_input,
            "tool_response": tool_response, "transcript_path": transcript}


_ANSI = re.compile(r"[\u001b\u009b][\[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]")


def antigravity_format(result: dict) -> dict:
    raw = _last_antigravity_raw or {}
    is_pre_tool = bool(raw.get("toolCall")) and "error" not in raw
    if result.get("continue") is False or result.get("decision") == "block":
        return {"decision": "deny", "reason": result.get("reason") or "Denied by hook"}
    extra = (result.get("hookSpecificOutput") or {}).get("additionalContext") or result.get("systemMessage")
    if extra:
        return {"injectSteps": [{"ephemeralMessage": _ANSI.sub("", str(extra))}]}
    if is_pre_tool:
        return {"decision": "allow"}
    return {}


# ---- copilot ---------------------------------------------------------------------------------------------------
def copilot_normalize(r: dict) -> dict:
    """GitHub Copilot CLI hooks: camelCase fields; ``toolArgs`` may arrive as a JSON string."""
    cwd = r.get("cwd") or os.getcwd()
    if not _valid_cwd(cwd):
        raise AdapterRejectedInput("invalid_cwd")
    args = r.get("toolArgs")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"args": args}
    result = r.get("toolResult")
    if isinstance(result, dict) and isinstance(result.get("textResultForLlm"), str):
        result = {"result": result["textResultForLlm"], "status": result.get("resultType")}
    return {"session_id": _s(r.get("sessionId")) or _s(r.get("session_id")), "cwd": cwd, "prompt": _s(r.get("prompt")),
            "tool_name": _s(r.get("toolName")), "tool_input": args, "tool_response": result,
            "transcript_path": _s(r.get("transcriptPath")),
            "reason": r.get("reason") if isinstance(r.get("reason"), str) else None}


# ---- raw -------------------------------------------------------------------------------------------------------
def raw_normalize(r: dict) -> dict:
    cwd = r.get("cwd") or os.getcwd()
    if not _valid_cwd(cwd):
        raise AdapterRejectedInput("invalid_cwd")
    return {"session_id": r.get("sessionId") or r.get("session_id") or "unknown", "cwd": cwd, "prompt": r.get("prompt"),
            "tool_name": r.get("toolName") or r.get("tool_name"), "tool_input": r.get("toolInput") or r.get("tool_input"),
            "tool_response": r.get("toolResponse") or r.get("tool_response"),
            "tool_use_id": r.get("toolUseId") or r.get("tool_use_id"),
            "transcript_path": r.get("transcriptPath") or r.get("transcript_path"),
            "file_path": r.get("filePath") or r.get("file_path"), "edits": r.get("edits"),
            "last_assistant_message": _s(r.get("last_assistant_message") or r.get("lastAssistantMessage"))}


ADAPTERS = {
    "claude-code": (claude_code_normalize, claude_code_format),
    "claude": (claude_code_normalize, claude_code_format),
    "gemini": (claude_code_normalize, claude_code_format),
    "gemini-cli": (claude_code_normalize, claude_code_format),
    "codex": (codex_normalize, codex_format),
    "cursor": (cursor_normalize, cursor_format),
    "windsurf": (windsurf_normalize, continue_format),
    "antigravity": (antigravity_normalize, antigravity_format),
    "antigravity-cli": (antigravity_normalize, antigravity_format),
    "copilot": (copilot_normalize, lambda r: {}),  # Copilot CLI 1.0 ignores these hooks' output
    "opencode": (raw_normalize, lambda r: r),
    "raw": (raw_normalize, lambda r: r),
}


def get_adapter(platform: str):
    return ADAPTERS.get(platform or "raw", ADAPTERS["raw"])


def normalize(platform: str, raw: Any) -> dict:
    norm, _ = get_adapter(platform)
    data = norm(raw if isinstance(raw, dict) else {})
    data.setdefault("platform", platform)
    data["platform"] = platform
    return data


def format_output(platform: str, result: dict) -> Any:
    _, fmt = get_adapter(platform)
    return fmt(result or {})


def parse_stdin(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    return json.loads(text)
