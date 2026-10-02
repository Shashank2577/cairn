"""MCP server: the tools agents call.

A compact core set is exposed by default, because every tool's schema costs tokens on every agent turn. Setting
`[mcp] tools = "all"` in `.cairn/config.toml` also exposes every graph, timeline-fact and session operation.
Locally this runs over stdio (agents launch `cairn mcp`); a team server serves the same tools over HTTP per
project (`/mcp/<project id>`, bearer token), see `server.py`.
"""
from __future__ import annotations

import contextvars
import inspect
import json
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from .core import Cairn, ago
from .engines import mapper

INSTRUCTIONS = ("Project memory for this repository. Call cairn_context with your task before editing; "
                "cairn_impact before changing a file or symbol; cairn_remember for durable learnings; "
                "cairn_session_search (then cairn_session_timeline, cairn_session_observations) to find what earlier "
                "agent sessions did.")

# Which project a tool call is about: the repository the agent runs in (stdio), or the project an HTTP
# endpoint serves (set per call by `bind`).
_RESOLVER: contextvars.ContextVar[Callable[[], Cairn] | None] = contextvars.ContextVar("cairn_mcp_project",
                                                                                       default=None)


@lru_cache(maxsize=1)
def _cairn() -> Cairn:
    c = Cairn.here()
    c.surface = "mcp"
    return c


def _project() -> Cairn:
    resolver = _RESOLVER.get()
    return resolver() if resolver else _cairn()


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, indent=1, default=str)


# ---- core tools ------------------------------------------------------------------------------------------
def cairn_context(task: str, targets: list[str] | None = None, budget: int = 1800) -> str:
    """Call first. Ranked, cited context for a task: dependents, co-change, past incidents, owning spec, conventions, past agent work."""
    return _project().context(task, targets, budget).render()


def cairn_status() -> str:
    """Project health in one call: layer counts, active spec progress, drift findings, last sync, model availability. Read-only."""
    c = _project()
    o = c.overview()
    L = o["layers"]
    lines = [f"# {o['project']}",
             f"model: {'available' if c.router.available else 'none (deterministic layers only)'}",
             f"last sync: {ago(o['last_sync']) or 'never'}"
             + (f" — failed: {', '.join(sorted(o['sync_failed']))}" if o["sync_failed"] else "")]
    lines.append(f"map: {L['map']['nodes']} nodes, {L['map']['files']} files")
    if L["specs"]["features"]:
        lines.append(f"specs: {L['specs']['features']} features, {L['specs']['done']}/{L['specs']['tasks']} tasks done")
    if o["active_spec"]:
        a = o["active_spec"]
        lines.append(f"active spec: {a['id'][5:]} — {a['name']} ({a['done']}/{a['total']} tasks)")
    lines.append(f"timeline: {L['timeline']['commits']} commits, {L['timeline']['warnings']} risk warnings")
    lines.append(f"memory: {L['memory']['memories']} memories; sessions: {L['sessions']['observations']} observations "
                 f"in {L['sessions']['sessions']} sessions")
    lines.append(f"drift: {o['drift']} open findings")
    return "\n".join(lines)


def cairn_impact(target: str, depth: int = 2, budget: int = 1500) -> str:
    """What breaks if a file/symbol changes (e.g. 'src/pay.py', 'Client.send'): risk, dependents, tests, co-change, warnings, owners."""
    return _project().impact(target, depth, budget).render()


def cairn_why(target: str, budget: int = 1200) -> str:
    """Why code is the way it is: rationale comments, origin commits, spec task/requirement, decisions, sessions."""
    return _project().why(target, budget).render()


def cairn_search(query: str, kinds: list[str] | None = None, limit: int = 12) -> str:
    """Search across layers. kinds ⊆ symbol,file,spec,task,req,commit,memory,obs,fact."""
    hits = _project().search(query, kinds, limit)
    return "\n".join(f"- {h['title']} [{h['id']}]" + (f" {h['path']}" if h.get("path") else "") for h in hits) or "No matches."


def cairn_trace(mode: str, a: str, b: str = "") -> str:
    """Explore the code map. mode: 'explain' (a node and its neighbours), 'path' (a→b), 'query' (question)."""
    c = _project()
    if mode == "path":
        return mapper.engine_text(c.project.root, "path", a, b)
    if mode == "query":
        return mapper.engine_text(c.project.root, "query", a, "--budget", "1200")
    return mapper.engine_text(c.project.root, "explain", a)


