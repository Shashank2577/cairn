"""Mermaid import: a flowchart subset, sequenceDiagram (flows) and C4 syntax become a diagram description.

Nothing is evaluated: the text is scanned line by line and only recognised constructs are read; styling,
interaction (click, callbacks, links) and unknown syntax are ignored with a warning, never executed. Facts
the standard needs (type, technology, evidence, provenance ...) come from ``%% cairn`` comments, in JSON form
(what ``mermaid_out`` writes) or short ``key=value`` form (for people)::

    %% cairn api type=container tech="Python · FastAPI" evidence="shop-api/main.py:3; docker-compose.yml: api"
    %% cairn api->db how="SQL" style=sync
    %% cairn:element api {"type":"container"}        (JSON; also :rel, :boundary, :msg, :diagram)
"""
from __future__ import annotations

import html.entities
import json
import re
import shlex
from typing import Any, NamedTuple

from ..limits import MAX_DIAGRAM_BYTES, MAX_ELEMENTS, MAX_RELATIONSHIPS
from .schema import BOUNDARY_KEYS, ELEMENT_KEYS, REL_KEYS, DiagramError, clean, normalize

MAX_LINES = 5000
MAX_LINE_CHARS = 20_000
MAX_WARNINGS = 100


class MermaidError(ValueError):
    """The text is not Mermaid this importer can read, or exceeds a limit."""


class Imported(NamedTuple):
    description: dict
    warnings: list[str]


_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]*>")
_ENT = re.compile(r"#(\d{1,7}|[A-Za-z]{2,10});")
_ENT_TAIL = re.compile(r"#(?:\d{1,7}|[A-Za-z]{2,10})$")
_WORD = re.compile(r"[A-Za-z0-9_]+")
_BOOL_KEYS = {"focus", "trust", "bidirectional", "rank_reverse", "computed", "legend"}
_LIST_KEYS = {"evidence", "fields", "contains"}
DIAGRAM_KEYS = ("diagram", "title", "scope", "description", "source_note", "legend")
_STYLE_WORDS = {"classdef", "class", "style", "linkstyle"}
_CLICK_WORDS = {"click", "callback", "call", "href", "link", "links"}
_OPENERS = [("(((", (")))",)), ("((", ("))",)), ("([", ("])",)), ("[(", (")]",)), ("[[", ("]]",)), ("{{", ("}}",)),
            ("[/", ("/]", "\\]")), ("[\\", ("\\]", "/]")), ("(", (")",)), ("[", ("]",)), ("{", ("}",)), (">", ("]",))]
_SHAPE_TYPE = {"([": "person", "[(": "data-store", "[[": "channel", "[/": "library", "{{": "container"}


# ------------------------------------------------------------------ text helpers
def unescape(s: str) -> str:
    """Decode Mermaid ``#60;`` and ``#quot;`` entities. Applied to text only, never to markup."""
    def sub(m: re.Match) -> str:
        v = m.group(1)
        if v.isdigit():
            n = int(v)
            return chr(n) if 0 < n < 0x110000 and not 0xD800 <= n <= 0xDFFF else ""
        cp = html.entities.name2codepoint.get(v)
        return chr(cp) if cp else m.group(0)
    return _ENT.sub(sub, s)


def _plain(raw: str) -> str:
    return clean(unescape(_TAG.sub("", raw)))


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        s = s[1:-1]
    if len(s) >= 2 and s[0] == s[-1] == "`":
        s = s[1:-1]
    return s


class _Warn:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.seen: set[str] = set()
        self.dropped = 0
        self.styling = 0

    def add(self, msg: str, once: bool = False) -> None:
        if once and msg in self.seen:
            return
        self.seen.add(msg)
        if len(self.items) < MAX_WARNINGS:
            self.items.append(msg)
        else:
            self.dropped += 1

    def done(self) -> list[str]:
        out = list(self.items)
        if self.styling:
            out.append(f"ignored {self.styling} styling statement(s) (classDef, class, style, linkStyle)")
        if self.dropped:
            out.append(f"... and {self.dropped} more warning(s)")
        return out


