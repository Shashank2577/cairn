"""View queries over the linked product model (design decision 1: one model, many views).

``view`` returns a diagram DESCRIPTION in the shared format (docs/diagram-standard/README.md section 9):
``diagram``, ``title``, ``scope``, ``description``, ``elements``, ``boundaries``, ``relationships``, with
evidence as ``repo/path:line`` (or entry) strings, provenance on every claim and ``rank_reverse`` on
consumer edges. A view shows one level. Over the token file's node budget, the least connected
elements collapse into one "+N more" element and the scope line says so. Zero model calls.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .link import UNRESOLVED_ID, link
from .model import Element, Model, Relationship, clip

TOKENS = Path(__file__).parent / "diagram" / "tokens.json"
LEVELS = {"context": "context", "container": "container", "containers": "container"}
INSIDE = ("container", "data-store", "channel", "library")
MAX_REFS = 5


def budget(key: str = "nodes_soft", default: int = 12) -> int:
    try:
        return int(json.loads(TOKENS.read_text(encoding="utf-8"))["budget"][key])
    except (OSError, ValueError, KeyError, TypeError):
        return default


def _source(src):
    """(root, repo, brain) from a Cairn instance or a repository path."""
    if hasattr(src, "project") and hasattr(src, "brain"):
        return Path(src.project.root), src.project.name, src.brain
    root = Path(src).resolve()
    return root, root.name, None


def _commit(model: Model) -> str:
    commits = Counter(e.built_at_commit for e in model.elements.values() if e.built_at_commit)
    return commits.most_common(1)[0][0][:12] if commits else ""


def _el(e: Element) -> dict:
    d = {"id": e.id, "name": e.name, "type": e.type}
    for k in ("kind", "tech", "desc"):
        v = getattr(e, k)
        if v:
            d[k] = v
    if "desc" not in d:  # the standard wants a one-line responsibility on every box
        d["desc"] = "Declared in system.yaml" if e.provenance == "declared" else e.type.replace("-", " ")
    d["provenance"] = e.provenance
    if e.type == "container" and e.kind:
        if e.meta.get("kind_declared"):
            d["kind_provenance"] = "declared"
        elif e.meta.get("kind_inferred"):
            d["kind_provenance"] = "inferred"
    if e.meta.get("count"):
        d["count"] = e.meta["count"]
    if e.repo:
        d["repo"] = e.repo
    if e.meta.get("not_mapped"):
        d["note"] = e.desc
    d["evidence"] = [x.ref() for x in e.evidence[:MAX_REFS]] or [e.repo or e.id]
    return d


def _rel(r: Relationship, *, context: bool = False) -> dict:
    d = {"from": r.from_id, "to": r.to_id}
    if r.what:
        d["what"] = clip(r.what, budget("label_what_chars", 32))
    if r.how and not context:
        d["how"] = clip(r.how, budget("label_how_chars", 36))
    d["style"] = r.style
    d["provenance"] = r.provenance
    d["rule"] = r.rule
    if r.provenance != "declared":
        d["computed"] = True
    if r.meta.get("rank_reverse") or r.meta.get("consumer"):
        d["rank_reverse"] = True
    if r.meta.get("candidates"):
        d["candidates"] = list(r.meta["candidates"])
    d["evidence"] = [x.ref() for x in r.evidence[:MAX_REFS]] or [r.rule]
    return d


def view(src, level: str = "container", scope: str | None = None, *, model: Model | None = None) -> dict:
    """The diagram description of one level ('context' or 'container') of this repository's product."""
    lvl = LEVELS.get(level)
    if lvl is None:
        raise ValueError(f"unknown level {level!r}: use context or container")
    root, repo, brain = _source(src)
    pm = model if model is not None else link(root, repo=repo, brain=brain)
    product = getattr(pm, "product", {"system": repo, "repos": [repo], "skipped": []})
    if lvl == "context":
        return _context(pm, product)
    return _containers(pm, product, scope)


def _boundary_name(product) -> str:
    return str(product.get("system") or "system")


