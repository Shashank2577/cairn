"""Outbound HTTP calls (FR-008): method, literal path or path template, and the configuration NAME that
supplies the base address when there is one. Driven by the ``http`` rules in ``catalog/clients.yaml``
(fetch, axios, requests, httpx, RestTemplate, WebClient, Feign, Go net/http, HttpClient, and shell
``curl``/``wget``/``ab``/``httpie`` commands).

A URL's host is returned in ``HttpCall.host`` only so the build can resolve it to a deploy unit; it is
dropped there and never stored (FR-029b).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..catalog import Catalog, compiled, expand
from .match import (
    RepoIndex,
    Url,
    Wrappers,
    imports_library,
    literals,
    param_index,
    parse_url_text,
    qualify,
    select_first,
    url,
)
from .routes import HTTP_METHODS, norm_path
from .syntax import Call, Deco, FileFacts

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"})


@dataclass
class HttpCall:
    file: str
    line: int
    method: str
    path: str | None
    base: str | None = None          # configuration name of the base address
    host: str | None = None          # transient (resolution only)
    client: str = ""
    via: list = field(default_factory=list)   # (file, line) of wrapper calls between the use site and here


def _families(rule) -> set[str]:
    lang = rule.get("language")
    return {lang} if isinstance(lang, str) else set(lang or [])


def extract(facts: dict[str, FileFacts], cat: Catalog, idx: RepoIndex, wrappers: Wrappers) -> list[HttpCall]:
    out: list[HttpCall] = []
    for ff in facts.values():
        for rule in cat.http:
            if ff.family not in _families(rule):
                continue
            if rule.get("commands"):
                out += _shell(rule, ff)
            elif rule.get("kind") == "annotation":
                out += _feign(rule, ff, idx)
            else:
                out += _calls(rule, ff, idx, wrappers)
    return out


def _method(rule, matched, node, ff, idx) -> str:
    table = rule.get("methods") or {}
    if matched is not None and matched in table:
        return str(table[matched]).upper()
    if isinstance(node, Deco) and node.name in table:
        return str(table[node.name]).upper()
    for sel in rule.get("methods_from") or []:
        if sel == "chain" and isinstance(node, Call):
            for c in reversed(node.chain):
                if c in table:
                    return str(table[c]).upper()
            continue
        v = select_first(node, [sel], ff, idx)
        if v is not None:
            vals = v.items if v.kind == "list" else (v,)
            for x in vals:
                t = x.text.rsplit(".", 1)[-1].upper() if x.kind in ("str", "name") else ""
                if t in HTTP_METHODS:
                    return t
    return str((rule.get("default_methods") or ["ANY"])[0]).upper()


def _calls(rule, ff: FileFacts, idx, wrappers) -> list[HttpCall]:
    out = []
    pats = [(compiled((pat,)), pat, m) for pat, m in expand(rule.get("match"), rule.get("methods"))]
    anyrx = compiled([pat for _, pat, _ in pats])
    loose = set(rule.get("loose") or [])
    lib_ok = imports_library(ff, rule.get("library")) if loose else False
    for c in ff.calls:
        q = qualify(ff, c.callee, idx)
        matched = None
        hit = False
        if anyrx.match(q) is None and not (lib_ok and c.name in loose):
            continue
        for rx, pat, m in pats:
            if rx.match(q):
                if pat in ("fetch",) and (c.callee in ff.funcs or c.callee in ff.binds or c.callee in ff.imports):
                    continue
                hit, matched = True, m
                break
        if not hit and lib_ok and c.name in loose and q == c.callee:
            hit, matched = True, c.name
        if not hit:
            continue
        method = _method(rule, matched, c, ff, idx)
        uval = select_first(c, rule.get("url") or ["arg0"], ff, idx, skip_empty=False)
        base = None
        bsel = select_first(c, rule.get("base") or [], ff, idx) if rule.get("base") else None
        if bsel is not None:
            b = url(bsel, ff)
            base = b.base if b is not None else None
        pi = param_index(ff, c, uval)
        if pi is not None:
            resolved = list(_through_wrappers(ff, pi, wrappers, idx))
            if resolved:
                for wf, wc, u, via in resolved:
                    out.append(_mk(wf.path, wc.line, method, u, base, rule, [(ff.path, c.line), *via]))
                continue
        u = url(uval, ff)
        out.append(_mk(ff.path, c.line, method, u, base, rule, []))
    return out


def _through_wrappers(def_ff, pi, wrappers: Wrappers, idx, hop: int = 1):
    func, index, pname = pi
    for ff, c, v in wrappers.callers(def_ff, func, index, pname):
        nxt = param_index(ff, c, v)
        if nxt is not None and hop < 2:
            for item in _through_wrappers(ff, nxt, wrappers, idx, hop + 1):
                yield item[0], item[1], item[2], [(ff.path, c.line), *item[3]]
            continue
        u = url(v, ff)
        if u is not None:
            yield ff, c, u, []


def _mk(file, line, method, u: Url | None, base, rule, via) -> HttpCall:
    if u is None:
        return HttpCall(file, line, method, None, base, None, rule.get("client", ""), via)
    host = u.host if u.host and u.host.lower() not in LOCAL_HOSTS else None
    return HttpCall(file, line, method, norm_path(u.path) if u.path else None, u.base or base, host,
                    rule.get("client", ""), via)


def _feign(rule, ff: FileFacts, idx) -> list[HttpCall]:
    out = []
    names = set(rule.get("match") or [])
    for d in ff.decos:
        if d.target_kind != "method" or d.name not in names:
            continue
        cls = next((x for x in d.cls_decos if x.name == rule.get("class")), None)
        if cls is None:
            continue
        v = select_first(d, rule.get("url") or ["arg0"], ff, idx, skip_empty=False)
        lits = literals(v, ff) if v is not None else []
        path = lits[0].text if lits and lits[0].text is not None else None
        base_v = select_first(d, rule.get("base") or [], ff, idx, cls_deco=cls)
        base = host = None
        if base_v is not None:
            if base_v.kind == "str" and base_v.text.startswith("${"):
                base = base_v.text.strip("${}").split(":")[0]
            else:
                b = url(base_v, ff)
                if b is not None:
                    base, host = b.base, b.host
        if host is None and base is None:
            hv = select_first(d, rule.get("host") or [], ff, idx, cls_deco=cls)
            if hv is not None and hv.kind == "str" and hv.text and not hv.text.startswith("${"):
                host = hv.text
        out.append(HttpCall(ff.path, d.line, _method(rule, None, d, ff, idx), norm_path(path) if path is not None
                            else None, base, host, rule.get("client", ""), []))
    return out


def _shell(rule, ff: FileFacts) -> list[HttpCall]:
    out = []
    table = rule.get("commands") or {}
    for line, name, args in ff.commands:
        spec = table.get(name)
        if not isinstance(spec, dict):
            continue
        target = next((a for a in args if a.startswith(("http://", "https://"))), None)
        if target is None:
            continue
        u = parse_url_text(target)
        if u is None:
            continue
        method = "GET"
        if spec.get("method_args"):
            method = next((a.upper() for a in args if a.upper() in HTTP_METHODS), "GET")
        for i, a in enumerate(args):
            if a in (spec.get("method_flags") or []) and i + 1 < len(args) and args[i + 1].upper() in HTTP_METHODS:
                method = args[i + 1].upper()
                break
            if a in (spec.get("data_flags") or []) or any(a.startswith(f + "=") for f in spec.get("data_flags") or []):
                method = "POST"
            if a in (spec.get("put_flags") or []):
                method = "PUT"
        host = u.host if u.host and u.host.lower() not in LOCAL_HOSTS else None
        out.append(HttpCall(ff.path, line, method, norm_path(u.path), None, host, f"shell {name}", []))
    return out
