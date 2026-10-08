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
LAYER_BUDGET = 12  # docs/diagram-standard/tokens.json budget.nodes_soft
CYCLE_COLLAPSE = 5  # an import cycle of this many units or more is drawn as one block
EVIDENCE_FILES = 12
_VENDOR_SEGMENTS = frozenset({"vendor", "vendors", "third_party", "third-party", "thirdparty", "node_modules",
                              "bower_components", "site-packages"})
_VENDOR_MARKERS = (".vendored", "VENDORED", "VENDOR.md", "UPSTREAM")
_VENDOR_WORDS = re.compile(r"vendored|bundled|third[- ]party|unmodified|verbatim|copied", re.I)
_ENTRY_ORDER = {"container": 0, "script": 1, "main": 2, "server": 3}
_SERVER_STEMS = frozenset({"server", "app", "wsgi", "asgi", "manage", "main"})
_SERVER_EXTS = frozenset({".py", ".js", ".ts", ".mjs", ".go"})
_TEST_RE = re.compile(r"(^|/)tests?(/|$)|(^|/)test_|_test\.")
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
    text = ig.read_text(encoding="utf-8") if ig.exists() else ""
    add = "".join(block for block, mark in ((IGNORE_BLOCK, "# cairn: tool folders"),
                                             (IGNORE_GENERATED, "# cairn: generated")) if mark not in text)
    if add:
        ig.parent.mkdir(parents=True, exist_ok=True)
        ig.write_text(text + ("\n" if text and not text.endswith("\n") else "") + add, encoding="utf-8")


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
            parts.append(ig.read_text(errors="replace", encoding="utf-8"))
        except OSError:
            parts.append("")
    try:
        import tomllib
        parts.append(json.dumps((tomllib.loads((root / ".cairn" / "config.toml").read_text(encoding="utf-8")).get("map") or {}),
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
            before = json.loads(fp.read_text(encoding="utf-8"))
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
                    fp.write_text(json.dumps(now), encoding="utf-8")
                    return True, "unchanged (only ignored files changed)"
                res = api.build(root, out=target, changed=wanted)
                if res["ok"]:
                    fp.write_text(json.dumps(now), encoding="utf-8")
                    return True, f"{len(wanted)} changed file{'s' if len(wanted) != 1 else ''} re-mapped; " + (
                        res["summary"] or f"{res['nodes']:,} nodes")
    res = api.build(root, out=target, force=force)
    if res["ok"]:
        if now is not None:
            target.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps(now), encoding="utf-8")
        return True, res["summary"] or f"{res['nodes']:,} nodes, {res['edges']:,} edges"
    if not (target / "graph.json").exists() and "no code files" in (res.get("summary") or "").lower():
        # Nothing graphable in the repo (cairn init on a fresh, empty repo): an
        # empty map is the correct result — cairn's own artifacts never self-map —
        # not a build failure. The engine writes no graph.json for this case, so a
        # later build re-derives it cheaply once real files appear.
        return True, "0 files"
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
        data = json.loads(path.read_text(encoding="utf-8"))
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

    def file_graph(self, max_files: int = 120, *, root: Path | None = None, vendored: Iterable[str] = (),
                   hints: dict[str, str] | None = None, layer_budget: int = LAYER_BUDGET) -> dict:
        """First-party code files (or their folders, past ``max_files``) as dependency layers (FR-026).

        Vendored and third-party folders are left out of the layers and reported under ``vendored``.
        Import cycles of ``CYCLE_COLLAPSE`` units or more collapse into one ``cycle`` block (``cycles``
        carries the members); smaller ones stay as units that share a layer. Each block gets a ``layer``
        (the longest dependency chain beneath it) and ``layers`` keeps at most ``layer_budget`` per layer,
        the rest listed under ``rest``. ``entry`` comes from real signals (see :func:`entry_signals`),
        never from position. Output is deterministic.
        """
        files = {f: sum(1 for i in ids if self.is_code(i)) for f, ids in self.by_file.items()}
        files = {f: n for f, n in files.items() if n}
        dirs = {f: _parent(f) for f in files}
        vend = vendored_folders(set(dirs.values()), root, vendored)
        first = {f: n for f, n in files.items() if dirs[f] not in vend}
        group = len(first) > max_files
        unit = (lambda f: dirs[f]) if group else (lambda f: f)
        sizes: dict[str, int] = defaultdict(int)
        count: dict[str, int] = defaultdict(int)
        members_of: dict[str, list[str]] = defaultdict(list)
        for f, n in first.items():
            u = unit(f)
            sizes[u] += n
            count[u] += 1
            members_of[u].append(f)
        weights: dict[tuple[str, str], int] = defaultdict(int)
        vlinks: dict[tuple[str, str], int] = defaultdict(int)
        for s, edges in self.out.items():
            fs = self.file_of(s)
            if fs not in first:
                continue
            us = unit(fs)
            for e in edges:
                if e.rel not in DEPENDENCY_RELS:
                    continue
                ft = self.file_of(e.other)
                if ft in first:
                    ut = unit(ft)
                    if ut != us:
                        weights[(us, ut)] += 1
                elif ft in files:
                    vlinks[(us, dirs[ft])] += 1
        succ: dict[str, set[str]] = defaultdict(set)
        total_in: dict[str, int] = defaultdict(int)
        total_out: dict[str, int] = defaultdict(int)
        for (a, b), w in weights.items():
            succ[a].add(b)
            total_out[a] += w
            total_in[b] += w
        comp = _components(sorted(sizes), succ)
        csucc: dict[int, set[int]] = defaultdict(set)
        for a, bs in succ.items():
            csucc[comp[a]].update(comp[b] for b in bs if comp[b] != comp[a])
        layer: dict[int, int] = {}
        for c in sorted(set(comp.values())):  # components arrive in reverse topological order
            layer[c] = 1 + max((layer[d] for d in csucc[c]), default=-1)
        comp_members: dict[int, list[str]] = defaultdict(list)
        for u, c in comp.items():
            comp_members[c].append(u)
        for m in comp_members.values():
            m.sort()
        big = sorted((c for c, m in comp_members.items() if len(m) >= CYCLE_COLLAPSE),
                     key=lambda c: (-len(comp_members[c]), comp_members[c][0]))
        cycle_id = {c: f"cycle:{i}" for i, c in enumerate(big)}
        block_of = {u: cycle_id.get(c, u) for u, c in comp.items()}

        kind = "folder" if group else "file"
        signals = entry_signals(first, root, hints, unit)

        def entry_of(us: Iterable[str]) -> list[dict]:
            seen: dict[tuple, dict] = {}
            for u in us:
                for sg in signals.get(u, ()):
                    seen.setdefault((sg["kind"], sg["detail"], sg["file"]), sg)
            return sorted(seen.values(), key=lambda g: (_ENTRY_ORDER.get(g["kind"], 9), g["detail"], g["file"]))[:4]

        def evidence(u: str) -> dict:
            fl = sorted(members_of[u], key=lambda f: (-first[f], f))
            return {"files": [{"path": f, "symbols": first[f]} for f in fl[:EVIDENCE_FILES]],
                    "files_total": len(fl), "imports_in": total_in.get(u, 0), "imports_out": total_out.get(u, 0)}

        blocks: dict[str, dict] = {}
        for u in sorted(sizes):
            b = block_of[u]
            if b != u:
                continue
            cm = comp_members[comp[u]]
            blocks[u] = {"id": u, "kind": kind, "symbols": sizes[u], "files": count[u], "layer": layer[comp[u]],
                         "cycle": cm if len(cm) > 1 else [], "entry": entry_of([u]), "evidence": evidence(u),
                         "test": bool(_TEST_RE.search(u))}
        cycles: list[dict] = []
        for c in big:
            cm = comp_members[c]
            bid = cycle_id[c]
            blocks[bid] = {"id": bid, "kind": "cycle", "label": f"cycle of {len(cm)} {kind}s", "symbols": sum(sizes[u] for u in cm),
                           "files": sum(count[u] for u in cm), "layer": layer[c], "cycle": [], "members": cm,
                           "entry": entry_of(cm), "test": False,
                           "evidence": {"members": len(cm), "files_total": sum(count[u] for u in cm),
                                        "imports_in": sum(w for (a, b), w in weights.items() if block_of[b] == bid and block_of[a] != bid),
                                        "imports_out": sum(w for (a, b), w in weights.items() if block_of[a] == bid and block_of[b] != bid)}}
            cycles.append({"id": bid, "size": len(cm), "links": [],
                           "members": [{"id": u, "symbols": sizes[u], "files": count[u], "entry": entry_of([u]),
                                        "evidence": evidence(u)} for u in cm]})
        cyc = {c["id"]: c for c in cycles}
        agg: dict[tuple[str, str], int] = defaultdict(int)
        for (a, b), w in sorted(weights.items()):
            ba, bb = block_of[a], block_of[b]
            if ba != bb:
                agg[(ba, bb)] += w
            if ba in cyc:
                cyc[ba]["links"].append({"source": a, "target": b if bb in (ba, b) else bb, "weight": w})
            if bb in cyc and bb != ba:
                cyc[bb]["links"].append({"source": a if ba == a else ba, "target": b, "weight": w})
        links = [{"source": a, "target": b, "weight": w} for (a, b), w in sorted(agg.items())]
        linked = {x for ln in links for x in (ln["source"], ln["target"])}
        isolated = sorted(b for b, n in blocks.items() if b not in linked and not n["entry"] and n["kind"] != "cycle")
        for b in isolated:
            del blocks[b]
        wdeg: dict[str, int] = defaultdict(int)
        for ln in links:
            wdeg[ln["source"]] += ln["weight"]
            wdeg[ln["target"]] += ln["weight"]

        def rank(n: dict) -> tuple:
            return (n["test"], not n["entry"], n["kind"] != "cycle", -wdeg.get(n["id"], 0), -n["symbols"], n["id"])
        by_layer: dict[int, list[dict]] = defaultdict(list)
        for n in blocks.values():
            by_layer[n["layer"]].append(n)
        layers = []
        for lv in sorted(by_layer, reverse=True):
            ordered = [n["id"] for n in sorted(by_layer[lv], key=rank)]
            layers.append({"layer": lv, "total": len(ordered), "shown": ordered[:layer_budget], "rest": ordered[layer_budget:]})
        vfolders = []
        for d in sorted(set(dirs[f] for f in files) & set(vend)):
            fl = [f for f in files if dirs[f] == d]
            vfolders.append({"id": d, "files": len(fl), "symbols": sum(files[f] for f in fl), "reason": vend[d]})
        vfolders.sort(key=lambda v: (-v["files"], v["id"]))
        vagg: dict[tuple[str, str], int] = defaultdict(int)
        for (a, d), w in vlinks.items():
            vagg[(block_of[a], d)] += w
        entries = sorted(({"id": n["id"], "signals": n["entry"]} for n in blocks.values() if n["entry"]),
                         key=lambda e: (_ENTRY_ORDER.get(e["signals"][0]["kind"], 9), e["id"]))
        return {"level": kind, "budget": layer_budget, "scope": {"files": len(first), "units": len(sizes)},
                "nodes": sorted(blocks.values(), key=lambda n: n["id"]), "links": links, "layers": layers,
                "cycles": cycles, "entries": entries, "isolated": isolated,
                "vendored": {"count": len(vfolders), "folders": vfolders,
                             "links": [{"source": a, "target": d, "weight": w} for (a, d), w in sorted(vagg.items())]}}

    def languages(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for f in self.by_file:
            ext = Path(f).suffix.lower()
            if ext:
                counts[ext] += 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8])


