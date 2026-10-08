"""SVG from layout geometry plus the tokens: light and dark from the same colour roles.

Colours are CSS custom properties defined once from ``tokens.json`` (light by default, dark under
``prefers-color-scheme``), so one file serves both themes. Every string that came from a description is
escaped; nothing from a description is ever placed in markup, a style or an identifier unescaped.
"""
from __future__ import annotations

import html
import math
import re
import zlib

from . import load_tokens
from .layout import layout as compute_layout
from .layout import text_width

_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
MIN_WIDTH = 760.0
LEFT = 40.0


def esc(value: object) -> str:
    """Text safe for element content and double-quoted attribute values."""
    return html.escape(_XML_BAD.sub("", str(value)), quote=True)


def _var(role: str) -> str:
    return "var(--" + role.replace("_", "-") + ")"


def _num(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _slug(title: str) -> str:
    base = "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in title.lower()).strip("-")[:40] or "diagram"
    return f"{base}-{zlib.crc32(title.encode('utf-8', 'replace')) & 0xFFFF:04x}"


def css(tokens: dict, theme: str = "auto") -> str:
    themes = tokens["themes"]

    def block(name: str) -> str:
        return ";".join("--" + k.replace("_", "-") + ":" + v for k, v in themes[name].items())

    F = tokens["font"]
    if theme == "dark":
        head = f"svg{{{block('dark')}}}"
    elif theme == "light":
        head = f"svg{{{block('light')}}}"
    else:
        head = f"svg{{{block('light')}}}@media (prefers-color-scheme:dark){{svg{{{block('dark')}}}}}"
    return (head + f"text{{font-family:{F['name']}}}.mono{{font-family:{F['tech']}}}")


def _rounded(pts: list[list[float]], radius: float) -> str:
    d = f"M{_num(pts[0][0])} {_num(pts[0][1])}"
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        l1, l2 = math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x1, y2 - y1)
        rr = min(radius, l1 / 2, l2 / 2)
        if rr < 0.5:
            d += f" L{_num(x1)} {_num(y1)}"
            continue
        ax, ay = x1 - (x1 - x0) / l1 * rr, y1 - (y1 - y0) / l1 * rr
        bx, by = x1 + (x2 - x1) / l2 * rr, y1 + (y2 - y1) / l2 * rr
        d += f" L{_num(ax)} {_num(ay)} Q{_num(x1)} {_num(y1)} {_num(bx)} {_num(by)}"
    return d + f" L{_num(pts[-1][0])} {_num(pts[-1][1])}"


def _mark_role(prov: str) -> str:
    return prov if prov in ("stale", "inferred") else "ink2"


def _text(t: dict) -> str:
    attrs = [f'x="{_num(t["x"])}"', f'y="{_num(t["y"])}"', f'font-size="{_num(t["size"])}"']
    if t.get("weight"):
        attrs.append(f'font-weight="{t["weight"]}"')
    if t.get("anchor"):
        attrs.append(f'text-anchor="{esc(t["anchor"])}"')
    if t.get("ls"):
        attrs.append(f'letter-spacing="{t["ls"]}"')
    if t.get("mono"):
        attrs.append('class="mono"')
    attrs.append(f'fill="{_var(t.get("role", "ink"))}"')
    body = esc(t["t"])
    if t.get("mark"):
        mark = (f'<tspan role="img" aria-label="{esc(t.get("aria", "provenance"))}" '
                f'fill="{_var(_mark_role(t.get("aria", "")))}" font-weight="700">{esc(t["mark"])}</tspan>')
        body = mark + " " + body if t.get("mark_first") else body + " " + mark
    return f"<text {' '.join(attrs)}>{body}</text>"


def _evidence_title(prov: str, refs: list[str]) -> str:
    shown = "; ".join(refs[:6]) + (f" (+{len(refs) - 6} more)" if len(refs) > 6 else "")
    return f"<title>{esc(prov)}: {esc(shown)}</title>" if shown else f"<title>{esc(prov)}</title>"


