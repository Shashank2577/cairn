"""In-process API of the graph engine — what Cairn's sync, CLI, HTTP server and MCP call.

Every function takes the project ``root`` and an optional explicit ``out_dir``
(default ``<root>/.cairn/graph``). Nothing here spawns a separate server or
installs git hooks; model calls (docs/papers/images, community names, PR triage)
go through :mod:`cairn.router` via :mod:`cairn.engines.graph.llm`, and every
deterministic path works with no model at all.
"""
from __future__ import annotations

import contextlib
import io
import json
import threading
from pathlib import Path
from typing import Any

from cairn.engines.graph import paths as _paths

# Serialises captured CLI runs and the hook guard (sys.argv / stdin are
# process-wide). Always taken AFTER the output-directory lock (paths.output_dir),
# never before, so the two can never be acquired in opposite orders.
_CLI_LOCK = threading.RLock()


class _ThreadRoutedStream(io.TextIOBase):
    """Stands in for sys.stdout/sys.stderr while a capture is active: text written
    by a capturing thread goes to that thread's buffer, everything else to the real
    stream. So an in-process build running in one of sync's worker threads stays
    quiet without swallowing the progress other threads print."""

    def __init__(self, base):
        self._base = base
        self._targets: dict[int, io.StringIO] = {}

    def write(self, s):  # type: ignore[override]
        target = self._targets.get(threading.get_ident())
        return (target if target is not None else self._base).write(s)

    def flush(self):  # type: ignore[override]
        target = self._targets.get(threading.get_ident())
        (target if target is not None else self._base).flush()

    def __getattr__(self, name):
        return getattr(self._base, name)


_ROUTE_LOCK = threading.Lock()
_routes: dict[str, _ThreadRoutedStream] = {}
_route_users = 0


@contextlib.contextmanager
def _capture():
    """Capture this thread's stdout/stderr; yields ``(out, err)`` StringIO buffers."""
    import sys
    global _route_users
    out, err = io.StringIO(), io.StringIO()
    ident = threading.get_ident()
    with _ROUTE_LOCK:
        if _route_users == 0:
            _routes["stdout"] = _ThreadRoutedStream(sys.stdout)
            _routes["stderr"] = _ThreadRoutedStream(sys.stderr)
            sys.stdout, sys.stderr = _routes["stdout"], _routes["stderr"]
        _route_users += 1
        _routes["stdout"]._targets[ident] = out
        _routes["stderr"]._targets[ident] = err
    try:
        yield out, err
    finally:
        with _ROUTE_LOCK:
            _routes["stdout"]._targets.pop(ident, None)
            _routes["stderr"]._targets.pop(ident, None)
            _route_users -= 1
            if _route_users == 0:
                if sys.stdout is _routes["stdout"]:
                    sys.stdout = _routes["stdout"]._base
                if sys.stderr is _routes["stderr"]:
                    sys.stderr = _routes["stderr"]._base
                _routes.clear()


# ── locations ──────────────────────────────────────────────────────────────────

def out_dir(root: "str | Path", out: "str | Path | None" = None) -> Path:
    """The output directory for ``root`` (``<root>/.cairn/graph`` unless overridden)."""
    if out is not None:
        return Path(out).resolve()
    return (Path(root).resolve() / _paths.GRAPH_OUT).resolve()


def graph_json(root: "str | Path", out: "str | Path | None" = None) -> Path:
    return out_dir(root, out) / "graph.json"


def _explicit(root: "str | Path", out: "str | Path | None") -> "Path | None":
    """``out`` as an override, or None when it is just the configured location —
    so the common case never takes the output-dir lock (a long build would
    otherwise hold queries up)."""
    if out is None:
        return None
    resolved = Path(out).resolve()
    return None if resolved == out_dir(root) else resolved


# ── build ──────────────────────────────────────────────────────────────────────

