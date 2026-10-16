"""Flows: how one request or event moves, step by step, from an entry point (FR-017, T034).

An entry point is what the system model already recorded — an endpoint (a route), a message handler (a
subscribe or a queue pop) or a command (a manifest or ``main`` entry). Nothing here is inferred from a
file's position or its name: if the model has no entry point, there is no flow.

The steps are the model relationships the entry point reaches: the inbound call, the store writes, the
publish, the next container, the subscriber that picks the message up and what it then calls. Two things
are marked rather than hidden, because a sequence diagram is mostly an ordering claim:

- steps whose position comes from one function body (same file, later line) keep their real order;
- every step whose position only follows from a callback, a middleware, an injection or a channel, and
  every step in a flow that spans more than one container, is marked ``inferred`` (FR-017).

Flows longer than the token file's ``sequence_messages`` (12 steps) or wider than its
``sequence_participants`` (6) are split, and the split is said in the scope line. Deterministic: no model
calls, no network, and every step keeps the evidence that justifies it.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .link import link
from .model import Model, Relationship, clip
from .views import MAX_REFS, budget, commit_of, element, scope_line, source

CALL_RULES = ("http-route-match", "http-config", "store-use", "pubsub-topic", "queue-key", "catalog-sdk",
               "declared")
PUBLISH_OPS = frozenset({"publish", "push"})
CONSUMER_OPS = frozenset({"subscribe", "pop"})
COMMAND_LABELS = frozenset({"[project.scripts]", "[project.gui-scripts]", "[tool.poetry.scripts]",
                            "scripts.start", "bin", "spring-boot-maven-plugin", "gradle application",
                            "csproj executable"})
KIND_RANK = {"endpoint": 0, "message": 1, "command": 2}
RULE_RANK = {"http-route-match": 0, "http-config": 1, "catalog-sdk": 2, "store-use": 3, "pubsub-topic": 4,
             "queue-key": 5, "declared": 6}
MAX_CONTAINER_HOPS = 2


@dataclass(frozen=True)
class Entry:
    """One entry point the system model recorded, and the place in the code it was recorded at."""

    id: str
    kind: str                      # endpoint | message | command
    name: str
    container: str
    how: str | None = None
    evidence: tuple[str, ...] = ()
    site: tuple[str, int] | None = None


@dataclass
class _Step:
    from_id: str
    to_id: str
    what: str | None
    how: str
    style: str
    evidence: list[str] = field(default_factory=list)
    site: tuple[str, int] | None = None
    rule: str = ""
    provenance: str = "extracted"
    via_channel: bool = False      # a delivery through a channel: FR-017 says its position is inferred


def view(src, pm: Model, product: dict, scope: str | None = None) -> dict:
    """The diagram description of one flow: the entry point ``scope`` names, else the busiest one.

    :func:`flows` returns them all, split into parts where the budgets need it; a diagram description is
    one diagram, so this is the first of the list.
    """
    built = flows(src, pm, product, scope)
    if not built:
        raise ValueError("no entry point in this product: nothing to follow yet")
    return built[0]


def flows(src, pm: Model | None = None, product: dict | None = None, scope: str | None = None,
          *, model: Model | None = None) -> list[dict]:
    """Every flow of this product, one description per flow and per split part, busiest entry point first.

    ``scope`` selects the entry point (by its name, its kind, a container or a route path). Read-only.
    """
    if pm is None:
        pm = model if model is not None else link(*source(src))
    if product is None:
        product = getattr(pm, "product", {"system": source(src)[1], "repos": [], "skipped": []})
    entries = sorted(entry_points(pm), key=lambda e: (KIND_RANK[e.kind], e.container, e.name, e.id))
    if scope:
        low = scope.lower()
        entries = [e for e in entries if low in (e.id.lower(), e.name.lower(), e.kind)
                   or low in _container_names(pm, e)]
        if not entries:
            raise ValueError(f"no entry point called {scope!r}")
    built: list[dict] = []
    for entry in entries:
        steps = steps_for(pm, entry)
        chunks = _split(steps)
        for part, chunk in enumerate(chunks, 1):
            built.append(_describe(pm, product, entry, chunk, part, len(chunks), len(steps)))
    built.sort(key=lambda d: d["title"])
    built.sort(key=lambda d: len(d["flow"]["messages"]), reverse=True)   # busiest flow first, stable
    return built


def entry_points(pm: Model) -> list[Entry]:
    """Every endpoint, message handler and command the model recorded, deterministically ordered."""
    out: list[Entry] = []
    for e in sorted(pm.elements.values(), key=lambda e: e.id):
        if e.kind == "route" and e.parent:
            method, path = e.meta.get("method"), e.meta.get("path")
            if not method or not path:
                continue
            site = _site(e.evidence)
            out.append(Entry(id=e.id, kind="endpoint", name=f"{method} {path}", container=e.parent,
                             how=f"{e.meta.get('framework')} {method} {path}".strip(),
                             evidence=tuple(x.ref() for x in e.evidence[:MAX_REFS]), site=site))
    for r in sorted(pm.relationships.values(), key=lambda r: r.id):
        if r.rule not in ("pubsub-topic", "queue-key") or r.meta.get("op") not in CONSUMER_OPS:
            continue
        name = r.what or f"Consumes {', '.join(r.meta.get('keys') or []) or 'a message'}"
        out.append(Entry(id=r.id, kind="message", name=name, container=r.from_id, how=r.how,
                         evidence=tuple(x.ref() for x in r.evidence[:MAX_REFS]), site=_site(r.evidence)))
    for e in sorted(pm.elements.values(), key=lambda e: e.id):
        if e.type != "container":
            continue
        label = next((x.entry for x in e.evidence if (x.kind == "manifest" and x.entry in COMMAND_LABELS)
                      or (x.kind == "entry" and x.entry)), None)
        if not label:
            continue
        ev = next(x for x in e.evidence if (x.kind == "manifest" and x.entry in COMMAND_LABELS)
                  or (x.kind == "entry" and x.entry))
        out.append(Entry(id=e.id, kind="command", name=f"{e.name} {label}".strip(), container=e.id,
                         evidence=(ev.ref(),), site=_site([ev])))
    return out


def steps_for(pm: Model, entry: Entry) -> list[_Step]:
    """The steps the entry point reaches across containers and stores, in the order the model supports.

    Two hops at most: from the entry's container to what it calls, and from there to what that calls
    (or, for a channel, to the subscriber the message reaches). Beyond that the walk would be a guess
    about what a request usually does next, which is exactly what this view must not draw.
    """
    inbound = [] if entry.kind == "command" else \
        [r for r in _rels(pm) if r.to_id == entry.container and r.rule in CALL_RULES
         and r.from_id != r.to_id]
    steps: list[_Step] = []
    seen_pairs: set[tuple[str, str]] = set()
    if entry.kind == "message":
        rel = pm.relationships.get(entry.id)
        if rel is not None:
            steps.append(_Step(rel.to_id, rel.from_id, rel.what or "Delivers a message",
                               rel.how or "channel delivery", "async",
                               [x.ref() for x in rel.evidence[:MAX_REFS]] or list(entry.evidence[:1]),
                               entry.site, rel.rule, rel.provenance, True))
            seen_pairs.add((rel.to_id, rel.from_id))
    if inbound:
        caller = sorted(inbound, key=lambda r: (r.from_id, r.rule))[0]
        steps.append(_Step(caller.from_id, entry.container, _inbound_what(caller, entry),
                           caller.how or entry.how or "HTTP", "async" if caller.style == "async" else "sync",
                           [x.ref() for x in caller.evidence[:MAX_REFS]] or list(entry.evidence[:1]),
                           entry.site, caller.rule, caller.provenance))
        seen_pairs.add((caller.from_id, entry.container))
    frontier = [entry.container]
    visited = {entry.container}
    for hop in range(MAX_CONTAINER_HOPS):
        nxt: list[str] = []
        for cid in frontier:
            for step in _outbound(pm, cid, entry):
                if (step.from_id, step.to_id) in seen_pairs:
                    continue
                seen_pairs.add((step.from_id, step.to_id))
                steps.append(step)
                target = pm.elements.get(step.to_id)
                if target is None or target.type != "container":
                    continue
                if hop + 1 < MAX_CONTAINER_HOPS and target.id not in visited:
                    visited.add(target.id)
                    nxt.append(target.id)
        frontier = nxt
        if not frontier:
            break
    return steps


def _rels(pm: Model) -> list[Relationship]:
    return sorted(pm.relationships.values(), key=lambda r: (r.from_id, r.to_id, r.rule, r.key))


def _outbound(pm: Model, cid: str, entry: Entry) -> list[_Step]:
    """One step per relationship this container has, ordered by where the call site is in the code."""
    out: list[_Step] = []
    for r in _rels(pm):
        if r.from_id != cid or r.rule not in CALL_RULES:
            continue
        target = pm.elements.get(r.to_id)
        if target is None or target.type not in ("container", "data-store", "channel", "external-system"):
            continue
        op = r.meta.get("op")
        what = r.what or _default_what(r, target.name)
        style = "async" if r.style == "async" else "sync"
        if op in CONSUMER_OPS:
            continue                       # the delivery into this container is the entry, not a step of it
        out.append(_Step(cid, r.to_id, what, r.how or _default_how(r), style,
                         [x.ref() for x in r.evidence[:MAX_REFS]], _site(r.evidence), r.rule, r.provenance))
        if r.to_id in {e.id for e in pm.elements.values() if e.type in ("data-store", "channel")}:
            out.extend(_deliveries(pm, r.to_id))
    out.sort(key=lambda s: (0 if entry.site and s.site and s.site[0] == entry.site[0] else 1,
                            s.site[0] if s.site else "", s.site[1] if s.site else 0,
                            RULE_RANK.get(s.rule, 9), s.to_id, s.what or ""))
    return out


def _deliveries(pm: Model, channel: str) -> list[_Step]:
    """The subscribers a channel delivers to: the other half of the pub/sub claim, in the model's words."""
    out: list[_Step] = []
    for r in _rels(pm):
        if r.from_id == channel or r.to_id != channel or r.meta.get("op") not in CONSUMER_OPS:
            continue
        out.append(_Step(channel, r.from_id, r.what or "Delivers a message", r.how or "channel delivery",
                         "async", [x.ref() for x in r.evidence[:MAX_REFS]], _site(r.evidence), r.rule,
                         r.provenance, True))
    return out


