"""View queries over the linked product model (design decision 1: one model, many views).

``view`` returns a diagram DESCRIPTION in the shared format (docs/diagram-standard/README.md section 9):
``diagram``, ``title``, ``scope``, ``description``, ``elements``, ``boundaries``, ``relationships``, with
evidence as ``repo/path:line`` (or entry) strings, provenance on every claim and ``rank_reverse`` on
consumer edges. A view shows one level. Over the token file's node budget, the least connected
elements collapse into one "+N more" element and the scope line says so. Zero model calls.

Levels: ``context`` and ``container`` are derived here; ``component`` and ``flow`` are delegated to
:mod:`cairn.system.components` and :mod:`cairn.system.flows` (imported inside the call to keep this
module the leaf both of them format their answers with), and ``code`` lists a scope's code-level claims.

Every budget key of the token file is honoured here, so a view never needs its own numbers:
``nodes_soft`` and ``relationships`` drive collapsing, ``boundaries`` and ``accents`` cap what a view may
draw, ``label_what_chars`` and ``label_how_chars`` clip labels, and ``nodes_hand_made`` is the floor a
computed view collapses to (hand-made diagrams use it as their own ceiling).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .link import UNRESOLVED_ID, link
from .model import Element, Model, Relationship, clip

TOKENS = Path(__file__).parent / "diagram" / "tokens.json"
LEVELS = {"context": "context", "container": "container", "containers": "container",
          "component": "component", "components": "component", "flow": "flow", "flows": "flow",
          "code": "code"}
INSIDE = ("container", "data-store", "channel", "library")
CODE_KINDS = ("route", "http-call", "deploy-ref")
MAX_REFS = 5
DEFAULTS = {"nodes_soft": 12, "nodes_hand_made": 9, "relationships": 16, "accents": 2, "boundaries": 3,
            "sequence_participants": 6, "sequence_messages": 12, "sequence_fragments": 1,
            "label_what_chars": 32, "label_how_chars": 36, "accents_min": 0}


def budget(key: str = "nodes_soft", default: int | None = None) -> int:
    """One number from the token file's budget block, so views and the checker cannot disagree."""
    try:
        return int(json.loads(TOKENS.read_text(encoding="utf-8"))["budget"][key])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULTS.get(key, 12) if default is None else default


def source(src):
    """(root, repo, brain) from a Cairn instance or a repository path."""
    if hasattr(src, "project") and hasattr(src, "brain"):
        return Path(src.project.root), src.project.name, src.brain
    root = Path(src).resolve()
    return root, root.name, None


def commit_of(model: Model) -> str:
    commits = Counter(e.built_at_commit for e in model.elements.values() if e.built_at_commit)
    return commits.most_common(1)[0][0][:12] if commits else ""


def element(e: Element) -> dict:
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


def relationship(r: Relationship, *, context: bool = False) -> dict:
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


LEVEL_NAMES = ("context", "containers", "components", "flows", "code")


def view(src, level: str = "container", scope: str | None = None, *, model: Model | None = None) -> dict:
    """The diagram description of one level of this repository's product.

    ``level`` is one of ``context``, ``containers``, ``components``, ``flows`` or ``code``; ``scope``
    narrows it (a container or a repository for Containers, a container for Components, an entry point
    for Flows). Unknown levels and scopes raise ValueError, so the CLI can fail with a message.
    """
    lvl = LEVELS.get(level)
    if lvl is None:
        raise ValueError(f"unknown level {level!r}: use {', '.join(LEVEL_NAMES)}")
    root, repo, brain = source(src)
    pm = model if model is not None else link(root, repo=repo, brain=brain)
    product = getattr(pm, "product", {"system": repo, "repos": [repo], "skipped": []})
    if lvl == "context":
        return context_view(pm, product)
    if lvl == "container":
        return containers(pm, product, scope)
    if lvl == "code":
        return code_view(pm, product, scope)
    if lvl == "component":
        from . import components
        return components.view(src, pm, product, scope)
    from . import flows
    return flows.view(src, pm, product, scope)


