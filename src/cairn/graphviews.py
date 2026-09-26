"""Graph data for the page: the whole map or a slice of it, one node's neighbourhood, and paths between nodes.

Everything reads the in-memory `MapIndex` (built from `.cairn/graph/graph.json`), so answers are dictionary walks.
"""
from __future__ import annotations

from collections import Counter, deque
from pathlib import PurePosixPath

from .engines.mapper import STRUCTURE_RELS, MapIndex, key


def summary(idx: MapIndex, built_at: float | None) -> dict:
    return {"nodes": len(idx), "edges": idx.edge_count(), "files": len(idx.by_file), "communities": idx.areas(),
            "languages": idx.languages(), "built_at": built_at, "built_at_commit": idx.built_at_commit}


def _node(idx: MapIndex, nid: str) -> dict:
    n = idx.nodes.get(nid, {})
    return {"id": nid, "label": idx.label(nid), "kind": n.get("file_type") or n.get("type") or "code",
            "file": idx.file_of(nid), "community": n.get("community"), "community_name": n.get("community_name"),
            "degree": idx.degree(nid), "location": n.get("source_location")}


def _links(idx: MapIndex, keep: set[str]) -> list[dict]:
    seen: dict[tuple[str, str, str], dict] = {}
    for s in keep:
        for e in idx.out.get(s, ()):
            if e.other in keep and e.other != s:
                k = (s, e.other, e.rel)
                if k in seen:
                    seen[k]["weight"] += 1
                else:
                    seen[k] = {"source": s, "target": e.other, "rel": e.rel, "weight": 1, "provenance": e.confidence}
    return list(seen.values())


def communities(idx: MapIndex, limit: int = 80) -> dict:
    """The map at a glance: one node per community (sized by members, named after its most connected code) and
    one link per pair of communities that depend on each other, weighted by how many links run between them."""
    g = idx.area_graph(limit)
    wanted = {a["id"] for a in g["nodes"]}
    files: dict[int, Counter] = {c: Counter() for c in wanted}
    for n in idx.nodes.values():
        c, f = n.get("community"), n.get("source_file")
        if c in wanted and f:
            files[c][f] += 1

    def area(counter: Counter) -> str:
        dirs = Counter()
        for f, k in counter.items():
            dirs[str(PurePosixPath(f).parent)] += k
        return dirs.most_common(1)[0][0] if dirs else ""
    nodes = [{"id": f"community:{a['id']}", "community": a["id"], "label": a["name"], "kind": "community",
              "size": a["size"], "top": a["top"], "degree": a["size"], "area": area(files[a["id"]]),
              "files": [[f, k] for f, k in files[a["id"]].most_common(3)]} for a in g["nodes"]]
    links = [{"source": f"community:{l['source']}", "target": f"community:{l['target']}", "rel": "depends_on",
              "weight": l["weight"]} for l in g["links"]]
    return {"nodes": nodes, "links": links, "truncated": len(idx.areas()) > limit, "total": len(idx.areas()),
            "level": "communities"}


def data(idx: MapIndex, scope: str = "all", limit: int = 600) -> dict:
    """Nodes and links for `scope`: communities | all | community:<id> | file:<path or folder> | around:<node id>."""
    if scope == "communities":
        return communities(idx, min(limit, 200))
    kind, _, arg = scope.partition(":")
    if kind == "community":
        ids = [n for n, d in idx.nodes.items() if str(d.get("community")) == arg]
    elif kind == "file" and arg in idx.by_file:
        base = list(idx.by_file[arg])
        ids = base + [e.other for n in base for e in (*idx.out.get(n, ()), *idx.inc.get(n, ()))
                      if e.rel not in STRUCTURE_RELS]
    elif kind == "file":  # a folder: everything in the files under it
        prefix = arg.strip("/") + "/"
        ids = [n for f, ns in idx.by_file.items() if f.startswith(prefix) for n in ns]
    elif kind == "around":
        ids = list(_neighbourhood(idx, arg, depth=2, cap=limit))
    else:
        ids = [n for n, d in idx.nodes.items() if d.get("file_type") != "rationale"]
    ids = list(dict.fromkeys(i for i in ids if i in idx.nodes))
    total = len(ids)
    if total > limit:
        if kind == "around":
            ids = ids[:limit]
        else:
            ids.sort(key=idx.degree, reverse=True)
            ids = ids[:limit]
    keep = set(ids)
    return {"nodes": [_node(idx, n) for n in ids], "links": _links(idx, keep), "truncated": total > len(ids),
            "total": total}


