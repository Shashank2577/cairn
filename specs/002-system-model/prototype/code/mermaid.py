"""PROTOTYPE (throwaway, not part of src/): Mermaid export from a Cairn diagram description, and a limited
Mermaid flowchart import back into one.

Export: every element, boundary and relationship with what + how, shapes per type, line style per
interaction, colours from tokens.json, and each element's type/kind/tech/evidence in a `%% cairn:` comment,
so the file round-trips. Layout, label masks, legend and dark mode are Mermaid's, not the standard's.

Import (flowchart subset only): `flowchart LR|TB`, node shapes [], (), ([]), [()], [[]], [//], {{}},
`A -->|label| B`, `A -.->|label| B`, `A ==> B`, `A --> B`, subgraph ... end, and `%% cairn:` comments.
Anything else (classDef, style, click, linkStyle, chained edges, `&`) is ignored with a warning.

Usage: python mermaid.py export <file.yaml> > out.mmd
       python mermaid.py import <file.mmd> --kind container > out.yaml
"""
from __future__ import annotations
import json, os, re, shlex, sys
import yaml

TOKENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../../docs/diagram-standard/tokens.json")
SHAPE = {"person": ('(["', '"])'), "system": ('["', '"]'), "external-system": ('["', '"]'), "container": ('["', '"]'),
         "data-store": ('[("', '")]'), "channel": ('[["', '"]]'), "library": ('[/"', '"/]'), "component": ('["', '"]'),
         "entity": ('["', '"]'), "code": ('["', '"]'), "state": ('("', '")')}
SHAPE_BACK = [(r'\(\["(.*)"\]\)', "person"), (r'\[\("(.*)"\)\]', "data-store"), (r'\[\["(.*)"\]\]', "channel"),
              (r'\[/"(.*)"/\]', "library"), (r'\{\{"(.*)"\}\}', "container"), (r'\("(.*)"\)', "state"),
              (r'\["(.*)"\]', None), (r'\[(.*)\]', None), (r'\((.*)\)', None)]
mid = lambda s: re.sub(r"[^A-Za-z0-9_]", "_", s)
q = lambda s: str(s).replace('"', "#quot;")