def _inbound_what(r: Relationship, entry: Entry) -> str | None:
    return r.what or (entry.name if entry.kind == "endpoint" else None)


def _default_what(r: Relationship, target: str) -> str:
    op = r.meta.get("op")
    if op in PUBLISH_OPS:
        return f"Publishes to {target}"
    if op in CONSUMER_OPS:
        return f"Consumes from {target}"
    if r.rule == "store-use":
        return f"Reads and writes {target}"
    if r.rule == "catalog-sdk":
        return f"Calls {target}"
    return f"Calls {target}"


def _default_how(r: Relationship) -> str:
    return {"http-route-match": "HTTP", "http-config": "HTTP", "store-use": "SQL", "catalog-sdk": "SDK",
            "pubsub-topic": "publish / subscribe", "queue-key": "queue"}.get(r.rule, "call")


def _split(steps: list[_Step]) -> list[list[_Step]]:
    """Sequences within the token file's message and participant budgets; never a step silently dropped."""
    messages, participants = budget("sequence_messages"), budget("sequence_participants")
    chunks: list[list[_Step]] = []
    current: list[_Step] = []
    seen: set[str] = set()
    for step in steps:
        want = seen | {step.from_id, step.to_id}
        if current and (len(current) >= messages or len(want) > participants):
            chunks.append(current)
            current, seen = [], set()
            want = {step.from_id, step.to_id}
        current.append(step)
        seen = want
    if current:
        chunks.append(current)
    return chunks


