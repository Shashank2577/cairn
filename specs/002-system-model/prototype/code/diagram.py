"""PROTOTYPE (throwaway, not part of src/): check and render Cairn diagram descriptions.

A diagram description is YAML/JSON:
  diagram: context|container|component|flow|data-model|state|landscape|code
  title, scope, description
  elements: [{id, name, type, kind?, tech?, desc?, at?: [col,row], focus?, provenance?, evidence?: [..], fields?: [..]}]
  boundaries: [{id, name, type, contains: [ids], trust?: bool}]
  relationships: [{from, to, what, how?, style: sync|async|build, provenance?, evidence?, data_class?}]
  flow (diagram: flow): participants: [ids], messages: [{from,to,what,how,style}], fragments: [{type,start,end,label,else_at?,else_label?}]
Usage: python diagram.py check|render <file.yaml>... [--tokens tokens.json] [--out dir] [--hand-made]
"""
from __future__ import annotations
import html, json, math, os, sys, textwrap
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOKENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../../docs/diagram-standard/tokens.json")
esc = lambda s: html.escape(str(s), quote=True)
BARE = {"uses", "use", "calls", "call", "talks to", "connects", "connects to", "->", "data", "request"}


# ------------------------------------------------------------------ check
def check(d: dict, T: dict, hand_made: bool = False) -> list[str]:
    v = []
    kind = d.get("diagram")
    K = T["diagram_kinds"].get(kind)
    if not K:
        return [f"R1 unknown diagram kind {kind!r}"]
    title = (d.get("title") or "").strip()
    if not title:
        v.append("R1 diagram has no title")
    elif not title.lower().startswith(K["title_prefix"].lower()):
        v.append(f"R1 title must start with the diagram type: '{K['title_prefix']} …'")
    if not d.get("scope"):
        v.append("R1 diagram has no scope (what system or container it describes)")
    if not d.get("description"):
        v.append("R13 no one-sentence description (text alternative)")
    els = {e.get("id"): e for e in d.get("elements", [])}
    B = T["budget"]
    if kind == "flow":
        f = d.get("flow") or {}
        parts = f.get("participants", [])
        for p in parts:
            if p not in els:
                v.append(f"R12 participant {p!r} is not an element")
        if len(parts) > B["sequence_participants"]:
            v.append(f"R8 {len(parts)} participants (budget {B['sequence_participants']}); split the flow")
        msgs = f.get("messages", [])
        if len(msgs) > B["sequence_messages"]:
            v.append(f"R8 {len(msgs)} messages (budget {B['sequence_messages']}); split the flow")
        for i, m in enumerate(msgs, 1):
            if m.get("from") not in parts or m.get("to") not in parts:
                v.append(f"R12 message {i} joins a non-participant")
            _label_rules(v, f"message {i}", m, K, B, T)
        if len(f.get("fragments", [])) > B["sequence_fragments"]:
            v.append(f"R8 more than {B['sequence_fragments']} fragment")
    else:
        allowed = set(K.get("primary", [])) | set(K.get("supporting", []))
        n_nodes = len(els)
        cap = B["nodes_hand_made"] if hand_made else B["nodes_soft"]
        if n_nodes > cap:
            v.append(f"R8 {n_nodes} elements (budget {cap}); split into overview and detail or collapse")
        if not any(e.get("type") in K.get("primary", []) for e in els.values()):
            v.append(f"R2 no primary element for a {kind} diagram ({', '.join(K.get('primary', []))})")
        rels = d.get("relationships", [])
        if len(rels) > B["relationships"]:
            v.append(f"R8 {len(rels)} relationships (budget {B['relationships']})")
        seen = set()
        for r in rels:
            tag = f"relationship {r.get('from')}→{r.get('to')}"
            if r.get("from") not in els or r.get("to") not in els:
                v.append(f"R12 {tag} joins an unknown element")
            if (r.get("to"), r.get("from"), r.get("what")) in seen:
                v.append(f"R6 {tag} duplicates its reverse; one arrow per direction with its own label")
            seen.add((r.get("from"), r.get("to"), r.get("what")))
            if r.get("bidirectional"):
                v.append(f"R6 {tag} is bidirectional; draw two labelled arrows")
            if r.get("style", "sync") not in T["relationship_styles"]:
                v.append(f"R5 {tag} has unknown style {r.get('style')!r}")
            _label_rules(v, tag, r, K, B, T)
            _evidence(v, tag, r, hand_made)
        for e in els.values():
            ET = T["element_types"].get(e.get("type"))
            tag = f"element {e.get('id')!r}"
            if not ET:
                v.append(f"R3 {tag} has no valid type (got {e.get('type')!r})")
                continue
            if e["type"] not in allowed:
                v.append(f"R2 {tag} is a {e['type']}, which belongs to another level than {kind}")
            if kind in ET["levels"] and ET.get("requires_tech") and not e.get("tech"):
                v.append(f"R3 {tag} has no technology")
            if not e.get("name"):
                v.append(f"R3 {tag} has no name")
            if not e.get("desc") and e["type"] not in ("entity", "state", "code"):
                v.append(f"R3 {tag} has no one-line responsibility")
            _evidence(v, tag, e, hand_made)
        focus = sum(1 for e in els.values() if e.get("focus"))
        if focus > B["accents"]:
            v.append(f"R11 {focus} accented elements (budget {B['accents']})")
        bds = d.get("boundaries", [])
        if len(bds) > B["boundaries"]:
            v.append(f"R9 {len(bds)} boundaries (budget {B['boundaries']})")
        for b in bds:
            if not b.get("name") or not b.get("type"):
                v.append(f"R9 boundary {b.get('id')!r} needs a name and a type (what it isolates or owns)")
            for c in b.get("contains", []):
                if c not in els:
                    v.append(f"R12 boundary {b.get('id')!r} contains unknown {c!r}")
    if d.get("legend") is False:
        v.append("R10 legend switched off; every diagram carries a legend")
    return v