# ------------------------------------------------------------------ cairn comments
class _Meta:
    def __init__(self, warn: _Warn) -> None:
        self.warn = warn
        self.diagram: dict = {}
        self.elements: list[tuple[str, dict]] = []     # target (a node or subgraph id), facts
        self.full: set[str] = set()                    # targets described completely by a JSON comment
        self.rels: list[tuple[str, dict]] = []         # "a->b", facts
        self.msgs: list[tuple[int, dict]] = []

    def add_line(self, line: str, n: int) -> None:
        body = line[2:].strip()
        if body.startswith("cairn:"):
            kind, _, rest = body[6:].partition(" ")
            self._json(kind.strip(), rest.strip(), n)
        elif body.startswith("cairn ") or body == "cairn":
            self._short(body[5:].strip(), n)

    def _json(self, kind: str, rest: str, n: int) -> None:
        try:
            if kind == "diagram":
                target, payload = "", rest
            else:
                target, _, payload = rest.partition(" ")
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (ValueError, RecursionError):
            self.warn.add(f"line {n}: unreadable %% cairn:{kind} comment ignored")
            return
        if kind in ("rel", "msg"):
            data["__json"] = True
        self._store(kind, target.strip(), data)
        if kind == "element":
            self.full.add(target.strip())

    def _short(self, rest: str, n: int) -> None:
        try:
            toks = shlex.split(rest)
        except ValueError:
            self.warn.add(f"line {n}: unreadable %% cairn comment ignored (unbalanced quotes)")
            return
        if not toks:
            return
        head, kv = toks[0], toks[1:]
        data: dict[str, Any] = {}
        target = head
        if head == "msg" and kv and kv[0].isdigit():
            target, kv = kv[0], kv[1:]
            head = "msg"
        for item in kv:
            if "=" not in item:
                self.warn.add(f"line {n}: ignored {item!r} in %% cairn comment (expected key=value)", once=True)
                continue
            k, v = item.split("=", 1)
            data[k.strip()] = v
        kind = "diagram" if head == "diagram" else "msg" if head == "msg" else "rel" if "->" in head else "element"
        self._store(kind, "" if kind == "diagram" else target, data)

    def _store(self, kind: str, target: str, data: dict) -> None:
        if kind == "diagram":
            self.diagram.update(data)
        elif kind in ("element", "boundary"):
            self.elements.append((target, data))
        elif kind == "rel":
            self.rels.append((target, data))
        elif kind == "msg" and target.isdigit():
            self.msgs.append((int(target), data))
        else:
            self.warn.add(f"unknown %% cairn:{kind} comment ignored", once=True)


