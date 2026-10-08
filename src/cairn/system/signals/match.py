"""Matching catalog rules against the language facts of ``syntax``.

- ``qualify`` turns a dotted callee into a qualified one by following imports and bindings, within the
  file and one hop into a first-party module: ``r.publish`` with ``r = redis.Redis.from_url(...)`` becomes
  ``redis.Redis.from_url().publish``; ``router.post`` with ``router`` imported from ``.orders`` becomes
  ``fastapi.APIRouter().post``. Rules are glob patterns over that string.
- ``select`` reads a rule's selector (``arg0``, ``kwarg.prefix``, ``field.arg0.topic`` ...) off a call.
- ``literal`` and ``url`` reduce a selected value to a literal, a configuration name or a URL's parts.
  URL hosts are returned for resolution only; callers must not store them (data-model.md).
- ``Wrappers`` resolves a value that is a parameter of the enclosing function through that function's
  callers, up to two hops (prototype lesson 4: wrappers hide calls).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from .syntax import Call, Deco, FileFacts, Val

_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):(?://)?")
_HOSTISH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*(?::\d+)?$")
_JS_EXTS = (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx", "/index.js", "/index.ts")


class RepoIndex:
    """First-party module lookup: Python dotted modules and JS relative paths -> parsed facts."""

    def __init__(self, facts: dict[str, FileFacts]):
        self.facts = facts
        self.py: dict[str, list[str]] = {}
        for path, ff in facts.items():
            if ff.family != "python":
                continue
            parts = list(PurePosixPath(path).with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                self.py.setdefault(".".join(parts[i:]), []).append(path)

    def module(self, from_file: str, origin: str) -> tuple[FileFacts, str] | None:
        """(facts of the defining file, the name inside it) for a first-party import origin."""
        ff = self.facts.get(from_file)
        if ff is None:
            return None
        if ff.family == "python":
            if "." not in origin.strip("."):
                return None
            mod, name = origin.rsplit(".", 1)
            if origin.startswith("."):
                dots = len(mod) - len(mod.lstrip("."))
                base = list(PurePosixPath(from_file).parent.parts)
                base = base[: max(0, len(base) - (dots - 1))]
                rel = mod.lstrip(".")
                target = ".".join(base + ([rel] if rel else []))
                cands = self.py.get(target, [])
            else:
                cands = self.py.get(mod, [])
            if len(cands) == 1 and cands[0] in self.facts:
                return self.facts[cands[0]], name
            return None
        if ff.family == "javascript":
            mod, _, name = origin.partition(".") if not origin.startswith(".") else _js_split(origin)
            if not mod.startswith("."):
                return None
            base = PurePosixPath(from_file).parent / mod
            norm = _norm(str(base))
            for ext in ("",) + _JS_EXTS:
                cand = norm + ext
                if cand in self.facts:
                    tgt = self.facts[cand]
                    return tgt, name or (tgt.default_export or "")
            return None
        return None


def _js_split(origin: str) -> tuple[str, str, str]:
    """``./routes/orders.router`` -> ("./routes/orders", ".", "router"); a bare path has no name."""
    last = origin.rsplit("/", 1)[-1]
    if "." in last and not last.endswith((".js", ".ts", ".mjs", ".cjs")) and not last.startswith("."):
        head, name = origin.rsplit(".", 1)
        return head, ".", name
    return origin, "", ""


def _norm(path: str) -> str:
    out: list[str] = []
    for p in path.replace("\\", "/").split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if out:
                out.pop()
            continue
        out.append(p)
    return "/".join(out)


# ------------------------------------------------------------------------------------------- qualify
def _bind_q(ff: FileFacts, head: str, idx: RepoIndex | None, depth: int) -> str | None:
    for v in ff.binds.get(head, []):
        if v.kind == "module":
            return v.text
        if v.kind in ("call", "type", "name"):
            root = re.split(r"[.(\[]", v.text, maxsplit=1)[0]
            if v.text == head or (root == head and v.kind == "name"):
                continue
            inner = qualify(ff, v.text, idx, depth + 1)
            return inner if v.kind == "name" else inner + "()"
    return None


def qualify(ff: FileFacts, dotted: str, idx: RepoIndex | None = None, depth: int = 0) -> str:
    if depth == 0:
        hit = ff.qcache.get(dotted)
        if hit is None:
            hit = ff.qcache[dotted] = _qualify(ff, dotted, idx, 0)
        return hit
    return _qualify(ff, dotted, idx, depth)


def _qualify(ff: FileFacts, dotted: str, idx: RepoIndex | None, depth: int) -> str:
    if depth > 5 or not dotted:
        return dotted
    segs = dotted.split(".")
    for k in range(len(segs), 0, -1):
        head = ".".join(segs[:k])
        if "(" in head or "[" in head:
            continue
        rest = segs[k:]
        q = _bind_q(ff, head, idx, depth)
        if q is None and head in ff.imports:
            q = ff.imports[head]
        if q is not None and idx is not None and depth < 3 and not q.endswith(")"):
            hit = idx.module(ff.path, q)
            if hit is not None:
                other, name = hit
                if name and (name in other.binds or name in other.imports):
                    q = qualify(other, name, idx, depth + 1)
        if q is not None:
            return ".".join([q, *rest]) if rest else q
    return dotted


def is_typed(qual: str, libraries) -> bool:
    for lib in libraries or ():
        lib = str(lib)
        if qual == lib or qual.startswith(lib + ".") or qual.startswith(lib + "(") or qual.startswith(lib + "/"):
            return True
    return False


def imports_library(ff: FileFacts, libraries) -> bool:
    origins = list(ff.imports.values()) + list(ff.namespaces)
    for lib in libraries or ():
        lib = str(lib)
        for o in origins:
            if o == lib or o.startswith(lib + ".") or o.startswith(lib + "/"):
                return True
    return False


# ------------------------------------------------------------------------------------------- selectors
def select(node: Call | Deco, selector: str, ff: FileFacts, idx: RepoIndex | None = None, *,
           cls_deco: Deco | None = None) -> list[Val]:
    args, kwargs = node.args, node.kwargs
    if selector == "args":
        return list(args)
    if selector.startswith("args_from"):
        return list(args[int(selector[9:] or 0):])
    if re.fullmatch(r"arg\d+", selector):
        i = int(selector[3:])
        return [args[i]] if i < len(args) else []
    if selector.startswith("kwarg."):
        v = kwargs.get(selector[6:])
        return [v] if v is not None else []
    if selector.startswith("field."):
        _, which, name = selector.split(".", 2)
        src = select(node, which, ff, idx)
        if not src:
            return []
        obj = _deref(src[0], ff)
        if obj.kind != "obj":
            return []
        if name == "*":
            return [Val("str", k) for k in obj.fields]
        v = obj.fields.get(name)
        return [v] if v is not None else []
    if selector.startswith("items."):
        src = select(node, selector[6:], ff, idx)
        out = []
        for v in src:
            v = _deref(v, ff)
            out.extend(v.items if v.kind == "list" else [v])
        return out
    if selector.startswith("recv.") and isinstance(node, Call):
        ctor = receiver_ctor(node, ff)
        return select(ctor, selector[5:], ff, idx) if ctor is not None else []
    if selector.startswith("class.") and cls_deco is not None:
        return select(cls_deco, selector[6:], ff, idx)
    if selector == "chain" and isinstance(node, Call):
        return [Val("str", c) for c in reversed(node.chain)]
    if selector == "name":
        return [Val("str", node.name if isinstance(node, Call) else node.name)]
    return []


def select_first(node, selectors, ff, idx=None, *, cls_deco=None, skip_empty=True) -> Val | None:
    for s in selectors or ():
        for v in select(node, s, ff, idx, cls_deco=cls_deco):
            if skip_empty and v.kind == "str" and not v.text:
                continue
            return v
    return None


def receiver_ctor(call: Call, ff: FileFacts) -> Call | None:
    """The constructor call bound to a call's receiver root (``client = httpx.Client(base_url=...)``)."""
    root = call.recv.split(".")[0].split("(")[0] if call.recv else ""
    for key in (call.recv, root):
        for v in ff.binds.get(key, []):
            if v.kind == "call" and v.call is not None:
                return v.call
    return None