def boundary_name(product) -> str:
    return str(product.get("system") or "system")


def scope_line(product, commit: str, extra: str = "") -> str:
    n = len(product.get("repos") or [])
    line = f"{boundary_name(product)} (software system, {n} repositor{'y' if n == 1 else 'ies'})"
    if commit:
        line += f" · commit {commit}"
    return line + (f" · {extra}" if extra else "")


def containers(pm: Model, product, scope: str | None) -> dict:
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
    ordered = sorted(rels, key=lambda r: (r.from_id, r.to_id, r.rule, r.key))
    elements, relationships, collapsed, arrows = fit([element(e) for e in els],
                                                    [relationship(r) for r in ordered])
    if collapsed:
        note = (note + " · " if note else "") + f"{collapsed} elements collapsed into “+{collapsed} more”"
    if arrows:
        note = (note + " · " if note else "") + f"{arrows} relationships left out (over the arrow budget)"
    elements = fit_accents(elements)
    inside = [e["id"] for e in elements if e["type"] in INSIDE and not outside(pm, e["id"])]
    name = boundary_name(product)
    n_inf = sum(1 for x in elements + relationships if x.get("provenance") in ("inferred", "ambiguous"))
    desc = (f"Computed by Cairn from code, manifests and deploy files with no model calls: {len(elements)} "
            f"elements and {len(relationships)} relationships, each with evidence"
            + (f"; {n_inf} marked inferred or ambiguous" if n_inf else "") + ".")
    if product.get("skipped"):
        desc += " Not mapped: " + ", ".join(f"{s['repo']} ({s['state']})" for s in product["skipped"]) + "."
    return {"diagram": "container", "title": f"Containers of {name}",
            "system": name, "repos": product.get("repos") or [repo],
            "scope": scope_line(product, commit_of(pm), note), "description": desc,
            "source_note": "computed, 0 model calls", "elements": elements,
            "boundaries": [{"id": "system:" + slug(name), "name": name, "type": "software system",
                            "contains": inside}] if inside else [],
            "relationships": relationships}


def outside(pm: Model, eid: str) -> bool:
    e = pm.elements.get(eid)
    return e is not None and bool(e.meta.get("external"))


def slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-") or "system"


def fit_boundaries(boundaries: list[dict]) -> list[dict]:
    """At most ``boundaries`` boundaries (R9); the first ones in the order the view built them."""
    return boundaries[:budget("boundaries")]


def fit_accents(elements: list[dict]) -> list[dict]:
    """Leave at most ``accents`` elements accented (R11), dropping focus from the least connected first.

    Accents are a drawing decision, never a claim: ``accents_min`` is 0, so a view that marks nothing is
    as correct as one that marks its focus, and a view that marks too many loses the surplus here rather
    than breaking the standard.
    """
    allowed = budget("accents")
    seen = 0
    for e in sorted(elements, key=lambda e: (e.get("type") == "person", e["name"].lower())):
        if e.get("focus"):
            seen += 1
            if seen > allowed:
                e["focus"] = False
    return elements


def fit(elements: list[dict], relationships: list[dict]):
    """Elements and arrows within the token file's budgets, and what that cost.

    Over the node budget the least connected elements collapse into one "+N more" element (R8); if the
    arrows are then over their own budget the node budget is lowered and the whole thing collapsed
    again, which merges arrows; whatever is still over the arrow budget is left out and counted, never
    silently. Collapsing is a pure function of its inputs, so it can simply be redone at a lower limit.
    """
    nodes, edges = budget(), budget("relationships")
    els, rels, gone = collapse(elements, relationships, nodes)
    floor = max(budget("nodes_hand_made"), 4)
    soft = edges + edges // 2  # the arrow budget is soft the same way
    while len(rels) > soft and nodes > floor:
        nodes -= 1
        els, rels, gone = collapse(elements, relationships, nodes)
    dropped = max(0, len(rels) - soft)
    return els, rels[:soft], gone, dropped