def _parent(path: str) -> str:
    return path.rpartition("/")[0] or "."


def vendored_folders(folders: Iterable[str], root: Path | None, configured: Iterable[str] = ()) -> dict[str, str]:
    """Which folders are vendored or third-party code, with the reason for each (FR-026).

    Signals: a path segment such as ``vendor/`` or ``node_modules/``; the ``map.vendored`` config list;
    ``linguist-vendored`` in ``.gitattributes``; a marker file (``.vendored``, ``VENDORED``) in the folder;
    and folders a NOTICE / THIRD_PARTY file names in an entry that calls them vendored or bundled.
    """
    prefixes: list[tuple[str, str]] = [(p.strip().strip("/"), "listed in map.vendored") for p in configured if p.strip().strip("/")]
    if root is not None:
        try:
            attrs = (root / ".gitattributes").read_text(encoding="utf-8", errors="replace")
        except OSError:
            attrs = ""
        for line in attrs.splitlines():
            parts = line.split()
            if len(parts) > 1 and "linguist-vendored" in parts[1:] and not parts[0].startswith("#"):
                prefixes.append((re.sub(r"(/\*+)+$", "", parts[0].lstrip("/")).rstrip("/"), "linguist-vendored in .gitattributes"))
        try:
            names = sorted(p.name for p in root.iterdir() if p.is_file())
        except OSError:
            names = []
        for name in names:
            if not re.match(r"(?i)(notice|third[_-]?party)", name):
                continue
            try:
                text = (root / name).read_text(encoding="utf-8", errors="replace")[:65536]
            except OSError:
                continue
            for entry in re.split(r"\n\s*\n|\n(?=\s*[-*] )", text):
                if _VENDOR_WORDS.search(entry):
                    prefixes.extend((t.rstrip("/"), f"listed in {name}") for t in re.findall(r"[\w.@-]+(?:/[\w.@-]+)+/?|[\w.@-]+/", entry))
    out: dict[str, str] = {}
    for d in sorted(set(folders)):
        segs = d.split("/")
        seg = next((s for s in segs if s in _VENDOR_SEGMENTS), None)
        if seg:
            out[d] = f"{seg}/ folder"
            continue
        why = next((r for p, r in prefixes if p and (d == p or d.startswith(p + "/"))), None)
        if why is None and root is not None and d != ".":
            why = next((f"{m} marker file" for m in _VENDOR_MARKERS if (root / d / m).is_file()), None)
        if why:
            out[d] = why
    return out


