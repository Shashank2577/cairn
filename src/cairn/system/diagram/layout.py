"""Layered layout for diagram descriptions, returning geometry as plain JSON-able dicts.

Boxes: rank columns in the direction data flows (cycles are cut, a consumer comes after the channel it
reads), order rows by the mean row of placed neighbours, keep each boundary's members in a band of rows so
that people, outside systems and anything else that is not a member stay outside it, put libraries on a row
of their own. Edges are orthogonal: every edge leaves a node side through a fanned port, runs through the
free gap between two columns (or a channel between two rows) and its label sits on an opaque mask in a
slot chosen not to touch another label or any box. Flows are drawn sequence style.

The geometry is the whole contract with the renderer: ``svg.render`` adds only colours from the tokens.
``self_test(geometry)`` returns the layout-rule violations (overlapping labels, labels over boxes, a line
behind a box, text smaller than the standard allows, a box inside a boundary it does not belong to).
"""
from __future__ import annotations

import re
import textwrap
from typing import Any, Iterable

from . import load_tokens
from .schema import element_map

LANE = 14.0                 # distance between parallel lines in a gap
GAP_PAD = 10.0              # clear space at the edges of a gap
FAN = 14.0                  # distance between ports on one side of a node (tokens say at least 12)
LABEL_LINE = 13.0
LABEL_PAD_V = 6.0
LABEL_PAD_H = 10.0
LEFT = 40.0
TOP = 92.0
MAX_NODE_W = 340.0
_CONSUME = re.compile(r"\b(subscrib|consum|receiv|listen|poll|pull|read|fetch|drain)", re.I)
_SEARCH_STEP = 4.0
_SEARCH_MAX = 120


# ------------------------------------------------------------------ text measure
def text_width(s: str, size: float, mono: bool = False) -> float:
    """Estimated rendered width. Deliberately a little generous so a label never overruns its mask."""
    return len(s) * size * (0.62 if mono else 0.58)


def _fit(s: str, size: float, width: float, mono: bool = False) -> str:
    if text_width(s, size, mono) <= width:
        return s
    n = max(1, int(width / (size * (0.62 if mono else 0.58))) - 1)
    return s[:n].rstrip() + "…"


def _wrap(s: str, size: float, width: float, lines: int) -> list[str]:
    chars = max(4, int(width / (size * 0.58)))
    out = textwrap.wrap(s, chars, break_long_words=True) or [""]
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][: max(0, chars - 1)].rstrip() + "…"
    return [_fit(x, size, width) for x in out]


def _r(x: float) -> float:
    return round(x, 1)


# ------------------------------------------------------------------ rectangles and a spatial hash
def _overlap(a: tuple, b: tuple, eps: float = 0.5) -> bool:
    return a[0] < b[0] + b[2] - eps and b[0] < a[0] + a[2] - eps and a[1] < b[1] + b[3] - eps and b[1] < a[1] + a[3] - eps


