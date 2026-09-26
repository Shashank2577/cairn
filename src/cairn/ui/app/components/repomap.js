// The repository map: one circle per repository (sized by nodes, with its largest communities inside),
// and arrows from the repository that imports to the one it imports from. Dashed arrows are packages
// a manifest lists but no file imports.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html } from "./ui.js";
import { labelLines, uniqueNames, lastSeg } from "./communitymap.js";
import { fmt, plural } from "../lib/format.js";

export function RepoMap({ d3, repos, links, current, colors, height, active, onLink, onPin, pinned, hoverRepo, onHoverRepo, onOpen, onOpenCommunity }) {
  const host = useRef(null);
  const [w, setW] = useState(900);
  useEffect(() => {
    const el = host.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(300, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = height || Math.max(420, Math.min(720, Math.round(w * 0.7)));

  const L = useMemo(() => {
    // Sized by nodes, but none drawn smaller than a quarter of the largest, so every repository stays readable.
    const max = Math.max(1, ...repos.map(r => r.nodes || 1));
    const root = d3.hierarchy({ children: repos.map(r => ({ r, value: Math.max(r.nodes || 1, max * 0.25) })) }).sum(x => x.value || 0).sort((a, b) => b.value - a.value);
    const pad = Math.max(60, Math.min(170, w * 0.15));
    d3.pack().size([w, H]).padding(pad)(root);
    const pos = new Map();
    (root.children || []).forEach((n, i) => {
      const r = n.data.r;
      // Its largest communities fill the lower part of the circle; the name sits on the rim above.
      // Named by its folder when that says something the repository name doesn't, otherwise by its own label.
      const segs = (r.communities || []).map(c => lastSeg(c.area));
      const cs = (r.communities || []).map((c, j) => {
        const seg = segs[j], distinct = seg && seg !== r.name && segs.filter(x => x === seg).length === 1;
        return { ...c, cid: String(c.id), name: distinct ? seg : c.label || seg || `community ${c.id}`, quals: [seg, c.label].filter(Boolean), qual: "", sub: "" };
      });
      uniqueNames(cs);
      const sib = cs.map(c => ({ c, r: Math.sqrt(Math.max(1, c.size)) }));
      if (sib.length) {
        d3.packSiblings(sib);
        const enc = d3.packEnclose(sib), k = (n.r * 0.68) / enc.r;
        sib.forEach(s => { s.x = n.x + (s.x - enc.x) * k; s.y = n.y + n.r * 0.1 + (s.y - enc.y) * k; s.r *= k * 0.94; });
      }
      pos.set(r.id, { x: n.x, y: n.y, r: n.r, repo: r, color: colors[i % colors.length], comms: sib });
    });
    // Zoom to the circles (with room for rings, names and arrows), keeping the box's aspect.
    const ps = [...pos.values()];
    let x0 = Math.min(...ps.map(p => p.x - p.r)) - 40, x1 = Math.max(...ps.map(p => p.x + p.r)) + 40;
    let y0 = Math.min(...ps.map(p => p.y - p.r)) - 46, y1 = Math.max(...ps.map(p => p.y + p.r)) + 30;
    let bw = x1 - x0, bh = y1 - y0;
    // As tall as the circles need, up to the available height; then keep the box's aspect.
    const Hd = Math.round(Math.max(300, Math.min(H, w * bh / bw)));
    if (bw / bh < w / Hd) { const nw = bh * w / Hd; x0 -= (nw - bw) / 2; bw = nw; } else { const nh = bw * Hd / w; y0 -= (nh - bh) / 2; bh = nh; }
    return { pos, vb: [x0, y0, bw, bh], k: w / bw, H: Hd };
  }, [repos, w, H]);
  const k = L.k;

  const maxW = Math.max(1, ...links.map(l => l.weight || 1));
  const geo = l => {
    const a = L.pos.get(l.source), b = L.pos.get(l.target);
    if (!a || !b) return null;
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
    const bend = Math.min(70, len * 0.22) * (l.kind === "depends" ? -1 : 1);
    const cx = (a.x + b.x) / 2 - uy * bend, cy = (a.y + b.y) / 2 + ux * bend;
    // Start and end on the rims, pointing at the control point so the arrow meets the circle cleanly.
    const edge = (p, tx, ty, gap) => { const vx = tx - p.x, vy = ty - p.y, m = Math.hypot(vx, vy) || 1; return [p.x + vx / m * (p.r + gap), p.y + vy / m * (p.r + gap)]; };
    const [x1, y1] = edge(a, cx, cy, 3 / k), [x2, y2] = edge(b, cx, cy, 5 / k);
    const ang = Math.atan2(y2 - cy, x2 - cx), s = (9 + 3 * Math.sqrt((l.weight || 1) / maxW)) / k;
    const head = `M${x2},${y2} L${x2 - s * Math.cos(ang - 0.42)},${y2 - s * Math.sin(ang - 0.42)} L${x2 - s * Math.cos(ang + 0.42)},${y2 - s * Math.sin(ang + 0.42)} Z`;
    const mx = 0.25 * x1 + 0.5 * cx + 0.25 * x2, my = 0.25 * y1 + 0.5 * cy + 0.25 * y2;
    return { d: `M${x1},${y1} Q${cx},${cy} ${x2},${y2}`, head, mx, my };
  };
  const key = l => `${l.source}>${l.target}>${l.kind}`;
  const act = active || pinned;
  return html`<div class="rmap" ref=${host} style=${{ height: L.H + 44 + "px" }}>
    <svg viewBox=${L.vb.join(" ")} width=${w} height=${L.H} role="group" aria-label=${`${plural(repos.length, "repository", "repositories")} and the packages they share`}>
      ${[...L.pos.values()].map(p => {
        const R = p.repo, dim = hoverRepo && hoverRepo !== R.id && !links.some(l => (l.source === hoverRepo && l.target === R.id) || (l.target === hoverRepo && l.source === R.id));
        const ro = 7 / k, arc = `M${p.x - p.r - ro},${p.y} A${p.r + ro},${p.r + ro} 0 0 1 ${p.x + p.r + ro},${p.y}`;
        const fs = Math.max(12, Math.min(17, p.r * k / 5.5)) / k;
        return html`<g key=${R.id} class=${`repo${R.id === current ? " here" : ""}${dim ? " dim" : ""}${hoverRepo === R.id ? " on" : ""}`} style=${{ "--rc": p.color }}>
          <circle class="ring3" cx=${p.x} cy=${p.y} r=${p.r + 12 / k}/>
          <circle class="ring2" cx=${p.x} cy=${p.y} r=${p.r + 6 / k}/>
          <circle class="rbody" cx=${p.x} cy=${p.y} r=${p.r} tabindex="0" role="link"
            aria-label=${`${R.name}: ${plural(R.nodes, "node")} in ${plural(R.files || 0, "file")}.${R.id === current ? " The project you are in." : ""} Open its map.`}
            onMouseEnter=${() => onHoverRepo(R.id)} onMouseLeave=${() => onHoverRepo(null)} onFocus=${() => onHoverRepo(R.id)} onBlur=${() => onHoverRepo(null)}
            onClick=${() => onOpen(R)} onKeyDown=${e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(R); } }}><title>${R.name}${R.description ? `\n${R.description}` : ""}</title></circle>
          <path id=${"rarc-" + R.id} d=${arc} fill="none" stroke="none"/>
          <text class="rname" style=${{ fontSize: fs + "px", strokeWidth: 4 / k + "px" }}><textPath href=${"#rarc-" + R.id} startOffset="50%" text-anchor="middle">${R.name}${R.id === current ? " (here)" : ""}</textPath></text>
          ${p.comms.map(s => { const { lines, fsPx: px } = labelLines(s.c, s.r * k), fsPx = px / k; const lh = l => l.cls === "cname" ? 1.1 : 1.05;
            const blockH = lines.reduce((a, l) => a + lh(l) * l.size, 0); let yy = s.y - fsPx * blockH / 2;
            return html`<g class="rcomm" key=${s.c.cid} tabindex="0" role="link" aria-label=${`${s.c.qual ? `${s.c.name}, ${s.c.qual}` : s.c.name}: ${plural(s.c.size, "node")} in ${R.name}. Open it.`}
              onClick=${e => { e.stopPropagation(); onOpenCommunity(R, s.c); }} onKeyDown=${e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpenCommunity(R, s.c); } }}>
              <title>${s.c.name}${s.c.qual ? ` · ${s.c.qual}` : ""}${s.c.area ? ` — ${s.c.area}/` : ""}\n${plural(s.c.size, "node")}</title>
              <circle cx=${s.x} cy=${s.y} r=${s.r}/>
              ${lines.map(l => { const h = fsPx * lh(l) * l.size; yy += h; return html`<text class=${l.cls} x=${s.x} y=${yy - h * 0.22} style=${{ fontSize: fsPx * l.size + "px" }}>${l.t}</text>`; })}
            </g>`; })}
        </g>`;
      })}
      ${links.map(l => { const g = geo(l); if (!g) return null; const on = act && key(act) === key(l); const fade = (act && !on) || (hoverRepo && l.source !== hoverRepo && l.target !== hoverRepo);
        const sw = (l.kind === "depends" ? 1.6 : 1.6 + 3.4 * Math.sqrt((l.weight || 1) / maxW)) / k;
        return html`<g key=${key(l)} class=${`rlink ${l.kind}${on ? " on" : ""}${fade ? " fade" : ""}`} tabindex="0" role="button"
          aria-label=${l.kind === "depends" ? `${nameOf(repos, l.source)} lists ${l.packages.join(", ")} from ${nameOf(repos, l.target)} in its manifest, but no file imports it` : `${nameOf(repos, l.source)} imports ${l.packages.join(", ")} from ${nameOf(repos, l.target)}, weight ${l.weight}`}
          onMouseEnter=${() => onLink(l)} onMouseLeave=${() => onLink(null)} onFocus=${() => onLink(l)} onBlur=${() => onLink(null)}
          onClick=${() => onPin(l)} onKeyDown=${e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPin(l); } }}>
          <path class="hit" d=${g.d} style=${{ strokeWidth: 16 / k }}/>
          <path class="line" d=${g.d} stroke-width=${sw} stroke-dasharray=${l.kind === "depends" ? `${7 / k} ${5 / k}` : undefined}/>
          <path class="head" d=${g.head}/>
          ${w < 520 ? null : html`<text class="rlbl" x=${g.mx} y=${g.my} style=${{ fontSize: 11.5 / k + "px", strokeWidth: 4 / k + "px" }}>${l.packages.slice(0, 2).join(", ")}${l.packages.length > 2 ? "…" : ""}</text>`}
        </g>`; })}
    </svg>
  </div>`;
}
const nameOf = (repos, id) => repos.find(r => r.id === id)?.name || id;

