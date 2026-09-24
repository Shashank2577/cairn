"""MCP server: eight tools, terse descriptions (tool schemas cost tokens on every agent turn)."""
from __future__ import annotations

import json
from functools import lru_cache

from .core import Cairn
from .engines import mapper

INSTRUCTIONS = ("Project memory for this repository. Call cairn_context with your task before editing; "
                "cairn_impact before changing a file or symbol; cairn_remember for durable learnings.")


@lru_cache(maxsize=1)
def _cairn() -> Cairn:
    return Cairn.here()


def cairn_context(task: str, targets: list[str] | None = None, budget: int = 1800) -> str:
    """Call first. Ranked, cited context for a task: dependents, co-change, past incidents, owning spec, conventions, past agent work."""
    return _cairn().context(task, targets, budget).render()


def cairn_impact(target: str, depth: int = 2, budget: int = 1500) -> str:
    """What breaks if a file/symbol changes (e.g. 'src/pay.py', 'Client.send'): risk, dependents, tests, co-change, warnings, owners."""
    return _cairn().impact(target, depth, budget).render()


def cairn_why(target: str, budget: int = 1200) -> str:
    """Why code is the way it is: rationale comments, origin commits, spec task/requirement, decisions, sessions."""
    return _cairn().why(target, budget).render()


def cairn_search(query: str, kinds: list[str] | None = None, limit: int = 12) -> str:
    """Search across layers. kinds ⊆ symbol,file,spec,task,req,commit,memory,obs,fact."""
    hits = _cairn().search(query, kinds, limit)
    return "\n".join(f"- {h['title']} [{h['id']}]" + (f" {h['path']}" if h.get("path") else "") for h in hits) or "No matches."


def cairn_trace(mode: str, a: str, b: str = "") -> str:
    """Explore the code map. mode: 'explain' (a node and its neighbours), 'path' (a→b), 'query' (question)."""
    c = _cairn()
    if mode == "path":
        return mapper.engine_text(c.project.root, "path", a, b)
    if mode == "query":
        return mapper.engine_text(c.project.root, "query", a, "--budget", "1200")
    return mapper.engine_text(c.project.root, "explain", a)


def cairn_specs(spec: str = "", drift: bool = False) -> str:
    """Spec board (features, stories, task progress, file trace) or, with drift=true, where code disagrees with specs."""
    c = _cairn()
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
    return "\n".join(out) or "No specs yet. Start with /speckit-constitution and /speckit-specify."


def cairn_remember(text: str, kind: str = "fact", supersedes: str = "") -> str:
    """Store a durable learning. kind: convention|decision|gotcha|preference|fact. One precise sentence."""
    res = _cairn().remember(text, kind, supersedes or None, source="agent")
    return json.dumps(res)


def cairn_recall(query: str, limit: int = 8) -> str:
    """Recall team memories (and related session learnings) about a topic."""
    c = _cairn()
    mems = c.memory.recall(query, limit)
    lines = [f"- [{m['kind']}] {m['text']} [memory:{m['id']}]" for m in mems]
    for h in c.brain.search(query, kinds=["obs"], limit=3):
        lines.append(f"- (session) {h['title']} [{h['id']}]")
    return "\n".join(lines) or "Nothing remembered about that yet."


TOOLS = [cairn_context, cairn_impact, cairn_why, cairn_search, cairn_trace, cairn_specs, cairn_remember, cairn_recall]


def build():
    try:
        from mcp.server.mcpserver import MCPServer as Server  # SDK v2
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server  # SDK v1
    server = Server(name="cairn", instructions=INSTRUCTIONS)
    for fn in TOOLS:
        server.tool()(fn)
    return server


def main() -> None:
    build().run("stdio")