class _Grid:
    """Uniform grid over rectangles: near-constant-time overlap queries, so checks stay near-linear."""

    def __init__(self, cell: float = 128.0):
        self.cell = cell
        self.buckets: dict[tuple[int, int], list[int]] = {}
        self.items: list[tuple[tuple, Any]] = []

    def _keys(self, r: tuple):
        c = self.cell
        for i in range(int(r[0] // c), int((r[0] + r[2]) // c) + 1):
            for j in range(int(r[1] // c), int((r[1] + r[3]) // c) + 1):
                yield (i, j)

    def add(self, r: tuple, tag: Any = None) -> None:
        idx = len(self.items)
        self.items.append((r, tag))
        for k in self._keys(r):
            self.buckets.setdefault(k, []).append(idx)

    def hits(self, r: tuple, eps: float = 0.5) -> list[Any]:
        seen: set[int] = set()
        out = []
        for k in self._keys(r):
            for idx in self.buckets.get(k, ()):
                if idx in seen:
                    continue
                seen.add(idx)
                rr, tag = self.items[idx]
                if _overlap(r, rr, eps):
                    out.append(tag)
        return out


# ------------------------------------------------------------------ nodes
def _element_type(T: dict, e: dict) -> dict:
    return T["element_types"].get(e.get("type") or "", {"shape": "box", "fill": "node_fill", "stroke": "node_stroke", "border": "solid"})


def _node_size(e: dict, shape: str, T: dict, show_desc: bool = True) -> tuple[float, float]:
    S, G = T["font"]["sizes"], T["grid"]
    pad = 16 if shape == "library" else 12
    glyph = 30 if shape in ("person", "component") else 0
    name = e.get("name") or e["id"]
    w = min(MAX_NODE_W, max(G["node_width"] + 12.0, text_width(name, S["name"]) + 2 * pad + glyph))
    if shape == "pill":
        return w, 60.0
    if shape == "compartment":
        n = min(len(e.get("fields") or []), 6) + (1 if len(e.get("fields") or []) > 6 else 0)
        return w, max(64.0, 58.0 + 15.0 * n + 8.0)
    top = {"cylinder": 18.0, "pipe": 4.0}.get(shape, 0.0)
    h = top + 33.0 + 14.0
    if e.get("tech"):
        h += 15.0
    if show_desc and e.get("desc"):
        h += 14.0 * len(_wrap(e["desc"], S["desc"], w - 2 * pad, 2))
    return w, max(float(G["node_min_height"]), h)


def _node_texts(e: dict, shape: str, x: float, y: float, w: float, h: float, T: dict, show_desc: bool) -> list[dict]:
    S = T["font"]["sizes"]
    pad = 16 if shape == "library" else 12
    glyph = 30 if shape in ("person", "component") else 0
    inner = w - 2 * pad
    prov = e.get("provenance", "extracted")
    mark = T["provenance"].get(prov, {}).get("mark", "")
    top = {"cylinder": 18.0, "pipe": 4.0}.get(shape, 0.0)
    cx = x + pad
    tag = (e.get("kind") or e.get("type") or "untyped").replace("-", " ").upper()
    tag_t = {"t": tag, "x": _r(cx), "y": _r(y + top + 16), "size": S["type_tag"], "mono": True, "weight": 700,
             "role": "muted", "ls": 0.8}
    if mark:
        tag_t["mark"], tag_t["aria"] = mark, prov
    out = [tag_t]
    name = _fit(e.get("name") or e["id"], S["name"], inner - glyph)
    if shape == "pill":
        out.append({"t": name, "x": _r(cx), "y": _r(y + 40), "size": S["name"], "weight": 650, "role": "ink"})
    else:
        out.append({"t": name, "x": _r(cx), "y": _r(y + top + 33), "size": S["name"], "weight": 650, "role": "ink"})
    if shape == "compartment":
        fields = e.get("fields") or []
        for i, fl in enumerate(fields[:6]):
            out.append({"t": _fit(fl, S["tech"], inner, True), "x": _r(cx), "y": _r(y + top + 58 + i * 15), "size": S["tech"],
                        "mono": True, "role": "ink2"})
        if len(fields) > 6:
            out.append({"t": f"+{len(fields) - 6} more", "x": _r(cx), "y": _r(y + top + 58 + 6 * 15), "size": S["tech"],
                        "mono": True, "role": "muted"})
    else:
        yy = y + top + 33
        if e.get("tech"):
            yy += 15
            out.append({"t": "[" + _fit(e["tech"], S["tech"], inner - 14, True) + "]", "x": _r(cx), "y": _r(yy),
                        "size": S["tech"], "mono": True, "role": "ink2"})
        if show_desc and e.get("desc") and shape != "pill":
            for ln in _wrap(e["desc"], S["desc"], inner, 2):
                yy += 14
                out.append({"t": ln, "x": _r(cx), "y": _r(yy), "size": S["desc"], "role": "muted"})
    if e.get("count") and e["count"] > 1:
        out.append({"t": f"×{e['count']}", "x": _r(x + w - pad), "y": _r(y + h - 8), "size": S["tech"], "mono": True,
                    "role": "ink2", "anchor": "end"})
    return out


def _node(e: dict, x: float, y: float, w: float, h: float, T: dict, show_desc: bool = True) -> dict:
    ET = _element_type(T, e)
    shape = ET["shape"]
    prov = e.get("provenance", "extracted")
    fill, stroke, sw = ET["fill"], ET["stroke"], T["stroke"]["node"]
    if prov == "stale":
        fill, stroke = "stale_wash", "stale"
    if e.get("focus"):
        fill, stroke, sw = "accent_wash", "accent", T["stroke"]["focus"]
    top = {"cylinder": 18.0, "pipe": 4.0}.get(shape, 0.0)
    return {
        "id": e["id"], "x": _r(x), "y": _r(y), "w": _r(w), "h": _r(h), "shape": shape, "type": e.get("type"),
        "kind": e.get("kind"), "name": e.get("name") or e["id"], "provenance": prov, "focus": bool(e.get("focus")),
        "fill": fill, "stroke": stroke, "stroke_w": sw,
        "dash": T["dash"]["external"] if ET.get("border") == "external" else None,
        "sep": _r(y + top + 42) if shape == "compartment" else None,
        "evidence": list(e.get("evidence") or []),
        "texts": _node_texts(e, shape, x, y, w, h, T, show_desc),
    }


# ------------------------------------------------------------------ labels
def _label_spec(what: str, how: str, prov: str, data_class: str | None, T: dict, mask: bool = True) -> dict | None:
    S = T["font"]["sizes"]
    mark = T["provenance"].get(prov, {}).get("mark", "")
    lines: list[dict] = []
    if what:
        ln = {"t": what, "size": S["label"], "mono": False, "weight": 500, "role": "ink"}
        if mark:
            ln["mark"], ln["aria"], ln["mark_first"] = mark, prov, True
        lines.append(ln)
    if how:
        lines.append({"t": "[" + how + "]", "size": S["label_tech"], "mono": True, "role": "ink2"})
    if data_class:
        lines.append({"t": "data: " + data_class, "size": S["label_tech"], "mono": True, "role": "sensitive"})
    if not lines:
        return None
    wmax = 0.0
    for ln in lines:
        wmax = max(wmax, text_width(ln["t"] + (" ◆" if ln.get("mark") else ""), ln["size"], ln["mono"]))
    return {"w": wmax + LABEL_PAD_H, "h": len(lines) * LABEL_LINE + LABEL_PAD_V, "lines": lines, "mask": mask}


def _label_at(spec: dict, cx: float, cy: float) -> dict:
    w, h = spec["w"], spec["h"]
    x0, y0 = cx - w / 2, cy - h / 2
    lines = []
    for i, ln in enumerate(spec["lines"]):
        d = dict(ln)
        d["x"], d["y"] = _r(cx), _r(y0 + 15 + i * LABEL_LINE)
        d["anchor"] = "middle"
        lines.append(d)
    return {"x": _r(x0), "y": _r(y0), "w": _r(w), "h": _r(h), "mask": spec["mask"], "lines": lines}


def _rect(box: dict) -> tuple:
    return (box["x"], box["y"], box["w"], box["h"])


# ------------------------------------------------------------------ ranking
def _rank_pair(r: dict, typ: dict[str, str | None]) -> tuple[str, str]:
    a, b = r["from"], r["to"]
    if r.get("rank_reverse") or (typ.get(b) == "channel" and _CONSUME.search(r.get("what") or "")):
        return b, a
    return a, b


def _ranks(ids: list[str], pairs: list[tuple[str, str]]) -> dict[str, int]:
    """Longest-path ranks after cutting cycles (back edges of a depth-first walk, iterative)."""
    adj: dict[str, list[str]] = {i: [] for i in ids}
    indeg = dict.fromkeys(ids, 0)
    for a, b in pairs:
        if a != b and a in adj and b in adj:
            adj[a].append(b)
            indeg[b] += 1
    color: dict[str, int] = {}
    keep: list[tuple[str, str]] = []
    for root in [i for i in ids if indeg[i] == 0] + ids:
        if color.get(root):
            continue
        color[root] = 1
        stack = [(root, iter(adj[root]))]
        while stack:
            u, it = stack[-1]
            for v in it:
                c = color.get(v, 0)
                if c == 0:
                    color[v] = 1
                    keep.append((u, v))
                    stack.append((v, iter(adj[v])))
                    break
                if c == 2:
                    keep.append((u, v))
            else:
                color[u] = 2
                stack.pop()
    fwd: dict[str, list[str]] = {i: [] for i in ids}
    deg = dict.fromkeys(ids, 0)
    for a, b in keep:
        fwd[a].append(b)
        deg[b] += 1
    rank = dict.fromkeys(ids, 0)
    queue = [i for i in ids if deg[i] == 0]
    while queue:
        u = queue.pop()
        for v in fwd[u]:
            if rank[v] < rank[u] + 1:
                rank[v] = rank[u] + 1
            deg[v] -= 1
            if deg[v] == 0:
                queue.append(v)
    return rank


# ------------------------------------------------------------------ cells (column, row)
def _bbox(cells: Iterable[tuple[int, int]]) -> tuple[int, int, int, int] | None:
    cs = list(cells)
    if not cs:
        return None
    return min(c for c, _ in cs), min(r for _, r in cs), max(c for c, _ in cs), max(r for _, r in cs)


def _valid_hints(els: list[dict], groups: list[list[str]]) -> dict[str, tuple[int, int]] | None:
    if not els or not all(isinstance(e.get("at"), list) and len(e["at"]) == 2 and min(e["at"]) >= 0 for e in els):
        return None
    pos = {e["id"]: (e["at"][0], e["at"][1]) for e in els}
    if len(set(pos.values())) != len(pos):
        return None
    boxes = []
    for mem in groups:
        bb = _bbox(pos[m] for m in mem if m in pos)
        if bb is None:
            continue
        boxes.append(bb)
        inside = {i for i, (c, r) in pos.items() if bb[0] <= c <= bb[2] and bb[1] <= r <= bb[3]}
        if inside - set(mem):
            return None
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                return None
    return pos


def _align(items: list[tuple[str, float | None]], lo: int, hi: int | None, occupied: set[int]) -> dict[str, int]:
    """Rows for ``items`` (id, preferred row or None), sorted by preference, as close to it as free rows allow."""
    ordered = sorted(range(len(items)), key=lambda k: (items[k][1] is None, items[k][1] if items[k][1] is not None else 0, k))
    out: dict[str, int] = {}
    prev = lo - 1
    for k in ordered:
        iid, want = items[k]
        row = max(lo, prev + 1, int(round(want)) if want is not None else prev + 1)
        while row in occupied:
            row += 1
        out[iid] = row
        prev = row
    if hi is not None and out and max(out.values()) > hi:
        out = {}
        row = lo
        for k in ordered:
            while row in occupied:
                row += 1
            out[items[k][0]] = row
            row += 1
    return out


def _auto_cells(order: list[str], typ: dict[str, str | None], rels: list[dict], groups: list[list[str]]) -> dict[str, tuple[int, int]]:
    libs = [i for i in order if typ.get(i) == "library"]
    libset = set(libs)
    runtime = [i for i in order if i not in libset]
    pairs = [_rank_pair(r, typ) for r in rels if r.get("style") != "build" and r["from"] not in libset and r["to"] not in libset]
    rank = _ranks(runtime, pairs)
    cols: dict[int, list[str]] = {}
    for i in runtime:
        cols.setdefault(rank[i], []).append(i)
    group_of: dict[str, int] = {}
    for gi, mem in enumerate(groups):
        for m in mem:
            group_of.setdefault(m, gi)
    rng: dict[int, tuple[int, int]] = {}
    for gi, mem in enumerate(groups):
        cs = [rank[m] for m in mem if m in rank and group_of.get(m) == gi]
        if cs:
            rng[gi] = (min(cs), max(cs))
    in_range = lambda c: any(lo <= c <= hi for lo, hi in rng.values())  # noqa: E731
    band_h: dict[int, int] = {}
    for gi in rng:
        per_col: dict[int, int] = {}
        for m in groups[gi]:
            if m in rank and group_of.get(m) == gi:
                per_col[rank[m]] = per_col.get(rank[m], 0) + 1
        band_h[gi] = max(per_col.values())
    has_lib = {gi for gi, mem in enumerate(groups) for m in mem if m in libset and group_of.get(m) == gi}
    offset: dict[int, int] = {}
    acc = 0
    for gi in sorted(rng):
        offset[gi] = acc
        acc += band_h[gi] + (1 if gi in has_lib else 0)
    total_band_rows = acc
    nbr: dict[str, list[str]] = {i: [] for i in runtime}
    for r in rels:
        a, b = r["from"], r["to"]
        if a in nbr and b in nbr and a != b:
            nbr[a].append(b)
            nbr[b].append(a)
    pos: dict[str, tuple[int, int]] = {}
    displaced: dict[int, list[tuple[str, float | None]]] = {}
    for c in sorted(cols):
        def pref(i: str) -> float | None:
            rows = [pos[o][1] for o in nbr[i] if o in pos]
            return sum(rows) / len(rows) if rows else None

        by_group: dict[int, list[tuple[str, float | None]]] = {}
        free: list[tuple[str, float | None]] = []
        for i in cols[c]:
            gi = group_of.get(i)
            if gi is not None and gi in rng:
                by_group.setdefault(gi, []).append((i, pref(i)))
            elif in_range(c):
                displaced.setdefault(c, []).append((i, pref(i)))
            else:
                free.append((i, pref(i)))
        for gi, items in by_group.items():
            lo = offset[gi]
            rows = _align(items, lo, lo + band_h[gi] - 1, set())
            for i, row in rows.items():
                pos[i] = (c, row)
        if free:
            for i, row in _align(free, 0, None, set()).items():
                pos[i] = (c, row)
    max_row = max((r for _, r in pos.values()), default=-1)
    trail = max(total_band_rows, 0)
    for c, items in displaced.items():
        used = {r for cc, r in pos.values() if cc == c}
        rows = _align(items, trail, None, used)
        for i, row in rows.items():
            pos[i] = (c, row)
    max_row = max((r for _, r in pos.values()), default=-1)
    occupied = set(pos.values())
    for lib in libs:
        users = [pos[r["from"]][0] for r in rels if r["to"] == lib and r["from"] in pos]
        users += [pos[r["to"]][0] for r in rels if r["from"] == lib and r["to"] in pos]
        gi = group_of.get(lib)
        if gi is not None and gi in rng:
            row = offset[gi] + band_h[gi]
            inside = [u for u in users if rng[gi][0] <= u <= rng[gi][1]]
            col = min(inside) if inside else rng[gi][0]
        else:
            row = max_row + 1
            col = min(users) if users else 0
        while (col, row) in occupied:
            col += 1
        pos[lib] = (col, row)
        occupied.add((col, row))
    # repair: a non-member that lands inside a boundary's cell box is moved below everything
    max_row = max((r for _, r in pos.values()), default=-1)
    for mem in groups:
        bb = _bbox(pos[m] for m in mem if m in pos)
        if bb is None:
            continue
        mset = set(mem)
        for i, (c, r) in list(pos.items()):
            if i not in mset and bb[0] <= c <= bb[2] and bb[1] <= r <= bb[3]:
                max_row += 1
                pos[i] = (c, max_row)
    return _compress(pos)


def _compress(pos: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    cols = {c: k for k, c in enumerate(sorted({c for c, _ in pos.values()}))}
    rows = {r: k for k, r in enumerate(sorted({r for _, r in pos.values()}))}
    return {i: (cols[c], rows[r]) for i, (c, r) in pos.items()}


# ------------------------------------------------------------------ box diagrams
def _simplify(pts: list[tuple[float, float]]) -> list[list[float]]:
    out: list[tuple[float, float]] = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 0.05 and abs(out[-1][1] - p[1]) < 0.05:
            continue
        out.append(p)
    i = 1
    while i < len(out) - 1:
        a, b, c = out[i - 1], out[i], out[i + 1]
        if (abs(a[0] - b[0]) < 0.05 and abs(b[0] - c[0]) < 0.05) or (abs(a[1] - b[1]) < 0.05 and abs(b[1] - c[1]) < 0.05):
            del out[i]
        else:
            i += 1
    return [[_r(x), _r(y)] for x, y in out]


def _plan(r: dict, pos: dict[str, tuple[int, int]]) -> dict:
    (cs, rs), (ct, rt) = pos[r["from"]], pos[r["to"]]
    if r["from"] == r["to"]:
        return {"kind": "loop", "a": "r", "b": "r", "cs": cs, "rs": rs, "ct": ct, "rt": rt}
    if cs == ct:
        if abs(rt - rs) == 1:
            a, b = ("b", "t") if rt > rs else ("t", "b")
            return {"kind": "vert", "a": a, "b": b, "cs": cs, "rs": rs, "ct": ct, "rt": rt, "rowgap": min(rs, rt)}
        return {"kind": "skip", "a": "r", "b": "r", "cs": cs, "rs": rs, "ct": ct, "rt": rt}
    if ct > cs:
        return {"kind": "adj" if ct == cs + 1 else "far", "a": "r", "b": "l", "cs": cs, "rs": rs, "ct": ct, "rt": rt,
                "rowgap": max(rs, rt)}
    return {"kind": "adj" if cs == ct + 1 else "far", "a": "l", "b": "r", "cs": cs, "rs": rs, "ct": ct, "rt": rt,
            "rowgap": max(rs, rt)}


def _lane_ends(p: dict) -> list[tuple[int, str]]:
    """(gap, zone) of the vertical lanes an edge uses; zone L is the half of a gap next to the column on its left."""
    k, cs, ct = p["kind"], p["cs"], p["ct"]
    if k in ("loop", "skip"):
        return [(cs, "L")]
    if k == "vert":
        return []
    if ct > cs:
        return [(cs, "L"), (ct - 1, "R")]
    return [(cs - 1, "R"), (ct, "L")]


def _slot(grid: _Grid, sp: dict, cx: float, cy: float, offsets: list[float]) -> float:
    """The first y in ``cy + offsets`` where a label of ``sp``'s size touches nothing already placed."""
    for off in offsets:
        rect = (cx - sp["w"] / 2, cy + off - sp["h"] / 2, sp["w"], sp["h"])
        if not grid.hits(rect):
            return cy + off
    return cy


def _search_offsets() -> list[float]:
    out = [0.0]
    for j in range(1, _SEARCH_MAX):
        out += [j * _SEARCH_STEP, -j * _SEARCH_STEP]
    return out


def _beside_offsets(h: float) -> list[float]:
    """For a straight line: on the line, then just above and below it, then further out."""
    out = [0.0]
    for j in range(_SEARCH_MAX):
        d = h / 2 + 3 + j * _SEARCH_STEP
        out += [-d, d]
    return out


def _box_layout(desc: dict, T: dict) -> dict:
    G = T["grid"]
    inset = float(G["boundary_inset"])
    emap = element_map(desc)
    order = list(emap)
    if not order:
        return {"kind": desc.get("diagram"), "width": 760.0, "height": TOP + 40, "scale": 1.0, "nodes": [], "edges": [],
                "boundaries": [], "legend": legend_items(desc, T)}
    typ = {i: emap[i].get("type") for i in order}
    rels_all = list(desc.get("relationships", []))
    keep_idx = [k for k, r in enumerate(rels_all) if r["from"] in emap and r["to"] in emap]
    rels = [rels_all[k] for k in keep_idx]
    groups = [[m for m in b.get("contains", []) if m in emap] for b in desc.get("boundaries", [])]
    bdefs = [b for b, g in zip(desc.get("boundaries", []), groups) if g]
    groups = [g for g in groups if g]
    pos = _valid_hints(list(emap.values()), groups) or _auto_cells(order, typ, rels, groups)
    ncols = max(c for c, _ in pos.values()) + 1
    nrows = max(r for _, r in pos.values()) + 1
    bcells = [_bbox(pos[m] for m in g) for g in groups]
    bcells = [bb for bb in bcells if bb is not None]

    plans = [_plan(r, pos) for r in rels]
    specs: list[dict | None] = [
        _label_spec(r.get("what") or "", r.get("how") or "", r.get("provenance", "extracted"), r.get("data_class"), T)
        for r in rels]

    # ---- sizes: each node grows so that its ports keep the fan distance
    size = {i: list(_node_size(emap[i], _element_type(T, emap[i])["shape"], T)) for i in order}
    ports: dict[tuple[str, str], list[tuple[int, int]]] = {}   # (node, side) -> [(edge, end)]; end 0 = from, 1 = to
    for k, (r, p) in enumerate(zip(rels, plans)):
        ports.setdefault((r["from"], p["a"]), []).append((k, 0))
        ports.setdefault((r["to"], p["b"]), []).append((k, 1))
    for (nid, side), lst in ports.items():
        need = FAN * (len(lst) + 1)
        if side in "lr":
            size[nid][1] = max(size[nid][1], need)
        else:
            size[nid][0] = min(max(size[nid][0], need), 4000.0)
    colw = [0.0] * ncols
    rowh = [0.0] * nrows
    for i, (c, r) in pos.items():
        colw[c] = max(colw[c], size[i][0])
        rowh[r] = max(rowh[r], size[i][1])

    # ---- vertical structure first (it does not depend on gap widths): rows, channels, node y, side ports
    chan_users: dict[int, list[int]] = {}
    for k, p in enumerate(plans):
        if p["kind"] in ("far", "vert"):
            chan_users.setdefault(p["rowgap"], []).append(k)
    lane_h = [0.0] * nrows
    for g, ks in chan_users.items():
        lane_h[g] = max((specs[k]["h"] if specs[k] else 0.0) for k in ks) + 6.0
    rowgap = []
    for g in range(nrows):
        base = float(G["row_gap"]) if g < nrows - 1 else 28.0
        h = max(base, 12.0 + len(chan_users.get(g, [])) * lane_h[g])
        for _, r0, _, r1 in bcells:
            if g == r0 - 1:
                h += inset + 18
            if g == r1:
                h += inset
        rowgap.append(h)
    top0 = TOP + (inset + 18 if any(r0 == 0 for _, r0, _, _ in bcells) else 0.0)
    rowy, y = [], top0
    for r in range(nrows):
        rowy.append(y)
        y += rowh[r] + rowgap[r]
    height = y
    node_y = {i: rowy[pos[i][1]] + (rowh[pos[i][1]] - size[i][1]) / 2 for i in order}
    cy = {i: node_y[i] + size[i][1] / 2 for i in order}
    port_y: dict[tuple[int, int], float] = {}
    for (nid, side), lst in ports.items():
        if side not in "lr":
            continue

        def key(item: tuple[int, int], nid=nid) -> tuple:
            k, end = item
            r = rels[k]
            if r["from"] == r["to"]:
                return (cy[nid] + (-0.1 if end == 0 else 0.1), k)
            return (cy[r["to"] if end == 0 else r["from"]], k)
        lst.sort(key=key)
        for idx, (k, end) in enumerate(lst):
            port_y[(k, end)] = node_y[nid] + size[nid][1] * (idx + 1) / (len(lst) + 1)
    straight = {k for k, p in enumerate(plans)
                if p["kind"] == "adj" and abs(port_y[(k, 0)] - port_y[(k, 1)]) < 0.5}

    # ---- horizontal structure: lanes per gap half, gap widths, column positions
    lane_users: dict[tuple[int, str], list[int]] = {}
    centre_w = [0.0] * ncols
    for k, p in enumerate(plans):
        if k not in straight:
            for end in _lane_ends(p):
                lane_users.setdefault(end, []).append(k)
        sp = specs[k]
        if p["kind"] in ("adj", "skip", "loop") and sp:
            g = p["cs"] if p["kind"] != "adj" else (p["cs"] if p["ct"] > p["cs"] else p["ct"])
            centre_w[g] = max(centre_w[g], sp["w"] + 16)
    gapw = []
    for g in range(ncols):
        nl, nr = len(lane_users.get((g, "L"), [])), len(lane_users.get((g, "R"), []))
        floor = float(G["col_gap"]) if g < ncols - 1 else 48.0
        w = max(floor, 2 * GAP_PAD + nl * LANE + centre_w[g] + nr * LANE)
        for c0, _, c1, _ in bcells:
            if g == c0 - 1:
                w += inset
            if g == c1:
                w += inset
        gapw.append(w)
    left0 = LEFT + (inset if any(c0 == 0 for c0, _, _, _ in bcells) else 0.0)
    colx, gap_left, x = [], [], left0
    for c in range(ncols):
        colx.append(x)
        gap_left.append(x + colw[c])
        x += colw[c] + gapw[c]
    width = max(x + 20.0, 760.0)

    nodes: dict[str, dict] = {}
    node_x: dict[str, float] = {}
    for i in order:
        c, _ = pos[i]
        node_x[i] = colx[c] + (colw[c] - size[i][0]) / 2
        nodes[i] = _node(emap[i], node_x[i], node_y[i], size[i][0], size[i][1], T)
    cx = {i: node_x[i] + size[i][0] / 2 for i in order}

    # ---- ports, final: top and bottom sides are ordered by the other end's x
    port_pt: dict[tuple[int, int], tuple[float, float]] = {}
    for (nid, side), lst in ports.items():
        w, h = size[nid]
        if side in "tb":
            lst.sort(key=lambda it: (cx[rels[it[0]]["to" if it[1] == 0 else "from"]], it[0]))
        for idx, (k, end) in enumerate(lst):
            f = (idx + 1) / (len(lst) + 1)
            if side == "r":
                pt = (node_x[nid] + w, port_y[(k, end)])
            elif side == "l":
                pt = (node_x[nid], port_y[(k, end)])
            elif side == "b":
                pt = (node_x[nid] + w * f, node_y[nid] + h)
            else:
                pt = (node_x[nid] + w * f, node_y[nid])
            port_pt[(k, end)] = pt

    def desired_jog(k: int) -> float:
        p = plans[k]
        if p["kind"] == "far":
            return rowy[p["rowgap"]] + rowh[p["rowgap"]] + 8.0
        return (port_pt[(k, 0)][1] + port_pt[(k, 1)][1]) / 2

    lane_x: dict[tuple[int, int], float] = {}     # (edge, lane end) -> x
    for (g, zone), ks in lane_users.items():
        def stub(k: int, g=g, zone=zone) -> tuple[int, float, float]:
            ends = _lane_ends(plans[k])
            end = ends.index((g, zone))
            return end, port_pt[(k, end)][1], desired_jog(k)
        # inner lanes (next to the nodes) go to runs that cannot cross another edge's stub
        down = sorted((k for k in ks if stub(k)[2] >= stub(k)[1]), key=lambda k: (-stub(k)[1], k))
        up = sorted((k for k in ks if stub(k)[2] < stub(k)[1]), key=lambda k: (stub(k)[1], k))
        for n, k in enumerate(down + up):
            if zone == "L":
                lane_x[(k, stub(k)[0])] = gap_left[g] + GAP_PAD + (n + 0.5) * LANE
            else:
                lane_x[(k, stub(k)[0])] = gap_left[g] + gapw[g] - GAP_PAD - (n + 0.5) * LANE

    def centre_x(g: int) -> float:
        nl, nr = len(lane_users.get((g, "L"), [])), len(lane_users.get((g, "R"), []))
        return (gap_left[g] + GAP_PAD + nl * LANE + gap_left[g] + gapw[g] - GAP_PAD - nr * LANE) / 2

    # ---- obstacles for label slots: boxes and boundary titles; boundary rectangles
    grid = _Grid()
    for nd in nodes.values():
        grid.add(_rect(nd), ("node", nd["id"]))
    bounds: list[dict] = []
    S = T["font"]["sizes"]
    for bdef, mem, bb in zip(bdefs, groups, bcells):
        c0, r0, c1, r1 = bb
        bx0, by0 = colx[c0] - inset, rowy[r0] - inset - 18
        bx1, by1 = colx[c1] + colw[c1] + inset, rowy[r1] + rowh[r1] + inset
        trust = bool(bdef.get("trust"))
        name, btype = bdef.get("name") or bdef["id"], bdef.get("type") or ""
        tw_name = text_width(name, 11)
        title = {"x": _r(bx0 + 10), "y": _r(by0 + 4), "w": _r(tw_name + 8 + text_width(btype.upper(), S["type_tag"], True) + 4),
                 "h": 14.0}
        bounds.append({
            "id": bdef["id"], "name": name, "type": btype, "trust": trust, "members": list(mem),
            "x": _r(bx0), "y": _r(by0), "w": _r(bx1 - bx0), "h": _r(by1 - by0), "title": title,
            "texts": [
                {"t": name, "x": _r(bx0 + 10), "y": _r(by0 + 15), "size": 11, "weight": 700, "role": "trust" if trust else "ink2"},
                {"t": btype.upper(), "x": _r(bx0 + 10 + tw_name + 8), "y": _r(by0 + 15), "size": S["type_tag"], "mono": True,
                 "role": "muted", "ls": 0.6},
            ],
            "stroke": "trust" if trust else "boundary_stroke",
        })
        grid.add((title["x"], title["y"], title["w"], title["h"]), ("title", bdef["id"]))

    # ---- routes and label slots
    points: dict[int, list[tuple[float, float]]] = {}
    labels: dict[int, dict] = {}

    def put(k: int, cxl: float, cyl: float) -> None:
        if specs[k]:
            lab = _label_at(specs[k], cxl, cyl)
            grid.add(_rect(lab), ("label", k))
            labels[k] = lab

    chan_lane = {k: n for ks in chan_users.values() for n, k in enumerate(ks)}
    ends_below = {r1 for _, _, _, r1 in bcells}
    centred: list[tuple[float, int]] = []
    for k, p in enumerate(plans):
        p1, p2 = port_pt[(k, 0)], port_pt[(k, 1)]
        kind = p["kind"]
        if kind in ("vert", "far"):
            g = p["rowgap"]
            ly = rowy[g] + rowh[g] + (inset if g in ends_below else 0.0) + 8.0 + (chan_lane[k] + 0.5) * lane_h[g]
            if kind == "vert":
                points[k] = [p1, (p1[0], ly), (p2[0], ly), p2] if abs(p1[0] - p2[0]) > 0.5 else [p1, p2]
                put(k, (p1[0] + p2[0]) / 2, ly)
            else:
                xa, xb = lane_x[(k, 0)], lane_x[(k, 1)]
                points[k] = [p1, (xa, p1[1]), (xa, ly), (xb, ly), (xb, p2[1]), p2]
                put(k, (xa + xb) / 2, ly)
        else:
            centred.append((desired_jog(k), k))
    offsets = _search_offsets()
    for want, k in sorted(centred):
        p, sp = plans[k], specs[k]
        p1, p2 = port_pt[(k, 0)], port_pt[(k, 1)]
        if p["kind"] == "adj":
            lx = centre_x(p["cs"] if p["ct"] > p["cs"] else p["ct"])
            if k in straight:
                ly = _slot(grid, sp, lx, want, _beside_offsets(sp["h"])) if sp else want
                points[k] = [p1, p2]
            else:
                ly = _slot(grid, sp, lx, want, offsets) if sp else want
                xa, xb = lane_x[(k, 0)], lane_x[(k, 1)]
                points[k] = [p1, (xa, p1[1]), (xa, ly), (xb, ly), (xb, p2[1]), p2]
        else:
            xa = lane_x[(k, 0)]
            lx = xa + 6.0 + (sp["w"] / 2 if sp else 0.0)
            ly = _slot(grid, sp, lx, want, offsets) if sp else want
            points[k] = [p1, (xa, p1[1]), (xa, p2[1]), p2]
        if sp:
            put(k, lx, ly)

    edges_out: list[dict] = []
    max_x, max_y = width, height
    for k, r in enumerate(rels):
        pts = _simplify(points[k])
        for px, py in pts:
            max_x, max_y = max(max_x, px + 20), max(max_y, py + 20)
        lab = labels.get(k)
        if lab:
            max_x, max_y = max(max_x, lab["x"] + lab["w"] + 20), max(max_y, lab["y"] + lab["h"] + 20)
        edges_out.append({
            "id": f"e{keep_idx[k]}", "index": keep_idx[k], "from": r["from"], "to": r["to"], "style": r.get("style", "sync"),
            "provenance": r.get("provenance", "extracted"), "data_class": r.get("data_class"), "what": r.get("what") or "",
            "how": r.get("how") or "", "evidence": list(r.get("evidence") or []), "points": pts,
            "labels": [lab] if lab else [],
        })
    for b in bounds:
        max_x, max_y = max(max_x, b["x"] + b["w"] + 20), max(max_y, b["y"] + b["h"] + 20)
    return {
        "kind": desc.get("diagram"), "width": _r(max_x), "height": _r(max(max_y, height)), "scale": 1.0,
        "nodes": [nodes[i] for i in order], "edges": edges_out, "boundaries": bounds, "legend": legend_items(desc, T),
    }


# ------------------------------------------------------------------ flows (sequence style)
def _flow_layout(desc: dict, T: dict) -> dict:
    emap = element_map(desc)
    f = desc.get("flow") or {}
    parts: list[str] = []
    for p in f.get("participants", []):
        if p in emap and p not in parts:
            parts.append(p)
    msgs = list(f.get("messages", []))
    HW, HH, ROW = 170.0, 70.0, 64.0
    n = len(parts)
    idx = {p: i for i, p in enumerate(parts)}
    gaps = [210.0] * max(n - 1, 0)
    specs: list[tuple[dict | None, dict | None]] = []
    for m in msgs:
        what = _label_spec(m.get("what") or "", "", m.get("provenance", "extracted"), None, T)
        how = _label_spec("", m.get("how") or "", m.get("provenance", "extracted"), m.get("data_class"), T)
        specs.append((what, how))
    spans = sorted(((abs(idx[m["to"]] - idx[m["from"]]), k) for k, m in enumerate(msgs) if m["from"] in idx and m["to"] in idx))
    for span, k in spans:
        if span == 0:
            continue
        need = max((s["w"] for s in specs[k] if s), default=0.0) + 60.0
        lo, hi = sorted((idx[msgs[k]["from"]], idx[msgs[k]["to"]]))
        have = sum(gaps[lo:hi])
        if have < need:
            add = (need - have) / span
            for g in range(lo, hi):
                gaps[g] += add
    xs = [LEFT + HW / 2]
    for g in gaps:
        xs.append(xs[-1] + g)
    y0 = TOP + HH + 56.0
    ys = [y0 + i * ROW for i in range(len(msgs))]
    end_y = (ys[-1] if ys else y0) + ROW * 0.7
    nodes = [_node(dict(emap[p], desc=None), xs[i] - HW / 2, TOP, HW, HH, T, show_desc=False) for i, p in enumerate(parts)]
    lifelines = [{"id": p, "x": _r(xs[i]), "y1": _r(TOP + HH), "y2": _r(end_y)} for i, p in enumerate(parts)]
    edges: list[dict] = []
    max_x = (xs[-1] + HW / 2 + LEFT) if xs else 760.0
    for k, m in enumerate(msgs):
        if m["from"] not in idx or m["to"] not in idx:
            continue
        y = ys[k]
        x1, x2 = xs[idx[m["from"]]], xs[idx[m["to"]]]
        what, how = specs[k]
        labels = []
        if x1 == x2:
            pts = [[x1, y], [x1 + 36, y], [x1 + 36, y + 18], [x1, y + 18]]
            lx = x1 + 44
            if what:
                labels.append(_label_at(what, lx + what["w"] / 2, y - 4 - what["h"] / 2))
            if how:
                labels.append(_label_at(how, lx + how["w"] / 2, y + 4 + how["h"] / 2))
            num_x = x1 + 12
        else:
            sgn = 1 if x2 > x1 else -1
            pts = [[x1 + sgn * 4, y], [x2 - sgn * 4, y]]
            mid = (x1 + x2) / 2
            if what:
                labels.append(_label_at(what, mid, y - 4 - what["h"] / 2))
            if how:
                labels.append(_label_at(how, mid, y + 4 + how["h"] / 2))
            num_x = min(x1, x2) + 12
        for lab in labels:
            max_x = max(max_x, lab["x"] + lab["w"] + 20)
        edges.append({
            "id": f"m{k}", "index": k, "number": k + 1, "from": m["from"], "to": m["to"], "style": m.get("style", "sync"),
            "provenance": m.get("provenance", "extracted"), "data_class": m.get("data_class"), "what": m.get("what") or "",
            "how": m.get("how") or "", "evidence": list(m.get("evidence") or []), "points": [[_r(a), _r(b)] for a, b in pts],
            "labels": labels, "badge": {"x": _r(num_x), "y": _r(y - 11), "n": k + 1},
        })
    fragments = []
    left_x = (xs[0] - HW / 2 + 6) if xs else LEFT
    right_x = (xs[-1] + HW / 2 - 6) if xs else 760.0
    for fr in f.get("fragments", []):
        s, e = fr.get("start"), fr.get("end")
        if not (isinstance(s, int) and isinstance(e, int) and 1 <= s <= e <= len(msgs)):
            continue
        fy0, fy1 = ys[s - 1] - 40, ys[e - 1] + 28
        head_t = (fr.get("type") or "").strip()
        texts = [{"t": head_t, "x": _r(left_x + 6), "y": _r(fy0 + 12), "size": 9.0, "mono": True, "weight": 700, "role": "muted"}]
        label_boxes = [{"x": _r(left_x + 6), "y": _r(fy0 + 3), "w": _r(text_width(head_t, 9.0, True) + 4), "h": 12.0}]
        if fr.get("label"):
            lt = "[" + fr["label"] + "]"
            texts.append({"t": lt, "x": _r(left_x + 56), "y": _r(fy0 + 12), "size": 9.0, "mono": True, "role": "muted"})
            label_boxes.append({"x": _r(left_x + 56), "y": _r(fy0 + 3), "w": _r(text_width(lt, 9.0, True) + 4), "h": 12.0})
        else_line = None
        if fr.get("else_at"):
            ey = ys[fr["else_at"] - 1] - 40
            lt = "[" + (fr.get("else_label") or "else") + "]"
            texts.append({"t": lt, "x": _r(left_x + 6), "y": _r(ey + 12), "size": 9.0, "mono": True, "role": "muted"})
            label_boxes.append({"x": _r(left_x + 6), "y": _r(ey + 3), "w": _r(text_width(lt, 9.0, True) + 4), "h": 12.0})
            else_line = {"y": _r(ey), "x1": _r(left_x), "x2": _r(right_x)}
        fragments.append({"x": _r(left_x), "y": _r(fy0), "w": _r(right_x - left_x), "h": _r(fy1 - fy0), "type": head_t,
                          "texts": texts, "label_boxes": label_boxes, "else": else_line,
                          "tab": {"x": _r(left_x), "y": _r(fy0), "w": 44.0, "h": 16.0}})
    legend = legend_items(desc, T)
    return {
        "kind": "flow", "width": _r(max(max_x, 760.0)), "height": _r(end_y + 30), "scale": 1.0, "nodes": nodes, "edges": edges,
        "boundaries": [], "lifelines": lifelines, "fragments": fragments, "legend": legend,
    }


# ------------------------------------------------------------------ legend
def legend_items(desc: dict, T: dict) -> list[dict]:
    """What the diagram uses, and nothing else: shapes, line styles, provenance marks, overlays."""
    items: list[dict] = []
    seen: set[str] = set()
    for e in desc.get("elements", []):
        t = e.get("type")
        if t in T["element_types"] and t not in seen:
            seen.add(t)
            ET = T["element_types"][t]
            items.append({"kind": "shape", "type": t, "shape": ET["shape"], "fill": ET["fill"], "stroke": ET["stroke"],
                          "dash": T["dash"]["external"] if ET.get("border") == "external" else None,
                          "label": t.replace("-", " ")})
    rels = (desc.get("flow") or {}).get("messages", []) if desc.get("diagram") == "flow" else desc.get("relationships", [])
    styles: list[str] = []
    for r in rels:
        s = r.get("style", "sync")
        if s in T["relationship_styles"] and s not in styles:
            styles.append(s)
    for s in styles:
        st = T["relationship_styles"][s]
        label = st["legend"]
        if desc.get("diagram") == "state":
            label = "Transition"
        elif desc.get("diagram") == "data-model":
            label = "Association"
        items.append({"kind": "line", "style": s, "dash": T["dash"][st["dash"]] if st["dash"] else None, "head": st["head"],
                      "label": label})
    provs: list[str] = []
    for x in list(desc.get("elements", [])) + list(rels):
        p = x.get("provenance", "extracted")
        if p != "extracted" and p in T["provenance"] and p not in provs:
            provs.append(p)
    for p in provs:
        items.append({"kind": "mark", "provenance": p, "mark": T["provenance"][p]["mark"],
                      "label": p + ": " + T["provenance"][p]["legend"]})
    if any(b.get("trust") for b in desc.get("boundaries", [])):
        items.append({"kind": "overlay", "name": "trust-boundary", "role": T["overlays"]["trust-boundary"]["color"],
                      "label": T["overlays"]["trust-boundary"]["legend"]})
    if any(r.get("data_class") for r in rels):
        items.append({"kind": "overlay", "name": "data-class", "role": T["overlays"]["data-class"]["color"],
                      "label": T["overlays"]["data-class"]["legend"]})
    if any((e.get("count") or 0) > 1 for e in desc.get("elements", [])):
        items.append({"kind": "badge", "label": "Number of identical replicas"})
    if not provs and not any(i["kind"] in ("overlay", "badge") for i in items):
        items.append({"kind": "note", "label": "Every box and arrow is backed by evidence; hover to see it."})
    return items


# ------------------------------------------------------------------ public entry points
def layout(desc: dict, tokens: dict | None = None) -> dict:
    """Geometry for a normalised description: nodes, edges (points and label boxes), boundaries, legend items."""
    T = tokens or load_tokens()
    if desc.get("diagram") == "flow" and desc.get("flow"):
        return _flow_layout(desc, T)
    return _box_layout(desc, T)


def _seg_hits_rect(p: list[float], q: list[float], r: tuple, eps: float = 0.5) -> bool:
    x0, x1 = sorted((p[0], q[0]))
    y0, y1 = sorted((p[1], q[1]))
    if x1 - x0 < 0.05:        # vertical
        return r[0] + eps < x0 < r[0] + r[2] - eps and y0 < r[1] + r[3] - eps and y1 > r[1] + eps
    if y1 - y0 < 0.05:        # horizontal
        return r[1] + eps < y0 < r[1] + r[3] - eps and x0 < r[0] + r[2] - eps and x1 > r[0] + eps
    return x0 < r[0] + r[2] - eps and x1 > r[0] + eps and y0 < r[1] + r[3] - eps and y1 > r[1] + eps


def self_test(geometry: dict, tokens: dict | None = None) -> list[str]:
    """Layout-rule violations of a geometry (empty means clean).

    Rules: no label overlapping another label or a box, no line passing behind a box it does not join,
    no box inside a boundary it does not belong to, no two boxes overlapping, ports at least the fan
    distance apart, rendered text never below the minimum size.
    """
    T = tokens or load_tokens()
    out: list[str] = []
    scale = float(geometry.get("scale", 1.0))
    min_px = float(T["font"]["min_rendered_px"])
    nodes = geometry.get("nodes", [])
    nrect = {n["id"]: _rect(n) for n in nodes}

    def small(texts: Iterable[dict], where: str) -> None:
        for t in texts:
            if t["size"] * scale < min_px:
                out.append(f"text-size: {where} text {t['t']!r} is {t['size'] * scale:.1f}px (minimum {min_px}px)")

    for n in nodes:
        small(n.get("texts", []), f"node {n['id']}")
        for t in n.get("texts", []):
            if t.get("anchor") != "end" and n["x"] + n["w"] < t["x"] + text_width(t["t"], t["size"], t.get("mono", False)) - 6:
                out.append(f"text-overflow: {t['t']!r} runs out of node {n['id']}")
    boxes = _Grid()
    for nid, r in nrect.items():
        boxes.add(r, nid)
    labels = _Grid()
    entries: list[tuple[tuple, str]] = []
    for e in geometry.get("edges", []):
        for lab in e.get("labels", []):
            small(lab["lines"], f"label of {e['id']}")
            entries.append((_rect(lab), f"label of {e['id']}"))
    for b in geometry.get("boundaries", []):
        small(b.get("texts", []), f"boundary {b['id']}")
        entries.append(((b["title"]["x"], b["title"]["y"], b["title"]["w"], b["title"]["h"]), f"title of boundary {b['id']}"))
    for fr in geometry.get("fragments", []):
        small(fr.get("texts", []), "fragment")
        for lb in fr.get("label_boxes", []):
            entries.append(((lb["x"], lb["y"], lb["w"], lb["h"]), "fragment label"))
    for rect, who in entries:
        for other in labels.hits(rect):
            out.append(f"label-overlap: {who} overlaps {other}")
        for nid in boxes.hits(rect):
            out.append(f"label-box: {who} overlaps box {nid}")
        labels.add(rect, who)
    for e in geometry.get("edges", []):
        pts = e["points"]
        ends = {e["from"], e["to"]}
        seen: set[str] = set()
        for p, q in zip(pts, pts[1:]):
            probe = (min(p[0], q[0]), min(p[1], q[1]), abs(p[0] - q[0]) + 0.01, abs(p[1] - q[1]) + 0.01)
            for nid in boxes.hits(probe, eps=-0.5):
                if nid in ends or nid in seen:
                    continue
                if _seg_hits_rect(p, q, nrect[nid]):
                    seen.add(nid)
                    out.append(f"line-behind-box: edge {e['from']}->{e['to']} passes behind box {nid}")
    ids = list(nrect)
    for i, a in enumerate(ids):
        for nid in boxes.hits(nrect[a]):
            if nid != a and ids.index(nid) > i:
                out.append(f"node-overlap: {a} and {nid} overlap")
    for b in geometry.get("boundaries", []):
        members = set(b.get("members", []))
        for nid in boxes.hits((b["x"], b["y"], b["w"], b["h"])):
            if nid not in members:
                out.append(f"boundary-intrusion: {nid} lies inside boundary {b['id']} but is not a member")
    fan = float(T["grid"]["fan_min_gap"])
    sides: dict[tuple[str, str], list[float]] = {}
    for e in geometry.get("edges", []):
        if geometry.get("kind") == "flow":
            break
        for nid, pt in ((e["from"], e["points"][0]), (e["to"], e["points"][-1])):
            r = nrect.get(nid)
            if not r:
                continue
            x, y = pt
            if abs(x - r[0]) < 0.2:
                sides.setdefault((nid, "l"), []).append(y)
            elif abs(x - r[0] - r[2]) < 0.2:
                sides.setdefault((nid, "r"), []).append(y)
            elif abs(y - r[1]) < 0.2:
                sides.setdefault((nid, "t"), []).append(x)
            else:
                sides.setdefault((nid, "b"), []).append(x)
    for (nid, side), vals in sides.items():
        vals.sort()
        for a, b in zip(vals, vals[1:]):
            if b - a < fan - 0.2:
                out.append(f"port-gap: ports on the {side} side of {nid} are {b - a:.1f}px apart (minimum {fan})")
                break
    return out
