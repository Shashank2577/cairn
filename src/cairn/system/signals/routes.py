"""Inbound HTTP routes (FR-007), driven by the framework rules in ``catalog/frameworks.yaml``.

Four pattern kinds are implemented once here: ``decorator`` (FastAPI, Flask), ``annotation`` (Spring,
with the class-level ``@RequestMapping`` prefix), ``call`` (Express, Fastify, Gin, net/http) and
``file-route`` (Django ``urls.py`` with ``include``, Next.js route handlers and API pages). Router
prefixes come from constructors (``APIRouter(prefix=)``, ``Blueprint(url_prefix=)``), groups
(``r.Group("/api")``) and mounts (``include_router``, ``register_blueprint``, ``app.use("/api", r)``),
resolved across first-party imports.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..catalog import Catalog, compiled, expand, glob
from .match import RepoIndex, literals, qualify, select_first
from .syntax import Deco, FileFacts, Val

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "ANY")


@dataclass
class Route:
    file: str
    line: int
    method: str
    path: str
    framework: str
    handler: str | None = None


def norm_path(path: str | None) -> str | None:
    """One spelling for every framework's path syntax: parameters become ``{}``, no query, a leading
    slash, no trailing slash. ``/orders/:id``, ``/orders/<int:id>``, ``/orders/{id}``, ``/orders/[id]``
    and ``^orders/(?P<id>\\d+)/$`` all become ``/orders/{}``."""
    if path is None:
        return None
    p = str(path).strip()
    if not p:
        return None
    p = re.sub(r"\(\?P<\w+>[^)]*\)", "{}", p)       # Django regex groups, before "?" means a query
    p = re.sub(r"\([^)]*\)", "{}", p)
    p = p.split("?", 1)[0].split("#", 1)[0]
    p = re.sub(r"(^|/)\^", r"\1", p).rstrip("$")
    p = re.sub(r"\$\{[^}]*\}", "{}", p)
    p = re.sub(r"\{[^}]*\}", "{}", p)
    p = re.sub(r"<[^>]*>", "{}", p)
    p = re.sub(r"\[\[?\.{0,3}[^\]]*\]?\]", "{}", p)
    p = re.sub(r"(?<=/):[A-Za-z_]\w*\??", "{}", p)
    p = re.sub(r"(?<=/)\*\w*", "{}", p)
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"/{2,}", "/", p)
    if len(p) > 1:
        p = p.rstrip("/")
    return p or "/"


def join(*parts: str | None) -> str:
    return "/".join(x.strip("/") for x in parts if x and x.strip("/")) or ""


def _families(rule) -> set[str]:
    lang = rule.get("language")
    return {lang} if isinstance(lang, str) else set(lang or [])


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name).lower())


def applies(rule, ff: FileFacts, deps: set[str]) -> bool:
    if ff.family not in _families(rule):
        return False
    detect = rule.get("detect") or []
    if not detect:
        return True
    want = {_norm(d) for d in detect}
    if want & deps:
        return True
    origins = list(ff.imports.values()) + list(ff.namespaces)
    for d in detect:
        for o in origins:
            if o == d or o.startswith(d + ".") or o.startswith(d + "/"):
                return True
    return False


def _methods(rule, matched: str | None, node, ff, idx) -> list[str]:
    table = rule.get("methods") or {}
    if matched is not None and matched in table:
        return [str(table[matched]).upper()]
    if isinstance(node, Deco) and node.name in table:
        return [str(table[node.name]).upper()]
    out: list[str] = []
    for sel in rule.get("methods_from") or []:
        v = select_first(node, [sel], ff, idx)
        if v is None:
            continue
        vals = v.items if v.kind == "list" else (v,)
        for x in vals:
            t = x.text.rsplit(".", 1)[-1].upper() if x.kind in ("str", "name") else ""
            if t in HTTP_METHODS:
                out.append(t)
        if out:
            return out
    return [str(m).upper() for m in rule.get("default_methods") or ["ANY"]]


def _path(rule, node, ff, idx) -> str | None:
    v = select_first(node, rule.get("path") or ["arg0"], ff, idx, skip_empty=False)
    if v is None:
        return None
    lits = literals(v, ff)
    return lits[0].text if lits and lits[0].text is not None else ("" if v.kind == "str" else None)


class _Prefixes:
    """Router identity -> prefix, from constructor, group and mount rules."""

    def __init__(self, facts: dict[str, FileFacts], idx: RepoIndex, rules):
        self.facts, self.idx, self.rules = facts, idx, rules
        self.mounts: dict[tuple[str, str], list[tuple[str, tuple[str, str] | None]]] = {}
        for ff in facts.values():
            for rule in rules:
                if ff.family not in _families(rule):
                    continue
                for pre in rule.get("prefixes") or []:
                    if pre.get("kind") != "mount":
                        continue
                    for c in ff.calls:
                        if not glob(qualify(ff, c.callee, idx), pre.get("match")):
                            continue
                        tgt = select_first(c, [pre.get("target") or "arg0"], ff, idx)
                        if tgt is None or tgt.kind != "name":
                            continue
                        path = select_first(c, pre.get("path") or [], ff, idx)
                        lit = literals(path, ff) if path is not None else []
                        text = lit[0].text if lit and lit[0].text else ""
                        ident = self.identity(ff, tgt.text.split(".")[0])
                        parent = self.identity(ff, c.recv.split(".")[0]) if c.recv else None
                        self.mounts.setdefault(ident, []).append((text, parent))

    def identity(self, ff: FileFacts, name: str) -> tuple[str, str]:
        if name in ff.imports:
            hit = self.idx.module(ff.path, ff.imports[name])
            if hit is not None and hit[1]:
                return hit[0].path, hit[1]
        return ff.path, name

    def prefix(self, ff: FileFacts, root: str, depth: int = 0) -> str:
        if depth > 3 or not root:
            return ""
        path, name = self.identity(ff, root)
        home = self.facts.get(path, ff)
        own = ""
        for v in home.binds.get(name, []):
            if v.kind != "call" or v.call is None:
                continue
            q = qualify(home, v.text, self.idx)
            for rule in self.rules:
                for pre in rule.get("prefixes") or []:
                    if pre.get("kind") == "constructor" and glob(q, pre.get("match")):
                        own = join(own, _lit(select_first(v.call, pre.get("path") or [], home, self.idx), home))
                    elif pre.get("kind") == "group" and glob(q, pre.get("match")):
                        inner = v.call.recv.split(".")[0] if v.call.recv else ""
                        own = join(self.prefix(home, inner, depth + 1),
                                   _lit(select_first(v.call, pre.get("path") or [], home, self.idx), home))
            break
        mounted = self.mounts.get((path, name)) or []
        if mounted:
            text, parent = mounted[0]
            outer = self.prefix(self.facts.get(parent[0], home), parent[1], depth + 1) if parent else ""
            return join(outer, text, own)
        return own


def _lit(v: Val | None, ff: FileFacts) -> str:
    if v is None:
        return ""
    lits = literals(v, ff)
    return lits[0].text if lits and lits[0].text else ""


def extract(facts: dict[str, FileFacts], cat: Catalog, idx: RepoIndex, deps_for) -> list[Route]:
    routes: list[Route] = []
    rules = cat.frameworks
    prefixes = _Prefixes(facts, idx, rules)
    django_includes = _django_includes(facts, cat, idx)
    for path, ff in facts.items():
        deps = deps_for(path)
        for rule in rules:
            if not applies(rule, ff, deps):
                continue
            fw = rule.get("display") or rule["framework"]
            for r in rule.get("routes") or []:
                kind = r.get("kind")
                if kind == "decorator":
                    routes += _decorators(r, fw, ff, idx, prefixes)
                elif kind == "annotation":
                    routes += _annotations(r, fw, ff, idx)
                elif kind == "call":
                    routes += _calls(r, fw, ff, idx, prefixes)
                elif kind == "file-route":
                    routes += _file_routes(r, fw, ff, idx, django_includes)
    seen, out = set(), []
    for r in routes:
        k = (r.file, r.line, r.method, r.path)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _decorators(r, fw, ff: FileFacts, idx, prefixes) -> list[Route]:
    out = []
    pats = expand(r.get("match"), r.get("methods"))
    anyrx = compiled([pat for pat, _ in pats])
    for d in ff.decos:
        q = qualify(ff, d.name, idx)
        if anyrx.match(q) is None:
            continue
        for pat, m in pats:
            if not compiled((pat,)).match(q):
                continue
            p = _path(r, d, ff, idx)
            if p is None:
                break
            root = d.name.split(".")[0] if "." in d.name else ""
            full = norm_path("/" + join(prefixes.prefix(ff, root), p))
            for meth in _methods(r, m, d, ff, idx):
                out.append(Route(ff.path, d.line, meth, full, fw, d.target))
            break
    return out


def _annotations(r, fw, ff: FileFacts, idx) -> list[Route]:
    out = []
    names = set(r.get("match") or [])
    skip = set(r.get("skip_class") or [])
    for d in ff.decos:
        if d.target_kind != "method" or d.name not in names:
            continue
        if any(cd.name in skip for cd in d.cls_decos):
            continue
        p = _path(r, d, ff, idx)
        if p is None:
            p = ""
        prefix = ""
        if r.get("class_prefix"):
            cd = next((x for x in d.cls_decos if x.name == r["class_prefix"]), None)
            if cd is not None:
                prefix = _path(r, cd, ff, idx) or ""
        full = norm_path("/" + join(prefix, p))
        for meth in _methods(r, None, d, ff, idx):
            out.append(Route(ff.path, d.line, meth, full, fw, d.target))
    return out


def _calls(r, fw, ff: FileFacts, idx, prefixes) -> list[Route]:
    out = []
    pats = expand(r.get("match"), r.get("methods"))
    anyrx = compiled([pat for pat, _ in pats])
    for c in ff.calls:
        q = qualify(ff, c.callee, idx)
        if anyrx.match(q) is None:
            continue
        for pat, m in pats:
            if not compiled((pat,)).match(q):
                continue
            p = _path(r, c, ff, idx)
            if p is None:
                break
            methods = None
            if r.get("method_in_path"):
                mm = re.match(r"^\s*([A-Z]+)\s+(\S.*)$", p)
                if mm and mm.group(1) in HTTP_METHODS:
                    methods, p = [mm.group(1)], mm.group(2)
                hm = re.match(r"^[A-Za-z0-9.\-]+(/.*)$", p)  # Go 1.22 "host/path" patterns
                if hm and not p.startswith("/"):
                    p = hm.group(1)
            if not (p.startswith("/") or p == "*"):
                break
            root = c.recv.split(".")[0].split("(")[0] if c.recv else ""
            full = norm_path("/" + join(prefixes.prefix(ff, root), p))
            handler = next((a.text for a in reversed(c.args) if a.kind == "name"), None)
            for meth in methods or _methods(r, m, c, ff, idx):
                out.append(Route(ff.path, c.line, meth, full, fw, handler))
            break
    return out


def _file_routes(r, fw, ff: FileFacts, idx, django_includes) -> list[Route]:
    path = ff.path
    files = r.get("files") or []
    name = PurePosixPath(path).name
    if r.get("match"):  # route calls in a conventional file (Django urls.py)
        if not any(fnmatch.fnmatchcase(name, f) or fnmatch.fnmatchcase(path, f) for f in files):
            return []
        prefix = django_includes.get(path, "")
        out = []
        for c in ff.calls:
            if not glob(qualify(ff, c.callee, idx), r["match"]):
                continue
            if len(c.args) > 1 and c.args[1].kind == "call" and glob(qualify(ff, c.args[1].text, idx), r.get("include")):
                continue  # a mount, not a route
            p = _path(r, c, ff, idx)
            if p is None:
                continue
            for meth in _methods(r, None, c, ff, idx):
                out.append(Route(path, c.line, meth, norm_path("/" + join(prefix, p)), fw,
                                 next((a.text for a in c.args[1:2] if a.kind == "name"), None)))
        return out
    hit = next((f for f in files if _route_glob(path, f)), None)
    if hit is None:
        return []
    base = hit.split("**")[0]
    rel = path[len(base):] if path.startswith(base) else path.split(base.rstrip("/"), 1)[-1]
    segs = [s for s in PurePosixPath(rel).parts]
    if segs and segs[-1].startswith("route."):
        segs = segs[:-1]
    elif segs:
        stem = PurePosixPath(segs[-1]).stem
        segs = segs[:-1] + ([] if stem == "index" else [stem])
    segs = [s for s in segs if not (s.startswith("(") and s.endswith(")")) and not s.startswith("@")]
    prefix = "/api" if "pages/api" in hit else ""
    url = norm_path(prefix + "/" + "/".join(segs))
    exports = r.get("exports")
    if exports:
        methods = [m for m in exports if m in ff.exports or m in ff.funcs]
    else:
        methods = [str(m).upper() for m in r.get("default_methods") or ["ANY"]]
    line = 1
    for m in methods:
        if m in ff.funcs:
            line = ff.funcs[m][1]
            break
    return [Route(path, line, m, url, fw, m) for m in methods]


def route_glob(path: str, pattern: str) -> bool:
    """A file-route pattern: ``app/**/route.*`` (any depth below ``app/``) or a bare file name."""
    if "/" not in pattern:
        return PurePosixPath(path).name == pattern
    return _route_glob(path, pattern)


def _route_glob(path: str, pattern: str) -> bool:
    base = pattern.split("**")[0]
    tail = pattern.split("**")[-1].lstrip("/")
    if not path.startswith(base):
        return False
    return fnmatch.fnmatchcase(PurePosixPath(path).name, tail or "*") and path.endswith(
        (".js", ".ts", ".jsx", ".tsx", ".mjs"))


def _django_includes(facts: dict[str, FileFacts], cat: Catalog, idx) -> dict[str, str]:
    """urls.py file -> the prefix under which another urls.py includes it."""
    rule = next((r for fw in cat.frameworks for r in fw.get("routes") or []
                 if fw.get("framework") == "django" and r.get("include")), None)
    if rule is None:
        return {}
    out: dict[str, str] = {}
    for ff in facts.values():
        if ff.family != "python" or not ff.path.endswith("urls.py"):
            continue
        for c in ff.calls:
            if not glob(qualify(ff, c.callee, idx), rule["match"]) or len(c.args) < 2:
                continue
            inc = c.args[1]
            if inc.kind != "call" or inc.call is None or not glob(qualify(ff, inc.text, idx), rule["include"]):
                continue
            mod = inc.call.args[0].text if inc.call.args and inc.call.args[0].kind == "str" else None
            prefix = c.args[0].text if c.args[0].kind == "str" else ""
            if not mod:
                continue
            for cand in idx.py.get(mod, []):
                out[cand] = join(out.get(ff.path, ""), prefix)
    return out
