"""Message publishing and consuming (FR-009): pub/sub, list queues (Redis RPUSH/LPUSH and BLPOP/BRPOP),
Kafka topics, AMQP queues and exchanges, SQS queues and NATS subjects, driven by the ``messaging`` rules
in ``catalog/clients.yaml``. A topic passed through a first-party helper (``publish(topic, event)``) is
resolved through the helper's callers, up to two hops. A client connection with no publish or consume
call is reported as a ``Connect`` (a store use, e.g. Redis as a cache), never as messaging.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..catalog import Catalog, compiled, glob
from .match import (
    RepoIndex,
    Wrappers,
    _deref,
    imports_library,
    literals,
    param_index,
    qualify,
    select,
    select_first,
)
from .syntax import FileFacts, Val


@dataclass
class ChannelOp:
    file: str
    line: int
    tech: str
    op: str                     # publish | subscribe | push | pop
    key: str | None
    key_config: str | None      # configuration name that supplies the key
    how: str
    client: str
    via: list = field(default_factory=list)


@dataclass
class Connect:
    file: str
    line: int
    tech: str
    client: str
    how: str
    target: Val | None = None   # instance hint (URL, host, configuration name); values never stored
    driver: str | None = None


def _families(rule) -> set[str]:
    lang = rule.get("language")
    return {lang} if isinstance(lang, str) else set(lang or [])


def connects(rule, ff: FileFacts, idx: RepoIndex, how: str) -> list[Connect]:
    """Client construction or connect calls (a use site) matching ``rule['connect']``."""
    out = []
    pats = rule.get("connect") or []
    if not pats:
        return out
    lib = rule.get("library")
    has_lib = imports_library(ff, lib) if lib else True
    if lib and not has_lib and not any(q.startswith(tuple(lib)) for q in quals(ff, idx)) and not any(
            str(p).startswith("*") for p in pats):
        return out
    for c in ff.calls:
        q = qualify(ff, c.callee, idx)
        typed = glob(q, pats)
        if not typed and not (has_lib and ff.family == "csharp" and glob(c.callee, pats)):
            continue
        if ff.family == "csharp" and not has_lib:
            continue
        tgt = select_first(c, rule.get("target") or [], ff, idx)
        if tgt is not None:
            tgt = _deref(tgt, ff)
        driver = None
        if rule.get("driver"):
            dv = select_first(c, [rule["driver"]], ff, idx)
            driver = dv.text if dv is not None and dv.kind == "str" else None
        out.append(Connect(ff.path, c.line, rule["tech"], rule.get("client", ""), how, tgt, driver))
    return out


def extract(facts: dict[str, FileFacts], cat: Catalog, idx: RepoIndex, wrappers: Wrappers):
    ops: list[ChannelOp] = []
    conns: list[Connect] = []
    for ff in facts.values():
        for rule in cat.messaging:
            if ff.family not in _families(rule):
                continue
            has_lib = imports_library(ff, rule.get("library"))
            how = cat.tech(rule["tech"]).get("how") or rule["tech"]
            conns += connects(rule, ff, idx, how)
            for op in rule.get("ops") or []:
                if op.get("kind") == "annotation":
                    ops += _annotations(rule, op, ff, idx)
                else:
                    ops += _calls(rule, op, ff, idx, wrappers, has_lib)
    seen, out = set(), []
    for o in ops:
        k = (o.file, o.line, o.op, o.key, o.key_config)
        if k not in seen:
            seen.add(k)
            out.append(o)
    return out, conns


def _keys(op, node, ff, idx) -> list:
    """Every literal key the first productive selector yields (``subscribe(['a', 'b'])`` gives both). A
    queue named by its URL (SQS) is keyed by the URL's last path segment, the queue name."""
    for sel in op.get("key") or []:
        out = []
        for v in _select_all(node, sel, ff, idx):
            out.extend(literals(v, ff))
        if out:
            for lit in out:
                if lit.text and "://" in lit.text:
                    lit.text = lit.text.rstrip("/").rsplit("/", 1)[-1] or None
            return [lit for lit in out if lit.text or lit.config]
    return []