def _scope_line(product, commit: str, extra: str = "") -> str:
    n = len(product.get("repos") or [])
    line = f"{_boundary_name(product)} (software system, {n} repositor{'y' if n == 1 else 'ies'})"
    if commit:
        line += f" · commit {commit}"
    line += " · 0 model calls"
    return line + (f" · {extra}" if extra else "")


def _containers(pm: Model, product, scope: str | None) -> dict:
    els = [e for e in pm.elements.values() if e.level == "container" or e.type == "person"]
    ids = {e.id for e in els}
    rels = [r for r in pm.relationships.values() if r.from_id in ids and r.to_id in ids and r.level != "code"]
    note = ""
    if scope:
        focus = {e.id for e in els if scope in (e.id, e.name, e.repo) or scope.lower() in
                 (a.lower() for a in e.meta.get("aliases") or [])}
        if not focus:
            raise ValueError(f"nothing in this product is called {scope!r}")
        keep = set(focus)
        for r in rels:
            if r.from_id in focus or r.to_id in focus:
                keep |= {r.from_id, r.to_id}
        els = [e for e in els if e.id in keep]
        rels = [r for r in rels if r.from_id in keep and r.to_id in keep]
        note = f"scoped to {scope} and its neighbours"
    els.sort(key=lambda e: (INSIDE.index(e.type) if e.type in INSIDE else 9, e.name.lower(), e.id))
    elements = [_el(e) for e in els]
    relationships = [_rel(r) for r in sorted(rels, key=lambda r: (r.from_id, r.to_id, r.rule, r.key))]
    nodes, edges = budget(), budget("relationships", 16)
    elements, relationships, collapsed = _collapse(elements, relationships, nodes)
    while len(relationships) > edges and nodes > 4:  # over the arrow budget too: collapse further
        nodes -= 1
        elements, relationships, collapsed = _collapse([_el(e) for e in els], [_rel(r) for r in sorted(
            rels, key=lambda r: (r.from_id, r.to_id, r.rule, r.key))], nodes)
    if collapsed:
        note = (note + " · " if note else "") + f"{collapsed} elements collapsed into “+{collapsed} more”"
    inside = [e["id"] for e in elements if e["type"] in INSIDE and not _outside(pm, e["id"])]
    name = _boundary_name(product)
    n_inf = sum(1 for x in elements + relationships if x.get("provenance") in ("inferred", "ambiguous"))
    desc = (f"Computed by Cairn from code, manifests and deploy files with no model calls: {len(elements)} "
            f"elements and {len(relationships)} relationships, each with evidence"
            + (f"; {n_inf} marked inferred or ambiguous" if n_inf else "") + ".")
    if product.get("skipped"):
        desc += " Not mapped: " + ", ".join(f"{s['repo']} ({s['state']})" for s in product["skipped"]) + "."
    return {"diagram": "container", "title": f"Containers of {name}",
            "scope": _scope_line(product, _commit(pm), note), "description": desc,
            "source_note": "computed, 0 model calls", "elements": elements,
            "boundaries": [{"id": "system:" + _slug(name), "name": name, "type": "software system",
                            "contains": inside}] if inside else [],
            "relationships": relationships}


def _outside(pm: Model, eid: str) -> bool:
    e = pm.elements.get(eid)
    return e is not None and bool(e.meta.get("external"))


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-") or "system"