def _defs(slug: str) -> str:
    def marker(name: str, body: str) -> str:
        return (f'<marker id="{slug}-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
                f'orient="auto-start-reverse">{body}</marker>')
    return ("<defs>"
            + marker("hf", '<path d="M0 1 L9 5 L0 9 z" fill="var(--line)"/>')
            + marker("ho", '<path d="M0 1 L9 5 L0 9" fill="none" stroke="var(--line)" stroke-width="1.3"/>')
            + marker("hs", '<path d="M0 1 L9 5 L0 9 z" fill="var(--stale)"/>')
            + "</defs>")


# ------------------------------------------------------------------ pieces
def _shape(n: dict, T: dict) -> str:
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    fill, stroke = _var(n["fill"]), _var(n["stroke"])
    sw = n["stroke_w"]
    dash = f' stroke-dasharray="{n["dash"]}"' if n.get("dash") else ""
    base = f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"'
    shape = n["shape"]
    r = T["radius"]["node"]
    if shape == "cylinder":
        ry = 8
        return (f'<path d="M{_num(x)} {_num(y + ry)} v{_num(h - 2 * ry)} a{_num(w / 2)} {ry} 0 0 0 {_num(w)} 0 v{_num(-(h - 2 * ry))}" {base}{dash}/>'
                f'<ellipse cx="{_num(x + w / 2)}" cy="{_num(y + ry)}" rx="{_num(w / 2)}" ry="{ry}" {base}/>')
    if shape == "pipe":
        rx = 10
        return (f'<path d="M{_num(x + rx)} {_num(y)} h{_num(w - 2 * rx)} a{rx} {_num(h / 2)} 0 0 1 0 {_num(h)} h{_num(-(w - 2 * rx))} '
                f'a{rx} {_num(h / 2)} 0 0 1 0 {_num(-h)} z" {base}{dash}/>'
                f'<ellipse cx="{_num(x + w - rx)}" cy="{_num(y + h / 2)}" rx="{rx}" ry="{_num(h / 2)}" fill="none" stroke="{stroke}" stroke-width="{sw}"/>')
    if shape == "pill":
        return f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" rx="{_num(min(h / 2, 18))}" {base}/>'
    out = f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" rx="{r}" {base}{dash}/>'
    if shape == "library":
        out += f'<line x1="{_num(x + 6)}" y1="{_num(y)}" x2="{_num(x + 6)}" y2="{_num(y + h)}" stroke="{stroke}" stroke-width="{sw}"/>'
    elif shape == "component":
        out += (f'<rect x="{_num(x + w - 22)}" y="{_num(y + 8)}" width="12" height="9" rx="1" fill="none" stroke="{stroke}"/>'
                f'<rect x="{_num(x + w - 25)}" y="{_num(y + 10)}" width="6" height="2" fill="{stroke}"/>'
                f'<rect x="{_num(x + w - 25)}" y="{_num(y + 13.5)}" width="6" height="2" fill="{stroke}"/>')
    elif shape == "person":
        out += (f'<circle cx="{_num(x + w - 18)}" cy="{_num(y + 13)}" r="4" fill="none" stroke="{stroke}" stroke-width="1.2"/>'
                f'<path d="M{_num(x + w - 25)} {_num(y + 25)} q7 -9 14 0" fill="none" stroke="{stroke}" stroke-width="1.2"/>')
    elif shape == "compartment" and n.get("sep") is not None:
        out += f'<line x1="{_num(x)}" y1="{_num(n["sep"])}" x2="{_num(x + w)}" y2="{_num(n["sep"])}" stroke="{stroke}" stroke-width="1"/>'
    return out


def _node(n: dict, T: dict) -> str:
    return (f'<g class="node" data-id="{esc(n["id"])}">{_evidence_title(n["provenance"], n["evidence"])}'
            + _shape(n, T) + "".join(_text(t) for t in n["texts"]) + "</g>")


