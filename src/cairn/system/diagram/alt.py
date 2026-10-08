"""The long text alternative: a Markdown table of every element and relationship with its evidence.

All cells are escaped so a name cannot add a column, a link, an image or raw HTML to the document.
"""
from __future__ import annotations

import re

from .schema import element_map, relationships

_MD_SPECIAL = re.compile(r"([\\`\[\]|*_])")


def md(value: object) -> str:
    """One table cell: HTML-neutralised, Markdown-special characters backslash-escaped, one line."""
    s = " ".join(str(value if value is not None else "").split())
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MD_SPECIAL.sub(r"\\\1", s)


def _evidence(x: dict) -> str:
    refs = "; ".join(md(r) for r in x.get("evidence", []))
    return f"{refs} ({md(x.get('provenance', 'extracted'))})"


def evidence_table(desc: dict) -> str:
    els = element_map(desc)
    out = [f"**{md(desc.get('title', ''))}**: {md(desc.get('description', ''))}", "",
           "| Element | Type | Technology | Responsibility | Evidence |", "|---|---|---|---|---|"]
    for e in els.values():
        out.append(f"| {md(e.get('name') or e['id'])} | {md(e.get('kind') or e.get('type') or '')} | {md(e.get('tech'))} "
                   f"| {md(e.get('desc'))} | {_evidence(e)} |")
    out += ["", "| # | From | To | What | How | Evidence |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(relationships(desc), 1):
        a, b = els.get(r["from"], {}), els.get(r["to"], {})
        out.append(f"| {i} | {md(a.get('name') or r['from'])} | {md(b.get('name') or r['to'])} | {md(r.get('what'))} "
                   f"| {md(r.get('how'))} | {_evidence(r)} |")
    return "\n".join(out) + "\n"