def cairn_specs(spec: str = "", drift: bool = False) -> str:
    """Spec board (features, stories, task progress, file trace) or, with drift=true, where code disagrees with specs."""
    c = _project()
    if drift:
        from . import drift as d
        found = d.check(c, spec or None)
        return "\n".join(f"- [{f['severity']}] {f['title']} [{f['cite']}] — {'; '.join(f['evidence'][:2])}"
                         for f in found) or "No drift found."
    from .engines import specs
    out = []
    for f in specs.features(c.project.root):
        if spec and not f["id"].startswith(spec):
            continue
        p = f["progress"]
        out.append(f"## {f['id']} — {f['title']} ({p['done']}/{p['total']} tasks)")
        if spec:
            out += [f"- [{'x' if t['done'] else ' '}] {t['id']} {t['text'][:110]}" for t in f["tasks"]]
    return "\n".join(out) or "No specs yet. Start with /cairn-constitution and /cairn-specify."


def cairn_remember(text: str, kind: str = "fact", supersedes: str = "", scope: str = "project") -> str:
    """Store a durable learning. kind: convention|decision|gotcha|preference|fact. One precise sentence. scope: project|team|user."""
    res = _project().remember(text, kind, supersedes or None, source="agent", scope=scope)
    return json.dumps(res)


def cairn_recall(query: str, limit: int = 8) -> str:
    """Recall team memories (and related session learnings) about a topic."""
    c = _project()
    mems = c.memory.recall(query, limit)
    lines = [f"- [{m['kind']}] {m['text']} [memory:{m['id']}]" for m in mems]
    for h in c.brain.search(query, kinds=["obs"], limit=3):
        lines.append(f"- (session) {h['title']} [{h['id']}]")
    return "\n".join(lines) or "Nothing remembered about that yet."


def cairn_facts(query: str, at: str = "", limit: int = 12) -> str:
    """What was true about something and when: facts with valid-from/until windows. at=YYYY-MM-DD answers 'as of that date'."""
    from .engines.temporal import TemporalService, run_sync
    c = _project()
    svc = TemporalService(c.project, c.router, c.brain)
    rows = run_sync(svc.facts_at(at, query, max_facts=limit) if at else svc.search_facts(query, max_facts=limit))
    if not rows:
        return "No facts recorded about that yet (facts are built from repository activity when a model is available)."
    return "\n".join(f"- {f['fact']} ({(f.get('valid_at') or '?')[:10]} → "
                     f"{(f.get('invalid_at') or 'now')[:10]}) [fact:{f['uuid']}]" for f in rows)


CORE: list[Callable[..., str]] = [cairn_context, cairn_status, cairn_impact, cairn_why, cairn_search, cairn_trace,
                                  cairn_specs, cairn_remember, cairn_recall, cairn_facts]
WRITES = {"cairn_remember", "cairn_build_corpus", "cairn_rebuild_corpus", "cairn_prime_corpus",
          "cairn_reprime_corpus", "cairn_query_corpus"}  # change stored data or spend the model budget
# Pull-request tools run the host's GitHub CLI with the host's login: not offered over the network.
NETWORK_HIDDEN = {"cairn_graph_list_prs", "cairn_graph_get_pr_impact", "cairn_graph_triage_prs"}
TOOLS = CORE  # kept for callers that list the core set


# ---- tools generated from the engines' own specs -----------------------------------------------------------
_JSON_TYPES = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}
_DOC_TYPES = {"str": str, "int": int, "float": float, "bool": bool, "dict": dict}


def _make_tool(name: str, description: str, params: list[tuple[str, type, bool, Any]],
               call: Callable[[dict], Any]) -> Callable[..., str]:
    """A plain function with a real signature (so the MCP library can derive the schema) that forwards its
    arguments to `call`."""
    def tool(**kwargs):
        return _text(call({k: v for k, v in kwargs.items() if v is not None}))
    parameters, notes = [], {}
    for pname, typ, required, default in sorted(params, key=lambda p: not p[2]):
        ann = typ if required else (typ | None)
        parameters.append(inspect.Parameter(pname, inspect.Parameter.KEYWORD_ONLY,
                                            default=inspect.Parameter.empty if required else default,
                                            annotation=ann))
        notes[pname] = ann
    tool.__signature__ = inspect.Signature(parameters, return_annotation=str)
    tool.__annotations__ = {**notes, "return": str}
    tool.__name__ = name
    tool.__doc__ = description
    return tool


def _graph_tools() -> list[Callable[..., str]]:
    from .engines.graph import api as graph_api
    out = []
    for spec in graph_api.tools(None).tools():
        schema = spec.get("input_schema") or {}
        req = set(schema.get("required", []))
        params = [(p, _JSON_TYPES.get(v.get("type"), str), p in req, v.get("default"))
                  for p, v in schema.get("properties", {}).items() if p != "project_path"]
        tool_name = spec["name"]

        def call(args: dict, _n: str = tool_name) -> str:
            return graph_api.tools(_project().project.root).call(_n, args)
        out.append(_make_tool(f"cairn_graph_{tool_name}", spec.get("description", ""), params, call))
    return out


