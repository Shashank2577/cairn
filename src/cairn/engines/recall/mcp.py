"""Recall's agent tools, as plain functions for Cairn's MCP server to register.

Progressive disclosure: ``recall_search`` (an index with IDs) -> ``recall_timeline`` (what happened
around a result) -> ``get_observations`` (full records, only for the IDs that matter) ->
``get_tool_uses`` (raw tool input/output, last resort). Plus the session-start context, structural
code reading (smart_*), knowledge corpora and, when a team server runtime is configured, the
``observation_*`` tools.

``TOOLS`` lists name/description/input schema; ``advertised_tools(runtime)`` applies the visibility
rule (server-only tools are hidden in the local runtime); ``call_tool(root, name, args)`` returns
``{"content": [{"type": "text", "text": ...}], "isError": bool}``.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from . import ingest, schema
from .context import inject_context
from .platforms import normalize_platform_source
from .projects import project_context
from .settings import load as load_settings
from .store import Store

log = logging.getLogger("cairn.recall")

WORKFLOW = """# Memory Search Workflow

**3-Layer Pattern (ALWAYS follow this):**

1. **Search** - Get index of results with IDs
   `recall_search(query="...", limit=20, project="...")`
   Returns: Table with IDs, titles, dates (~50-100 tokens/result)

2. **Timeline** - Get context around interesting results
   `recall_timeline(anchor=<ID>, depth_before=3, depth_after=3)`
   Returns: Chronological context showing what was happening

3. **Fetch** - Get full details ONLY for relevant IDs
   `get_observations(ids=[...])`  # ALWAYS batch for 2+ items
   Returns: Complete details (~500-1000 tokens/result)

4. **Disclose raw tool I/O** - Last resort, when the observation summary does not answer the question
   `get_tool_uses(ids=[...])`
   Returns: The original tool_input / tool_response bodies (UNSUMMARIZED — can be thousands of tokens each)