def _describe(pm: Model, product: dict, entry: Entry, steps: list[_Step], part: int, parts: int,
              total: int) -> dict:
    ids: list[str] = []
    for s in steps:
        for i in (s.from_id, s.to_id):
            if i not in ids:
                ids.append(i)
    spans = sum(1 for i in ids if i in pm.elements and pm.elements[i].type == "container") > 1
    messages = [_message(s, i, steps, spans) for i, s in enumerate(steps)]
    inferred = sum(1 for m in messages if m["provenance"] == "inferred")
    elements = [_participant(pm, i) for i in ids]
    owner = pm.elements.get(entry.container)
    title = f"Flow of {_titled(entry, owner)}"
    if parts > 1:
        title += f" ({part} of {parts})"
    note = f"from {entry.kind} {entry.name}" + (f" in {owner.name}" if owner is not None else "")
    note += f" · {inferred} of {len(messages)} steps inferred" if inferred else \
        " · ordered by position in one function body"
    if len(steps) < total:
        note += f" · {total - len(steps)} steps continue in the following parts"
    desc = (f"The steps {entry.name} reaches across "
            + ("more than one container, so every step's position is inferred"
               if spans else "this container, ordered by where each call site sits in the code")
            + ". Every step carries its evidence; computed with no model calls.")
    return {"diagram": "flow", "title": title, "scope": scope_line(product, commit_of(pm), note),
            "description": desc, "source_note": "computed, 0 model calls", "elements": elements,
            "boundaries": [], "flow": {"participants": ids, "messages": messages, "fragments": []}}