def _doc_type(spec: str) -> tuple[type, bool, Any]:
    """'str', 'str (text) a|b', 'list[str]|null', 'bool|null' -> (type, required, default)."""
    head = spec.split(" ", 1)[0]
    optional = "|null" in head
    base = head.replace("|null", "")
    typ = list if base.startswith("list") else _DOC_TYPES.get(base, str)
    m = re.search(r"\(([^)]*)\)", spec)
    default = None
    if m:
        raw = m.group(1)
        default = {"true": True, "false": False}.get(raw.lower(), int(raw) if raw.isdigit() else raw)
    return typ, not optional and m is None, default


def _timeline_tools() -> list[Callable[..., str]]:
    from .engines.temporal import TemporalService
    from .engines.temporal.api import TOOLS as SPECS
    from .engines.temporal.api import call_tool_sync
    out = []
    for spec in SPECS:
        params = [(p, *_doc_type(t)) for p, t in spec.params.items()]

        def call(args: dict, _n: str = spec.name) -> Any:
            c = _project()
            if _RESOLVER.get() is not None:  # served to other people: this project's facts only
                args = {k: v for k, v in args.items() if k not in ("group_id", "group_ids")}
            return call_tool_sync(TemporalService(c.project, c.router, c.brain), _n, args)
        fn = _make_tool(spec.name, spec.description, params, call)
        if spec.writes:
            WRITES.add(spec.name)
        out.append(fn)
    return out


SESSION_CORE = {"recall_search", "recall_timeline", "get_observations"}  # search → timeline → full detail
SESSION_NAMES = {"recall_search": "cairn_session_search", "recall_timeline": "cairn_session_timeline",
                 "get_observations": "cairn_session_observations", "get_tool_uses": "cairn_session_tool_uses",
                 "session_start_context": "cairn_session_context", "important_workflow": "cairn_session_workflow"}


def session_tools(mode: str = "core") -> list[Callable[..., str]]:
    """The session memory engine's tools: the search workflow by default, everything with mode "all"."""
    from .engines.recall import mcp as recall_mcp
    out = []
    for spec in recall_mcp.advertised_tools("local"):
        name = spec["name"]
        if mode != "all" and name not in SESSION_CORE:
            continue
        schema = spec.get("input_schema") or spec.get("inputSchema") or {}
        req = set(schema.get("required", []))
        params = [(p, _JSON_TYPES.get(v.get("type"), str), p in req, v.get("default"))
                  for p, v in schema.get("properties", {}).items()]

        def call(args: dict, _n: str = name) -> str:
            res = recall_mcp.call_tool(_project().project.root, _n, args)
            text = "\n".join(part.get("text", "") for part in res.get("content", []) if isinstance(part, dict))
            return f"Error: {text}" if res.get("isError") else text
        out.append(_make_tool(SESSION_NAMES.get(name, f"cairn_{name}"), spec.get("description", ""), params, call))
    return out


def all_tools(mode: str = "core") -> list[Callable[..., str]]:
    tools = list(CORE) + session_tools(mode)
    if mode == "all":
        tools += _graph_tools() + _timeline_tools()
    return tools


def bind(fn: Callable[..., str], resolver: Callable[[], Cairn]) -> Callable[..., str]:
    """The same tool, answering about the project `resolver` returns (used by the HTTP endpoint)."""
    def bound(**kwargs):
        token = _RESOLVER.set(resolver)
        try:
            return fn(**kwargs)
        finally:
            _RESOLVER.reset(token)
    bound.__signature__ = inspect.signature(fn)
    bound.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    bound.__name__ = fn.__name__
    bound.__doc__ = fn.__doc__
    return bound


def build(*, mode: str | None = None, resolver: Callable[[], Cairn] | None = None, read_only: bool = False):
    try:
        from mcp.server.mcpserver import MCPServer as Server  # SDK v2
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server  # SDK v1
    if mode is None:
        try:
            mode = str(_project().project.cfg("mcp.tools", "core"))
        except SystemExit:
            mode = "core"
    server = Server(name="cairn", instructions=INSTRUCTIONS)
    for fn in all_tools(mode):
        if read_only and fn.__name__ in WRITES:
            continue
        if resolver is not None and fn.__name__ in NETWORK_HIDDEN:
            continue
        server.tool()(bind(fn, resolver) if resolver else fn)
    return server


def main() -> None:
    build().run("stdio")
