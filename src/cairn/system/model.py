"""Types of the system model and their storage in the repository's own brain.db.

Rules every writer follows (data-model.md):
- ids are stable strings: ``<repo>:<type>:<key>`` for elements; relationship ids hash (from, to, rule, key).
- evidence paths are stored POSIX-style and relative to the repository root.
- configuration values are never stored, only names; names the catalog marks sensitive are stored as kind.
- a claim that is no longer derived is marked stale, never silently deleted (recheck.py).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from .limits import MAX_EVIDENCE_PER_CLAIM, MAX_LABEL_CHARS

LEVELS = ("landscape", "context", "container", "component", "code")
ELEMENT_TYPES = ("person", "system", "external-system", "container", "data-store", "channel", "library",
                 "component", "code", "entity", "state")
STYLES = ("sync", "async", "build", "return")
PROVENANCE = ("extracted", "declared", "inferred", "ambiguous", "stale")


def posix(path: str | None) -> str | None:
    """A repository-relative path in POSIX form, whatever OS produced it; never absolute, never '..'."""
    if path is None:
        return None
    p = str(path).replace("\\", "/")
    if PureWindowsPath(p).drive:
        p = p.split(":", 1)[1]
    out: list[str] = []
    for x in PurePosixPath(p).parts:
        if x in ("/", "", "."):
            continue
        if x == "..":
            if out:
                out.pop()          # resolve against what came before; never climb above the root
            continue
        out.append(x)
    return "/".join(out) or None


def clip(text: Any, limit: int = MAX_LABEL_CHARS) -> str | None:
    if text is None:
        return None
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


@dataclass(frozen=True)
class Evidence:
    repo: str
    kind: str                       # route, call, publish, subscribe, driver, image, script, dockerfile, manifest, declared, config
    file: str | None = None
    line: int | None = None
    entry: str | None = None        # manifest or deploy entry, e.g. "services.db image postgres:16"
    commit: str = ""

    def __post_init__(self):
        object.__setattr__(self, "file", posix(self.file))
        object.__setattr__(self, "entry", clip(self.entry))

    def ref(self) -> str:
        """Human and checker readable: ``repo/path:line`` or ``repo/path: entry`` or the entry alone."""
        base = "/".join(x for x in (self.repo, self.file) if x)
        if self.line:
            return f"{base}:{self.line}" if base else str(self.line)
        if self.entry:
            return f"{base}: {self.entry}" if base else self.entry
        return base or self.kind


@dataclass
class Element:
    id: str
    type: str
    name: str
    level: str = "container"
    repo: str = ""
    kind: str | None = None
    tech: str | None = None
    desc: str | None = None
    parent: str | None = None
    provenance: str = "extracted"
    evidence: list[Evidence] = field(default_factory=list)
    stale_reason: str | None = None
    stale_since: float | None = None
    built_at_commit: str = ""
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.type not in ELEMENT_TYPES:
            raise ValueError(f"unknown element type {self.type!r}")
        if self.provenance not in PROVENANCE:
            raise ValueError(f"unknown provenance {self.provenance!r}")
        self.name = clip(self.name) or self.id
        self.tech, self.desc, self.kind = clip(self.tech), clip(self.desc), clip(self.kind, 40)

    def add_evidence(self, *ev: Evidence) -> None:
        for e in ev:
            if e not in self.evidence and len(self.evidence) < MAX_EVIDENCE_PER_CLAIM:
                self.evidence.append(e)


@dataclass
class Relationship:
    from_id: str
    to_id: str
    rule: str                       # http-route-match, pubsub-topic, queue-key, store-use, catalog-sdk, package-dep, declared, ...
    how: str | None = None
    what: str | None = None
    style: str = "sync"
    level: str = "container"
    repo: str = ""
    provenance: str = "extracted"
    evidence: list[Evidence] = field(default_factory=list)
    data_class: str | None = None
    stale_reason: str | None = None
    stale_since: float | None = None
    built_at_commit: str = ""
    meta: dict = field(default_factory=dict)
    key: str = ""                   # disambiguates two relationships between the same pair by the same rule

    def __post_init__(self):
        if self.style not in STYLES:
            raise ValueError(f"unknown style {self.style!r}")
        if self.provenance not in PROVENANCE:
            raise ValueError(f"unknown provenance {self.provenance!r}")
        self.what, self.how = clip(self.what, 80), clip(self.how, 120)

    @property
    def id(self) -> str:
        h = hashlib.sha1(f"{self.from_id}\0{self.to_id}\0{self.rule}\0{self.key}".encode("utf-8")).hexdigest()
        return "rel:" + h[:16]

    def add_evidence(self, *ev: Evidence) -> None:
        for e in ev:
            if e not in self.evidence and len(self.evidence) < MAX_EVIDENCE_PER_CLAIM:
                self.evidence.append(e)


@dataclass
class Model:
    elements: dict[str, Element] = field(default_factory=dict)
    relationships: dict[str, Relationship] = field(default_factory=dict)

    def add(self, e: Element) -> Element:
        if e.id in self.elements:
            self.elements[e.id].add_evidence(*e.evidence)
            return self.elements[e.id]
        self.elements[e.id] = e
        return e

    def relate(self, r: Relationship) -> Relationship:
        if r.id in self.relationships:
            self.relationships[r.id].add_evidence(*r.evidence)
            return self.relationships[r.id]
        self.relationships[r.id] = r
        return r


# ---------------------------------------------------------------- storage
def _ev_rows(claim_id: str, evs: Iterable[Evidence]):
    return [(claim_id, e.repo, e.file, e.line, e.entry, e.kind, e.commit) for e in evs]


def save(brain, model: Model, repo: str, commit: str = "") -> dict:
    """Replace this repository's computed claims with ``model``'s, in one transaction.

    Stale claims of this repository (marked by recheck) are kept until recheck clears them, so they stay
    visible; everything else for the repository is rewritten. Returns counts.
    """
    now = time.time()
    with brain.tx() as db:
        db.execute("DELETE FROM sm_evidence WHERE claim_id IN (SELECT id FROM sm_element WHERE repo=? AND stale_reason IS NULL)"
                   " OR claim_id IN (SELECT id FROM sm_relationship WHERE repo=? AND stale_reason IS NULL)", (repo, repo))
        db.execute("DELETE FROM sm_element WHERE repo=? AND stale_reason IS NULL", (repo,))
        db.execute("DELETE FROM sm_relationship WHERE repo=? AND stale_reason IS NULL", (repo,))
        for e in model.elements.values():
            db.execute("INSERT OR REPLACE INTO sm_element(id, repo, level, type, kind, name, tech, descr, parent,"
                       " provenance, stale_reason, stale_since, built_at_commit, meta, updated_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (e.id, e.repo or repo, e.level, e.type, e.kind, e.name, e.tech, e.desc, e.parent, e.provenance,
                        e.stale_reason, e.stale_since, e.built_at_commit or commit, json.dumps(e.meta), now))
            db.execute("DELETE FROM sm_evidence WHERE claim_id=?", (e.id,))
            db.executemany("INSERT INTO sm_evidence VALUES(?,?,?,?,?,?,?)", _ev_rows(e.id, e.evidence))
        for r in model.relationships.values():
            db.execute("INSERT OR REPLACE INTO sm_relationship(id, repo, from_id, to_id, level, what, how, style, rule,"
                       " provenance, data_class, stale_reason, stale_since, built_at_commit, meta, updated_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (r.id, r.repo or repo, r.from_id, r.to_id, r.level, r.what, r.how, r.style, r.rule, r.provenance,
                        r.data_class, r.stale_reason, r.stale_since, r.built_at_commit or commit,
                        json.dumps({**r.meta, "key": r.key}), now))
            db.execute("DELETE FROM sm_evidence WHERE claim_id=?", (r.id,))
            db.executemany("INSERT INTO sm_evidence VALUES(?,?,?,?,?,?,?)", _ev_rows(r.id, r.evidence))
    return {"elements": len(model.elements), "relationships": len(model.relationships)}


def load(brain, repo: str | None = None) -> Model:
    """The stored model, for one repository or all of them (the store holds only this repository's
    claims plus nothing from siblings; siblings are read with ``load_readonly``)."""
    m = Model()
    where, args = ("WHERE repo=?", (repo,)) if repo is not None else ("", ())
    evs: dict[str, list[Evidence]] = {}
    for row in brain.q("SELECT * FROM sm_evidence"):
        evs.setdefault(row["claim_id"], []).append(
            Evidence(repo=row["repo"], kind=row["kind"], file=row["file"], line=row["line"], entry=row["entry"],
                     commit=row["commit_confirmed"]))
    for row in brain.q(f"SELECT * FROM sm_element {where}", args):
        m.elements[row["id"]] = Element(
            id=row["id"], type=row["type"], name=row["name"], level=row["level"], repo=row["repo"], kind=row["kind"],
            tech=row["tech"], desc=row["descr"], parent=row["parent"], provenance=row["provenance"],
            evidence=evs.get(row["id"], []), stale_reason=row["stale_reason"], stale_since=row["stale_since"],
            built_at_commit=row["built_at_commit"], meta=json.loads(row["meta"] or "{}"))
    for row in brain.q(f"SELECT * FROM sm_relationship {where}", args):
        meta = json.loads(row["meta"] or "{}")
        r = Relationship(from_id=row["from_id"], to_id=row["to_id"], rule=row["rule"], how=row["how"], what=row["what"],
                         style=row["style"], level=row["level"], repo=row["repo"], provenance=row["provenance"],
                         evidence=evs.get(row["id"], []), data_class=row["data_class"], stale_reason=row["stale_reason"],
                         stale_since=row["stale_since"], built_at_commit=row["built_at_commit"],
                         meta={k: v for k, v in meta.items() if k != "key"}, key=meta.get("key", ""))
        m.relationships[row["id"]] = r
    return m


def to_dict(x) -> dict:
    d = asdict(x)
    d["evidence"] = [e.ref() for e in x.evidence]
    if isinstance(x, Relationship):
        d["id"] = x.id
    return d
