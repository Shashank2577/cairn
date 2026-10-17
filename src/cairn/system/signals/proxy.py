"""Reverse-proxy configuration (FR-034's ``config`` pattern kind, outbound side): an nginx-style
``location`` that ``proxy_pass``es to another service (``location /api/cart/ { proxy_pass
http://$cart:8080/; }``).

Driven by the ``proxies`` rules in the catalog (file globs plus the directive). The upstream host may be
a template variable (nginx ``envsubst`` style, ``${NAME}`` or ``$NAME``); it is returned as the
configuration NAME for the build to resolve from the deploy description, never as a value read here.
Comments (``#``) are skipped. A ``location`` with several ``proxy_pass`` directives yields one pass each;
the modifier form (``location ~ ^/api/``) is read, with the regex taken as an opaque prefix label.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..catalog import Catalog, glob

LOCATION = re.compile(r"^\s*location\s+(?:[=~^]\s+)?(?P<path>\S+)\s*\{")
PROXY_PASS = re.compile(r"^\s*proxy_pass\s+(?P<url>[^;]+);")
VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


@dataclass
class ProxyPass:
    file: str
    line: int
    path: str                 # the location prefix, as written ("/api/cart/")
    host: str | None = None   # literal upstream host, when the URL has one
    config: str | None = None  # configuration name supplying the upstream host (${HOST} / $HOST)
    port: str | None = None
    proxy: str = ""


def extract(files, cat: Catalog) -> list[ProxyPass]:
    out: list[ProxyPass] = []
    for rel in files.paths:
        for rule in cat.proxies:
            if not glob(rel, rule.get("files")):
                continue
            out += _parse(rel, files.read(rel), rule)
    return out


def _parse(rel: str, text: str | None, rule) -> list[ProxyPass]:
    if not text:
        return []
    directive = rule.get("directive") or "proxy_pass"
    rx_pass = re.compile(r"^\s*" + re.escape(directive) + r"\s+(?P<url>[^;]+);")
    out: list[ProxyPass] = []
    loc: tuple[str, int] | None = None       # (path, line) of the enclosing location
    depth = 0                                # brace depth inside that location block
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        m = LOCATION.match(line)
        if m is not None:
            loc = (m.group("path").lstrip("^"), i)
            depth = line.count("{") - line.count("}")
            continue
        if loc is None:
            continue
        p = rx_pass.match(line)
        if p is not None and depth >= 1:
            out.append(_pass(rel, i, loc[0], p.group("url").strip(), rule))
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            loc = None
    return out


def _pass(rel: str, line: int, path: str, target: str, rule) -> ProxyPass:
    pp = ProxyPass(file=rel, line=line, path=path, proxy=rule.get("proxy", ""))
    body = target
    if "://" in body:
        body = body.split("://", 1)[1]
    host_part, _, rest = body.partition("/")
    host_part = host_part.strip()
    if ":" in host_part:
        host_part, port = host_part.rsplit(":", 1)
        pp.port = port if port.isdigit() else None
    vm = VAR.search(host_part)
    if vm is not None:
        pp.config = vm.group(1)
        if host_part != vm.group(0):   # a literal host with a variable in it: keep the literal
            pp.host = host_part.replace(vm.group(0), "x")
    else:
        pp.host = host_part or None
    return pp