/** What a link means, with its import sites: hovering shows it, clicking keeps it. */
export function LinkCard({ l, repos, onEvidence, onClose, pinned }) {
  const from = nameOf(repos, l.source), to = nameOf(repos, l.target);
  const ev = l.evidence || [];
  const files = new Set(ev.map(e => e.file)).size;
  return html`<div class="panel lcard">
    <div class="row" style="justify-content:space-between;gap:8px"><h3>${from} → ${to}</h3>${pinned ? html`<button class="icon-btn sm" onClick=${onClose} aria-label="Back to the list of repositories">✕</button>` : null}</div>
    <p class="sub" style="margin:4px 0 8px">${l.kind === "depends"
      ? html`<b>${from}</b> lists ${l.packages.map((p, i) => html`${i ? ", " : ""}<code>${p}</code>`)} in its manifest, but no file imports it. Drawn dashed.`
      : html`<b>${from}</b> imports ${l.packages.map((p, i) => html`${i ? ", " : ""}<code>${p}</code>`)} from <b>${to}</b>${ev.length ? ` in ${plural(files, "file")}` : ""}.`}</p>
    ${l.kind !== "depends" ? html`<p class="sub">Weight ${fmt(l.weight)}: importing files × modules imported, not raw import statements.${l.declared ? " Also listed in its manifest." : " Not listed in its manifest."}</p>` : null}
    ${ev.length ? html`<h4>Where it is imported</h4><ul class="evlist">${ev.slice(0, 12).map(e => html`<li><button type="button" onClick=${() => onEvidence(l, e)} title=${`Open ${from}'s map at ${e.name}`}>
      <span class="path">${e.file}:${e.line}</span><code>${e.name}</code></button></li>`)}${ev.length > 12 ? html`<li class="sub">and ${ev.length - 12} more</li>` : null}</ul>
      <p class="sub" style="margin-top:6px">Click a line to open ${from}'s map there.</p>` : null}
    ${!pinned ? html`<p class="sub" style="margin-top:6px">Click the arrow to keep this open.</p>` : null}
  </div>`;
}