def _collapse(elements: list[dict], relationships: list[dict], limit: int):
    if len(elements) <= limit:
        return elements, relationships, 0
    degree: Counter = Counter()
    for r in relationships:
        degree[r["from"]] += 1
        degree[r["to"]] += 1
    rank = sorted(elements, key=lambda e: (e["type"] == "person", -degree[e["id"]], e["type"] != "container",
                                           e["name"].lower()))
    keep = {e["id"] for e in rank[: limit - 1]}
    gone = [e for e in elements if e["id"] not in keep]
    gid = "collapsed:more"
    types = Counter(e["type"] for e in gone)
    group = {"id": gid, "name": f"+{len(gone)} more", "type": types.most_common(1)[0][0],
             "tech": "several", "desc": "Collapsed: " + ", ".join(e["name"] for e in gone[:6]) + (" …" if len(gone) > 6 else ""),
             "provenance": "extracted" if all(e["provenance"] == "extracted" for e in gone) else "inferred",
             "count": len(gone), "evidence": [ref for e in gone for ref in e["evidence"][:1]][:MAX_REFS]}
    out_rels: dict[tuple, dict] = {}
    for r in relationships:
        a = r["from"] if r["from"] in keep else gid
        b = r["to"] if r["to"] in keep else gid
        if a == b:
            continue
        key = (a, b, r["style"])
        if key in out_rels:
            m = out_rels[key]
            m["evidence"] = list(dict.fromkeys(m["evidence"] + r["evidence"]))[:MAX_REFS]
            if m.get("what") != r.get("what"):
                m.pop("what", None)
            if m.get("how") != r.get("how"):
                m["how"] = "several"
            if r["provenance"] != "extracted":
                m["provenance"] = r["provenance"]
            continue
        out_rels[key] = {**r, "from": a, "to": b}
    kept = [e for e in elements if e["id"] in keep] + [group]
    return kept, list(out_rels.values()), len(gone)


def _context(pm: Model, product) -> dict:
    name = _boundary_name(product)
    sid = "system:" + _slug(name)
    inside = {e.id for e in pm.elements.values() if e.level == "container" and e.type in INSIDE
              and not e.meta.get("external")}
    people = [e for e in pm.elements.values() if e.type == "person"]
    outside = {e.id: e for e in pm.elements.values() if e.level == "container" and e.id not in inside
               and e.type in ("external-system", "data-store", "channel", "system")}
    sys_ev = [x for e in pm.elements.values() if e.id in inside for x in e.evidence[:1]][:MAX_REFS]
    n_cont = sum(1 for e in pm.elements.values() if e.id in inside and e.type == "container")
    system = {"id": sid, "name": name, "type": "system",
              "desc": f"{n_cont} container{'s' if n_cont != 1 else ''} across {len(product.get('repos') or [])} "
                      f"repositor{'y' if len(product.get('repos') or []) == 1 else 'ies'}",
              "provenance": "extracted", "evidence": [x.ref() for x in sys_ev] or [name]}
    agg: dict[tuple[str, str], list[Relationship]] = defaultdict(list)
    for r in pm.relationships.values():
        a = sid if r.from_id in inside else r.from_id
        b = sid if r.to_id in inside else r.to_id
        if a == b or (a != sid and b != sid):
            continue
        if not ((a in outside or a == sid or a.startswith("actor:")) and (b in outside or b == sid)):
            continue
        agg[(a, b)].append(r)
    rels = []
    for (a, b), rs in sorted(agg.items()):
        whats = sorted({r.what for r in rs if r.what})
        prov = "extracted"
        for p in ("declared", "inferred", "ambiguous"):
            if any(r.provenance == p for r in rs) and all(r.provenance != "extracted" for r in rs):
                prov = p
        d = {"from": a, "to": b}
        if whats:
            d["what"] = clip("; ".join(whats[:2]) + (" …" if len(whats) > 2 else ""), budget("label_what_chars", 32))
        elif b == UNRESOLVED_ID:
            d["what"] = "Calls unresolved HTTP targets"
        d["computed"] = True
        d.update({"style": "async" if all(r.style == "async" for r in rs) else "sync", "provenance": prov,
                  "rule": "aggregate", "evidence": [x.ref() for r in rs for x in r.evidence[:1]][:MAX_REFS]})
        rels.append(d)
    used = {x for d in rels for x in (d["from"], d["to"])}
    elements = [_el(e) for e in sorted(people, key=lambda e: e.name)] + [system] + \
        [_el(e) for e in sorted(outside.values(), key=lambda e: e.name) if e.id in used]
    return {"diagram": "context", "title": f"System context of {name}", "scope": _scope_line(product, _commit(pm)),
            "description": f"{name} as one box with the people and outside systems around it; computed with no "
                           "model calls.",
            "source_note": "computed, 0 model calls", "elements": elements, "boundaries": [],
            "relationships": rels}