**Why:** 10x token savings. Never fetch full details without filtering first, and never reach for layer 4 before layer 3 answered."""

SERVER_ONLY_TOOL_NAMES = ("observation_add", "observation_record_event", "observation_search", "observation_context",
                          "observation_generation_status", "memory_add", "memory_search", "memory_context")

_CORE_TOOLS: list[dict] = [
    {"name": "important_workflow",
     "description": "LAYERED WORKFLOW (ALWAYS FOLLOW):\n1. recall_search(query) → Get index with IDs (~50-100"
                    " tokens/result)\n2. recall_timeline(anchor=ID) → Get context around interesting results\n3."
                    " get_observations([IDs]) → Fetch full details ONLY for filtered IDs\n4. get_tool_uses([IDs])"
                    " → Raw tool_input/tool_response, ONLY when the summary is not enough\nNEVER fetch full details"
                    " without filtering first. 10x token savings.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "recall_search",
     "description": "Step 1: Search memory. Returns index with IDs. Params: query, limit, project, platformSource, type,"
                    " obs_type, dateStart, dateEnd, offset, orderBy",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Search query"},
         "limit": {"type": "number", "description": "Max results (default 20)"},
         "project": {"type": "string", "description": "Filter by project name"},
         "platformSource": {"type": "string", "description": "Filter by platform source (e.g. claude, codex, cursor)"
                                                             " — restricts results to that agent's own memory"},
         "type": {"type": "string", "description": "Document category to search: 'observations', 'sessions', or"
                                                   " 'prompts' (default: all). Any other value is treated as an"
                                                   " observation-type filter (alias for obs_type)."},
         "obs_type": {"type": "string", "description": "Filter observations by their type (e.g. bugfix, feature)."
                                                       " Comma-separated for multiple."},
         "dateStart": {"type": "string", "description": "Start date filter (ISO)"},
         "dateEnd": {"type": "string", "description": "End date filter (ISO)"},
         "offset": {"type": "number", "description": "Pagination offset"},
         "orderBy": {"type": "string", "description": "Sort order: date_desc or date_asc"}},
         "additionalProperties": True}},
    {"name": "recall_timeline",
     "description": "Step 2: Get context around results. Params: anchor (observation ID) OR query (finds anchor"
                    " automatically), depth_before, depth_after, project",
     "input_schema": {"type": "object", "properties": {
         "anchor": {"type": "number", "description": "Observation ID to center the timeline around"},
         "query": {"type": "string", "description": "Query to find anchor automatically"},
         "depth_before": {"type": "number", "description": "Items before anchor (default 3)"},
         "depth_after": {"type": "number", "description": "Items after anchor (default 3)"},
         "project": {"type": "string", "description": "Filter by project name"}},
         "additionalProperties": True}},
    {"name": "get_observations",
     "description": "Step 3: Fetch full details for filtered IDs. Params: ids (array of observation IDs, required),"
                    " orderBy, limit, project",
     "input_schema": {"type": "object", "properties": {
         "ids": {"type": "array", "items": {"type": "number"}, "description": "Array of observation IDs to fetch"
                                                                              " (required)"}},
         "required": ["ids"], "additionalProperties": True}},
    {"name": "get_tool_uses",
     "description": "Step 4 (raw tool I/O, rarely needed): fetch the ORIGINAL tool_input/tool_response for tool calls"
                    " you already identified. Requires ids — run recall_search/recall_timeline/get_observations"
                    " first and pass only the ids you actually need; these payloads are large and unsummarized. ids"
                    " accept numeric tool_uses ids or tool_use_id strings. Params: ids (required), limit, project,"
                    " contentSessionId.",
     "input_schema": {"type": "object", "properties": {
         "ids": {"type": "array", "items": {"type": ["number", "string"]},
                 "description": "Tool-use ids to fetch (required). Numeric tool_uses.id or opaque tool_use_id"
                                " strings."},
         "limit": {"type": "number", "description": "Max rows to return"},
         "project": {"type": "string", "description": "Filter by project name"},
         "contentSessionId": {"type": "string", "description": "Filter to one content session"}},
         "required": ["ids"], "additionalProperties": True}},
    {"name": "session_start_context",
     "description": "Render the exact SessionStart context for a project — the same text hooks inject at startup."
                    " Params: project OR projects, platformSource, full, colors.",
     "input_schema": {"type": "object", "properties": {
         "project": {"type": "string", "description": "Project name, e.g. my-repo or my-repo/feature-worktree"},
         "projects": {"oneOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string"}],
                      "description": "Project chain for context injection. Array or comma-separated string; last"
                                     " project is treated as primary."},
         "platformSource": {"type": "string", "description": "Optional platform source filter, e.g. claude, codex,"
                                                             " cursor"},
         "full": {"type": "boolean", "description": "When true, request full context instead of configured limits"},
         "colors": {"type": "boolean", "description": "When true, request human terminal-color formatting"}},
         "additionalProperties": False}},
    {"name": "observation_add",
     "description": "Insert a manual observation directly into storage — does NOT enqueue generation. Server"
                    " runtime only. Params: content (required), projectId (optional), serverSessionId, kind, metadata.",
     "input_schema": {"type": "object", "properties": {
         "projectId": {"type": "string"}, "serverSessionId": {"type": "string"},
         "kind": {"type": "string", "description": "Observation kind (default: manual)"},
         "content": {"type": "string", "description": "Observation content (required)"},
         "metadata": {"type": "object", "additionalProperties": True}},
         "required": ["content"], "additionalProperties": False}},
    {"name": "observation_record_event",
     "description": "Record an agent event: the event is queued and generation runs in the worker. Server runtime"
                    " only.",
     "input_schema": {"type": "object", "properties": {
         "projectId": {"type": "string"},
         "eventType": {"type": "string", "description": "Event type (required), e.g. PostToolUse, UserPromptSubmit"},
         "sourceType": {"type": "string", "enum": ["hook", "worker", "provider", "server", "api"]},
         "serverSessionId": {"type": "string"}, "contentSessionId": {"type": "string"},
         "memorySessionId": {"type": "string"}, "platformSource": {"type": "string"},
         "payload": {"description": "Event payload (any JSON value)"},
         "occurredAtEpoch": {"type": "number"},
         "generate": {"type": "boolean", "description": "If false, skip generation (default: true)"}},
         "required": ["eventType"], "additionalProperties": False}},
    {"name": "observation_search",
     "description": "Full-text search across generated observations. Server runtime only. Params: query (required),"
                    " projectId, platformSource, limit (default 20, max 100).",
     "input_schema": {"type": "object", "properties": {
         "projectId": {"type": "string"}, "query": {"type": "string"}, "platformSource": {"type": "string"},
         "limit": {"type": "number"}}, "required": ["query"], "additionalProperties": False}},
    {"name": "observation_context",
     "description": "Get top-N relevant observations for context injection. Returns matched observations AND a"
                    " pre-joined context string suitable for prompt injection. Server runtime only.",
     "input_schema": {"type": "object", "properties": {
         "projectId": {"type": "string"}, "query": {"type": "string"}, "platformSource": {"type": "string"},
         "limit": {"type": "number"}}, "required": ["query"], "additionalProperties": False}},
    {"name": "observation_generation_status",
     "description": "Look up the status of an observation generation job by id. Server runtime only.",
     "input_schema": {"type": "object", "properties": {"jobId": {"type": "string"}}, "required": ["jobId"],
                      "additionalProperties": False}},
]

ALIASES = {"search": "recall_search", "timeline": "recall_timeline"}


def all_tools() -> list[dict]:
    from . import corpus, smartread
    return [*_CORE_TOOLS, *smartread.TOOLS, *corpus.TOOLS]


TOOLS = _CORE_TOOLS  # the recall core; ``all_tools()`` adds smart_* and corpus tools


def advertised_tools(runtime: str = "local") -> list[dict]:
    """Server-only tools are advertised only in the team-server runtime."""
    tools = all_tools()
    if runtime == "server":
        return tools
    return [t for t in tools if t["name"] not in SERVER_ONLY_TOOL_NAMES]


def _text(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _json(payload: Any) -> dict:
    return _text(json.dumps(payload, indent=2, default=str))


def _ids(value: Any) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [v.strip() for v in value.split(",") if v.strip()]
    return list(value) if isinstance(value, list) else []


def _project(root: Path, args: dict) -> str:
    return args.get("projectId") or args.get("project") or project_context(str(root)).primary


def call_tool(root: Path | str, name: str, args: dict | None = None, *, router: Any = None,
              runtime: str | None = None) -> dict:
    """Run one recall tool against the repository at ``root``."""
    root = Path(root)
    args = dict(args or {})
    name = ALIASES.get(name, name)
    runtime = runtime or str(load_settings(root).get("runtime") or "local")
    try:
        if name in SERVER_ONLY_TOOL_NAMES and runtime != "server":
            return _text(f"{name} requires the team-server runtime (`[recall] runtime = \"server\"`). Use"
                         " recall_search/recall_timeline/get_observations for local memory access.", True)
        if name == "important_workflow":
            return _text(WORKFLOW)
        if name in ("smart_search", "smart_outline", "smart_unfold"):
            from . import smartread
            res = smartread.call_tool(name, args, str(root))
            return _text(res["text"], bool(res.get("is_error")))
        if name in ("build_corpus", "list_corpora", "prime_corpus", "query_corpus", "rebuild_corpus",
                    "reprime_corpus"):
            from . import corpus
            from .search import RecallSearch

            def search_fn(query: str, limit: int) -> list:
                with RecallSearch(root) as s:
                    return s.semantic_ids(query, limit)
            text = corpus.call_tool(root, name, args, router=router, search_fn=search_fn)
            return _text(text, text.startswith("Error"))
        if name == "session_start_context":
            projects = args.get("projects")
            if isinstance(projects, str):
                projects = [p.strip() for p in projects.split(",") if p.strip()]
            if not projects and isinstance(args.get("project"), str) and args["project"].strip():
                projects = [args["project"].strip()]
            if not projects:
                return _text('session_start_context: "project" or "projects" is required', True)
            return _text(inject_context(root, projects, platform_source=args.get("platformSource"),
                                        for_human=bool(args.get("colors")), full=bool(args.get("full"))))
        store = Store.open(root, project_name=project_context(str(root)).primary)
        try:
            return _core(root, store, name, args)
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - a tool reports errors as text, never raises
        log.warning("%s failed: %s", name, exc)
        return _text(f"Tool execution failed: {exc}", True)


def _core(root: Path, store: Store, name: str, args: dict) -> dict:
    from .search import RecallSearch
    if name == "recall_search":
        with RecallSearch(root, store) as s:
            res = s.search(args)
        return res if "content" in res else _json(res)
    if name == "recall_timeline":
        with RecallSearch(root, store) as s:
            return s.timeline(args)
    if name == "get_observations":
        ids = [int(i) for i in _ids(args.get("ids")) if str(i).strip().lstrip("-").isdigit()]
        if not ids:
            return _json([])
        order = args.get("orderBy") if args.get("orderBy") in ("date_desc", "date_asc") else "date_desc"
        src = args.get("platformSource") or args.get("platform_source")
        return _json(store.get_observations_by_ids(ids, order_by=order, limit=args.get("limit"),
                                                   project=args.get("project"), platform_source=src))
    if name == "get_tool_uses":
        ids = _ids(args.get("ids"))
        if not ids:
            return _json([])
        limit = args.get("limit")
        limit = min(int(limit), 200) if isinstance(limit, (int, float)) and limit > 0 else None
        return _json(store.get_tool_uses_by_ids(ids, limit=limit, project=args.get("project"),
                                                content_session_id=args.get("contentSessionId"),
                                                platform_source=args.get("platformSource")))
    if name == "observation_add":
        if not isinstance(args.get("content"), str) or not args["content"].strip():
            return _text('observation_add: "content" is required', True)
        return _json(save_memory(store, args["content"], project=_project(root, args), kind=args.get("kind"),
                                 metadata=args.get("metadata"), root=root))
    if name == "observation_record_event":
        return _json(record_event(root, store, args))
    if name == "observation_search":
        if not isinstance(args.get("query"), str) or not args["query"].strip():
            return _text('observation_search: "query" is required', True)
        limit = min(int(args.get("limit") or 20), 100)
        with RecallSearch(root, store) as s:
            res = s.search({"query": args["query"], "type": "observations", "limit": limit, "format": "json",
                            **({"platformSource": args["platformSource"]} if args.get("platformSource") else {})})
        return _json({"observations": res.get("observations", []), "count": len(res.get("observations", []))})
    if name == "observation_context":
        if not isinstance(args.get("query"), str) or not args["query"].strip():
            return _text('observation_context: "query" is required', True)
        limit = min(int(args.get("limit") or 10), 50)
        with RecallSearch(root, store) as s:
            res = s.search({"query": args["query"], "type": "observations", "limit": limit, "format": "json"})
            ctx = s.semantic_context(args["query"], limit=limit) if len(args["query"]) >= 20 else {"context": ""}
        obs = res.get("observations", [])
        context = ctx.get("context") or "\n".join(f"- {o.get('title')}: {o.get('narrative') or ''}" for o in obs)
        return _json({"observations": obs, "context": context})
    if name == "observation_generation_status":
        job = str(args.get("jobId") or args.get("job_id") or "").strip()
        if not job.isdigit():
            return _text('observation_generation_status: "jobId" is required', True)
        row = store.db.execute("SELECT id, status, retry_count, created_at_epoch FROM pending_messages WHERE id=?",
                               (int(job),)).fetchone()
        return _json(dict(row) if row else {"id": int(job), "status": "completed"})
    return _text(f"Unknown tool: {name}", True)


def save_memory(store: Store, text: str, *, title: str | None = None, project: str | None = None,
                kind: str | None = None, metadata: dict | None = None, root: Path | None = None,
                platform_source: str | None = None) -> dict:
    """Save a manual memory as an observation in the project's manual session (and index it)."""
    project = project or (metadata or {}).get("project") or project_context(str(root or os.getcwd())).primary
    src = platform_source or (metadata or {}).get("platformSource") or "claude"
    memory_id = store.get_or_create_manual_session(project, src)
    t = (title or "").strip() or (text[:60].strip() + ("..." if len(text) > 60 else ""))
    obs = {"type": "discovery", "title": t, "subtitle": "Manual memory" if not kind else f"Manual memory ({kind})",
           "facts": [], "narrative": text, "concepts": [], "files_read": [], "files_modified": [],
           "metadata": json.dumps(metadata) if metadata else None}
    res = store.store_observation(memory_id, project, obs, 0, 0)
    if root is not None and load_settings(root).get("vectors"):
        try:
            from .vectorsync import VectorSync
            VectorSync(root, store).sync_observations([res["id"]])
        except Exception as exc:  # noqa: BLE001
            log.debug("manual memory not indexed yet: %s", exc)
    return {"success": True, "id": res["id"], "title": t, "project": project,
            "message": f"Memory saved as observation #{res['id']}"}