def _deref(v: Val, ff: FileFacts, depth: int = 0) -> Val:
    """A name bound in this file to a literal, object or list -> that value (constants)."""
    while v.kind == "name" and depth < 3:
        cands = [b for b in ff.binds.get(v.text, []) if b.kind in ("str", "tmpl", "obj", "list", "env", "config")]
        if not cands:
            break
        v, depth = cands[0], depth + 1
    return v


# ------------------------------------------------------------------------------------------- values
@dataclass
class Lit:
    """A literal key, or the configuration name that supplies it."""
    text: str | None = None
    config: str | None = None


def literals(v: Val | None, ff: FileFacts) -> list[Lit]:
    if v is None:
        return []
    v = _deref(v, ff)
    if v.kind == "str":
        return [Lit(text=v.text)] if v.text else []
    if v.kind == "tmpl":
        lits = "".join(p[1] for p in v.parts if p[0] == "lit")
        return [Lit(text=v.text)] if lits.strip("{}.:/-_ ") else []
    if v.kind in ("env", "config"):
        return [Lit(config=v.text)]
    if v.kind == "list":
        out = []
        for it in v.items:
            out.extend(literals(it, ff))
        return out
    if v.kind == "name":
        last = v.text.rsplit(".", 1)[-1]
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", last) and "." in v.text:
            return []  # an enum or constant from elsewhere: unknown, not guessed
    return []


