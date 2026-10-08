"""Mermaid export: a flowchart (a sequenceDiagram for flows) that renders natively, with every fact kept in
``%% cairn`` JSON comments so ``mermaid_in`` restores the description without loss.

Escaping rule: nothing from a description reaches Mermaid except through ``esc`` (labels), ``mid`` (ids) or
``jsonc`` (comments). Labels turn every character Mermaid or HTML could act on into a numeric entity, so a name
cannot close a node, start a ``%%`` directive, add an edge, a click handler or markup.
"""
from __future__ import annotations

import json
import re

from . import load_tokens
from .schema import element_map

_RESERVED = {"end", "graph", "flowchart", "subgraph", "class", "classdef", "style", "linkstyle", "click", "default",
             "direction", "call", "href", "callback", "participant", "actor", "note", "loop", "alt", "else", "opt",
             "par", "and", "rect", "critical", "break", "activate", "deactivate", "autonumber", "title"}
_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_DANGER = re.compile(r"[\"#%&<>\[\]{}()|;`\\\x00-\x1f\x7f]")
SHAPES = {"person": ('(["', '"])'), "system": ('["', '"]'), "external-system": ('["', '"]'), "container": ('["', '"]'),
          "data-store": ('[("', '")]'), "channel": ('[["', '"]]'), "library": ('[/"', '"/]'), "component": ('["', '"]'),
          "entity": ('["', '"]'), "code": ('["', '"]'), "state": ('("', '")')}


def esc(value: object) -> str:
    """Label text with every active character as a numeric entity (``#60;`` style, Mermaid's own form)."""
    return _DANGER.sub(lambda m: f"#{ord(m.group(0))};", " ".join(str(value).split()))


def jsonc(obj: object) -> str:
    """JSON on one line with no ``%``, ``<`` or ``>`` so it can never form a directive or markup."""
    s = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return s.replace("%", "\\u0025").replace("<", "\\u003c").replace(">", "\\u003e")


def _ids(ids: list[str], taken: set[str] | None = None) -> dict[str, str]:
    """Stable Mermaid-safe ids; the original id travels in the comments."""
    out: dict[str, str] = {}
    used: set[str] = set(taken or ())
    for n, i in enumerate(ids, 1):
        cand = i if _SAFE_ID.match(i) and i.lower() not in _RESERVED else f"n{n}"
        while cand in used or cand.lower() in _RESERVED:
            cand = f"{cand}_{n}"
        used.add(cand)
        out[i] = cand
    return out


def _title_block(title: str) -> list[str]:
    safe = jsonc(title)
    return ["---", f"title: {safe}", "---"]


def _init(theme: dict) -> str:
    init = {"theme": "base", "themeVariables": {"edgeLabelBackground": theme["label_mask"], "lineColor": theme["line"],
            "primaryTextColor": theme["ink"], "clusterBkg": "transparent", "clusterBorder": theme["boundary_stroke"]},
            "flowchart": {"curve": "linear"}}
    return "%%{init: " + json.dumps(init, separators=(",", ":")) + "}%%"


def _node_label(e: dict, mark: str) -> str:
    kind = (e.get("kind") or e.get("type") or "untyped").replace("-", " ").upper()
    parts = [f"<small>{esc(kind)}{(' ' + esc(mark)) if mark else ''}</small>", f"<b>{esc(e.get('name') or e['id'])}</b>"]
    if e.get("tech"):
        parts.append(f"[{esc(e['tech'])}]")
    if e.get("desc"):
        parts.append(esc(e["desc"]))
    for fl in (e.get("fields") or [])[:6]:
        parts.append(esc(fl))
    return "<br/>".join(parts)


def _meta(desc: dict) -> dict:
    return {k: desc[k] for k in ("diagram", "title", "scope", "description", "source_note", "legend") if k in desc}