def build(
    root: "str | Path",
    *,
    out: "str | Path | None" = None,
    force: bool = False,
    changed: "list[str | Path] | None" = None,
    no_cluster: bool = False,
    block: bool = True,
) -> dict:
    """Incrementally (re)build the code graph — ``cairn graph update``.

    Deterministic and local (tree-sitter ASTs, no model). ``changed`` limits
    re-extraction to those files (a commit's diff); ``None`` rebuilds the whole
    code corpus with the incremental cache. Writes ``graph.json``, the report,
    ``graph.html`` and the manifest into the output directory.

    Returns ``{"ok", "graph", "nodes", "edges", "communities", "summary"}``.
    """
    from cairn.engines.graph.watch import _rebuild_code

    import os
    from cairn.engines.graph.hooks import _viz_limit

    root = Path(root).resolve()
    out = _explicit(root, out)
    target = out_dir(root, out)
    limit = _viz_limit(root)
    set_limit = limit is not None and "CAIRN_GRAPH_VIZ_NODE_LIMIT" not in os.environ
    with _paths.output_dir(out), _capture() as (buf_out, buf_err):
        if set_limit:
            os.environ["CAIRN_GRAPH_VIZ_NODE_LIMIT"] = str(limit)
        try:
            ok = _rebuild_code(
                root,
                changed_paths=[Path(c) if Path(c).is_absolute() else root / c for c in changed]
                if changed is not None else None,
                force=force, no_cluster=no_cluster, block_on_lock=block,
            )
        except Exception as exc:  # noqa: BLE001 — surfaced in the result, never raised into sync
            print(f"[cairn graph] rebuild failed: {type(exc).__name__}: {exc}", file=buf_err)
            ok = False
        finally:
            from cairn.engines.graph.cache import release_stat_index
            release_stat_index()
            if set_limit:
                os.environ.pop("CAIRN_GRAPH_VIZ_NODE_LIMIT", None)
    gj = target / "graph.json"
    info = stats(gj) if gj.exists() else {"nodes": 0, "edges": 0, "communities": 0}
    log = (buf_out.getvalue() + buf_err.getvalue()).strip().splitlines()
    summary = next((ln.split("]", 1)[-1].strip() for ln in reversed(log)
                    if "nodes" in ln and "edges" in ln), "") or (log[-1] if log else "")
    return {"ok": bool(ok) and gj.exists(), "graph": str(gj), "summary": summary, "log": log[-40:], **info}


def extract(root: "str | Path", *, out: "str | Path | None" = None, args: "list[str] | None" = None) -> dict:
    """Full extraction — ``cairn graph extract``: code by AST plus documents, papers
    and images through the model router. ``args`` are extra CLI flags
    (``["--code-only"]``, ``["--mode", "deep"]``, ``["--backend", "claude-code"]`` ...).

    Returns ``{"code", "stdout", "stderr"}`` (``code`` 0 on success)."""
    from cairn.engines.graph.cache import release_stat_index
    argv = ["extract", str(Path(root).resolve()), *(args or [])]
    with _paths.output_dir(_explicit(root, out)):
        try:
            return run(argv, root=root)
        finally:
            release_stat_index()


# ── ask ────────────────────────────────────────────────────────────────────────

def run(argv: list[str], *, root: "str | Path | None" = None) -> dict:
    """Run any ``cairn graph`` subcommand in-process and capture its output.

    Serialised: the command line state (sys.argv) is process-wide, so two runs
    never interleave. ``root`` scopes model calls to that project's router.
    Returns ``{"code", "stdout", "stderr"}``."""
    from cairn.engines.graph.cli import main
    from cairn.engines.graph.llm import active_project

    import os
    with _paths.output_dir(None), _CLI_LOCK, active_project(root), _capture() as (out, err):
        # In-process means in-process: no hash-seed re-run in a child interpreter
        # (the console command does that); the output must land in these buffers.
        previous = os.environ.get("CAIRN_GRAPH_NO_REEXEC")
        os.environ["CAIRN_GRAPH_NO_REEXEC"] = "1"
        try:
            code = main(list(argv))
        finally:
            if previous is None:
                os.environ.pop("CAIRN_GRAPH_NO_REEXEC", None)
            else:
                os.environ["CAIRN_GRAPH_NO_REEXEC"] = previous
    return {"code": code, "stdout": out.getvalue(), "stderr": err.getvalue()}


def text(root: "str | Path", *args: str, out: "str | Path | None" = None) -> str:
    """Run a read-only subcommand (``query``/``path``/``explain``/``affected``/
    ``god-nodes`` ...) against the project's graph and return its text."""
    gj = graph_json(root, out)
    argv = list(args)
    if "--graph" not in argv and not any(a.startswith("--graph=") for a in argv):
        argv += ["--graph", str(gj)]
    res = run(argv, root=root)
    return (res["stdout"] or res["stderr"]).strip()


def query(root: "str | Path", question: str, *, budget: int = 2000, dfs: bool = False,
          context: "list[str] | None" = None, out: "str | Path | None" = None) -> str:
    """Scoped subgraph for a plain-language question (``cairn graph query``)."""
    args = ["query", question, "--budget", str(budget)]
    if dfs:
        args.append("--dfs")
    for c in context or []:
        args += ["--context", c]
    return text(root, *args, out=out)