def entry_signals(files: Iterable[str], root: Path | None, hints: dict[str, str] | None,
                  unit) -> dict[str, list[dict]]:
    """Entry points from real signals, keyed by unit: system-model containers (``hints``: file path to
    container name), ``[project.scripts]`` in pyproject, ``bin``/``main`` in package.json, ``__main__.py``
    and server modules. Position in the dependency graph plays no part."""
    files = sorted(files)
    out: dict[str, list[dict]] = defaultdict(list)

    def add(f: str, kind: str, detail: str) -> None:
        out[unit(f)].append({"kind": kind, "detail": detail, "file": f})
    fileset = set(files)
    for path, name in sorted((hints or {}).items()):
        if path in fileset:
            add(path, "container", f"container {name}")
    if root is not None:
        try:
            import tomllib
            proj = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("project") or {}
        except (OSError, ValueError):
            proj = {}
        for table in ("scripts", "gui-scripts"):
            for name, target in sorted((proj.get(table) or {}).items()):
                mod = str(target).split(":")[0].split("[")[0].strip().replace(".", "/")
                cands = [mod + ".py", mod + "/__init__.py"]
                hit = sorted((f for f in files if any(f == c or f.endswith("/" + c) for c in cands)),
                             key=lambda f: (f.count("/"), f))
                if hit:
                    add(hit[0], "script", f"script {name} in [project.{table}]")
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pkg = {}
        if isinstance(pkg, dict):
            bins = pkg.get("bin")
            bins = {pkg.get("name", "bin"): bins} if isinstance(bins, str) else (bins if isinstance(bins, dict) else {})
            for name, target in sorted(bins.items()):
                t = str(target).removeprefix("./")
                if t in fileset:
                    add(t, "script", f"script {name} in package.json bin")
            m = str(pkg.get("main") or "").removeprefix("./")
            if m in fileset:
                add(m, "main", "package.json main")
    for f in files:
        base = f.rpartition("/")[2]
        stem, dot, ext = base.rpartition(".")
        if base == "__main__.py":
            add(f, "main", "__main__.py")
        elif dot and stem in _SERVER_STEMS and "." + ext in _SERVER_EXTS:
            add(f, "main" if stem == "main" else "server", f"{base} entry module" if stem == "main" else f"{base} server module")
    return out


def _components(nodes: list[str], succ: dict[str, set[str]]) -> dict[str, int]:
    """Strongly connected components (iterative Tarjan). Component ids come out in reverse
    topological order: a component's successors always have smaller ids."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    comp: dict[str, int] = {}
    counter = 0
    ncomp = 0
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
                    cid = ncomp
                    ncomp += 1
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