def _label_rules(v, tag, r, K, B, T):
    what = (r.get("what") or "").strip()
    how = (r.get("how") or "").strip()
    if not what and not (how and r.get("provenance", "extracted") == "extracted" and r.get("computed")):
        v.append(f"R4 {tag} has no 'what' label")
    elif what.lower() in BARE:
        v.append(f"R4 {tag} label {what!r} is too vague; say what is exchanged or why")
    elif len(what) > B["label_what_chars"]:
        v.append(f"R4 {tag} 'what' longer than {B['label_what_chars']} characters")
    if K.get("how") == "required" and not how and r.get("style") != "return":
        v.append(f"R4 {tag} has no 'how' (protocol, route or mechanism)")
    if K.get("how") == "forbidden" and how:
        v.append(f"R4 {tag} names a technology at a level that must not ({how!r})")
    if how and len(how) > B["label_how_chars"]:
        v.append(f"R4 {tag} 'how' longer than {B['label_how_chars']} characters")


def _evidence(v, tag, x, hand_made):
    prov = x.get("provenance", "extracted")
    if prov not in ("extracted", "declared", "inferred", "ambiguous", "stale"):
        v.append(f"R7 {tag} has unknown provenance {prov!r}")
    if not x.get("evidence"):
        v.append(f"R7 {tag} has no evidence reference" + (" (hand-made: cite the document or person that declares it)" if hand_made else ""))


# ------------------------------------------------------------------ render helpers
class Theme:
    def __init__(self, T):
        self.T = T

    def css(self):
        L, D = self.T["themes"]["light"], self.T["themes"]["dark"]
        var = lambda th: ";".join(f"--{k.replace('_', '-')}:{val}" for k, val in th.items())
        F = self.T["font"]
        return (f"svg{{{var(L)}}}@media (prefers-color-scheme:dark){{svg{{{var(D)}}}}}"
                f"text{{font-family:{F['name']}}}text:not([fill]){{fill:var(--ink)}}.mono{{font-family:{F['tech']}}}"
                f".muted{{fill:var(--muted)}}.ink2{{fill:var(--ink2)}}")


def wrap(s, width, lines):
    out = textwrap.wrap(str(s), max(1, width)) or [""]
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][: max(0, width - 1)].rstrip() + "…"
    return out


def tw(s, size, mono=False):
    return len(str(s)) * size * (0.6 if mono else 0.56)