def _titled(entry: Entry, owner) -> str:
    """The entry point named the way a reader would: with its container when the name alone is ambiguous."""
    if owner is not None and owner.name and owner.name.lower() not in entry.name.lower():
        return f"{owner.name} {entry.name}"
    return entry.name


def _message(step: _Step, i: int, steps: list[_Step], spans: bool) -> dict:
    previous = steps[i - 1] if i else None
    same_body = bool(previous and step.site and previous.site and step.site[0] == previous.site[0]
                     and step.site[1] >= previous.site[1])
    ordered = not spans and not step.via_channel and (i == 0 or same_body)
    d = {"from": step.from_id, "to": step.to_id, "style": step.style,
         "provenance": step.provenance if ordered else "inferred",
         "rule": step.rule, "computed": True, "evidence": step.evidence or ["no evidence recorded"]}
    if step.what:
        d["what"] = clip(step.what, budget("label_what_chars", 32))
    if step.how:
        d["how"] = clip(step.how, budget("label_how_chars", 36))
    return d


def _participant(pm: Model, eid: str) -> dict:
    e = pm.elements.get(eid)
    if e is None:
        return {"id": eid, "name": eid, "type": "system", "desc": "Not in the model",
                "provenance": "inferred", "evidence": [eid]}
    d = element(e)
    if e.type in ("container", "data-store", "channel", "component") and not d.get("tech"):
        d["tech"] = e.type.replace("-", " ")   # the standard requires a technology on these boxes
    return d


def _site(evidence) -> tuple[str, int] | None:
    """(file, line) of the operation site in the evidence, so steps can be ordered by position."""
    for ev in evidence:
        if ev.file and ev.line:
            return ev.file, ev.line
    return None


def _container_names(pm: Model, entry: Entry) -> set[str]:
    e = pm.elements.get(entry.container)
    return {e.name.lower(), e.repo.lower()} if e and e.name and e.repo else set()


def by_container(pm: Model) -> dict[str, list[Entry]]:
    """Entry points grouped by their container, for the UI's container drill-down."""
    out: dict[str, list[Entry]] = defaultdict(list)
    for e in sorted(entry_points(pm), key=lambda e: (KIND_RANK[e.kind], e.container, e.name, e.id)):
        out[e.container].append(e)
    return dict(out)


