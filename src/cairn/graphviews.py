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


ROW_CAP = 200   # the most rows one Browse level hands back; the page asks for the next page, never for all of them


def _file_table(idx: MapIndex) -> dict[str, dict]:
    """Per file: its symbol count, its three best-connected symbols and the files that depend on it.

    Built on first use and kept on the index (which is rebuilt whenever the map is), so the first level of Browse
    pays one pass over the edges and every later level reads dictionaries.
    """
    cached = getattr(idx, "_file_table", None)
    if cached is not None:
        return cached
    table: dict[str, dict] = {}
    for f, ids in idx.by_file.items():
        syms = [n for n in ids if idx.nodes[n].get("file_type") == "code" and "/" not in idx.label(n)
                and idx.label(n) != PurePosixPath(f).name]
        syms.sort(key=lambda n: (-idx.degree(n), idx.label(n)))
        table[f] = {"symbols": len(syms), "top": [(idx.degree(n), idx.label(n)) for n in syms[:3]], "dep": set()}
    for s, edges in idx.out.items():
        fs = idx.file_of(s)
        if not fs:
            continue
        for e in edges:
            ft = idx.file_of(e.other)
            if ft and ft != fs and e.rel not in STRUCTURE_RELS and ft in table:
                table[ft]["dep"].add(fs)
    idx._file_table = table  # type: ignore[attr-defined]
    return table


def _roll(table: dict[str, int] | None, files: list[str]) -> int:
    return sum((table or {}).get(f, 0) for f in files)


def browse(idx: MapIndex, path: str = "", facts: dict[str, dict[str, int]] | None = None, offset: int = 0,
           limit: int = ROW_CAP, sort: str = "name") -> dict:
    """One level of the repository: the folders and files directly under `path`, never the whole tree.

    Each row carries what makes it worth opening: file and symbol counts, its best-connected symbols, how many
    other files depend on it, and what Cairn knows about it (commits, past fixes, specs, memories). `facts` maps
    a fact name to {file: count}. At most `limit` (<= ROW_CAP) rows come back; `total` says how many exist.
    """
    facts = facts or {}
    path = path.strip("/")
    table = _file_table(idx)
    prefix = path + "/" if path else ""
    kids: dict[str, list[str]] = {}
    for f in table:
        if f.startswith(prefix):
            head, _, rest = f[len(prefix):].partition("/")
            kids.setdefault(head + ("/" if rest else ""), []).append(f)
    rows = []
    for name, files in kids.items():
        is_dir = name.endswith("/")
        under = set(files)
        dep: set[str] = set()
        top: list[tuple[int, str]] = []
        for f in files:
            dep |= table[f]["dep"]
            top += table[f]["top"]
        dep -= under
        symbols = sum(table[f]["symbols"] for f in files)
        fixes, commits = _roll(facts.get("fixes"), files), _roll(facts.get("commits"), files)
        heat = len(dep) + fixes * 4
        rows.append({"name": name.rstrip("/"), "path": prefix + name.rstrip("/"), "kind": "folder" if is_dir else "file",
                     "files": len(files), "symbols": symbols,
                     "top": [t for _, t in sorted(top, key=lambda x: (-x[0], x[1]))[:3]],
                     "dependents": len(dep), "commits": commits, "fixes": fixes,
                     "specs": _roll(facts.get("specs"), files), "memories": _roll(facts.get("memories"), files),
                     "risk": "high" if heat >= 40 else "medium" if heat >= 12 else "low", "heat": heat})
    if sort == "risk":
        rows.sort(key=lambda r: (-r["heat"], r["name"]))
    else:
        rows.sort(key=lambda r: (r["kind"] != "folder", r["name"].lower()))
    limit = max(1, min(limit, ROW_CAP))
    crumbs, acc = [], ""
    for part in path.split("/") if path else []:
        acc = f"{acc}/{part}" if acc else part
        crumbs.append({"name": part, "path": acc})
    return {"path": path, "crumbs": crumbs, "rows": rows[offset:offset + limit], "total": len(rows), "offset": offset,
            "files": sum(r["files"] for r in rows), "sort": sort}