def node_svg(e, x, y, w, h, T):
    t = e["type"]
    ET = T["element_types"][t]
    fill, stroke = f"var(--{ET['fill'].replace('_', '-')})", f"var(--{ET['stroke'].replace('_', '-')})"
    sw = T["stroke"]["node"]
    dash = ""
    if ET["border"] == "external":
        dash = f' stroke-dasharray="{T["dash"]["external"]}"'
    prov = e.get("provenance", "extracted")
    if prov == "stale":
        stroke, fill = "var(--stale)", "var(--stale-wash)"
    if e.get("focus"):
        stroke, fill, sw = "var(--accent)", "var(--accent-wash)", T["stroke"]["focus"]
    r = T["radius"]["node"]
    s = []
    shape = ET["shape"]
    if shape == "cylinder":
        ry = 8
        s.append(f'<path d="M{x} {y+ry} v{h-2*ry} a{w/2} {ry} 0 0 0 {w} 0 v{-(h-2*ry)}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        s.append(f'<ellipse cx="{x+w/2}" cy="{y+ry}" rx="{w/2}" ry="{ry}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        top = y + 2 * ry + 2
    elif shape == "pipe":
        rx = 10
        s.append(f'<path d="M{x+rx} {y} h{w-2*rx} a{rx} {h/2} 0 0 1 0 {h} h{-(w-2*rx)} a{rx} {h/2} 0 0 1 0 {-h} z" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        s.append(f'<ellipse cx="{x+w-rx}" cy="{y+h/2}" rx="{rx}" ry="{h/2}" fill="none" stroke="{stroke}" stroke-width="{sw}"/>')
        top = y + 4
    elif shape == "pill":
        s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{min(h/2, 18)}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
                 f'<text x="{x+w/2}" y="{y+h/2+5}" font-size="{T["font"]["sizes"]["name"]}" font-weight="650" text-anchor="middle">{esc(e["name"])}</text>')
        return "".join(s)
    else:
        s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        if shape == "library":
            s.append(f'<line x1="{x+6}" y1="{y}" x2="{x+6}" y2="{y+h}" stroke="{stroke}" stroke-width="{sw}"/>')
        if shape == "component":
            s.append(f'<rect x="{x+w-22}" y="{y+8}" width="12" height="9" rx="1" fill="none" stroke="{stroke}"/><rect x="{x+w-25}" y="{y+10}" width="6" height="2" fill="{stroke}"/><rect x="{x+w-25}" y="{y+13.5}" width="6" height="2" fill="{stroke}"/>')
        if shape == "person":
            s.append(f'<circle cx="{x+w-18}" cy="{y+13}" r="4" fill="none" stroke="{stroke}" stroke-width="1.2"/><path d="M{x+w-25} {y+25} q7 -9 14 0" fill="none" stroke="{stroke}" stroke-width="1.2"/>')
        top = y
    F = T["font"]["sizes"]
    pad = 12 if shape != "library" else 16
    cx = x + pad
    kindtxt = (e.get("kind") or t).replace("-", " ").upper()
    mark = T["provenance"].get(prov, {}).get("mark", "")
    s.append(f'<text x="{cx}" y="{top+16}" class="mono" font-size="{F["type_tag"]}" font-weight="700" letter-spacing=".8" fill="var(--muted)">{esc(kindtxt)}{(" " + esc(mark)) if mark else ""}</text>')
    if shape == "compartment":
        s.append(f'<text x="{cx}" y="{top+33}" font-size="{F["name"]}" font-weight="650">{esc(e["name"])}</text>')
        s.append(f'<line x1="{x}" y1="{top+42}" x2="{x+w}" y2="{top+42}" stroke="{stroke}" stroke-width="1"/>')
        for i, fl in enumerate(e.get("fields", [])[:6]):
            s.append(f'<text x="{cx}" y="{top+58+i*15}" class="mono" font-size="{F["tech"]}" fill="var(--ink2)">{esc(fl)}</text>')
        return "".join(s)
    name_lines = wrap(e["name"], int((w - 2 * pad) / (F["name"] * 0.58)), 1)
    s.append(f'<text x="{cx}" y="{top+33}" font-size="{F["name"]}" font-weight="650">{esc(name_lines[0])}</text>')
    yy = top + 33
    if e.get("tech"):
        yy += 15
        s.append(f'<text x="{cx}" y="{yy}" class="mono" font-size="{F["tech"]}" fill="var(--ink2)">[{esc(wrap(e["tech"], int((w-2*pad)/(F["tech"]*0.6))-2, 1)[0])}]</text>')
    if e.get("desc") and shape != "pill":
        for ln in wrap(e["desc"], int((w - 2 * pad) / (F["desc"] * 0.55)), 2):
            yy += 14
            s.append(f'<text x="{cx}" y="{yy}" font-size="{F["desc"]}" fill="var(--muted)">{esc(ln)}</text>')
    return "".join(s)


def evidence_title(x):
    ev = x.get("evidence") or []
    prov = x.get("provenance", "extracted")
    return f"<title>{esc(prov)}: " + esc("; ".join(map(str, ev[:6])) + (f" (+{len(ev)-6} more)" if len(ev) > 6 else "")) + "</title>"


def defs(T):
    return ('<defs>'
            '<marker id="hf" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 1 L9 5 L0 9 z" fill="var(--line)"/></marker>'
            '<marker id="ho" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 1 L9 5 L0 9" fill="none" stroke="var(--line)" stroke-width="1.3"/></marker>'
            '<marker id="hs" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 1 L9 5 L0 9 z" fill="var(--stale)"/></marker>'
            '</defs>')


def rounded(pts, r=8):
    d = f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        l1 = math.hypot(x1 - x0, y1 - y0); l2 = math.hypot(x2 - x1, y2 - y1)
        rr = min(r, l1 / 2, l2 / 2)
        if rr < 0.5:
            d += f" L{x1:.1f} {y1:.1f}"; continue
        ax, ay = x1 - (x1 - x0) / l1 * rr, y1 - (y1 - y0) / l1 * rr
        bx, by = x1 + (x2 - x1) / l2 * rr, y1 + (y2 - y1) / l2 * rr
        d += f" L{ax:.1f} {ay:.1f} Q{x1:.1f} {y1:.1f} {bx:.1f} {by:.1f}"
    d += f" L{pts[-1][0]:.1f} {pts[-1][1]:.1f}"
    return d


def label_svg(cx, cy, what, how, prov, data_class, T, anchor="middle"):
    F = T["font"]["sizes"]
    mark = T["provenance"].get(prov, {}).get("mark", "")
    l1 = (mark + " " if mark else "") + what
    lines = [(l1, F["label"], False, "var(--ink)")]
    if how:
        lines.append(("[" + how + "]", F["label_tech"], True, "var(--ink2)"))
    if data_class:
        lines.append(("data: " + data_class, F["label_tech"], True, "var(--sensitive)"))
    wmax = max(tw(t, s, m) for t, s, m, _ in lines) + 10
    hh = len(lines) * 13 + 6
    x0 = cx - wmax / 2 if anchor == "middle" else cx
    out = [f'<rect x="{x0:.1f}" y="{cy-hh/2:.1f}" width="{wmax:.1f}" height="{hh}" rx="3" fill="var(--label-mask)" opacity=".96"/>']
    for i, (t, s, m, c) in enumerate(lines):
        tx = cx if anchor == "middle" else cx + 5
        out.append(f'<text x="{tx:.1f}" y="{cy-hh/2+15+i*13:.1f}" font-size="{s}" text-anchor="{anchor}" {"class=\"mono\" " if m else ""}fill="{c}" font-weight="{500 if not m else 400}">{esc(t)}</text>')
    return "".join(out)


# ------------------------------------------------------------------ box diagrams
def layout(d, T):
    """Layered layout: rank by data-flow direction (rank_reverse flips consumer edges), order rows by the
    mean row of already-placed neighbours, put libraries on a bottom row under their first user."""
    els = d["elements"]
    ids = [e["id"] for e in els]
    if all("at" in e for e in els):
        return {e["id"]: tuple(e["at"]) for e in els}
    typ = {e["id"]: e["type"] for e in els}
    rels = [r for r in d.get("relationships", []) if r["from"] in ids and r["to"] in ids]
    flow = [((r["to"], r["from"]) if r.get("rank_reverse") else (r["from"], r["to"])) for r in rels if r.get("style") != "build"]
    rank = {i: 0 for i in ids if typ[i] != "library"}
    for _ in range(len(ids) + 1):
        changed = False
        for a, b in flow:
            if a in rank and b in rank and rank[b] < rank[a] + 1 <= len(ids):
                rank[b] = rank[a] + 1; changed = True
        if not changed:
            break
    cols = {}
    for i in sorted(rank, key=lambda i: (rank[i], ids.index(i))):
        cols.setdefault(rank[i], []).append(i)
    pos = {}
    for c in sorted(cols):
        def bary(i):
            nb = [pos[o][1] for a, b in flow for (x, o) in ((a, b), (b, a)) if x == i and o in pos]
            return (sum(nb) / len(nb)) if nb else 99
        for row, i in enumerate(sorted(cols[c], key=lambda i: (bary(i), ids.index(i)))):
            pos[i] = (c, row)
    bottom = max((r for _, r in pos.values()), default=-1) + 1
    for i in ids:
        if typ[i] == "library":
            users = [pos[r["from"]][0] for r in rels if r["to"] == i and r["from"] in pos]
            col = min(users) if users else 0
            while (col, bottom) in pos.values():
                col += 1
            pos[i] = (col, bottom)
    return pos


def render_boxes(d, T):
    G = T["grid"]
    W, H = G["node_width"] + 12, 104
    CG, RG = 210, 64
    LEFT, TOP = 40, (122 if d.get("boundaries") else 92)
    if d["diagram"] == "state":
        H = 60
    pos = layout(d, T)
    els = {e["id"]: e for e in d["elements"]}
    xy = {i: (LEFT + c * (W + CG), TOP + r * (H + RG)) for i, (c, r) in pos.items()}
    if d["diagram"] in ("data-model",):
        for e in els.values():
            pass
    # boundaries
    parts = []
    bsvg = []
    for b in d.get("boundaries", []):
        mem = [xy[c] for c in b.get("contains", []) if c in xy]
        if not mem:
            continue
        inset = G["boundary_inset"]
        x0 = min(p[0] for p in mem) - inset; y0 = min(p[1] for p in mem) - inset - 18
        x1 = max(p[0] for p in mem) + W + inset; y1 = max(p[1] for p in mem) + H + inset
        col = "var(--trust)" if b.get("trust") else "var(--boundary-stroke)"
        bsvg.append(f'<g class="boundary"><rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" rx="{T["radius"]["boundary"]}" fill="var(--boundary-fill)" stroke="{col}" stroke-dasharray="{T["dash"]["boundary"]}" stroke-width="{T["stroke"]["boundary"]}"/>'
                    f'<text x="{x0+10}" y="{y0+15}" font-size="11" font-weight="700" fill="{col}">{esc(b["name"])}</text>'
                    f'<text x="{x0+10+tw(b["name"],11)+8}" y="{y0+15}" class="mono" font-size="{T["font"]["sizes"]["type_tag"]}" fill="var(--muted)" letter-spacing=".6">{esc(b["type"].upper())}</text></g>')
    # edges: attach sides + fanning
    rels = d.get("relationships", [])
    sides = {}
    plan = []
    for k, r in enumerate(rels):
        (cs, rs), (ct, rt) = pos[r["from"]], pos[r["to"]]
        if cs == ct and abs(rt - rs) > 1:
            a, b = "r", "r"          # skip over the box in between through the right-hand gap
        elif cs == ct:
            a, b = ("b", "t") if rt > rs else ("t", "b")
        elif ct > cs:
            a, b = "r", "l"
        else:
            a, b = "l", "r"
        plan.append((k, a, b))
        sides.setdefault((r["from"], a), []).append(k)
        sides.setdefault((r["to"], b), []).append(k)

    def attach(nid, side, k):
        lst = sides[(nid, side)]
        # order by the other end's position so lines don't cross at the node
        def other(kk):
            rr = rels[kk]; o = rr["to"] if rr["from"] == nid else rr["from"]
            return xy[o][1] if side in "lr" else xy[o][0]
        lst = sorted(lst, key=other)
        i = lst.index(k); n = len(lst)
        x, y = xy[nid]
        if side in "lr":
            yy = y + H * (i + 1) / (n + 1)
            return (x + (W if side == "r" else 0), yy)
        xx = x + W * (i + 1) / (n + 1)
        return (xx, y + (H if side == "b" else 0))

    lanes = {}
    esvg, lsvg = [], []
    for k, a, b in plan:
        r = rels[k]
        p1, p2 = attach(r["from"], a, k), attach(r["to"], b, k)
        (cs, rs), (ct, rt) = pos[r["from"]], pos[r["to"]]
        if a == "r" and b == "r":
            gx = LEFT + cs * (W + CG) + W + 36 + (lanes.setdefault(("s", cs), 0) % 3) * 12
            lanes[("s", cs)] += 1
            pts = [p1, (gx, p1[1]), (gx, p2[1]), p2]
            lab = (gx + 6, p2[1] - 34, "start")
        elif a in "lr":
            if abs(ct - cs) <= 1:
                gapx = (LEFT + max(cs, ct) * (W + CG)) - CG / 2
                ln = lanes.setdefault(("v", min(cs, ct)), 0); lanes[("v", min(cs, ct))] += 1
                mx = gapx + (ln - 0) * 0 if p1[1] == p2[1] else gapx + (ln % 3 - 1) * 14
                pts = [p1, (mx, p1[1]), (mx, p2[1]), p2] if abs(p1[1] - p2[1]) > 0.5 else [p1, p2]
                lab = (mx, (p1[1] + p2[1]) / 2) if abs(p1[1] - p2[1]) > 40 else ((p1[0] + p2[0]) / 2, min(p1[1], p2[1]) - 2)
            else:
                g1 = p1[0] + (CG / 2 if a == "r" else -CG / 2)
                g2 = p2[0] - (CG / 2 if a == "r" else -CG / 2)
                rowy = TOP + max(rs, rt) * (H + RG) + H + RG / 2
                ln = lanes.setdefault(("h", max(rs, rt)), 0); lanes[("h", max(rs, rt))] += 1
                rowy += (ln % 3 - 1) * 12
                pts = [p1, (g1, p1[1]), (g1, rowy), (g2, rowy), (g2, p2[1]), p2]
                lab = ((g1 + g2) / 2, rowy)
        else:
            my = (p1[1] + p2[1]) / 2
            pts = [p1, (p1[0], my), (p2[0], my), p2] if abs(p1[0] - p2[0]) > 0.5 else [p1, p2]
            lab = ((p1[0] + p2[0]) / 2, my)
        st = T["relationship_styles"][r.get("style", "sync")]
        dash = f' stroke-dasharray="{T["dash"][st["dash"]]}"' if st["dash"] else ""
        prov = r.get("provenance", "extracted")
        col = "var(--stale)" if prov == "stale" else "var(--line)"
        mk = "hs" if prov == "stale" else ("hf" if st["head"] == "filled" else "ho")
        esvg.append(f'<path d="{rounded(pts)}" fill="none" stroke="{col}" stroke-width="{T["stroke"]["line"]}"{dash} marker-end="url(#{mk})">{evidence_title(r)}</path>')
        lsvg.append(f'<g>{evidence_title(r)}{label_svg(lab[0], lab[1], r.get("what") or "", r.get("how"), prov, r.get("data_class"), T, anchor=(lab[2] if len(lab) > 2 else "middle"))}</g>')
    nsvg = [f'<g class="node" data-id="{esc(i)}">{evidence_title(e)}{node_svg(e, *xy[i], W, H, T)}</g>' for i, e in els.items()]
    maxc = max(c for c, _ in pos.values()); maxr = max(r for _, r in pos.values())
    width = LEFT * 2 + (maxc + 1) * W + maxc * CG
    body_h = TOP + (maxr + 1) * H + maxr * RG + 60
    return width, body_h, "".join(bsvg) + "".join(esvg) + "".join(lsvg) + "".join(nsvg)


# ------------------------------------------------------------------ sequence
def render_flow(d, T):
    f = d["flow"]
    els = {e["id"]: e for e in d["elements"]}
    parts = f["participants"]
    SP, LEFT, TOP, HW, HH, ROW = 210, 40, 92, 170, 70, 50
    xs = {p: LEFT + HW / 2 + i * SP for i, p in enumerate(parts)}
    msgs = f["messages"]
    y0 = TOP + HH + 34
    out = []
    end_y = y0 + len(msgs) * ROW + 10
    for p in parts:
        e = els[p]
        out.append(f'<line x1="{xs[p]}" y1="{TOP+HH}" x2="{xs[p]}" y2="{end_y}" stroke="var(--rule)" stroke-dasharray="4 4"/>')
    for fr in f.get("fragments", []):
        a, b = fr["start"] - 1, fr["end"] - 1
        fx0 = min(xs[p] for p in parts) - HW / 2 + 6; fx1 = max(xs[p] for p in parts) + HW / 2 - 6
        fy0 = y0 + a * ROW - 30; fy1 = y0 + b * ROW + 18
        out.append(f'<rect x="{fx0}" y="{fy0}" width="{fx1-fx0}" height="{fy1-fy0}" fill="none" stroke="var(--muted)" rx="2"/>'
                   f'<path d="M{fx0} {fy0+16} h{40} l8 -8 v-8" fill="var(--paper)" stroke="var(--muted)"/>'
                   f'<text x="{fx0+6}" y="{fy0+12}" class="mono" font-size="9" font-weight="700" fill="var(--muted)">{esc(fr["type"])}</text>'
                   f'<text x="{fx0+56}" y="{fy0+12}" class="mono" font-size="9" fill="var(--muted)">[{esc(fr.get("label",""))}]</text>')
        if fr.get("else_at"):
            ey = y0 + (fr["else_at"] - 1) * ROW - 30
            out.append(f'<line x1="{fx0}" y1="{ey}" x2="{fx1}" y2="{ey}" stroke="var(--muted)" stroke-dasharray="4 3"/>'
                       f'<text x="{fx0+6}" y="{ey+12}" class="mono" font-size="9" fill="var(--muted)">[{esc(fr.get("else_label","else"))}]</text>')
    for i, m in enumerate(msgs):
        y = y0 + i * ROW
        st = T["relationship_styles"][m.get("style", "sync")]
        dash = f' stroke-dasharray="{T["dash"][st["dash"]]}"' if st["dash"] else ""
        mk = "hf" if st["head"] == "filled" else "ho"
        x1, x2 = xs[m["from"]], xs[m["to"]]
        if x1 == x2:
            path = f"M{x1} {y} h36 v18 h-36"
            lx, anchor = x1 + 44, "start"
        else:
            sgn = 1 if x2 > x1 else -1
            path = f"M{x1+sgn*4} {y} L{x2-sgn*4} {y}"
            lx, anchor = (x1 + x2) / 2, "middle"
        out.append(f'<path d="{path}" fill="none" stroke="var(--line)" stroke-width="1.2"{dash} marker-end="url(#{mk})">{evidence_title(m)}</path>')
        ix = min(x1, x2) + 14 if x1 != x2 else x1 + 8
        out.append(f'<circle cx="{(min(x1,x2) if x1!=x2 else x1)+12}" cy="{y-11}" r="7" fill="var(--ink)"/><text x="{(min(x1,x2) if x1!=x2 else x1)+12}" y="{y-8}" font-size="{T["font"]["sizes"]["index"]}" font-weight="700" text-anchor="middle" fill="var(--paper)">{i+1}</text>')
        F = T["font"]["sizes"]
        t1 = m["what"]; t2 = f'[{m["how"]}]' if m.get("how") else ""
        if anchor == "middle":
            out.append(f'<text x="{lx}" y="{y-8}" font-size="{F["label"]}" text-anchor="middle" font-weight="500">{esc(t1)}</text>')
            if t2:
                out.append(f'<text x="{lx}" y="{y+14}" class="mono" font-size="{F["label_tech"]}" text-anchor="middle" fill="var(--ink2)">{esc(t2)}</text>')
        else:
            out.append(f'<text x="{lx}" y="{y+4}" font-size="{F["label"]}" font-weight="500">{esc(t1)}</text>')
            if t2:
                out.append(f'<text x="{lx}" y="{y+17}" class="mono" font-size="{F["label_tech"]}" fill="var(--ink2)">{esc(t2)}</text>')
    for p in parts:
        e = els[p]
        out.append(f'<g>{evidence_title(e)}{node_svg(dict(e, desc=None), xs[p]-HW/2, TOP, HW, HH, T)}</g>')
    width = LEFT * 2 + (len(parts) - 1) * SP + HW
    return width, end_y + 30, "".join(out)


# ------------------------------------------------------------------ legend & page
def mini_shape(t, x, y, T):
    ET = T["element_types"][t]
    fill, stroke = f"var(--{ET['fill'].replace('_', '-')})", f"var(--{ET['stroke'].replace('_', '-')})"
    dash = f' stroke-dasharray="{T["dash"]["external"]}"' if ET["border"] == "external" else ""
    sh = ET["shape"]; w, h = 26, 15
    if sh == "cylinder":
        return f'<path d="M{x} {y+3} v{h-6} a13 3 0 0 0 26 0 v{-(h-6)}" fill="{fill}" stroke="{stroke}"/><ellipse cx="{x+13}" cy="{y+3}" rx="13" ry="3" fill="{fill}" stroke="{stroke}"/>'
    if sh == "pipe":
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7.5" fill="{fill}" stroke="{stroke}"/><ellipse cx="{x+w-5}" cy="{y+h/2}" rx="4" ry="{h/2}" fill="none" stroke="{stroke}"/>'
    if sh == "pill":
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7.5" fill="{fill}" stroke="{stroke}"/>'
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{fill}" stroke="{stroke}"{dash}/>'
    if sh == "person":
        s += f'<circle cx="{x+13}" cy="{y+6}" r="2.6" fill="none" stroke="{stroke}"/><path d="M{x+8} {y+13} q5 -5 10 0" fill="none" stroke="{stroke}"/>'
    if sh == "library":
        s += f'<line x1="{x+4}" y1="{y}" x2="{x+4}" y2="{y+h}" stroke="{stroke}"/>'
    if sh == "component":
        s += f'<rect x="{x+16}" y="{y+4}" width="7" height="6" fill="none" stroke="{stroke}"/>'
    if sh == "compartment":
        s += f'<line x1="{x}" y1="{y+6}" x2="{x+w}" y2="{y+6}" stroke="{stroke}"/>'
    return s


def legend(d, T, y, width):
    items = []
    types = []
    for e in d.get("elements", []):
        if e["type"] not in types:
            types.append(e["type"])
    x = 40
    out = [f'<line x1="40" y1="{y}" x2="{width-40}" y2="{y}" stroke="var(--rule)"/>',
           f'<text x="40" y="{y+20}" class="mono" font-size="9" font-weight="700" letter-spacing=".8" fill="var(--muted)">LEGEND</text>']
    x = 110
    yy = y + 16
    for t in types:
        ET = T["element_types"][t]
        e = {"type": t, "name": "", "provenance": "extracted"}
        mini = mini_shape(t, x, yy - 7, T)
        lbl = t.replace("-", " ")
        out.append(mini + f'<text x="{x+32}" y="{yy+6}" font-size="10" fill="var(--ink2)">{esc(lbl)}</text>')
        x += 40 + tw(lbl, 10) + 14
    styles = []
    rels = d.get("relationships", []) or (d.get("flow") or {}).get("messages", [])
    for r in rels:
        s = r.get("style", "sync")
        if s not in styles:
            styles.append(s)
    for s in styles:
        st = dict(T["relationship_styles"][s])
        if d["diagram"] == "state":
            st["legend"] = "Transition"
        elif d["diagram"] == "data-model":
            st["legend"] = "Association"
        dash = f' stroke-dasharray="{T["dash"][st["dash"]]}"' if st["dash"] else ""
        mk = "hf" if st["head"] == "filled" else "ho"
        out.append(f'<path d="M{x} {yy} h28" stroke="var(--line)" stroke-width="1.2"{dash} marker-end="url(#{mk})"/><text x="{x+36}" y="{yy+4}" font-size="10" fill="var(--ink2)">{esc(st["legend"])}</text>')
        x += 50 + tw(st["legend"], 10) + 14
    provs = []
    for xx in list(d.get("elements", [])) + list(rels):
        p = xx.get("provenance", "extracted")
        if p != "extracted" and p not in provs:
            provs.append(p)
    yy2 = yy + 26
    x2 = 110
    for p in provs:
        P = T["provenance"][p]
        out.append(f'<text x="{x2}" y="{yy2+4}" font-size="10" font-weight="700" fill="var(--{"stale" if p=="stale" else "ink2"})">{esc(P["mark"])}</text><text x="{x2+14}" y="{yy2+4}" font-size="10" fill="var(--ink2)">{esc(p)}: {esc(P["legend"])}</text>')
        x2 += 30 + tw(p + ": " + P["legend"], 10)
    if any(b.get("trust") for b in d.get("boundaries", [])):
        out.append(f'<rect x="{x2}" y="{yy2-5}" width="26" height="14" fill="none" stroke="var(--trust)" stroke-dasharray="6 4"/><text x="{x2+32}" y="{yy2+6}" font-size="10" fill="var(--ink2)">{esc(T["overlays"]["trust-boundary"]["legend"])}</text>')
        x2 += 40 + tw(T["overlays"]["trust-boundary"]["legend"], 10)
    if any(r.get("data_class") for r in rels):
        out.append(f'<text x="{x2}" y="{yy2+4}" class="mono" font-size="9.5" fill="var(--sensitive)">data:</text><text x="{x2+34}" y="{yy2+4}" font-size="10" fill="var(--ink2)">{esc(T["overlays"]["data-class"]["legend"])}</text>')
        x2 += 50 + tw(T["overlays"]["data-class"]["legend"], 10)
    if not provs and not any(b.get("trust") for b in d.get("boundaries", [])) and not any(r.get("data_class") for r in rels):
        out.append(f'<text x="110" y="{yy2+4}" font-size="10" fill="var(--muted)">Every box and arrow is backed by evidence; hover to see it.</text>')
    return y + 54, "".join(out), max(x, x2) + 40


def render(d, T):
    if d["diagram"] == "flow":
        w, h, body = render_flow(d, T)
    else:
        w, h, body = render_boxes(d, T)
    w = max(w, 760)
    ly, leg, lw = legend(d, T, h, w)
    w = max(w, lw)
    ly, leg, _ = legend(d, T, h, w)
    slug = "".join(ch if ch.isalnum() else "-" for ch in d["title"].lower())[:48]
    hdr = (f'<text x="40" y="38" font-size="{T["font"]["sizes"]["title"]}" font-weight="700">{esc(d["title"])}</text>'
           f'<text x="40" y="58" font-size="11" fill="var(--muted)">Scope: {esc(d["scope"])}{(" · " + esc(d["source_note"])) if d.get("source_note") else ""}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {ly:.0f}" width="{w:.0f}" height="{ly:.0f}" role="img" aria-labelledby="{slug}-t {slug}-d">'
            f'<title id="{slug}-t">{esc(d["title"])}</title><desc id="{slug}-d">{esc(d["description"])}</desc>'
            f'<style>{Theme(T).css()}</style>{defs(T)}<rect width="100%" height="100%" fill="var(--paper)"/>{hdr}{body}{leg}</svg>')


def alt_markdown(d):
    """Long text alternative: every element and relationship with evidence."""
    out = [f"**{d['title']}**: {d['description']}", ""]
    els = {e["id"]: e for e in d.get("elements", [])}
    out += ["| Element | Type | Technology | Responsibility | Evidence |", "|---|---|---|---|---|"]
    for e in els.values():
        out.append(f"| {e['name']} | {e.get('kind') or e['type']} | {e.get('tech','')} | {e.get('desc','')} | {'; '.join(map(str, e.get('evidence', [])))} ({e.get('provenance','extracted')}) |")
    rels = d.get("relationships") or [dict(m, n=i + 1) for i, m in enumerate((d.get("flow") or {}).get("messages", []))]
    out += ["", "| # | From | To | What | How | Evidence |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(rels, 1):
        out.append(f"| {i} | {els[r['from']]['name']} | {els[r['to']]['name']} | {r['what']} | {r.get('how','')} | {'; '.join(map(str, r.get('evidence', [])))} ({r.get('provenance','extracted')}) |")
    return "\n".join(out) + "\n"


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    cmd, files = argv[0], [a for a in argv[1:] if not a.startswith("--")]
    tok = DEFAULT_TOKENS
    out = None
    if "--tokens" in argv:
        tok = argv[argv.index("--tokens") + 1]; files.remove(tok)
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]; files.remove(out)
    T = json.load(open(tok))
    bad = 0
    for f in files:
        d = yaml.safe_load(open(f))
        v = check(d, T, hand_made="--hand-made" in argv)
        print(f"{os.path.basename(f)}: {'OK' if not v else str(len(v)) + ' violation(s)'}")
        for x in v:
            print("   ", x)
        bad += bool(v)
        if cmd == "render":
            o = out or os.path.dirname(f)
            base = os.path.splitext(os.path.basename(f))[0]
            open(os.path.join(o, base + ".svg"), "w").write(render(d, T))
            open(os.path.join(o, base + ".md"), "w").write(alt_markdown(d))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