@dataclass
class Url:
    path: str | None = None
    base: str | None = None        # configuration name that supplies the base address
    host: str | None = None        # transient: resolve, never store
    scheme: str | None = None


def url(v: Val | None, ff: FileFacts, depth: int = 0) -> Url | None:
    if v is None or depth > 3:
        return None
    if v.kind == "name":
        d = _deref(v, ff)
        if d is v:
            return None
        return url(d, ff, depth + 1)
    if v.kind in ("env", "config"):
        return Url(base=v.text)
    if v.kind == "str":
        return parse_url_text(v.text)
    if v.kind == "tmpl":
        parts = list(v.parts)
        base = None
        head = None
        if parts and parts[0][0] in ("var", "env", "config"):
            kind, text = parts[0]
            if kind in ("env", "config"):
                base = text
            else:
                b = url(Val("name", text), ff, depth + 1)
                if b is not None:
                    base, head = b.base, b
            parts = parts[1:]
        rendered = "".join(p[1] if p[0] == "lit" else "{}" for p in parts)
        if head is not None and head.host and not base:
            path = (head.path or "").rstrip("/") + rendered
            return Url(path=path or "/", host=head.host, scheme=head.scheme)
        if base is not None or (rendered.startswith("/") and v.parts[0][0] != "lit"):
            return Url(path=rendered or "/", base=base) if rendered.startswith("/") or not rendered else Url(base=base)
        return parse_url_text(rendered)
    return None


def parse_url_text(text: str) -> Url | None:
    t = (text or "").strip()
    if not t:
        return None
    m = _SCHEME.match(t)
    if m and "://" in t[: m.end() + 2]:
        try:
            sp = urlsplit(t.replace("{}", "X"))
            host = sp.hostname
        except ValueError:
            return None
        path = t[m.end():].split("/", 1)
        rest = "/" + path[1] if len(path) > 1 else "/"
        return Url(path=rest.split("?", 1)[0].split("#", 1)[0], host=host, scheme=m.group(1).lower())
    if t.startswith("/"):
        return Url(path=t.split("?", 1)[0].split("#", 1)[0])
    return None


def host_of(text: str | None) -> tuple[str | None, str | None]:
    """(scheme, host) from a URL, a ``host:port``, a ``key=value;`` connection string or a JDBC URL.
    Values are inspected transiently and never stored."""
    if not text:
        return None, None
    t = str(text).strip()
    if t.lower().startswith("jdbc:"):
        t = t[5:]
    m = _SCHEME.match(t)
    if m and "://" in t:
        try:
            sp = urlsplit(t)
            return m.group(1).lower(), sp.hostname
        except ValueError:
            return m.group(1).lower(), None
    if ";" in t or "=" in t:
        for part in t.split(";"):
            k, _, val = part.partition("=")
            if k.strip().lower() in ("server", "host", "data source", "address", "addr"):
                return None, val.strip().split(",")[0].split(":")[0] or None
        m2 = re.search(r"\bhost=([^\s;]+)", t)
        return (None, m2.group(1)) if m2 else (None, None)
    first = t.split(",")[0]
    if _HOSTISH.match(first):
        return None, first.split(":")[0]
    return None, None


# ------------------------------------------------------------------------------------------- wrappers
def param_index(ff: FileFacts, call: Call | Deco, v: Val | None) -> tuple[str, int, str] | None:
    """When ``v`` is a parameter of the enclosing function: (function, index, name)."""
    if v is None or v.kind != "name" or not isinstance(call, Call) or not call.func:
        return None
    sig = ff.funcs.get(call.func)
    if not sig:
        return None
    params = sig[0]
    if v.text in params:
        return call.func, params.index(v.text), v.text
    return None


class Wrappers:
    """Callers of first-party functions, by function name, for parameter resolution."""

    def __init__(self, facts: dict[str, FileFacts]):
        self.facts = facts
        self.by_name: dict[str, list[tuple[FileFacts, Call]]] = {}
        for ff in facts.values():
            for c in ff.calls:
                self.by_name.setdefault(c.name, []).append((ff, c))

    def callers(self, def_ff: FileFacts, func: str, index: int, pname: str):
        """(caller facts, caller call, argument value) for each call of ``func`` that can reach it."""
        sig = def_ff.funcs.get(func)
        offset = 1 if sig and sig[0] and sig[0][0] in ("self", "cls") else 0
        for ff, c in self.by_name.get(func, []):
            if c.callee.endswith("()"):
                continue
            reach = ff is def_ff or func in ff.imports or bool(c.recv)
            if not reach:
                continue
            i = index - offset if c.recv and offset else index
            v = c.kwargs.get(pname) if pname in c.kwargs else (c.args[i] if 0 <= i < len(c.args) else None)
            if v is not None:
                yield ff, c, v
