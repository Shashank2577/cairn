"""Map layer: the structural graph of code, docs, schemas and rationale.

Extraction runs in-process in the graph engine (:mod:`cairn.engines.graph`: tree-sitter
ASTs, deterministic, local, no model). Cairn reads the engine's node-link JSON into an
in-memory index (cached by mtime) so impact and neighbourhood queries are pure dictionary
walks.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Edges along which a change propagates to the *source* of the edge (source depends on target).
DEPENDENCY_RELS = frozenset({"calls", "indirect_call", "imports", "imports_from", "inherits", "uses",
                             "references", "re_exports", "mixes_in", "depends_on", "implements"})
STRUCTURE_RELS = frozenset({"contains", "method", "defines"})
CODE_TYPES = frozenset({"code"})
FILE_EXTS = frozenset([".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".rb", ".php", ".cs", ".c", ".h", ".cc", ".cpp", ".hpp", ".swift", ".m", ".mm", ".lua", ".ex", ".exs", ".erl", ".hs", ".ml", ".dart", ".vue", ".svelte", ".sql", ".sh", ".bash", ".md", ".mdx", ".rst", ".txt", ".json", ".yaml", ".yml", ".toml", ".tf", ".proto", ".graphql", ".html", ".css", ".scss"])


def key(label: str) -> str:
    """Normalise a label for lookup: '.send()' / 'send()' / 'Send' -> 'send'."""
    return label.strip().strip("`").lower().lstrip(".").removesuffix("()")


# Agent tool folders are not part of the system (.cairn/ itself is always skipped).
IGNORE_FILE = ".cairn/graphignore"
IGNORE_BLOCK = "# cairn: tool folders are not part of the system\n.claude/\n.cursor/\n.gemini/\n"
# Generated and third-party code: bundles, minified assets and vendored libraries describe someone else's system.
IGNORE_GENERATED = ("# cairn: generated and vendored code\n*.min.js\n*.min.css\n*.bundle.js\n*.map\n"
                    "vendor/\nnode_modules/\ndist/\n")


def ensure_ignores(root: Path) -> None:
    ig = Path(root) / IGNORE_FILE
    text = ig.read_text() if ig.exists() else ""
    add = "".join(block for block, mark in ((IGNORE_BLOCK, "# cairn: tool folders"),
                                             (IGNORE_GENERATED, "# cairn: generated")) if mark not in text)
    if add:
        ig.parent.mkdir(parents=True, exist_ok=True)
        ig.write_text(text + ("\n" if text and not text.endswith("\n") else "") + add)


def map_dir(root: Path) -> Path:
    """Where the project's map lives (the project's configured map directory)."""
    from ..project import Project
    return Project(root=Path(root).resolve()).map_dir


FINGERPRINT = "fingerprint.json"
INCREMENTAL_LIMIT = 150  # more changed files than this: one incremental pass over the whole corpus instead


def _corpus(root: Path) -> dict[str, list[int]] | None:
    """Every file git knows about or would add (tracked + untracked, not ignored): path -> [mtime_ns, size]."""
    try:
        res = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "-co", "--exclude-standard"],
                             capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    out: dict[str, list[int]] = {}
    for rel in res.stdout.decode(errors="replace").split("\0"):
        if not rel or rel.startswith(".cairn/"):
            continue
        try:
            st = (root / rel).stat()
        except OSError:
            continue
        out[rel] = [st.st_mtime_ns, st.st_size]
    return out


def _rules_digest(root: Path) -> int:
    """A digest of everything besides the files themselves that decides what the map contains."""
    import zlib

    from .. import __version__
    parts = [__version__]
    for ig in (root / IGNORE_FILE, root / ".gitignore"):
        try:
            parts.append(ig.read_text(errors="replace"))
        except OSError:
            parts.append("")
    try:
        import tomllib
        parts.append(json.dumps((tomllib.loads((root / ".cairn" / "config.toml").read_text()).get("map") or {}),
                                sort_keys=True))
    except (OSError, ValueError):
        parts.append("")
    return zlib.crc32("\x00".join(parts).encode())


def _user_facing_error(summary: str, root: Path) -> str:
    """Translate the graph engine's own CLI-flavored failure message into terms
    a person reading the sync result on the web page understands: no
    ``[cairn graph]`` prefix, and — since the page has no way to pass a
    command-line flag — the actual terminal command to run instead of
    ``Pass --force to override.``"""
    text = re.sub(r"^\[cairn graph\]\s*(WARNING:\s*)?", "", summary)
    return text.replace("Pass --force to override.",
                         f"Run `cairn graph update {root} --force` from a terminal to override.")


def build(root: Path, force: bool = False, out_dir: Path | None = None) -> tuple[bool, str]:
    """(Re)build the map. Nothing changed: skipped. A few files changed: only those are re-extracted.
    Otherwise one incremental pass over the corpus. Deterministic, local, no model calls."""
    from .graph import api
    root = Path(root)
    ensure_ignores(root)
    target = Path(out_dir or map_dir(root))
    now = _corpus(root)
    if now is not None:
        now["\x00rules"] = [_rules_digest(root), 0]  # ignore rules or engine changed: the whole map is redone
    fp = target / FINGERPRINT
    if not force and now is not None and fp.exists() and (target / "graph.json").exists():
        try:
            before = json.loads(fp.read_text())
        except ValueError:
            before = None
        if isinstance(before, dict):
            if before.get("\x00rules") != now["\x00rules"]:
                before = {}  # nothing in the old map can be trusted to follow the new rules
            changed = [p for p, v in now.items() if before.get(p) != v and not p.startswith("\x00")]
            removed = [p for p in before if p not in now]
            if not changed and not removed:
                return True, "unchanged"
            if not removed and len(changed) <= INCREMENTAL_LIMIT:
                from .graph.detect import ignored_predicate
                ignored = ignored_predicate(root.resolve())
                wanted = [p for p in changed if not ignored(root.resolve() / p)]
                if not wanted:
                    fp.write_text(json.dumps(now))
                    return True, "unchanged (only ignored files changed)"
                res = api.build(root, out=target, changed=wanted)
                if res["ok"]:
                    fp.write_text(json.dumps(now))
                    return True, f"{len(wanted)} changed file{'s' if len(wanted) != 1 else ''} re-mapped; " + (
                        res["summary"] or f"{res['nodes']:,} nodes")
    res = api.build(root, out=target, force=force)
    if res["ok"]:
        if now is not None:
            target.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps(now))
        return True, res["summary"] or f"{res['nodes']:,} nodes, {res['edges']:,} edges"
    return False, _user_facing_error(res["summary"], root) if res["summary"] else "map build failed"


def engine_text(root: Path, *args: str, timeout: int = 60, out_dir: Path | None = None) -> str:
    """Run a read-only map query (path/explain/query/affected/god-nodes) and return its text."""
    del timeout  # in-process; kept for callers of the old signature
    from .graph import api
    try:
        return api.text(root, *args, out=out_dir or map_dir(root))
    except Exception as exc:  # noqa: BLE001 — a query never takes the caller down
        return f"(map query failed: {exc})"


@dataclass
class Edge:
    other: str
    rel: str
    confidence: str
    location: str | None


@dataclass
class MapIndex:
    nodes: dict[str, dict] = field(default_factory=dict)
    out: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    inc: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    by_label: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    by_file: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    built_at_commit: str | None = None
    mtime: float = 0.0

    # ---- loading --------------------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> MapIndex:
        idx = cls()
        if not path.exists():
            return idx
        data = json.loads(path.read_text())
        idx.mtime = path.stat().st_mtime
        idx.built_at_commit = data.get("built_at_commit")
        for n in data.get("nodes", []):
            nid = n["id"]
            idx.nodes[nid] = n
            k = key(n.get("label", ""))
            if k:
                idx.by_label[k].append(nid)
            if n.get("source_file"):
                idx.by_file[n["source_file"]].append(nid)
        for e in data.get("links", data.get("edges", [])):
            s, t, rel = e["source"], e["target"], e.get("relation", "related")
            conf = e.get("confidence", "EXTRACTED")
            loc = e.get("source_location")
            idx.out[s].append(Edge(t, rel, conf, loc))
            idx.inc[t].append(Edge(s, rel, conf, loc))
        return idx

    # ---- basics ---------------------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.nodes)

    def edge_count(self) -> int:
        return sum(len(v) for v in self.out.values())

    def label(self, nid: str) -> str:
        n = self.nodes.get(nid, {})
        return n.get("label") or nid

    def file_of(self, nid: str) -> str | None:
        return self.nodes.get(nid, {}).get("source_file")

    def is_code(self, nid: str) -> bool:
        return self.nodes.get(nid, {}).get("file_type") in CODE_TYPES

    def degree(self, nid: str) -> int:
        return sum(1 for e in self.out.get(nid, ()) if e.rel not in STRUCTURE_RELS) + \
            sum(1 for e in self.inc.get(nid, ()) if e.rel not in STRUCTURE_RELS)

    def describe(self, nid: str) -> str:
        n = self.nodes.get(nid, {})
        loc = n.get("source_location") or ""
        f = n.get("source_file") or ""
        return f"{self.label(nid)} ({f}{':' + loc if loc else ''})" if f else self.label(nid)

    # ---- resolution -----------------------------------------------------------------------------
    def resolve(self, target: str, limit: int = 5) -> list[str]:
        """Map a user string (id, path, label, Class.method, partial) to node ids, best first."""
        t = target.strip().strip("`").removesuffix("()")
        if not t:
            return []
        if t in self.nodes:
            return [t]
        cands: list[str] = []
        norm = t.replace("\\", "/").lstrip("./")
        if norm in self.by_file:  # a file: prefer its file-level node(s), else its symbols
            ids = self.by_file[norm]
            files = [i for i in ids if self.label(i) in (Path(norm).name, norm)]
            return (files or ids)[:limit]
        dotted = "." in t.lstrip(".") and "/" not in t and Path(t).suffix.lower() not in FILE_EXTS
        tail = t.split(".")[-1] if dotted else t
        for k in dict.fromkeys((key(t), key(tail))):
            cands += self.by_label.get(k, [])
        if dotted and cands:  # Class.method -> prefer members whose container matches
            owner = key(t.split(".")[-2])
            scoped = [c for c in cands if any(key(self.label(e.other)) == owner for e in self.inc.get(c, ())
                                              if e.rel in STRUCTURE_RELS)]
            cands = scoped or cands
        if not cands:
            low = t.lower()
            cands = [k for k, n in self.nodes.items() if low in (n.get("label") or "").lower()][:50]
            if not cands:
                cands = [f for f in self.by_file if f.endswith(norm)][:limit]
                cands = [nid for f in cands for nid in self.by_file[f][:1]]
        seen: dict[str, None] = {}
        for c in cands:
            seen.setdefault(c, None)
        return sorted(seen, key=lambda c: (not self.is_code(c), -self.degree(c)))[:limit]

    def members(self, nid: str) -> list[str]:
        """Symbols structurally inside a node (a file's classes/functions, a class's methods)."""
        out, q = [], deque([nid])
        seen = {nid}
        while q and len(out) < 400:
            cur = q.popleft()
            for e in self.out.get(cur, ()):
                if e.rel in STRUCTURE_RELS and e.other not in seen:
                    seen.add(e.other)
                    out.append(e.other)
                    q.append(e.other)
        return out

    # ---- traversals -----------------------------------------------------------------------------
    def dependents(self, targets: Iterable[str], depth: int = 2, limit: int = 200) -> list[dict]:
        """Reverse traversal: who breaks if ``targets`` change. BFS with depth and provenance."""
        start = set()
        for t in targets:
            start.add(t)
            start.update(self.members(t))
        seen = set(start)
        out: list[dict] = []
        frontier = deque((s, 0) for s in start)
        while frontier and len(out) < limit:
            cur, d = frontier.popleft()
            if d >= depth:
                continue
            for e in self.inc.get(cur, ()):
                if e.rel not in DEPENDENCY_RELS or e.other in seen:
                    continue
                seen.add(e.other)
                out.append({"id": e.other, "label": self.label(e.other), "file": self.file_of(e.other),
                            "depth": d + 1, "rel": e.rel, "via": self.label(cur), "via_id": cur,
                            "via_file": self.file_of(cur), "provenance": e.confidence,
                            "location": e.location})
                frontier.append((e.other, d + 1))
        return out

    def dependencies(self, nid: str, limit: int = 40) -> list[dict]:
        out = []
        for e in self.out.get(nid, ()):
            if e.rel in DEPENDENCY_RELS:
                out.append({"id": e.other, "label": self.label(e.other), "file": self.file_of(e.other),
                            "rel": e.rel, "provenance": e.confidence})
        return out[:limit]

    @staticmethod
    def _line(node: dict) -> int | None:
        loc = (node or {}).get("source_location") or ""
        return int(loc[1:]) if loc[1:].isdigit() else None

    def _span(self, nid: str) -> tuple[str, int, int] | None:
        """(file, first line, last line) of a symbol: up to the next symbol that starts after it."""
        n = self.nodes.get(nid, {})
        f, start = n.get("source_file"), self._line(n)
        if not f or start is None:
            return None
        starts = sorted(l for o in self.by_file.get(f, ()) if (l := self._line(self.nodes[o])) and l > start
                        and self.nodes[o].get("file_type") == "code" and self.label(o) != Path(f).name)
        return f, start, (starts[0] - 1 if starts else 10**9)

    def rationale(self, nids: Iterable[str], limit: int = 12) -> list[dict]:
        """`# WHY:`/`# NOTE:` comments, docstrings and design-doc refs attached to these nodes —
        directly, or by location (comments inside a symbol's body are often attached to the file)."""
        out, seen = [], set()

        def add(rid: str, owner: str) -> None:
            if rid in seen:
                return
            seen.add(rid)
            n = self.nodes.get(rid, {})
            out.append({"id": rid, "text": n.get("label", ""), "file": n.get("source_file"),
                        "location": n.get("source_location"), "for": self.label(owner)})
        nids = list(nids)
        for nid in nids:
            for e in self.inc.get(nid, ()):
                if e.rel == "rationale_for":
                    add(e.other, nid)
        for nid in nids:
            span = self._span(nid)
            if not span or self.label(nid) == Path(span[0]).name:
                continue
            f, a, b = span
            for o in self.by_file.get(f, ()):
                node = self.nodes[o]
                if node.get("file_type") == "rationale" and (ln := self._line(node)) and a <= ln <= b:
                    add(o, nid)
        return out[:limit]

    def hubs(self, top: int = 10) -> list[dict]:
        ranked = sorted((n for n in self.nodes if self.is_code(n)), key=self.degree, reverse=True)[:top]
        return [{"id": n, "label": self.label(n), "file": self.file_of(n), "degree": self.degree(n)} for n in ranked]

    def areas(self, top_members: int = 5) -> list[dict]:
        groups: dict[int, list[str]] = defaultdict(list)
        names: dict[int, str] = {}
        for nid, n in self.nodes.items():
            c = n.get("community")
            if c is None:
                continue
            groups[c].append(nid)
            if n.get("community_name"):
                names[c] = n["community_name"]
        out = []
        for c, ids in groups.items():
            ids.sort(key=self.degree, reverse=True)
            out.append({"id": c, "name": names.get(c, f"Area {c}"), "size": len(ids),
                        "top": [self.label(i) for i in ids[:top_members]]})
        return sorted(out, key=lambda a: a["size"], reverse=True)

    def area_graph(self, limit: int = 60) -> dict:
        """Communities as nodes, aggregated cross-community dependency edges — the UI overview."""
        areas = self.areas()[:limit]
        keep = {a["id"] for a in areas}
        weights: dict[tuple[int, int], int] = defaultdict(int)
        for s, edges in self.out.items():
            cs = self.nodes.get(s, {}).get("community")
            if cs not in keep:
                continue
            for e in edges:
                if e.rel in STRUCTURE_RELS:
                    continue
                ct = self.nodes.get(e.other, {}).get("community")
                if ct in keep and ct != cs:
                    a, b = sorted((cs, ct))
                    weights[(a, b)] += 1
        links = [{"source": a, "target": b, "weight": w} for (a, b), w in weights.items() if w >= 2]
        return {"nodes": areas, "links": sorted(links, key=lambda l: -l["weight"])[:limit * 4]}

    def area_members(self, area: int, limit: int = 160) -> dict:
        ids = [n for n, d in self.nodes.items() if d.get("community") == area]
        ids.sort(key=self.degree, reverse=True)
        ids = ids[:limit]
        keep = set(ids)
        links = [{"source": s, "target": e.other, "rel": e.rel} for s in ids for e in self.out.get(s, ())
                 if e.other in keep and e.rel not in ("contains",)]
        nodes = [{"id": n, "label": self.label(n), "file": self.file_of(n), "type": self.nodes[n].get("file_type"),
                  "degree": self.degree(n)} for n in ids]
        return {"nodes": nodes, "links": links}

    def file_graph(self, max_files: int = 120) -> dict:
        """Code files (or their folders, past ``max_files``) and the cross-file dependencies between them.

        Each node gets a ``layer``: the longest dependency chain beneath it, so foundations sit at 0 and
        entry points on top. Import cycles are collapsed first and share a layer (``cycle`` names them).
        """
        files = {f: sum(1 for i in ids if self.is_code(i)) for f, ids in self.by_file.items()}
        files = {f: n for f, n in files.items() if n}
        group = len(files) > max_files
        unit = (lambda f: str(Path(f).parent) if group else f)
        sizes: dict[str, int] = defaultdict(int)
        for f, n in files.items():
            sizes[unit(f)] += n
        weights: dict[tuple[str, str], int] = defaultdict(int)
        for s, edges in self.out.items():
            fs = self.file_of(s)
            if fs not in files:
                continue
            for e in edges:
                ft = self.file_of(e.other)
                if e.rel in DEPENDENCY_RELS and ft in files and unit(ft) != unit(fs):
                    weights[(unit(fs), unit(ft))] += 1
        succ: dict[str, set[str]] = defaultdict(set)
        for a, b in weights:
            succ[a].add(b)
        comp = _components(list(sizes), succ)
        csucc: dict[int, set[int]] = defaultdict(set)
        for a, bs in succ.items():
            csucc[comp[a]].update(comp[b] for b in bs if comp[b] != comp[a])
        layer: dict[int, int] = {}
        for c in sorted(set(comp.values())):  # components arrive in reverse topological order
            layer[c] = 1 + max((layer[d] for d in csucc[c]), default=-1)
        members: dict[int, list[str]] = defaultdict(list)
        for u, c in comp.items():
            members[c].append(u)
        nodes = [{"id": u, "symbols": sizes[u], "layer": layer[comp[u]],
                  "cycle": sorted(members[comp[u]]) if len(members[comp[u]]) > 1 else []} for u in sizes]
        links = [{"source": a, "target": b, "weight": w} for (a, b), w in weights.items()]
        return {"level": "folder" if group else "file", "nodes": nodes, "links": links}

    def languages(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for f in self.by_file:
            ext = Path(f).suffix.lower()
            if ext:
                counts[ext] += 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8])


def _components(nodes: list[str], succ: dict[str, set[str]]) -> dict[str, int]:
    """Strongly connected components (iterative Tarjan). Component ids come out in reverse
    topological order: a component's successors always have smaller ids."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    comp: dict[str, int] = {}
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(succ.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(succ.get(w, ())))))
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    cid = len(set(comp.values()))
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp[w] = cid
                        if w == v:
                            break
    return comp


_CACHE: dict[str, MapIndex] = {}


def index(map_json: Path) -> MapIndex:
    """Process-wide cached index; reloads only when the map file changes."""
    key = str(map_json)
    try:
        mtime = map_json.stat().st_mtime
    except FileNotFoundError:
        return MapIndex()
    cur = _CACHE.get(key)
    if cur is None or cur.mtime != mtime:
        cur = MapIndex.load(map_json)
        _CACHE[key] = cur
    return cur