def collapse(elements: list[dict], relationships: list[dict], limit: int):
    # the node budget is soft: up to half again as many elements stay legible, where collapsing (a
    # 13-element product behind "+N more") would hide more than it saves
    if len(elements) <= limit + limit // 2:
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
             "count": len(gone), "evidence": [ref for e in gone for ref in e["evidence"][:1]][:MAX_REFS],
             "members": [{"id": e["id"], "name": e["name"], "type": e["type"], "provenance": e.get("provenance")}
                         for e in gone]}
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


def context_view(pm: Model, product) -> dict:
    name = boundary_name(product)
    sid = "system:" + slug(name)
    inside = {e.id for e in pm.elements.values() if e.level == "container" and e.type in INSIDE
              and not e.meta.get("external")}
    people = [e for e in pm.elements.values() if e.type == "person"]
    beyond = {e.id: e for e in pm.elements.values() if e.level == "container" and e.id not in inside
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
        if not ((a in beyond or a == sid or a.startswith("actor:")) and (b in beyond or b == sid)):
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
    elements = [element(e) for e in sorted(people, key=lambda e: e.name)] + [system] + \
        [element(e) for e in sorted(beyond.values(), key=lambda e: e.name) if e.id in used]
    return {"diagram": "context", "title": f"System context of {name}", "scope": scope_line(product, commit_of(pm)),
            "system": name, "repos": product.get("repos") or [repo],
            "description": f"{name} as one box with the people and outside systems around it; computed with no "
                           "model calls.",
            "source_note": "computed, 0 model calls", "elements": elements, "boundaries": [],
            "relationships": rels}


def code_view(pm: Model, product, scope: str | None) -> dict:
    """The Code view: the members of one scope, one level, from the code-level claims of the model.

    Code is where a reader drills from a component to the routes and call sites that make it up. The
    folder dependency layers overlay lives in the map layer (FR-026), not here: this view is only the
    claims the system model made, so every box still opens its evidence.
    """
    els = [e for e in pm.elements.values() if e.level == "code" and e.kind in CODE_KINDS]
    note = ""
    if scope:
        focus = {e.id for e in pm.elements.values()
                 if scope.lower() in (e.id.lower(), e.name.lower(), (e.repo or "").lower())}
        if not focus:
            raise ValueError(f"nothing in this product is called {scope!r}")
        els = [e for e in els if e.parent in focus or e.id in focus]
        note = f"scoped to {scope}"
    els = _one_claim_per_site(els)
    els.sort(key=lambda e: (e.kind, e.name.lower(), e.id))
    elements, _rels, collapsed, _dropped = fit([element(e) for e in els], [])
    if collapsed:
        note = (note + " · " if note else "") + f"{collapsed} elements collapsed into “+{collapsed} more”"
    routes = sum(1 for e in els if e.kind == "route")
    calls = sum(1 for e in els if e.kind == "http-call")
    desc = (f"The code-level claims of the system model: {routes} inbound route"
            f"{'s' if routes != 1 else ''} and {calls} outbound call site{'s' if calls != 1 else ''}, "
            f"each with evidence. Computed with no model calls.")
    title = f"Code of {scope}" if scope else f"Code of {boundary_name(product)}"
    return {"diagram": "code", "title": title, "scope": scope_line(product, commit_of(pm), note),
            "description": desc, "source_note": "computed, 0 model calls",
            "elements": fit_accents(elements), "boundaries": [], "relationships": []}


def _one_claim_per_site(els: list[Element]) -> list[Element]:
    """One box per place in the code, however many deploy units claim it.

    Two deploy units that both own ``main.go`` produce the same route twice with different element ids;
    the reader wants the route once, so the duplicates collapse here rather than showing up as boxes.
    """
    seen: set[tuple] = set()
    out: list[Element] = []
    for e in sorted(els, key=lambda e: (e.kind, e.name.lower(), e.id)):
        first = e.evidence[0].ref() if e.evidence else e.id
        key = (e.kind, e.name, first)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out