def export(d: dict, theme: str = "light") -> str:
    T = json.load(open(TOKENS))
    th = T["themes"][theme]
    els = {e["id"]: e for e in d.get("elements", [])}
    init = {"theme": "base", "themeVariables": {"edgeLabelBackground": th["label_mask"], "lineColor": th["line"],
            "primaryTextColor": th["ink"],
            "clusterBkg": "transparent", "clusterBorder": th["boundary_stroke"]}, "flowchart": {"curve": "linear"}}
    out = [f"---\ntitle: {d['title']}\n---", "%%{init: " + json.dumps(init) + "}%%", "flowchart LR",
           "%% cairn:diagram " + json.dumps({"kind": d["diagram"], "scope": d.get("scope", ""), "description": d.get("description", "")}, ensure_ascii=False)]

    def node(e, ind="  "):
        a, b = SHAPE.get(e["type"], ('["', '"]'))
        kind = (e.get("kind") or e["type"]).replace("-", " ").upper()
        mark = T["provenance"].get(e.get("provenance", "extracted"), {}).get("mark", "")
        parts = [f"<small>{kind}{(' ' + mark) if mark else ''}</small>", f"<b>{q(e['name'])}</b>"]
        if e.get("tech"):
            parts.append(f"[{q(e['tech'])}]")
        if e.get("desc"):
            parts.append(q(e["desc"]))
        meta = {k: e[k] for k in ("type", "kind", "tech", "desc", "provenance") if e.get(k)}
        meta["evidence"] = e.get("evidence", [])
        return [f"{ind}%% cairn:element {mid(e['id'])} {json.dumps(meta, ensure_ascii=False)}",
                f"{ind}{mid(e['id'])}{a}{'<br/>'.join(parts)}{b}"]

    inside = set()
    for b in d.get("boundaries", []):
        out.append(f'  subgraph {mid(b["id"])}["{q(b["name"])} · {q(b["type"])}"]')
        out.append(f"    %% cairn:boundary {mid(b['id'])} {json.dumps({'type': b['type'], 'trust': bool(b.get('trust'))})}")
        for c in b.get("contains", []):
            out += node(els[c], "    "); inside.add(c)
        out.append("  end")
    for i, e in els.items():
        if i not in inside:
            out += node(e)
    build_idx, i = [], 0
    for r in d.get("relationships", []):
        st = r.get("style", "sync")
        arrow = "-.->" if st in ("async", "build") else "-->"
        lab = q(r.get("what") or "") + (f"<br/>[{q(r['how'])}]" if r.get("how") else "")
        if r.get("data_class"):
            lab += f"<br/>data: {r['data_class']}"
        meta = {k: r[k] for k in ("what", "how", "style", "provenance", "data_class") if r.get(k)}
        meta["evidence"] = r.get("evidence", [])
        out.append(f"  %% cairn:rel {mid(r['from'])}->{mid(r['to'])} {json.dumps(meta, ensure_ascii=False)}")
        out.append(f'  {mid(r["from"])} {arrow}|"{lab}"| {mid(r["to"])}')
        if st == "build":
            build_idx.append(i)
        i += 1
    # colour roles from the shared tokens
    out += [f"  classDef base fill:{th['node_fill']},stroke:{th['node_stroke']},color:{th['ink']},stroke-width:1.2px",
            f"  classDef store fill:{th['store_fill']},stroke:{th['node_stroke']},color:{th['ink']}",
            f"  classDef ext fill:{th['external_fill']},stroke:{th['external_stroke']},color:{th['ink']},stroke-dasharray:4 3",
            f"  classDef focus fill:{th['accent_wash']},stroke:{th['accent']},color:{th['ink']},stroke-width:1.8px"]
    for e in els.values():
        cls = "focus" if e.get("focus") else "ext" if e["type"] == "external-system" else "store" if e["type"] in ("data-store", "channel") else "base"
        out.append(f"  class {mid(e['id'])} {cls}")
    for b in d.get("boundaries", []):
        out.append(f"  style {mid(b['id'])} fill:transparent,stroke:{th['trust'] if b.get('trust') else th['boundary_stroke']},stroke-dasharray:6 4")
    if build_idx:
        out.append(f"  linkStyle {','.join(map(str, build_idx))} stroke-dasharray:1.5 3.5")
    out.append(f"  linkStyle default stroke:{th['line']}")
    return "\n".join(out) + "\n"