def _neighbourhood(idx: MapIndex, nid: str, depth: int, cap: int) -> list[str]:
    if nid not in idx.nodes:
        return []
    out, seen, q = [nid], {nid}, deque([(nid, 0)])
    while q and len(out) < cap:
        cur, d = q.popleft()
        if d >= depth:
            continue
        for e in (*idx.out.get(cur, ()), *idx.inc.get(cur, ())):
            if e.other not in seen and e.other in idx.nodes:
                seen.add(e.other)
                out.append(e.other)
                q.append((e.other, d + 1))
    return out


def node(idx: MapIndex, nid: str) -> dict | None:
    if nid not in idx.nodes:
        return None
    neighbours = []
    for direction, edges in (("out", idx.out.get(nid, ())), ("in", idx.inc.get(nid, ()))):
        for e in edges:
            if e.other in idx.nodes:
                neighbours.append({"id": e.other, "label": idx.label(e.other), "rel": e.rel, "direction": direction,
                                   "file": idx.file_of(e.other), "kind": _node(idx, e.other)["kind"],
                                   "provenance": e.confidence, "location": e.location})
    return {"node": _node(idx, nid), "neighbours": neighbours[:300], "rationale": idx.rationale([nid], limit=8)}


def path(idx: MapIndex, a: str, b: str, max_hops: int = 8) -> dict:
    """Shortest connection between two nodes, following links in either direction."""
    src, dst = (idx.resolve(x, limit=1) for x in (a, b))
    if not src or not dst:
        return {"path": [], "nodes": [], "links": [], "found": False,
                "missing": [x for x, r in ((a, src), (b, dst)) if not r]}
    start, goal = src[0], dst[0]
    prev: dict[str, tuple[str, dict] | None] = {start: None}
    q = deque([(start, 0)])
    while q:
        cur, d = q.popleft()
        if cur == goal or d >= max_hops:
            continue
        for e in idx.out.get(cur, ()):
            if e.other not in prev:
                prev[e.other] = (cur, {"source": cur, "target": e.other, "rel": e.rel})
                q.append((e.other, d + 1))
        for e in idx.inc.get(cur, ()):
            if e.other not in prev:
                prev[e.other] = (cur, {"source": e.other, "target": cur, "rel": e.rel})
                q.append((e.other, d + 1))
    if goal not in prev:
        return {"path": [], "nodes": [_node(idx, start), _node(idx, goal)], "links": [], "found": False}
    ids, links, cur = [goal], [], goal
    while prev[cur] is not None:
        before, link = prev[cur]
        links.append(link)
        ids.append(before)
        cur = before
    ids.reverse()
    links.reverse()
    return {"path": ids, "nodes": [_node(idx, n) for n in ids], "links": links, "found": True}


def nodes_in_text(idx: MapIndex, text: str, limit: int = 40) -> list[str]:
    """Node ids for the `NODE <label> [src=<file> …` lines a graph query prints."""
    import re
    out: list[str] = []
    for m in re.finditer(r"^NODE (.+?) \[src=([^\s\]]+)", text, re.MULTILINE):
        label, src = m.group(1).strip(), m.group(2).split(":", 1)[0]
        for nid in idx.by_label.get(key(label), []):
            if idx.file_of(nid) == src and nid not in out:
                out.append(nid)
                break
        if len(out) >= limit:
            break
    return out
