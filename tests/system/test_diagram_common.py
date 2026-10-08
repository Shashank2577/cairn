"""Shared helpers for the diagram tests (no tests here)."""
from __future__ import annotations

import copy
from pathlib import Path

from cairn.system.diagram import schema

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "docs" / "diagram-standard" / "examples").glob("*.yaml"))
TAAZAA = sorted((ROOT / "docs" / "diagram-standard" / "taazaa-original").glob("*.yaml"))

HOSTILE = [
    "<script>alert(1)</script>",
    'he said "hi"',
    "it's",
    "a]b",
    "a|b",
    "line1\nline2",
    "%%{init: {'securityLevel':'loose'}}%%",
    "click X call evil()",
    "A --> B",
    "<![CDATA[x]]>",
    "&#x3C;b&#x3E;",
    "end",
    "subgraph evil",
    "![x](http://evil.example/x.png)",
]


def load_example(stem: str) -> dict:
    return schema.load_file(next(p for p in EXAMPLES if p.stem.startswith(stem)))


BASE = {
    "diagram": "container",
    "title": "Containers of X",
    "scope": "X (software system)",
    "description": "The web app calls the API, which stores data in a database.",
    "elements": [
        {"id": "web", "name": "web", "type": "container", "kind": "web-app", "tech": "Node.js", "desc": "Storefront",
         "evidence": ["web/server.js:1"]},
        {"id": "api", "name": "api", "type": "container", "kind": "service", "tech": "FastAPI", "desc": "Orders API",
         "evidence": ["api/main.py:1"]},
        {"id": "db", "name": "db", "type": "data-store", "tech": "postgres", "desc": "Stores orders", "evidence": ["api/db.py:1"]},
    ],
    "relationships": [
        {"from": "web", "to": "api", "what": "Creates orders", "how": "HTTP POST /orders", "style": "sync", "evidence": ["web/api.js:3"]},
        {"from": "api", "to": "db", "what": "Stores orders", "how": "SQL", "style": "sync", "evidence": ["api/db.py:7"]},
    ],
}


def valid(**over) -> dict:
    d = copy.deepcopy(BASE)
    d.update(over)
    return schema.normalize(d)


def many(n: int, rels: int | None = None, *, kind: str = "container") -> dict:
    """A chain-ish description with n containers, for budgets and performance."""
    els = [{"id": f"e{i}", "name": f"Element {i}", "type": "container", "tech": "x", "desc": "does a thing",
            "evidence": [f"r/f{i}.py:1"]} for i in range(n)]
    m = rels if rels is not None else n - 1
    rl = [{"from": f"e{i % n}", "to": f"e{(i * 7 + 1) % n}", "what": f"Does thing {i}", "how": "HTTP", "style": "sync",
           "evidence": [f"r/f{i}.py:2"]} for i in range(m)]
    rl = [r for r in rl if r["from"] != r["to"]]
    return schema.normalize({"diagram": kind, "title": "Containers of Many", "scope": "many", "description": "many things",
                             "elements": els, "relationships": rl})