def path(root: "str | Path", a: str, b: str, *, out: "str | Path | None" = None) -> str:
    """Shortest path between two concepts (``cairn graph path``)."""
    return text(root, "path", a, b, out=out)


def explain(root: "str | Path", target: str, *, out: "str | Path | None" = None) -> str:
    """A node and its neighbourhood in plain language (``cairn graph explain``)."""
    return text(root, "explain", target, out=out)


def load(root: "str | Path", *, out: "str | Path | None" = None):
    """The project's graph as a directed networkx graph (stored edge direction kept)."""
    from cairn.engines.graph.affected import load_graph
    return load_graph(graph_json(root, out))


def affected(root: "str | Path", target: str, *, depth: int = 2, relations: "list[str] | None" = None,
             out: "str | Path | None" = None) -> dict:
    """Reverse traversal from ``target``: what is impacted if it changes.

    Returns ``{"seed", "label", "hits": [{"id", "label", "file", "depth", "relation",
    "via_file", "via_location"}], "text"}`` (``seed`` None when no unique match)."""
    from cairn.engines.graph.affected import (
        DEFAULT_AFFECTED_RELATIONS, affected_nodes, format_affected, resolve_seed,
    )
    root = Path(root).resolve()
    G = load(root, out=out)
    rels = tuple(relations) if relations else DEFAULT_AFFECTED_RELATIONS
    seed = resolve_seed(G, target, root)
    txt = format_affected(G, target, relations=rels, depth=depth, root=root)
    if seed is None:
        return {"seed": None, "label": None, "hits": [], "text": txt}
    hits = [{
        "id": h.node_id,
        "label": G.nodes[h.node_id].get("label", h.node_id),
        "file": G.nodes[h.node_id].get("source_file"),
        "depth": h.depth,
        "relation": h.via_relation,
        "via_file": h.via_file,
        "via_location": h.via_location,
    } for h in affected_nodes(G, seed, relations=rels, depth=depth)]
    return {"seed": seed, "label": G.nodes[seed].get("label", seed), "hits": hits, "text": txt}


def god_nodes(root: "str | Path", *, top: int = 10, out: "str | Path | None" = None) -> list[dict]:
    """The most-connected real entities (``cairn graph god-nodes``)."""
    from cairn.engines.graph.analyze import god_nodes as _god
    return _god(load(root, out=out), top_n=top)


def stats(graph: "str | Path") -> dict:
    """Node/edge/community counts and the confidence mix of a graph.json."""
    try:
        data = json.loads(Path(graph).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"nodes": 0, "edges": 0, "communities": 0}
    nodes = data.get("nodes", [])
    links = data.get("links", data.get("edges", []))
    conf: dict[str, int] = {}
    for e in links:
        c = e.get("confidence", "EXTRACTED")
        conf[c] = conf.get(c, 0) + 1
    return {
        "nodes": len(nodes),
        "edges": len(links),
        "communities": len({n.get("community") for n in nodes if n.get("community") is not None}),
        "confidence": conf,
        "built_at_commit": data.get("built_at_commit"),
    }


def tools(root: "str | Path | None" = None, *, out: "str | Path | None" = None):
    """The graph query tools (query_graph, get_node, get_neighbors, get_community,
    god_nodes, graph_stats, shortest_path, list_prs, get_pr_impact, triage_prs) and
    resources, callable in-process — see :class:`cairn.engines.graph.serve.GraphTools`."""
    from cairn.engines.graph.serve import make_tools
    return make_tools(str(graph_json(root, out)) if root is not None else None)


def report(root: "str | Path", *, out: "str | Path | None" = None) -> str:
    """GRAPH_REPORT.md (hubs, surprising connections, communities, questions)."""
    p = out_dir(root, out) / "GRAPH_REPORT.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ── views (HTML strings; libraries bundled, see assets.py) ──────────────────────

def _communities(G) -> dict[int, list[str]]:
    comms: dict[int, list[str]] = {}
    for nid, d in G.nodes(data=True):
        if d.get("community") is not None:
            comms.setdefault(int(d["community"]), []).append(nid)
    return comms


def _labels(root: "str | Path", out: "str | Path | None") -> dict[int, str]:
    p = out_dir(root, out) / ".graph_labels.json"
    try:
        return {int(k): v for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}