def _label(lab: dict) -> str:
    out = ""
    if lab.get("mask"):
        out += (f'<rect x="{_num(lab["x"])}" y="{_num(lab["y"])}" width="{_num(lab["w"])}" height="{_num(lab["h"])}" rx="3" '
                f'fill="var(--label-mask)" opacity=".96"/>')
    return out + "".join(_text(t) for t in lab["lines"])


def _edge(e: dict, T: dict, slug: str) -> tuple[str, str]:
    st = T["relationship_styles"].get(e["style"], T["relationship_styles"]["sync"])
    stale = e["provenance"] == "stale"
    dash = f' stroke-dasharray="{T["dash"][st["dash"]]}"' if st["dash"] else ""
    marker = "hs" if stale else ("hf" if st["head"] == "filled" else "ho")
    colour = "var(--stale)" if stale else "var(--line)"
    title = _evidence_title(e["provenance"], e["evidence"])
    path = (f'<path d="{_rounded(e["points"], T["radius"]["boundary"])}" fill="none" stroke="{colour}" '
            f'stroke-width="{T["stroke"]["line"]}"{dash} marker-end="url(#{slug}-{marker})">{title}</path>')
    labels = "".join(f"<g>{title}{_label(lab)}</g>" for lab in e.get("labels", []))
    if e.get("badge"):
        b = e["badge"]
        labels += (f'<circle cx="{_num(b["x"])}" cy="{_num(b["y"])}" r="7" fill="var(--ink)"/>'
                   f'<text x="{_num(b["x"])}" y="{_num(b["y"] + 3)}" font-size="{T["font"]["sizes"]["index"]}" font-weight="700" '
                   f'text-anchor="middle" fill="var(--paper)">{b["n"]}</text>')
    return path, labels


def _boundary(b: dict, T: dict) -> str:
    return (f'<g class="boundary" data-id="{esc(b["id"])}"><rect x="{_num(b["x"])}" y="{_num(b["y"])}" width="{_num(b["w"])}" '
            f'height="{_num(b["h"])}" rx="{T["radius"]["boundary"]}" fill="var(--boundary-fill)" stroke="{_var(b["stroke"])}" '
            f'stroke-dasharray="{T["dash"]["boundary"]}" stroke-width="{T["stroke"]["boundary"]}"/>'
            + "".join(_text(t) for t in b["texts"]) + "</g>")


def _flow_extras(g: dict) -> tuple[str, str]:
    lines = "".join(
        f'<line x1="{_num(ll["x"])}" y1="{_num(ll["y1"])}" x2="{_num(ll["x"])}" y2="{_num(ll["y2"])}" stroke="var(--rule)" stroke-dasharray="4 4"/>'
        for ll in g.get("lifelines", []))
    frags = ""
    for f in g.get("fragments", []):
        tab = f["tab"]
        frags += (f'<rect x="{_num(f["x"])}" y="{_num(f["y"])}" width="{_num(f["w"])}" height="{_num(f["h"])}" fill="none" '
                  f'stroke="var(--muted)" rx="2"/>'
                  f'<path d="M{_num(tab["x"])} {_num(tab["y"] + tab["h"])} h{_num(tab["w"] - 8)} l8 -8 v{_num(-(tab["h"] - 8))}" '
                  f'fill="var(--paper)" stroke="var(--muted)"/>')
        if f.get("else"):
            e = f["else"]
            frags += (f'<line x1="{_num(e["x1"])}" y1="{_num(e["y"])}" x2="{_num(e["x2"])}" y2="{_num(e["y"])}" '
                      f'stroke="var(--muted)" stroke-dasharray="4 3"/>')
        frags += "".join(_text(t) for t in f["texts"])
    return lines, frags