def find(idx: MapIndex, q: str, limit: int = 12) -> dict:
    """Files, folders and symbols whose name contains `q`: the "find a file or area" box."""
    needle = q.strip().lower()
    if len(needle) < 2:
        return {"folders": [], "files": [], "symbols": []}
    folders: dict[str, int] = {}
    files = []
    for f in idx.by_file:
        low = f.lower()
        if needle in low:
            name = PurePosixPath(f).name.lower()
            files.append((0 if name.startswith(needle) else 1 if needle in name else 2, len(f), f))
        parts = f.split("/")[:-1]
        for i in range(len(parts)):
            d = "/".join(parts[:i + 1])
            if needle in parts[i].lower():
                folders[d] = folders.get(d, 0) + 1
    files.sort()
    syms = []
    for nid, n in idx.nodes.items():
        lbl = str(n.get("label") or "")
        if n.get("file_type") == "code" and needle in lbl.lower() and not idx.is_external(nid):
            syms.append((0 if lbl.lower().startswith(needle) else 1, -idx.degree(nid), nid))
    syms.sort()
    return {"folders": [{"path": d, "name": d.rsplit("/", 1)[-1]} for d in sorted(folders, key=lambda d: (len(d), d))[:limit]],
            "files": [{"path": f, "name": PurePosixPath(f).name} for _, _, f in files[:limit]],
            "symbols": [{"id": nid, "label": idx.label(nid), "file": idx.file_of(nid)} for _, _, nid in syms[:limit]]}


def focus(idx: MapIndex, nid: str, depth: int = 2, cap: int = 150) -> dict | None:
    """A node and what surrounds it, nearest first, never more than `cap` nodes.

    `total` is the size of the full neighbourhood within `depth` hops, so the page can say "showing 150 of 2,310"
    and offer more. Structure links (file contains symbol) are not walked: they would make every file a hub.
    """
    if nid.startswith("community:"):  # an area, not a node: its best-connected members stand for it
        members = sorted((n for n, d in idx.nodes.items() if str(d.get("community")) == nid[10:] and not idx.is_external(n)),
                         key=lambda n: (-idx.degree(n), n))
        keep = members[:max(1, cap)]
        return {"center": None, "nodes": [dict(_node(idx, n), hop=1) for n in keep], "links": _links(idx, set(keep)),
                "shown": len(keep), "total": len(members), "depth": 1, "by_hop": {"1": len(members)}} if members else None
    seeds = [nid]
    if nid not in idx.nodes and nid in idx.by_file:  # a file: its own symbols are the centre
        seeds = sorted((n for n in idx.by_file[nid] if idx.nodes[n].get("file_type") == "code"),
                       key=lambda n: (-idx.degree(n), n))[:40]
    seeds = [n for n in seeds if n in idx.nodes]
    if not seeds:
        return None
    depth = max(1, min(depth, 3))
    hops: dict[str, int] = {n: 0 for n in seeds}
    ring = list(seeds)
    for d in range(1, depth + 1):
        nxt = []
        for cur in ring:
            for e in (*idx.out.get(cur, ()), *idx.inc.get(cur, ())):
                if e.rel not in STRUCTURE_RELS and e.other in idx.nodes and e.other not in hops \
                        and not idx.is_external(e.other):
                    hops[e.other] = d
                    nxt.append(e.other)
        ring = nxt
    ordered = sorted(hops, key=lambda n: (hops[n], -idx.degree(n), n))
    keep = ordered[:max(1, cap)]
    nodes = [dict(_node(idx, n), hop=hops[n]) for n in keep]
    return {"center": nid if len(seeds) == 1 else None, "file": nid if len(seeds) > 1 or nid in idx.by_file else None, "nodes": nodes, "links": _links(idx, set(keep)), "shown": len(keep), "total": len(hops),
            "depth": depth, "by_hop": {str(h): sum(1 for v in hops.values() if v == h) for h in range(depth + 1)}}


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