def _select_all(node, sel, ff, idx):
    return [v for v in select(node, sel, ff, idx) if not (v.kind == "str" and not v.text)]


def _calls(rule, op, ff: FileFacts, idx, wrappers: Wrappers, has_lib: bool) -> list[ChannelOp]:
    out = []
    pats = op.get("match") or []
    loose = set(op.get("loose") or [])
    loose_if_key = bool(op.get("loose_if_key"))
    lib = rule.get("library")
    rx = compiled(pats)
    names = _names(pats)
    if not has_lib and not any(q.startswith(tuple(lib or ("\0",))) for q in quals(ff, idx)) and not (
            pats and any(p.startswith("*") for p in pats)):
        return out
    for c in ff.calls:
        q = qualify(ff, c.callee, idx)
        typed = bool(pats) and rx.match(q) is not None
        if not typed:
            untyped = q == c.callee or not any(q.startswith(x) for x in (lib or []))
            if not (has_lib and untyped and (c.name in loose or (loose_if_key and c.name in names))):
                continue
            if loose_if_key and c.name not in loose and not _keys(op, c, ff, idx):
                continue
        if c.callee.endswith("()") and not typed:
            continue
        keys = _keys(op, c, ff, idx)
        if loose_if_key and not typed and not keys:
            continue
        via: list = []
        if not keys:
            # a parameter of the enclosing helper: resolve through its callers (two hops)
            for sel in op.get("key") or []:
                for v in _select_all(c, sel, ff, idx):
                    pi = param_index(ff, c, v)
                    if pi is None:
                        continue
                    resolved = list(_through(ff, pi, wrappers))
                    for wf, wc, lits, path in resolved:
                        for lit in lits:
                            out.append(ChannelOp(wf.path, wc.line, rule["tech"], op["op"], lit.text, lit.config,
                                                 op.get("how") or "", rule.get("client", ""),
                                                 [(ff.path, c.line), *path]))
                    if resolved:
                        via = ["resolved"]
                if via:
                    break
            if via:
                continue
            if op.get("needs_key"):
                continue
            out.append(ChannelOp(ff.path, c.line, rule["tech"], op["op"], None, None, op.get("how") or "",
                                 rule.get("client", "")))
            continue
        for lit in keys:
            out.append(ChannelOp(ff.path, c.line, rule["tech"], op["op"], lit.text, lit.config, op.get("how") or "",
                                 rule.get("client", "")))
    return out


def quals(ff: FileFacts, idx) -> list[str]:
    """Every call's qualified callee in this file (computed once, shared by every rule)."""
    hit = ff.qcache.get("\0all")
    if hit is None:
        hit = ff.qcache["\0all"] = [qualify(ff, c.callee, idx) for c in ff.calls]
    return hit


def _names(pats) -> set[str]:
    return {p.rsplit(".", 1)[-1] for p in pats or []}


def _through(def_ff, pi, wrappers: Wrappers, hop: int = 1):
    func, index, pname = pi
    for ff, c, v in wrappers.callers(def_ff, func, index, pname):
        nxt = param_index(ff, c, v)
        if nxt is not None and hop < 2:
            for item in _through(ff, nxt, wrappers, hop + 1):
                yield item[0], item[1], item[2], [(ff.path, c.line), *item[3]]
            continue
        lits = literals(v, ff)
        if lits:
            yield ff, c, lits, []


def _annotations(rule, op, ff: FileFacts, idx) -> list[ChannelOp]:
    out = []
    names = set(op.get("match") or [])
    if not imports_library(ff, rule.get("library")) and not any(
            o.rsplit(".", 1)[-1] in names for o in ff.imports.values()):
        return out
    for d in ff.decos:
        if d.name not in names:
            continue
        keys = _keys(op, d, ff, idx)
        for lit in keys or [None]:
            out.append(ChannelOp(ff.path, d.line, rule["tech"], op["op"], lit.text if lit else None,
                                 lit.config if lit else None, op.get("how") or "", rule.get("client", "")))
    return out