def _coerce(key: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if key in _BOOL_KEYS:
        return value.strip().lower() in ("true", "yes", "1", "on")
    if key in _LIST_KEYS:
        return [x.strip() for x in value.split(";") if x.strip()]
    if key == "at":
        return [x.strip() for x in value.split(",")]
    if key == "count":
        return value.strip()
    return value


def _apply(target: dict, facts: dict, allowed: tuple[str, ...], skip: tuple[str, ...], warn: _Warn, where: str) -> None:
    for k, v in facts.items():
        if k in skip:
            continue
        if k not in allowed:
            warn.add(f"unknown key {k!r} in %% cairn comment for {where} ignored", once=True)
            continue
        target[k] = _coerce(k, v)


# ------------------------------------------------------------------ label parsing
def _split_label(raw: str) -> list[tuple[str, str]]:
    """Parts of a label as (raw html, plain text); empty parts dropped."""
    out = []
    for part in _BR.split(raw):
        plain = _plain(part)
        if plain:
            out.append((part, plain))
    return out


def _node_text(raw: str) -> dict:
    parts = _split_label(raw)
    info: dict[str, str] = {}
    rest: list[str] = []
    for html_part, plain in parts:
        low = html_part.strip().lower()
        if low.startswith("<small") and "name" not in info:
            continue
        if low.startswith("<b>") and "name" not in info:
            info["name"] = plain
        elif plain.startswith("[") and plain.endswith("]") and "tech" not in info and len(plain) > 2:
            info["tech"] = plain[1:-1].strip()
        else:
            rest.append(plain)
    if "name" not in info and rest:
        info["name"] = rest.pop(0)
    if rest:
        info["desc"] = " ".join(rest)
    return info


def _edge_text(raw: str) -> dict:
    info: dict[str, str] = {}
    what: list[str] = []
    for _, plain in _split_label(raw):
        if plain.startswith("[") and plain.endswith("]") and "how" not in info and len(plain) > 2:
            info["how"] = plain[1:-1].strip()
        elif plain.lower().startswith("data:") and "data_class" not in info:
            info["data_class"] = plain[5:].strip()
        else:
            what.append(plain)
    if what:
        info["what"] = " ".join(what)
    return info


def _what_how(text: str) -> dict:
    info = _edge_text(text)
    if "how" not in info and "what" in info:
        m = re.match(r"^(.*?)\s*\[(.+)\]$", info["what"])
        if m and m.group(1):
            info["what"], info["how"] = m.group(1), m.group(2)
    return info


# ------------------------------------------------------------------ line preparation
def _prepare(text: str | bytes, warn: _Warn) -> tuple[list[tuple[int, str]], str | None]:
    if isinstance(text, bytes):
        if len(text) > MAX_DIAGRAM_BYTES:
            raise MermaidError(f"larger than {MAX_DIAGRAM_BYTES} bytes")
        text = text.decode("utf-8", "replace")
    if len(text) > MAX_DIAGRAM_BYTES:
        raise MermaidError(f"larger than {MAX_DIAGRAM_BYTES} bytes")
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise MermaidError("binary data is not Mermaid")
    raw = text.split("\n")
    if len(raw) > MAX_LINES:
        raise MermaidError(f"{len(raw)} lines; the limit is {MAX_LINES}")
    title = None
    lines: list[tuple[int, str]] = []
    i = 0
    while i < len(raw) and not raw[i].strip():
        i += 1
    if i < len(raw) and raw[i].strip() == "---":
        j = i + 1
        while j < len(raw) and raw[j].strip() != "---":
            m = re.match(r"^\s*title\s*:\s*(.*)$", raw[j])
            if m:
                title = _front_title(m.group(1))
            j += 1
        i = j + 1
    for n in range(i, len(raw)):
        ln = raw[n].strip()
        if len(ln) > MAX_LINE_CHARS:
            warn.add(f"line {n + 1}: longer than {MAX_LINE_CHARS} characters, ignored")
            continue
        if ln:
            lines.append((n + 1, ln))
    return lines, title


def _front_title(v: str) -> str:
    v = v.strip()
    if v.startswith('"'):
        try:
            return clean(json.loads(v))
        except ValueError:
            pass
    return clean(_unquote(v))


def _split_statements(line: str) -> list[str]:
    """Split on ``;`` outside quotes and brackets (Mermaid allows several statements per line)."""
    out, cur, depth, quote = [], [], 0, False
    for ch in line:
        if ch == '"':
            quote = not quote
        elif not quote:
            if ch in "[({":
                depth += 1
            elif ch in "])}":
                depth = max(0, depth - 1)
            elif ch == ";" and depth == 0 and not _ENT_TAIL.search("".join(cur[-9:])):
                out.append("".join(cur))
                cur = []
                continue
        cur.append(ch)
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


# ------------------------------------------------------------------ flowchart
class _Node(NamedTuple):
    id: str
    opener: str | None
    text: str | None


_LINK_TEXT = re.compile(r"(?P<l><)?(?P<a>--|==|-\.)(?P<t>[ \t]+[^\n]*?[ \t]+)(?P<b>-->|---|==>|===|\.->|\.-|--o|--x)")
_LINK = re.compile(r"(?P<l><)?(?P<body>-{2,}|={2,}|-\.+-?)(?P<h>[>ox])?")
_PIPE = re.compile(r"\s*\|\s*(.*?)\s*\|")


def _parse_node(s: str, pos: int) -> tuple[_Node, int] | None:
    m = _WORD.match(s, pos)
    if not m:
        return None
    nid, pos = m.group(0), m.end()
    opener = None
    text = None
    for o, closers in _OPENERS:
        if s.startswith(o, pos):
            body = pos + len(o)
            if body < len(s) and s[body] == '"':
                end = s.find('"', body + 1)
                if end < 0:
                    return None
                text, after = s[body + 1:end], end + 1
                while after < len(s) and s[after] == " ":
                    after += 1
                closer = next((c for c in closers if s.startswith(c, after)), None)
                if closer is None:
                    return None
                pos = after + len(closer)
            else:
                hits = [(s.find(c, body), c) for c in closers if s.find(c, body) >= 0]
                if not hits:
                    return None
                at, closer = min(hits)
                text, pos = s[body:at], at + len(closer)
            opener = o
            break
    if s.startswith(":::", pos):
        m2 = _WORD.match(s, pos + 3)
        pos = m2.end() if m2 else pos + 3
    return _Node(nid, opener, _unquote(text) if text is not None else None), pos


def _skip_ws(s: str, pos: int) -> int:
    while pos < len(s) and s[pos] in " \t":
        pos += 1
    return pos


def _parse_link(s: str, pos: int) -> tuple[dict, int] | None:
    m = _LINK_TEXT.match(s, pos)
    if m:
        body = m.group("a") + m.group("b")
        info = {"dotted": "." in body, "bidir": bool(m.group("l")), "label": m.group("t").strip()}
        return info, m.end()
    m = _LINK.match(s, pos)
    if not m:
        return None
    body = m.group("body")
    info = {"dotted": "." in body, "bidir": bool(m.group("l")), "label": None}
    pos = m.end()
    p = _PIPE.match(s, pos)
    if p:
        info["label"] = _unquote(p.group(1))
        pos = p.end()
    return info, pos


def _parse_flowchart(lines: list[tuple[int, str]], kind: str, warn: _Warn) -> dict:
    els: dict[str, dict] = {}
    edges: list[dict] = []
    subs: list[dict] = []
    stack: list[dict] = []
    member_of: dict[str, str] = {}
    sub_ids: set[str] = set()

    def touch(node: _Node) -> None:
        e = els.setdefault(node.id, {"id": node.id, "name": node.id})
        if len(els) > MAX_ELEMENTS:
            raise MermaidError(f"more than {MAX_ELEMENTS} nodes")
        if node.text is not None:
            info = _node_text(node.text)
            e.update({k: v for k, v in info.items()})
            hint = _SHAPE_TYPE.get(node.opener or "")
            if node.opener == "(" and kind == "state":
                hint = "state"
            if hint and "type" not in e:
                e["type"] = hint
            if node.opener and node.opener not in _SHAPE_TYPE and node.opener not in ("[", "(", "{{"):
                warn.add(f"shape {node.opener!r} treated as a plain box", once=True)
        if stack and node.id not in member_of and node.id not in sub_ids:
            member_of[node.id] = stack[-1]["id"]
            stack[-1]["contains"].append(node.id)

    for n, line in lines:
        if line.startswith("%%"):
            continue
        for stmt in _split_statements(line):
            word = stmt.split(None, 1)[0].lower() if stmt else ""
            if re.match(r"^(flowchart|graph)\b", stmt, re.I):
                continue
            if word == "subgraph":
                sid, title = _subgraph(stmt[8:].strip(), len(subs) + 1)
                if stack:
                    warn.add(f"line {n}: nested subgraph {sid!r} flattened (boundaries do not nest)", once=True)
                if sid in els or sid in sub_ids:
                    sid = f"{sid}_{len(subs) + 1}"
                name, _, btype = title.partition(" · ")
                sub = {"id": sid, "name": clean(unescape(_TAG.sub("", name))) or sid, "contains": []}
                if btype:
                    sub["type"] = clean(unescape(btype))
                subs.append(sub)
                sub_ids.add(sid)
                stack.append(sub)
                continue
            if word == "end":
                if stack:
                    stack.pop()
                else:
                    warn.add(f"line {n}: stray 'end' ignored")
                continue
            if word in _STYLE_WORDS:
                warn.styling += 1
                continue
            if word in _CLICK_WORDS:
                warn.add(f"line {n}: '{word}' ignored (interactions and callbacks are never evaluated)")
                continue
            if word in ("direction", "acctitle", "accdescr", "title"):
                continue
            if not _chain(stmt, n, touch, edges, sub_ids, warn):
                warn.add(f"line {n}: ignored: {stmt[:80]}")
    for sub in stack:
        warn.add(f"subgraph {sub['id']!r} is never closed; closed at the end")
    return {"elements": list(els.values()), "edges": edges, "boundaries": subs}


def _subgraph(rest: str, n: int) -> tuple[str, str]:
    m = re.match(r'^(\w+)\s*\[\s*"?(.*?)"?\s*\]$', rest)
    if m:
        return m.group(1), m.group(2)
    title = _unquote(rest) or f"group {n}"
    if re.fullmatch(r"\w+", title):
        return title, title
    return f"sg{n}", title


def _chain(stmt: str, n: int, touch, edges: list[dict], sub_ids: set[str], warn: _Warn) -> bool:
    pos = 0
    groups: list[list[_Node]] = []
    links: list[dict] = []
    while True:
        group: list[_Node] = []
        while True:
            pos = _skip_ws(stmt, pos)
            parsed = _parse_node(stmt, pos)
            if parsed is None:
                return False if not groups and not group else _bail(stmt, n, warn)
            node, pos = parsed
            group.append(node)
            pos = _skip_ws(stmt, pos)
            if pos < len(stmt) and stmt[pos] == "&":
                pos += 1
                continue
            break
        groups.append(group)
        pos = _skip_ws(stmt, pos)
        if pos >= len(stmt):
            break
        link = _parse_link(stmt, pos)
        if link is None:
            return _bail(stmt, n, warn)
        info, pos = link
        links.append(info)
    for g in groups:
        for node in g:
            touch(node)
    for i, info in enumerate(links):
        for a in groups[i]:
            for b in groups[i + 1]:
                if a.id in sub_ids or b.id in sub_ids:
                    warn.add(f"line {n}: edge to or from a subgraph ignored")
                    continue
                if len(edges) >= MAX_RELATIONSHIPS:
                    raise MermaidError(f"more than {MAX_RELATIONSHIPS} edges")
                e = {"from": a.id, "to": b.id, "style": "async" if info["dotted"] else "sync"}
                if info["label"]:
                    e.update(_edge_text(info["label"]))
                if info["bidir"]:
                    e["bidirectional"] = True
                    warn.add(f"line {n}: a two-headed arrow is imported as bidirectional (the standard asks for two arrows)")
                edges.append(e)
    return True


def _bail(stmt: str, n: int, warn: _Warn) -> bool:
    warn.add(f"line {n}: could not read (unbalanced brackets or unknown syntax): {stmt[:80]}")
    return True


# ------------------------------------------------------------------ assembly shared by all three syntaxes
def _finish_elements(els: list[dict], meta: _Meta, warn: _Warn, boundary_ids: set[str]) -> dict[str, str]:
    """Apply element comments; returns {mermaid id: final id}."""
    by_mid = {e["id"]: e for e in els}
    target_seen: dict[str, bool] = {}
    for target, facts in meta.elements:
        if target in boundary_ids:
            continue
        e = by_mid.get(target)
        if e is None:
            warn.add(f"%% cairn comment for unknown node {target!r} ignored", once=True)
            continue
        if target in meta.full and not target_seen.get(target):
            for k in [k for k in e if k not in ("id", "name")]:
                del e[k]
        target_seen[target] = True
        _apply(e, facts, ELEMENT_KEYS, ("id",), warn, target)
        if isinstance(facts.get("id"), str) and facts["id"].strip():
            e["_final"] = facts["id"]
    idmap: dict[str, str] = {}
    for e in els:
        mid = e["id"]
        final = clean(e.pop("_final", mid)) or mid
        e["id"] = final
        idmap[mid] = final
    return idmap


def _finish_boundaries(subs: list[dict], meta: _Meta, idmap: dict[str, str], warn: _Warn) -> list[dict]:
    by_id = {s["id"]: s for s in subs}
    for target, facts in meta.elements:
        s = by_id.get(target)
        if s is not None:
            _apply(s, facts, BOUNDARY_KEYS, ("id", "contains"), warn, target)
            if isinstance(facts.get("id"), str) and facts["id"].strip():
                s["_final"] = facts["id"]
    for s in subs:
        s["id"] = clean(s.pop("_final", s["id"])) or s["id"]
        s["contains"] = [idmap.get(c, c) for c in s["contains"]]
    return subs


def _replace_if_json(target: dict, facts: dict) -> None:
    """A JSON comment describes the edge completely: what the text of the chart gave is replaced, not merged."""
    if facts.get("__json"):
        for k in [k for k in target if k not in ("from", "to")]:
            del target[k]


def _finish_rels(edges: list[dict], meta: _Meta, idmap: dict[str, str], warn: _Warn) -> list[dict]:
    pending: dict[str, list[dict]] = {}
    for target, facts in meta.rels:
        pending.setdefault(target.replace(" ", ""), []).append(facts)
    out = []
    for e in edges:
        key = f"{e['from']}->{e['to']}"
        facts = pending.get(key, []).pop(0) if pending.get(key) else None
        if facts:
            _replace_if_json(e, facts)
            _apply(e, facts, REL_KEYS, ("from", "to", "__json"), warn, key)
        e["from"], e["to"] = idmap.get(e["from"], e["from"]), idmap.get(e["to"], e["to"])
        out.append(e)
    return out


def _diagram_head(meta: _Meta, front_title: str | None, kind: str | None, default_kind: str, warn: _Warn) -> dict:
    facts = dict(meta.diagram)
    if "kind" in facts and "diagram" not in facts:
        facts["diagram"] = facts.pop("kind")
    d: dict[str, Any] = {}
    if front_title:
        d["title"] = front_title
    _apply(d, facts, DIAGRAM_KEYS, (), warn, "the diagram")
    d.setdefault("diagram", kind or default_kind)
    return d


def _finalize(raw: dict) -> dict:
    try:
        return normalize(raw)
    except DiagramError as exc:
        raise MermaidError(str(exc)) from exc


# ------------------------------------------------------------------ sequence diagrams
_PARTICIPANT = re.compile(r"^(participant|actor)\s+(\w+)(?:\s+as\s+(.+))?$", re.I)
_MESSAGE = re.compile(r"^(\w+)\s*(?P<arrow>--?>>?|--?[x)])\s*[+-]?\s*(\w+)\s*:\s*(.*)$")
_FRAGMENTS = {"alt", "opt", "loop", "par", "critical", "break"}


def _parse_sequence(lines: list[tuple[int, str]], warn: _Warn) -> dict:
    parts: dict[str, dict] = {}
    msgs: list[dict] = []
    frags: list[dict] = []
    stack: list[dict | None] = []

    def part(pid: str) -> None:
        parts.setdefault(pid, {"id": pid, "name": pid})

    for n, line in lines:
        if line.startswith("%%") or line.lower().startswith("sequencediagram"):
            continue
        for stmt in _split_statements(line):
            m = _PARTICIPANT.match(stmt)
            if m:
                pid = m.group(2)
                e = parts.setdefault(pid, {"id": pid, "name": pid})
                if m.group(3):
                    e["name"] = _plain(m.group(3)) or pid
                if m.group(1).lower() == "actor":
                    e["type"] = "person"
                if len(parts) > MAX_ELEMENTS:
                    raise MermaidError(f"more than {MAX_ELEMENTS} participants")
                continue
            m = _MESSAGE.match(stmt)
            if m:
                a, arrow, b, text = m.group(1), m.group("arrow"), m.group(3), m.group(4)
                part(a)
                part(b)
                style = "return" if arrow.startswith("--") and arrow.endswith(">") else \
                    "async" if arrow.endswith(")") else "sync"
                if arrow.endswith("x"):
                    warn.add(f"line {n}: lost-message arrow imported as a synchronous message", once=True)
                if len(msgs) >= MAX_RELATIONSHIPS:
                    raise MermaidError(f"more than {MAX_RELATIONSHIPS} messages")
                msg = {"from": a, "to": b, "style": style}
                msg.update(_what_how(text))
                msgs.append(msg)
                continue
            word = stmt.split(None, 1)[0].lower()
            rest = stmt[len(word):].strip()
            if word in _FRAGMENTS:
                fr = {"type": word, "start": len(msgs) + 1}
                if rest:
                    fr["label"] = _plain(rest)
                stack.append(fr)
            elif word == "rect":
                stack.append(None)
            elif word in ("else", "and", "option"):
                top = stack[-1] if stack else None
                if top is not None and "else_at" not in top:
                    top["else_at"] = len(msgs) + 1
                    if rest:
                        top["else_label"] = _plain(rest)
                elif top is not None:
                    warn.add(f"line {n}: only one else branch per fragment is imported", once=True)
            elif word == "end":
                if not stack:
                    warn.add(f"line {n}: stray 'end' ignored")
                    continue
                top = stack.pop()
                if top is not None:
                    top["end"] = len(msgs)
                    if top["end"] >= top["start"]:
                        frags.append(top)
                    else:
                        warn.add(f"line {n}: empty {top['type']} fragment dropped")
            elif word in ("activate", "deactivate", "autonumber", "title", "box", "links", "link"):
                if word == "title":
                    continue
                if word in ("links", "link"):
                    warn.add(f"line {n}: '{word}' ignored (never evaluated)")
            elif word == "note":
                warn.add("notes are ignored", once=True)
            else:
                warn.add(f"line {n}: ignored: {stmt[:80]}")
    for _ in [s for s in stack if s is not None]:
        warn.add("a fragment is never closed; it is dropped", once=True)
    frags.sort(key=lambda f: (f["start"], -f["end"]))
    return {"elements": list(parts.values()), "messages": msgs, "fragments": frags}


# ------------------------------------------------------------------ C4 syntax
_C4_HEADERS = {"c4context": "context", "c4container": "container", "c4component": "component", "c4dynamic": "flow",
               "c4deployment": "container"}
_C4_ELEMENTS = {
    "person": ("person", 2), "person_ext": ("person", 2), "system": ("system", 2), "system_ext": ("external-system", 2),
    "systemdb": ("system", 2), "systemdb_ext": ("external-system", 2), "systemqueue": ("system", 2),
    "systemqueue_ext": ("external-system", 2), "container": ("container", 3), "container_ext": ("external-system", 3),
    "containerdb": ("data-store", 3), "containerdb_ext": ("external-system", 3), "containerqueue": ("channel", 3),
    "containerqueue_ext": ("external-system", 3), "component": ("component", 3), "component_ext": ("external-system", 3),
    "componentdb": ("data-store", 3), "componentqueue": ("channel", 3), "componentdb_ext": ("external-system", 3),
    "componentqueue_ext": ("external-system", 3)}
_C4_BOUNDARIES = {"boundary": None, "enterprise_boundary": "enterprise", "system_boundary": "software system",
                  "container_boundary": "container"}
_C4_RELS = {"rel", "rel_d", "rel_down", "rel_u", "rel_up", "rel_l", "rel_left", "rel_r", "rel_right", "rel_neighbor",
            "rel_back", "rel_back_neighbor", "birel", "birel_d", "birel_u", "birel_l", "birel_r", "relindex"}
_C4_IGNORED = ("update", "layout", "lay_", "showlegend", "hidelegend", "sprite", "tags", "add")


def _c4_args(s: str) -> list[str]:
    out, cur, quote, depth = [], [], False, 0
    for ch in s:
        if ch == '"':
            quote = not quote
            continue
        if not quote:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    out.append("".join(cur).strip())
    return [a for a in out if not a.startswith("$")] if out != [""] else []


def _parse_c4(lines: list[tuple[int, str]], header: str, warn: _Warn) -> tuple[str, dict, str | None]:
    kind = _C4_HEADERS[header]
    if header == "c4deployment":
        warn.add("C4Deployment is imported as a container view (nodes become plain elements)")
    els: dict[str, dict] = {}
    rels: list[dict] = []
    subs: list[dict] = []
    stack: list[dict | None] = []
    title = None
    pending_open: dict | None = None
    for n, line in lines:
        if line.startswith("%%") or line.lower() in _C4_HEADERS:
            continue
        if line == "{" and pending_open is not None:
            stack.append(pending_open)
            pending_open = None
            continue
        if line == "}":
            if stack:
                stack.pop()
            else:
                warn.add(f"line {n}: stray '}}' ignored")
            continue
        if line.lower().startswith("title "):
            title = _plain(line[6:])
            continue
        m = re.match(r"^(\w+)\s*\((.*)\)\s*(\{)?$", line)
        if not m:
            warn.add(f"line {n}: ignored: {line[:80]}")
            continue
        macro, argtext, opens = m.group(1).lower(), m.group(2), m.group(3)
        args = _c4_args(argtext)
        if macro in _C4_ELEMENTS and len(args) >= 2:
            etype, tech_at = _C4_ELEMENTS[macro]
            if macro.startswith("system") and macro.endswith(("db",)) and kind != "context":
                etype = "data-store"
            if macro.startswith("systemqueue") and kind != "context":
                etype = "channel"
            e: dict[str, Any] = {"id": args[0], "name": _plain(args[1]) or args[0], "type": etype}
            if tech_at == 3 and len(args) > 2:
                e["tech"] = _plain(args[2])
            if len(args) > tech_at:
                e["desc"] = _plain(args[tech_at])
            if len(els) >= MAX_ELEMENTS:
                raise MermaidError(f"more than {MAX_ELEMENTS} elements")
            els[args[0]] = e
            if stack and stack[-1] is not None:
                stack[-1]["contains"].append(args[0])
        elif macro in _C4_BOUNDARIES and len(args) >= 2:
            b: dict[str, Any] = {"id": args[0], "name": _plain(args[1]) or args[0], "contains": [],
                                 "type": (_plain(args[2]) if len(args) > 2 else None) or _C4_BOUNDARIES[macro] or "boundary"}
            if stack:
                warn.add(f"line {n}: nested boundary flattened", once=True)
            subs.append(b)
            if opens:
                stack.append(b)
            else:
                pending_open = b
        elif macro in _C4_RELS:
            off = 1 if macro == "relindex" else 0
            if len(args) < 3 + off:
                warn.add(f"line {n}: relationship with too few arguments ignored")
                continue
            a, b2 = args[off], args[off + 1]
            if macro in ("rel_back", "rel_back_neighbor"):
                a, b2 = b2, a
            r: dict[str, Any] = {"from": a, "to": b2, "what": _plain(args[off + 2]), "style": "sync"}
            if len(args) > off + 3:
                r["how"] = _plain(args[off + 3])
            if macro.startswith("birel"):
                r["bidirectional"] = True
            if len(rels) >= MAX_RELATIONSHIPS:
                raise MermaidError(f"more than {MAX_RELATIONSHIPS} relationships")
            rels.append(r)
        elif macro.startswith(_C4_IGNORED):
            continue
        else:
            warn.add(f"line {n}: ignored: {line[:80]}")
    desc: dict[str, Any] = {"elements": list(els.values())}
    if kind == "flow":
        ids = [e["id"] for e in desc["elements"]]
        for r in rels:
            for x in (r["from"], r["to"]):
                if x not in els:
                    els[x] = {"id": x, "name": x}
                    desc["elements"].append(els[x])
                    ids.append(x)
        desc["flow"] = {"participants": ids, "messages": rels}
        for r in rels:
            r.pop("bidirectional", None)
    else:
        desc["relationships"] = rels
        if subs:
            desc["boundaries"] = [s for s in subs if s["contains"]]
    return kind, desc, title


# ------------------------------------------------------------------ entry points
def looks_like_mermaid(text: str) -> bool:
    for line in text.lstrip("﻿").splitlines()[:60]:
        s = line.strip().lower()
        if not s or s.startswith("%%") or s in ("---",) or re.match(r"^[a-z_]+\s*:", s):
            continue
        return bool(re.match(r"^(flowchart|graph|sequencediagram|c4\w+)\b", s))
    return False


def parse(text: str | bytes, kind: str | None = None) -> Imported:
    """Read Mermaid into a normalised description plus warnings. Raises ``MermaidError`` on unusable input."""
    warn = _Warn()
    lines, front_title = _prepare(text, warn)
    if not lines:
        raise MermaidError("the document is empty")
    meta = _Meta(warn)
    body: list[tuple[int, str]] = []
    for n, ln in lines:
        if ln.startswith("%%"):
            if not ln.startswith("%%{"):
                meta.add_line(ln, n)
            continue
        body.append((n, ln))
    if not body:
        raise MermaidError("no diagram found (only comments)")
    head = body[0][1].split(None, 1)[0].lower()
    if head in ("flowchart", "graph") or re.match(r"^(flowchart|graph)", body[0][1], re.I):
        raw = _parse_flowchart(body[1:], kind or "container", warn)
        d = _diagram_head(meta, front_title, kind, "container", warn)
        sub_ids = {s["id"] for s in raw["boundaries"]}
        idmap = _finish_elements(raw["elements"], meta, warn, sub_ids)
        doc = {**{k: v for k, v in d.items()}, "elements": raw["elements"],
               "relationships": _finish_rels(raw["edges"], meta, idmap, warn)}
        if raw["boundaries"]:
            doc["boundaries"] = _finish_boundaries(raw["boundaries"], meta, idmap, warn)
        return Imported(_finalize(doc), warn.done())
    if head == "sequencediagram":
        raw = _parse_sequence(body[1:], warn)
        d = _diagram_head(meta, front_title, "flow" if kind is None else kind, "flow", warn)
        d["diagram"] = "flow"
        sub: set[str] = set()
        idmap = _finish_elements(raw["elements"], meta, warn, sub)
        msgs = raw["messages"]
        for n, facts in meta.msgs:
            if 1 <= n <= len(msgs):
                _replace_if_json(msgs[n - 1], facts)
                _apply(msgs[n - 1], facts, REL_KEYS, ("from", "to", "__json"), warn, f"message {n}")
        for m in msgs:
            m["from"], m["to"] = idmap.get(m["from"], m["from"]), idmap.get(m["to"], m["to"])
        doc = {**d, "elements": raw["elements"],
               "flow": {"participants": [e["id"] for e in raw["elements"]], "messages": msgs, "fragments": raw["fragments"]}}
        return Imported(_finalize(doc), warn.done())
    if head in _C4_HEADERS:
        ckind, doc, ctitle = _parse_c4(body, head, warn)
        d = _diagram_head(meta, front_title or ctitle, ckind, ckind, warn)
        d["diagram"] = ckind
        return Imported(_finalize({**d, **doc}), warn.done())
    raise MermaidError(f"unsupported Mermaid diagram type {body[0][1].split()[0][:30]!r} "
                       "(flowchart, graph, sequenceDiagram and C4 syntax are supported)")