def to_mermaid(desc: dict, theme: str = "light", tokens: dict | None = None) -> str:
    """Export any description; flows become a sequenceDiagram, everything else a flowchart."""
    T = tokens or load_tokens()
    if desc.get("diagram") == "flow" and desc.get("flow"):
        return _sequence(desc)
    th = T["themes"][theme]
    els = element_map(desc)
    ids = _ids(list(els))
    bids = _ids([b["id"] for b in desc.get("boundaries", [])], set(ids.values()))
    out = [*_title_block(desc.get("title", "")), _init(th), "flowchart LR", "%% cairn:diagram " + jsonc(_meta(desc))]
    for i, e in els.items():
        shape = SHAPES.get(e.get("type") or "", ('["', '"]'))
        mark = T["provenance"].get(e.get("provenance", "extracted"), {}).get("mark", "")
        out.append(f"  %% cairn:element {ids[i]} {jsonc(e)}")
        out.append(f"  {ids[i]}{shape[0]}{_node_label(e, mark)}{shape[1]}")
    for b in desc.get("boundaries", []):
        out.append(f'  subgraph {bids[b["id"]]}["{esc(b.get("name") or b["id"])} · {esc(b.get("type") or "")}"]')
        out.append(f"    %% cairn:boundary {bids[b['id']]} {jsonc(b)}")
        out.extend(f"    {ids[c]}" for c in b.get("contains", []) if c in ids)
        out.append("  end")
    rels = [r for r in desc.get("relationships", []) if r["from"] in ids and r["to"] in ids]
    dotted: list[int] = []
    for n, r in enumerate(rels):
        st = r.get("style", "sync")
        arrow = "-.->" if st in ("async", "build") else "-->"
        lab = esc(r.get("what") or "") + (f"<br/>[{esc(r['how'])}]" if r.get("how") else "")
        if r.get("data_class"):
            lab += f"<br/>data: {esc(r['data_class'])}"
        out.append(f"  %% cairn:rel {ids[r['from']]}->{ids[r['to']]} {jsonc(r)}")
        out.append(f'  {ids[r["from"]]} {arrow}|"{lab}"| {ids[r["to"]]}' if lab else f"  {ids[r['from']]} {arrow} {ids[r['to']]}")
        if st == "build":
            dotted.append(n)
    out += [f"  classDef base fill:{th['node_fill']},stroke:{th['node_stroke']},color:{th['ink']},stroke-width:1.2px",
            f"  classDef store fill:{th['store_fill']},stroke:{th['node_stroke']},color:{th['ink']}",
            f"  classDef ext fill:{th['external_fill']},stroke:{th['external_stroke']},color:{th['ink']},stroke-dasharray:4 3",
            f"  classDef focus fill:{th['accent_wash']},stroke:{th['accent']},color:{th['ink']},stroke-width:1.8px"]
    for i, e in els.items():
        t = e.get("type")
        cls = "focus" if e.get("focus") else "ext" if t == "external-system" else "store" if t in ("data-store", "channel") else "base"
        out.append(f"  class {ids[i]} {cls}")
    for b in desc.get("boundaries", []):
        col = th["trust"] if b.get("trust") else th["boundary_stroke"]
        out.append(f"  style {bids[b['id']]} fill:transparent,stroke:{col},stroke-dasharray:6 4")
    if dotted:
        out.append(f"  linkStyle {','.join(map(str, dotted))} stroke-dasharray:1.5 3.5")
    out.append(f"  linkStyle default stroke:{th['line']}")
    return "\n".join(out) + "\n"


_ARROW = {"sync": "->>", "return": "-->>", "async": "--)", "build": "--)"}


def _sequence(desc: dict) -> str:
    els = element_map(desc)
    f = desc["flow"]
    parts = [p for p in f.get("participants", []) if p in els]
    ids = _ids(parts)
    out = [*_title_block(desc.get("title", "")), "sequenceDiagram", "%% cairn:diagram " + jsonc(_meta(desc))]
    for p in parts:
        e = els[p]
        out.append(f"  %% cairn:element {ids[p]} {jsonc(e)}")
        kw = "actor" if e.get("type") == "person" else "participant"
        out.append(f"  {kw} {ids[p]} as {esc(e.get('name') or p)}")
    msgs = f.get("messages", [])
    opens: dict[int, list[dict]] = {}
    closes: dict[int, int] = {}
    elses: dict[int, dict] = {}
    for fr in f.get("fragments", []):
        s, e = fr.get("start"), fr.get("end")
        if isinstance(s, int) and isinstance(e, int) and 1 <= s <= e <= len(msgs):
            opens.setdefault(s, []).append(fr)
            closes[e] = closes.get(e, 0) + 1
            if fr.get("else_at"):
                elses[fr["else_at"]] = fr
    for n, m in enumerate(msgs, 1):
        for fr in opens.get(n, []):
            out.append(f"  {esc(fr.get('type') or 'opt')} {esc(fr.get('label') or '')}".rstrip())
        if n in elses:
            out.append(f"  else {esc(elses[n].get('else_label') or '')}".rstrip())
        if m["from"] in ids and m["to"] in ids:
            text = esc(m.get("what") or "") + (f"<br/>[{esc(m['how'])}]" if m.get("how") else "")
            out.append(f"  %% cairn:msg {n} {jsonc(m)}")
            out.append(f"  {ids[m['from']]}{_ARROW.get(m.get('style', 'sync'), '->>')}{ids[m['to']]}: {text}")
        out.extend("  end" for _ in range(closes.get(n, 0)))
    return "\n".join(out) + "\n"
