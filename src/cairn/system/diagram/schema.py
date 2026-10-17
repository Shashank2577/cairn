"""Diagram description: load (YAML or JSON), validate the shape, normalise to plain dicts.

A description is the structure the system model emits: ``diagram``, ``title``, ``scope``, ``description``,
``elements``, ``boundaries``, ``relationships`` (or ``flow.participants`` and ``flow.messages``). The loader
checks only what later stages need to run safely (types, ids, sizes); whether the diagram follows the
standard is the checker's job. Every YAML read goes through ``system.safeyaml``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .. import safeyaml
from ..limits import (
    MAX_DIAGRAM_BYTES,
    MAX_ELEMENTS,
    MAX_EVIDENCE_PER_CLAIM,
    MAX_LABEL_CHARS,
    MAX_RELATIONSHIPS,
)
from ..model import clip

MAX_FIELDS = 40
MAX_FRAGMENTS = 50

_CTRL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff￾￿]")

ELEMENT_KEYS = ("id", "name", "type", "kind", "tech", "desc", "at", "focus", "provenance", "count", "fields",
                "evidence")
REL_KEYS = ("from", "to", "what", "how", "style", "provenance", "data_class", "bidirectional", "rank_reverse",
            "computed", "evidence")
BOUNDARY_KEYS = ("id", "name", "type", "contains", "trust")
FRAGMENT_KEYS = ("type", "start", "end", "label", "else_at", "else_label")


class DiagramError(ValueError):
    """The input is not a usable diagram description (unreadable, unsafe, or the wrong shape)."""


# ------------------------------------------------------------------ field helpers
def clean(value: Any, limit: int = MAX_LABEL_CHARS) -> str:
    """One line of printable text, whitespace collapsed, control characters removed, length bounded."""
    return clip(_CTRL.sub("", str(value)), limit) or ""


def _text(v: Any, where: str, limit: int = MAX_LABEL_CHARS) -> str | None:
    if v is None:
        return None
    if isinstance(v, (dict, list, set, tuple)):
        raise DiagramError(f"{where} must be text, not a collection")
    return clean(v, limit) or None


def _flag(v: Any, where: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "on")
    raise DiagramError(f"{where} must be true or false")


def _int(v: Any, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise DiagramError(f"{where} must be a whole number")
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DiagramError(f"{where} must be a whole number") from exc


def _str_list(v: Any, where: str, cap: int) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (str, int, float)):
        v = [v]
    if not isinstance(v, (list, tuple)):
        raise DiagramError(f"{where} must be a list")
    out: list[str] = []
    for item in v:
        if isinstance(item, (dict, list)):
            raise DiagramError(f"{where} must be a list of text")
        s = clean(item)
        if s:
            out.append(s)
        if len(out) >= cap:
            break
    return out


def _mapping(v: Any, where: str) -> dict:
    if not isinstance(v, dict):
        raise DiagramError(f"{where} must be a mapping")
    return v


def _seq(v: Any, where: str, limit: int) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        raise DiagramError(f"{where} must be a list")
    if len(v) > limit:
        raise DiagramError(f"{where} has {len(v)} entries; the limit is {limit}")
    return v


# ------------------------------------------------------------------ normalisation
def _element(raw: Any, i: int) -> dict:
    where = f"elements[{i}]"
    d = _mapping(raw, where)
    eid = _text(d.get("id"), f"{where}.id")
    if not eid:
        raise DiagramError(f"{where} has no id")
    out: dict[str, Any] = {"id": eid}
    for key in ("name", "type", "kind", "tech", "desc", "provenance"):
        if key in d:
            val = _text(d[key], f"{where}.{key}")
            if val is not None:
                out[key] = val
    if d.get("at") is not None:
        at = d["at"]
        if not isinstance(at, (list, tuple)) or len(at) != 2:
            raise DiagramError(f"{where}.at must be [column, row]")
        out["at"] = [_int(at[0], f"{where}.at"), _int(at[1], f"{where}.at")]
    if "focus" in d and d["focus"] is not None:
        out["focus"] = _flag(d["focus"], f"{where}.focus")
    if d.get("count") is not None:
        out["count"] = _int(d["count"], f"{where}.count")
    if d.get("fields") is not None:
        out["fields"] = _str_list(d["fields"], f"{where}.fields", MAX_FIELDS)
    if "evidence" in d and d["evidence"] is not None:
        out["evidence"] = _str_list(d["evidence"], f"{where}.evidence", MAX_EVIDENCE_PER_CLAIM)
    return {k: out[k] for k in ELEMENT_KEYS if k in out}


def _relationship(raw: Any, where: str) -> dict:
    d = _mapping(raw, where)
    src, dst = _text(d.get("from"), f"{where}.from"), _text(d.get("to"), f"{where}.to")
    if not src or not dst:
        raise DiagramError(f"{where} needs both from and to")
    out: dict[str, Any] = {"from": src, "to": dst}
    for key in ("what", "how", "style", "provenance", "data_class"):
        if key in d:
            val = _text(d[key], f"{where}.{key}")
            if val is not None:
                out[key] = val
    for key in ("bidirectional", "rank_reverse", "computed"):
        if d.get(key) is not None:
            out[key] = _flag(d[key], f"{where}.{key}")
    if "evidence" in d and d["evidence"] is not None:
        out["evidence"] = _str_list(d["evidence"], f"{where}.evidence", MAX_EVIDENCE_PER_CLAIM)
    return {k: out[k] for k in REL_KEYS if k in out}


def _boundary(raw: Any, i: int) -> dict:
    where = f"boundaries[{i}]"
    d = _mapping(raw, where)
    bid = _text(d.get("id"), f"{where}.id")
    if not bid:
        raise DiagramError(f"{where} has no id")
    out: dict[str, Any] = {"id": bid}
    for key in ("name", "type"):
        if key in d:
            val = _text(d[key], f"{where}.{key}")
            if val is not None:
                out[key] = val
    out["contains"] = _str_list(d.get("contains"), f"{where}.contains", MAX_ELEMENTS)
    if d.get("trust") is not None:
        out["trust"] = _flag(d["trust"], f"{where}.trust")
    return {k: out[k] for k in BOUNDARY_KEYS if k in out}


def _fragment(raw: Any, i: int) -> dict:
    where = f"flow.fragments[{i}]"
    d = _mapping(raw, where)
    out: dict[str, Any] = {}
    for key in ("type", "label", "else_label"):
        if key in d:
            val = _text(d[key], f"{where}.{key}")
            if val is not None:
                out[key] = val
    for key in ("start", "end", "else_at"):
        if d.get(key) is not None:
            out[key] = _int(d[key], f"{where}.{key}")
    return {k: out[k] for k in FRAGMENT_KEYS if k in out}


def _flow(raw: Any) -> dict:
    d = _mapping(raw, "flow")
    msgs = _seq(d.get("messages"), "flow.messages", MAX_RELATIONSHIPS)
    return {
        "participants": _str_list(d.get("participants"), "flow.participants", MAX_ELEMENTS),
        "messages": [_relationship(m, f"flow.messages[{i}]") for i, m in enumerate(msgs)],
        "fragments": [_fragment(f, i) for i, f in enumerate(_seq(d.get("fragments"), "flow.fragments", MAX_FRAGMENTS))],
    }


def normalize(doc: Any) -> dict:
    """Validate the shape of a parsed document and return the canonical description (plain dicts and lists)."""
    if doc is None:
        raise DiagramError("the document is empty")
    if not isinstance(doc, dict):
        raise DiagramError("a diagram description is a mapping with diagram, title, scope, elements ...")
    out: dict[str, Any] = {}
    for key in ("diagram", "title", "scope", "description", "source_note"):
        if key in doc:
            val = _text(doc[key], key, 600 if key in ("description", "source_note") else MAX_LABEL_CHARS)
            if val is not None:
                out[key] = val
    if doc.get("legend") is not None:
        out["legend"] = _flag(doc["legend"], "legend")
    out["elements"] = [_element(e, i) for i, e in enumerate(_seq(doc.get("elements"), "elements", MAX_ELEMENTS))]
    if doc.get("boundaries") is not None:
        out["boundaries"] = [_boundary(b, i) for i, b in enumerate(_seq(doc["boundaries"], "boundaries", MAX_ELEMENTS))]
    if doc.get("relationships") is not None or doc.get("flow") is None:
        rels = _seq(doc.get("relationships"), "relationships", MAX_RELATIONSHIPS)
        out["relationships"] = [_relationship(r, f"relationships[{i}]") for i, r in enumerate(rels)]
    if doc.get("flow") is not None:
        out["flow"] = _flow(doc["flow"])
    return out


# ------------------------------------------------------------------ loading and dumping
def from_dict(doc: Any) -> dict:
    return normalize(doc)


def loads(text: str | bytes, *, max_bytes: int = MAX_DIAGRAM_BYTES) -> dict:
    """Parse YAML or JSON text (safe loader, bounded) into a normalised description."""
    try:
        doc = safeyaml.loads(text, max_bytes=max_bytes)
    except safeyaml.UnsafeYAML as exc:
        raise DiagramError(str(exc)) from exc
    return normalize(doc)


def load_file(path: str | Path, *, max_bytes: int = MAX_DIAGRAM_BYTES) -> dict:
    try:
        doc = safeyaml.load_file(Path(path), max_bytes=max_bytes)
    except safeyaml.UnsafeYAML as exc:
        raise DiagramError(str(exc)) from exc
    except OSError as exc:
        raise DiagramError(f"cannot read {Path(path).name}: {exc.strerror or exc}") from exc
    return normalize(doc)


def dumps(desc: dict) -> str:
    """Canonical YAML for a description; ``loads(dumps(d)) == d``."""
    return yaml.safe_dump(desc, sort_keys=False, allow_unicode=True, width=200, default_flow_style=False)


# ------------------------------------------------------------------ accessors used by every stage
def element_map(desc: dict) -> dict[str, dict]:
    """Elements by id; the first of two with the same id wins (the checker reports duplicates)."""
    out: dict[str, dict] = {}
    for e in desc.get("elements", []):
        out.setdefault(e["id"], e)
    return out


def relationships(desc: dict) -> list[dict]:
    """The edges of a diagram: flow messages for a flow, relationships otherwise."""
    if desc.get("diagram") == "flow":
        return list((desc.get("flow") or {}).get("messages", []))
    return list(desc.get("relationships", []))