# ------------------------------------------------------------------ legend
def _legend(items: list[dict], T: dict, slug: str, y: float, width: float) -> tuple[str, float]:
    size = T["font"]["sizes"]["legend"]
    out = [f'<line x1="{_num(LEFT)}" y1="{_num(y)}" x2="{_num(width - LEFT)}" y2="{_num(y)}" stroke="var(--rule)"/>',
           f'<text x="{_num(LEFT)}" y="{_num(y + 20)}" class="mono" font-size="9" font-weight="700" letter-spacing=".8" '
           f'fill="var(--muted)">LEGEND</text>']
    x0, x, row_y, row_h = 110.0, 110.0, y + 16, 26.0
    limit = width - LEFT
    for it in items:
        label = it["label"]
        kind = it["kind"]
        tw = text_width(label, size)
        need = {"shape": 40 + tw, "line": 36 + tw, "mark": 18 + tw, "overlay": 40 + tw, "badge": 40 + tw, "note": tw}[kind]
        if x > x0 and x + need > limit:
            x, row_y = x0, row_y + row_h
        if kind == "shape":
            ET_fill, ET_stroke = _var(it["fill"]), _var(it["stroke"])
            dash = f' stroke-dasharray="{it["dash"]}"' if it.get("dash") else ""
            out.append(_mini_shape(it["shape"], x, row_y - 7, ET_fill, ET_stroke, dash))
            out.append(f'<text x="{_num(x + 32)}" y="{_num(row_y + 6)}" font-size="{size}" fill="var(--ink2)">{esc(label)}</text>')
        elif kind == "line":
            dash = f' stroke-dasharray="{it["dash"]}"' if it.get("dash") else ""
            marker = "hf" if it["head"] == "filled" else "ho"
            out.append(f'<path d="M{_num(x)} {_num(row_y)} h28" fill="none" stroke="var(--line)" stroke-width="1.2"{dash} '
                       f'marker-end="url(#{slug}-{marker})"/>')
            out.append(f'<text x="{_num(x + 36)}" y="{_num(row_y + 4)}" font-size="{size}" fill="var(--ink2)">{esc(label)}</text>')
        elif kind == "mark":
            out.append(f'<text x="{_num(x)}" y="{_num(row_y + 4)}" font-size="{size}" font-weight="700" '
                       f'fill="{_var(_mark_role(it["provenance"]))}"><tspan role="img" aria-label="{esc(it["provenance"])}">{esc(it["mark"])}</tspan></text>')
            out.append(f'<text x="{_num(x + 16)}" y="{_num(row_y + 4)}" font-size="{size}" fill="var(--ink2)">{esc(label)}</text>')
        elif kind == "overlay":
            role = _var(it["role"])
            if it["name"] == "trust-boundary":
                out.append(f'<rect x="{_num(x)}" y="{_num(row_y - 6)}" width="26" height="14" fill="none" stroke="{role}" '
                           f'stroke-dasharray="{T["dash"]["boundary"]}"/>')
            else:
                out.append(f'<text x="{_num(x)}" y="{_num(row_y + 4)}" class="mono" font-size="{T["font"]["sizes"]["label_tech"]}" fill="{role}">data:</text>')
            out.append(f'<text x="{_num(x + 36)}" y="{_num(row_y + 4)}" font-size="{size}" fill="var(--ink2)">{esc(label)}</text>')
        elif kind == "badge":
            out.append(f'<text x="{_num(x)}" y="{_num(row_y + 4)}" class="mono" font-size="{T["font"]["sizes"]["label_tech"]}" fill="var(--ink2)">×3</text>')
            out.append(f'<text x="{_num(x + 36)}" y="{_num(row_y + 4)}" font-size="{size}" fill="var(--ink2)">{esc(label)}</text>')
        else:
            out.append(f'<text x="{_num(x)}" y="{_num(row_y + 4)}" font-size="{size}" fill="var(--muted)">{esc(label)}</text>')
        x += need + 22
    return "".join(out), row_y + 28