def import_(text: str, kind: str) -> tuple[dict, list[str]]:
    warn, els, rels, bds, stack = [], {}, [], [], []
    meta_el, meta_rel, dmeta = {}, {}, {}
    title = ""
    m = re.search(r"^title:\s*(.+)$", text, re.M)
    if m:
        title = m.group(1).strip()
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or ln in ("---",) or ln.startswith("title:"):
            continue
        if ln.startswith("%% cairn ") or ln.startswith("%% cairn: "):   # short hand-written form
            toks = shlex.split(ln.split(" ", 2)[2])
            kv = dict(x.split("=", 1) for x in toks[1:] if "=" in x)
            for k in ("evidence",):
                if k in kv:
                    kv[k] = [s.strip() for s in kv[k].split(";")]
            if "->" in toks[0]:
                meta_rel[toks[0]] = kv
            elif toks[0] == "diagram":
                dmeta.update(kv); kind = kv.get("kind", kind)
            else:
                meta_el[toks[0]] = kv
            continue
        if ln.startswith("%% cairn:element"):
            _, _, nid, js = ln.split(" ", 3); meta_el[nid] = json.loads(js); continue
        if ln.startswith("%% cairn:rel"):
            _, _, pair, js = ln.split(" ", 3); meta_rel[pair] = json.loads(js); continue
        if ln.startswith("%% cairn:diagram"):
            dm = json.loads(ln.split(" ", 2)[2]); kind = dm.get("kind", kind); dmeta.update(dm); continue
        if ln.startswith("%%"):
            continue
        if re.match(r"^(flowchart|graph)\s+(LR|RL|TB|TD|BT)\b", ln):
            continue
        sm = re.match(r'^subgraph\s+(\w+)(?:\s*\["?(.*?)"?\])?$', ln)
        if sm:
            name = (sm.group(2) or sm.group(1)).split(" · ")
            bds.append({"id": sm.group(1), "name": name[0], "type": name[1] if len(name) > 1 else "", "contains": []}); stack.append(bds[-1]); continue
        if ln == "end":
            stack and stack.pop(); continue
        if re.match(r"^(classDef|class|style|linkStyle|click|direction)\b", ln):
            continue
        em = re.match(r'^(\w+)(\S*?)\s*(-->|-\.->|==>|---)\s*(?:\|"?(.*?)"?\|)?\s*(\w+)(\S*)$', ln)
        if em:
            a, ashape, arrow, label, b, bshape = em.groups()
            for nid, sh in ((a, ashape), (b, bshape)):
                if sh:
                    _node(nid + sh, els, stack, warn)
                els.setdefault(nid, {"id": nid, "name": nid})
            parts = re.split(r"<br\s*/?>", label or "")
            what = re.sub(r"<[^>]+>", "", parts[0]).strip() if parts and parts[0] else ""
            how = next((p.strip()[1:-1] for p in parts[1:] if p.strip().startswith("[")), "")
            r = {"from": a, "to": b, "what": what, "how": how, "style": "async" if arrow == "-.->" else "sync"}
            rels.append(r); continue
        if "&" in ln or re.search(r"-->.*-->", ln):
            warn.append(f"unsupported (chained or & edges): {ln}"); continue
        if not _node(ln, els, stack, warn):
            warn.append(f"ignored: {ln}")
    for r in rels:   # comments may come before or after the edge
        r.update(meta_rel.get(f"{r['from']}->{r['to']}", {}))
    for nid, e in els.items():
        e.update({k: (v == "true" if k == "focus" else v) for k, v in meta_el.get(nid, {}).items()})
        e.setdefault("type", "container" if kind in ("container", "component") else "system")
        e.setdefault("evidence", [])
    d = {"diagram": kind, "title": title, "scope": dmeta.get("scope", ""), "description": dmeta.get("description", ""),
         "elements": list(els.values()), "boundaries": [b for b in bds if b["contains"]], "relationships": rels}
    return d, warn


def _node(ln, els, stack, warn):
    nm = re.match(r"^(\w+)(.+)$", ln)
    if not nm:
        return False
    nid, rest = nm.groups()
    for rx, t in SHAPE_BACK:
        sm = re.fullmatch(rx, rest.strip())
        if sm:
            txt = re.split(r"<br\s*/?>", sm.group(1))
            clean = [re.sub(r"<[^>]+>", "", x).strip() for x in txt]
            clean = [c for c in clean if c]
            name = next((c for c in clean if not c.isupper() and not c.startswith("[")), clean[0] if clean else nid)
            e = els.setdefault(nid, {"id": nid})
            e["name"] = name
            tech = next((c[1:-1] for c in clean if c.startswith("[") and c.endswith("]")), None)
            if tech:
                e["tech"] = tech
            if t:
                e["type"] = t
            if stack:
                stack[-1]["contains"].append(nid)
            return True
    return False


if __name__ == "__main__":
    cmd, f = sys.argv[1], sys.argv[2]
    if cmd == "export":
        sys.stdout.write(export(yaml.safe_load(open(f)), sys.argv[3] if len(sys.argv) > 3 else "light"))
    else:
        kind = sys.argv[sys.argv.index("--kind") + 1] if "--kind" in sys.argv else "container"
        d, w = import_(open(f).read(), kind)
        sys.stdout.write(yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=200))
        for x in w:
            print("WARN", x, file=sys.stderr)