def graph_view_html(root: "str | Path", *, asset_base: "str | None" = None, node_limit: "int | None" = None,
                    out: "str | Path | None" = None) -> "str | None":
    """Interactive graph page (vis-network). Above the node limit an aggregated
    community view is returned; None when that view would have < 2 communities."""
    from networkx.readwrite import json_graph
    from cairn.engines.graph.exporters.html import render_html, _viz_node_limit
    gj = graph_json(root, out)
    data = json.loads(gj.read_text(encoding="utf-8"))
    if "links" not in data and "edges" in data:
        data = dict(data, links=data["edges"])
    try:
        G = json_graph.node_link_graph(data, edges="links")
    except TypeError:
        G = json_graph.node_link_graph(data)
    hyper = data.get("hyperedges") or (data.get("graph") or {}).get("hyperedges")
    if hyper:
        G.graph["hyperedges"] = hyper
    labels = _labels(root, out)
    limit = node_limit if node_limit is not None else _viz_node_limit()
    return render_html(G, _communities(G), community_labels=labels or None,
                       node_limit=limit, asset_base=asset_base,
                       label_path=str(gj.with_name("graph.html")))


def tree_view_html(root: "str | Path", *, asset_base: "str | None" = None, max_children: int = 200,
                   out: "str | Path | None" = None) -> str:
    """Collapsible file/symbol tree (d3)."""
    from cairn.engines.graph.tree_html import render_tree_html
    data = json.loads(graph_json(root, out).read_text(encoding="utf-8"))
    return render_tree_html(data, max_children=max_children,
                            project_label=Path(root).resolve().name, asset_base=asset_base)


def callflow_view_html(root: "str | Path", *, asset_base: "str | None" = None, lang: str = "en",
                       max_sections: int = 15, out: "str | Path | None" = None) -> str:
    """Architecture and call-flow document with Mermaid diagrams."""
    from cairn.engines.graph.callflow_html import render_callflow_html
    html, _ = render_callflow_html(Path(root).resolve(), graph_out=out_dir(root, out), lang=lang,
                                   max_sections=max_sections, asset_base=asset_base)
    return html


def asset(name: str) -> "tuple[bytes, str]":
    """A bundled browser library by name → ``(bytes, media_type)`` (KeyError if unknown)."""
    from cairn.engines.graph.assets import ASSETS, asset_path
    return asset_path(name).read_bytes(), ASSETS[name]["media_type"]


# ── PRs ─────────────────────────────────────────────────────────────────────────

def prs(argv: "list[str] | None" = None, *, root: "str | Path | None" = None) -> int:
    """The PR dashboard (``cairn graph prs [--triage] [--worktrees] [--conflicts]
    [--wrong-base] [--base B] [--repo R] [--graph P] [N]``), printed to stdout.
    Needs the GitHub CLI. Returns the exit status."""
    from cairn.engines.graph.cli import main
    from cairn.engines.graph.llm import active_project
    argv = list(argv or [])
    if root is not None and "--graph" not in argv and not any(a.startswith("--graph=") for a in argv):
        argv += ["--graph", str(graph_json(root))]
    with active_project(root):
        return main(["prs", *argv])


def pr_list(root: "str | Path", *, base: "str | None" = None, repo: "str | None" = None) -> str:
    """Open PRs with CI, review state and graph impact, as text (needs ``gh``)."""
    args: dict[str, Any] = {}
    if base:
        args["base"] = base
    if repo:
        args["repo"] = repo
    return tools(root).call("list_prs", args)


# ── agent tool-use nudge ────────────────────────────────────────────────────────

def hook_guard(root: "str | Path", kind: str, payload: "dict | None" = None, *, strict: bool = False,
               out: "str | Path | None" = None) -> str:
    """The PreToolUse nudge toward the graph for one agent tool call.

    ``kind`` is ``"search"`` (Bash/Grep), ``"read"`` (Read/Glob) or ``"gemini"``
    (BeforeTool decision). Returns the hook's JSON output ('' when there is nothing
    to say): a nudge to run ``cairn graph query`` when a fresh graph covers the
    target, a softer note when the graph is stale for it, and — with ``strict`` —
    one denied raw Read per session. Never blocks search or listing."""
    import sys
    from cairn.engines.graph.cli import _run_hook_guard

    data = json.dumps(payload or {}).encode("utf-8")

    class _Stdin:
        buffer = io.BytesIO(data)

    root = Path(root).resolve()
    with _paths.output_dir(_explicit(root, out)), _CLI_LOCK, _capture() as (buf, _err):
        saved_stdin = sys.stdin
        sys.stdin = _Stdin()  # type: ignore[assignment]
        try:
            _run_hook_guard(kind, strict=strict, root=root)
        finally:
            sys.stdin = saved_stdin
    return buf.getvalue()