def _mini_shape(shape: str, x: float, y: float, fill: str, stroke: str, dash: str) -> str:
    w, h = 26, 15
    if shape == "cylinder":
        return (f'<path d="M{_num(x)} {_num(y + 3)} v{h - 6} a13 3 0 0 0 26 0 v{-(h - 6)}" fill="{fill}" stroke="{stroke}"/>'
                f'<ellipse cx="{_num(x + 13)}" cy="{_num(y + 3)}" rx="13" ry="3" fill="{fill}" stroke="{stroke}"/>')
    if shape == "pipe":
        return (f'<rect x="{_num(x)}" y="{_num(y)}" width="{w}" height="{h}" rx="7.5" fill="{fill}" stroke="{stroke}"/>'
                f'<ellipse cx="{_num(x + w - 5)}" cy="{_num(y + h / 2)}" rx="4" ry="{h / 2}" fill="none" stroke="{stroke}"/>')
    if shape == "pill":
        return f'<rect x="{_num(x)}" y="{_num(y)}" width="{w}" height="{h}" rx="7.5" fill="{fill}" stroke="{stroke}"/>'
    out = f'<rect x="{_num(x)}" y="{_num(y)}" width="{w}" height="{h}" rx="3" fill="{fill}" stroke="{stroke}"{dash}/>'
    if shape == "person":
        out += (f'<circle cx="{_num(x + 13)}" cy="{_num(y + 6)}" r="2.6" fill="none" stroke="{stroke}"/>'
                f'<path d="M{_num(x + 8)} {_num(y + 13)} q5 -5 10 0" fill="none" stroke="{stroke}"/>')
    elif shape == "library":
        out += f'<line x1="{_num(x + 4)}" y1="{_num(y)}" x2="{_num(x + 4)}" y2="{_num(y + h)}" stroke="{stroke}"/>'
    elif shape == "component":
        out += f'<rect x="{_num(x + 16)}" y="{_num(y + 4)}" width="7" height="6" fill="none" stroke="{stroke}"/>'
    elif shape == "compartment":
        out += f'<line x1="{_num(x)}" y1="{_num(y + 6)}" x2="{_num(x + w)}" y2="{_num(y + 6)}" stroke="{stroke}"/>'
    return out


# ------------------------------------------------------------------ page
def render(desc: dict, geometry: dict | None = None, tokens: dict | None = None, theme: str = "auto") -> str:
    """One self-contained SVG. ``theme`` is ``auto`` (follows the viewer), ``light`` or ``dark``."""
    if theme not in ("auto", "light", "dark"):
        raise ValueError("theme must be auto, light or dark")
    T = tokens or load_tokens()
    g = geometry if geometry is not None else compute_layout(desc, T)
    title = desc.get("title") or "Diagram"
    slug = _slug(title)
    width = max(float(g["width"]), MIN_WIDTH)
    edges = [_edge(e, T, slug) for e in g["edges"]]
    lifelines, frags = _flow_extras(g)
    body = (frags if g.get("kind") != "flow" else lifelines + frags)
    body += "".join(_boundary(b, T) for b in g["boundaries"])
    body += "".join(p for p, _ in edges) + "".join(lab for _, lab in edges)
    body += "".join(_node(n, T) for n in g["nodes"])
    legend, total = _legend(g["legend"], T, slug, float(g["height"]) + 6, width)
    scope = desc.get("scope") or ""
    note = desc.get("source_note")
    head = (f'<text x="{_num(LEFT)}" y="38" font-size="{T["font"]["sizes"]["title"]}" font-weight="700" fill="var(--ink)">{esc(title)}</text>'
            f'<text x="{_num(LEFT)}" y="58" font-size="11" fill="var(--muted)">Scope: {esc(scope)}{" · " + esc(note) if note else ""}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_num(width)} {_num(total)}" width="{_num(width)}" height="{_num(total)}" '
            f'role="img" aria-labelledby="{slug}-t {slug}-d">'
            f'<title id="{slug}-t">{esc(title)}</title><desc id="{slug}-d">{esc(desc.get("description") or title)}</desc>'
            f"<style>{css(T, theme)}</style>{_defs(slug)}"
            f'<rect width="100%" height="100%" fill="var(--paper)"/>{head}{body}{legend}</svg>')
