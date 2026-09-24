"""Map layer: the structural graph of code, docs, schemas and rationale.

Extraction is delegated to the map engine (tree-sitter ASTs, deterministic, local, no model).
Cairn reads the engine's node-link JSON into an in-memory index (cached by mtime) so impact
and neighbourhood queries are pure dictionary walks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Edges along which a change propagates to the *source* of the edge (source depends on target).
DEPENDENCY_RELS = frozenset({"calls", "indirect_call", "imports", "imports_from", "inherits", "uses",
                             "references", "re_exports", "mixes_in", "depends_on", "implements"})
STRUCTURE_RELS = frozenset({"contains", "method", "defines"})
CODE_TYPES = frozenset({"code"})
FILE_EXTS = frozenset((
    ".py .pyi .ts .tsx .js .jsx .mjs .cjs .go .rs .java .kt .kts .scala .rb .php .cs .c .h .cc .cpp .hpp "
    ".swift .m .mm .lua .ex .exs .erl .hs .ml .dart .vue .svelte .sql .sh .bash .md .mdx .rst .txt .json "
    ".yaml .yml .toml .tf .proto .graphql .html .css .scss").split())

_ENV = {**os.environ, "GRAPHIFY_QUERY_LOG_DISABLE": "1"}


def key(label: str) -> str:
    """Normalise a label for lookup: '.send()' / 'send()' / 'Send' -> 'send'."""
    return label.strip().strip("`").lower().lstrip(".").removesuffix("()")


def _engine(*args: str, cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "graphify", *args], cwd=str(cwd), env=_ENV,
                          capture_output=True, text=True, timeout=timeout, errors="replace")


IGNORE_BLOCK = "# cairn: tool folders are not part of the system\n.cairn/\n.specify/\n.claude/\n.cursor/\n.gemini/\n"


def ensure_ignores(root: Path) -> None:
    ig = root / ".graphifyignore"
    text = ig.read_text() if ig.exists() else ""
    if "# cairn:" not in text:
        ig.write_text(text + ("\n" if text and not text.endswith("\n") else "") + IGNORE_BLOCK)


def build(root: Path, force: bool = False) -> tuple[bool, str]:
    """Incrementally (re)build the map. Deterministic, local, no model calls."""
    ensure_ignores(root)
    args = ["update", str(root)]
    if force:
        args.append("--force")
    try:
        res = _engine(*args, cwd=root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    out = (res.stdout + res.stderr).strip().splitlines()
    summary = next((ln.split("]", 1)[-1].strip() for ln in out if "Rebuilt" in ln or "nodes" in ln), "")
    return res.returncode == 0 and (root / "graphify-out" / "graph.json").exists(), summary or (out[-1] if out else "")


def engine_text(root: Path, *args: str, timeout: int = 60) -> str:
    """Run a read-only map query (path/explain/query) and return its text."""
    graph = root / "graphify-out" / "graph.json"
    try:
        res = _engine(*args, "--graph", str(graph), cwd=root, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(map query failed: {exc})"
    return (res.stdout or res.stderr).strip()


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
    def load(cls, path: Path) -> "MapIndex":
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
                            "depth": d + 1, "rel": e.rel, "via": self.label(cur), "provenance": e.confidence,
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

    def rationale(self, nids: Iterable[str], limit: int = 12) -> list[dict]:
        """`# WHY:`/`# NOTE:` comments, docstrings and design-doc refs attached to these nodes."""
        out, seen = [], set()
        for nid in nids:
            for e in self.inc.get(nid, ()):
                if e.rel == "rationale_for" and e.other not in seen:
                    seen.add(e.other)
                    n = self.nodes.get(e.other, {})
                    out.append({"id": e.other, "text": n.get("label", ""), "file": n.get("source_file"),
                                "location": n.get("source_location"), "for": self.label(nid)})
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

    def languages(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for f in self.by_file:
            ext = Path(f).suffix.lower()
            if ext:
                counts[ext] += 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8])


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