def record_event(root: Path, store: Store, args: dict) -> dict:
    """Queue an agent event through the capture choke point (the hook path, without a hook)."""
    et = str(args.get("eventType") or "").strip()
    payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
    csid = str(args.get("contentSessionId") or payload.get("session_id") or f"api-{schema.now_ms()}")
    src = normalize_platform_source(args.get("platformSource") or payload.get("platformSource"))
    cwd = payload.get("cwd") or str(root)
    settings = load_settings(root)
    low = et.lower()
    if low in ("posttooluse", "tool_use", "observation"):
        res = ingest.ingest_observation(store, settings, content_session_id=csid,
                                        tool_name=str(payload.get("tool_name") or payload.get("toolName") or "tool"),
                                        tool_input=payload.get("tool_input"), tool_response=payload.get("tool_response"),
                                        cwd=cwd, platform_source=src, tool_use_id=payload.get("tool_use_id"))
    elif low in ("userpromptsubmit", "prompt", "session_init"):
        res = ingest.session_init(store, content_session_id=csid, project=project_context(cwd).primary,
                                  prompt=payload.get("prompt"), platform_source=src, cwd=cwd)
    elif low in ("stop", "assistant_message", "summarize"):
        res = ingest.queue_summarize(store, content_session_id=csid,
                                     last_assistant_message=payload.get("last_assistant_message")
                                     or payload.get("message"), platform_source=src, cwd=cwd)
    elif low in ("sessionend", "session_end"):
        res = ingest.session_end(store, content_session_id=csid, platform_source=src)
    else:
        return {"status": "ignored", "reason": f"unknown eventType {et}"}
    if args.get("generate") is not False and res.get("messageId"):
        res["jobId"] = str(res["messageId"])
    return res
