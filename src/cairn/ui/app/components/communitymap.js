// The community map: the first thing the Map shows. Communities are circles sized by how many nodes
// they hold, packed inside the area of the repository they live in (drawn like contour rings), so the
// structure reads at a glance and no two labels can overlap. Lines between circles are dependencies,
// weakest hidden. Click an area to zoom into it; click a community to open its members.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html } from "./ui.js";
import { fmt, plural } from "../lib/format.js";
import { reducedMotion } from "../state.js";

let packLoading = null;
export function loadPack() {
  if (window.d3?.pack) return Promise.resolve(window.d3);
  if (!packLoading) packLoading = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "/ui/vendor/d3-hierarchy/d3-hierarchy.min.js";
    s.onload = () => resolve(window.d3);
    s.onerror = () => { packLoading = null; reject(new Error("Couldn't load the layout library.")); };
    document.head.appendChild(s);
  });
  return packLoading;
}

const CW = 6.4; // average character width at 12px, for fitting labels inside circles
const fit2 = (text, chars) => { const n = Math.floor(chars); return !text ? "" : text.length <= n ? text : n >= 3 ? text.slice(0, n - 1) + "…" : ""; };
const bareName = t => String(t || "").replace(/\.(py|js|mjs|ts|tsx|jsx|md|rs|go|java|rb|sh|toml|json)$/, "");
/**
 * The lines to write inside a circle of on-screen radius er: the name (shrunk to fit, then split, then shortened), its
 * qualifier when names repeat in the area, then busiest members if there is room. Text never goes below 8px.
 */
export function labelLines(c, er) {
  const F = er > 26 ? 1.72 : 1.85, MIN = 8;
  const pref = Math.max(MIN, Math.min(15, er / 3.4));
  const fitFs = t => Math.min(pref, er * F * 12 / (CW * Math.max(1, t.length)));
  let name = c.name, fsPx = fitFs(name);
  if (fsPx < MIN + 0.5) { const b = bareName(name); if (fitFs(b) > fsPx) { name = b; fsPx = fitFs(b); } }
  fsPx = Math.max(MIN, fsPx);
  const n = Math.floor(er * F / (CW * fsPx / 12) + 1e-6);
  const rows = Math.max(1, Math.floor(er * 1.5 / (fsPx * 1.12)));
  const out = [];
  if (name.length <= n) out.push({ t: name, cls: "cname", size: 1 });
  else {
    const cut = Math.max(name.lastIndexOf("/", n), name.lastIndexOf("_", n), name.lastIndexOf(" ", n), name.lastIndexOf("-", n), name.lastIndexOf(".", n));
    if (rows >= 2 && cut > n * 0.35) out.push({ t: name.slice(0, cut + 1), cls: "cname", size: 1 }, { t: fit2(name.slice(cut + 1), n), cls: "cname", size: 1 });
    else out.push({ t: fit2(name, n), cls: "cname", size: 1 });
  }
  const small = (t, cls) => { const f = Math.max(MIN, Math.min(fsPx * 0.85, er * F * 12 / (CW * Math.max(1, t.length)))); return { t: fit2(t, Math.floor(er * F / (CW * f / 12))), cls, size: f / fsPx }; };
  if (c.qual) {
    if (out.length < rows) out.push(small(c.qual, "cqual"));
    else if (out.length === 1) out[0].t = fit2(`${name} · ${c.qual}`, n); // no room for a second row: squeeze both in
  }
  if (!c.qual && er > 38 && out.length < rows && c.sub) out.push(small(c.sub, "csub"));
  return { lines: out.filter(l => l.t), fsPx };
}

export function CommunityMap({ d3, regions, links, height, focusRegion, onRegion, onOpen, hover, onHover, highlight, threshold }) {
  const host = useRef(null);
  const [w, setW] = useState(900);
  useEffect(() => {
    const el = host.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(300, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = height || Math.max(460, Math.min(900, Math.round(w * 0.9), (typeof window !== "undefined" ? window.innerHeight : 900) - 170));

  const layout = useMemo(() => {
    const root = d3.hierarchy({ children: regions.map(r => ({ region: r, children: r.communities.map(c => ({ c, value: Math.max(1, c.size) })) })) })
      .sum(d => d.value || 0).sort((a, b) => b.value - a.value);
    d3.pack().size([w, H]).padding(n => n.depth === 0 ? 16 : 4)(root);
    const regionNodes = root.children || [];
    const pos = new Map();
    for (const rn of regionNodes) for (const cn of rn.children || []) pos.set(cn.data.c.id, { x: cn.x, y: cn.y, r: cn.r, c: cn.data.c, region: rn.data.region });
    return { regionNodes, pos };
  }, [regions, w, H]);

  // Zoom into one area by animating the viewBox; labels grow with the zoom so more of them fit.
  const [vb, setVb] = useState([0, 0, w, H]);
  const target = useMemo(() => {
    const rn = focusRegion && layout.regionNodes.find(n => n.data.region.key === focusRegion);
    if (!rn) return [0, 0, w, H];
    const pad = 18, k = Math.min(w / (2 * rn.r + 2 * pad), H / (2 * rn.r + 2 * pad));
    return [rn.x - w / (2 * k), rn.y - H / (2 * k), w / k, H / k];
  }, [focusRegion, layout, w, H]);
  useEffect(() => {
    if (reducedMotion()) { setVb(target); return; }
    const from = vb, start = performance.now(), ms = 520;
    let raf = 0;
    const tick = t => {
      const e = Math.min(1, (t - start) / ms), q = 1 - Math.pow(1 - e, 3);
      setVb(from.map((v, i) => v + (target[i] - v) * q));
      if (e < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target.join(",")]);
  const k = w / vb[2];

  const maxW = Math.max(1, ...links.map(l => l.weight || 1));
  const shown = links.filter(l => (l.weight || 1) >= threshold && layout.pos.has(l.source) && layout.pos.has(l.target));
  const hoverLinks = hover ? links.filter(l => (l.source === hover || l.target === hover) && layout.pos.has(l.source) && layout.pos.has(l.target)) : [];
  const linked = new Set(hoverLinks.flatMap(l => [l.source, l.target]));
  // Lines run between circle edges, so they never cross the labels of the two communities they join.
  const curve = (a, b) => {
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
    const x1 = a.x + ux * a.r, y1 = a.y + uy * a.r, x2 = b.x - ux * b.r, y2 = b.y - uy * b.r;
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, bend = Math.min(50, Math.hypot(x2 - x1, y2 - y1) * 0.18);
    return `M${x1},${y1} Q${mx - uy * bend},${my + ux * bend} ${x2},${y2}`;
  };
  const lineW = l => (0.7 + 3.6 * Math.sqrt((l.weight || 1) / maxW)) / Math.sqrt(k);

  const fit = (text, px) => { const n = Math.floor(px / CW); return !text ? "" : text.length <= n ? text : n > 3 ? text.slice(0, n - 1) + "…" : ""; };

  return html`<div class="cmap" ref=${host} style=${{ height: H + "px" }}>
    <svg viewBox=${vb.join(" ")} width=${w} height=${H} role="group" aria-label="Communities of the code, grouped by area">
      <g class="regions">${layout.regionNodes.map(rn => {
        const R = rn.data.region, arc = `M${rn.x - rn.r - 4},${rn.y} A${rn.r + 4},${rn.r + 4} 0 0 1 ${rn.x + rn.r + 4},${rn.y}`;
        const dim = focusRegion && focusRegion !== R.key;
        return html`<g key=${R.key} class=${"region" + (dim ? " dim" : "")} style=${{ "--rc": R.color }}>
          <circle class="ring3" cx=${rn.x} cy=${rn.y} r=${rn.r + 11 / k}/>
          <circle class="ring2" cx=${rn.x} cy=${rn.y} r=${rn.r + 5.5 / k}/>
          <circle class="ring" cx=${rn.x} cy=${rn.y} r=${rn.r} tabindex=${focusRegion === R.key ? -1 : 0} role="button"
            aria-label=${`Area ${R.label}: ${plural(R.communities.length, "community", "communities")}, ${fmt(R.size)} nodes. Zoom in.`}
            onClick=${() => onRegion(focusRegion === R.key ? null : R.key)} onKeyDown=${e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onRegion(focusRegion === R.key ? null : R.key); } }}/>
          <path id=${"arc-" + R.key} d=${arc} fill="none" stroke="none"/>
          ${rn.r * k > 24 ? html`<text class="rlabel" style=${{ fontSize: `${Math.min(13, Math.max(10, rn.r * k / 4)) / k}px`, strokeWidth: `${4 / k}px`, letterSpacing: `${0.2 / k}px` }}><textPath href=${"#arc-" + R.key} startOffset="50%" text-anchor="middle">${fit(R.label, Math.PI * rn.r * k * 0.8)}</textPath></text>` : null}
        </g>`;
      })}</g>
      <g class="clinks">${shown.map(l => html`<path key=${l.source + ">" + l.target} d=${curve(layout.pos.get(l.source), layout.pos.get(l.target))} stroke-width=${lineW(l)} class=${hover && !linked.has(l.source) ? "fade" : ""}/>`)}</g>
      <g class="comms">${[...layout.pos.values()].map(p => {
        const c = p.c, R = p.region, er = p.r * k, hl = highlight?.has(c.id), dim = (focusRegion && focusRegion !== R.key) || (highlight?.size && !hl) || (hover && hover !== c.id && !linked.has(c.id));
        const { lines, fsPx } = labelLines(c, er), fs = fsPx / k;
        const lh = l => l.cls === "cname" ? 1.1 : 1.05, size = l => l.size;
        const blockH = lines.reduce((a, l) => a + lh(l) * size(l), 0);
        let yy = p.y - fs * blockH / 2;
        return html`<g key=${c.id} class=${`comm${hl ? " hl" : ""}${dim ? " dim" : ""}${hover === c.id ? " on" : ""}`} style=${{ "--rc": R.color }}
          tabindex="0" role="button" aria-label=${`${c.qual ? `${c.name}, ${c.qual}` : c.name}, in ${R.label}: ${plural(c.size, "node")}. ${c.sub ? "Includes " + c.sub + ". " : ""}Open.`}
          onMouseEnter=${() => onHover(c.id)} onMouseLeave=${() => onHover(null)} onFocus=${() => onHover(c.id)} onBlur=${() => onHover(null)}
          onClick=${() => onOpen(c)} onKeyDown=${e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(c); } }}>
          <title>${c.full}${c.sub ? `\n${c.sub}` : ""}\n${plural(c.size, "node")}</title>
          <circle cx=${p.x} cy=${p.y} r=${p.r}/>
          ${lines.map(l => { const h = fs * lh(l) * size(l); yy += h; return html`<text class=${l.cls} x=${p.x} y=${yy - h * 0.22} style=${{ fontSize: fs * size(l) + "px" }}>${l.t}</text>`; })}
        </g>`;
      })}</g>
      <g class="clinks top">${hoverLinks.map(l => html`<path key=${"h" + l.source + ">" + l.target} d=${curve(layout.pos.get(l.source), layout.pos.get(l.target))} stroke-width=${lineW(l) * 1.4}/>`)}</g>
    </svg>
  </div>`;
}

/** Group communities into areas by folder: the deepest folder level that gives a readable number of areas. */
export function buildRegions(comms, colors) {
  const strip = p => String(p || "").replace(/^\.?\//, "").replace(/^\.$/, "");
  const withArea = comms.map(c => ({ ...c, area: strip(c.area) }));
  const prefix = (a, d) => a ? a.split("/").slice(0, d).join("/") : "not in a file";
  let best = null;
  for (let d = 1; d <= 6; d++) {
    const groups = new Map();
    for (const c of withArea) { const k = prefix(c.area, d); groups.set(k, (groups.get(k) || 0) + c.size); }
    const n = groups.size, total = [...groups.values()].reduce((a, b) => a + b, 0), biggest = Math.max(...groups.values()) / (total || 1);
    if (!best || (n <= 14 && (best.n < 5 || biggest < best.biggest))) best = { d, n, biggest };
    if (n >= 7 && n <= 14 && biggest < 0.45) break;
  }
  const groups = new Map();
  for (const c of withArea) { const k = prefix(c.area, best.d); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(c); }
  // Drop the prefix most of the code shares (src/<package>/…) so the labels say what differs.
  const total = withArea.reduce((a, c) => a + c.size, 0) || 1;
  let common = "";
  for (let d = 1; d <= 3; d++) {
    const share = new Map();
    for (const c of withArea) if (c.area.split("/").length >= d) { const k = c.area.split("/").slice(0, d).join("/"); share.set(k, (share.get(k) || 0) + c.size); }
    const top = [...share].sort((a, b) => b[1] - a[1])[0];
    if (top && top[1] / total >= 0.6 && [...groups.keys()].some(k => k.startsWith(top[0] + "/"))) common = top[0]; else break;
  }
  const regions = [...groups].map(([key, cs]) => {
    const parent = common.includes("/") ? common.slice(0, common.lastIndexOf("/")) : "";
    const label = common && key.startsWith(common + "/") ? key.slice(common.length + 1)
      : common && key === common ? `${key.split("/").pop()}, top level`
      : parent && key.startsWith(parent + "/") ? key.slice(parent.length + 1)
      : parent && key === parent ? `${key.split("/").pop()}, top level` : key;
    return { key: key.replace(/[^a-zA-Z0-9]+/g, "-") || "root", path: key, label: label || key, size: cs.reduce((a, c) => a + c.size, 0), communities: cs };
  }).sort((a, b) => b.size - a.size);
  regions.forEach((r, i) => {
    r.color = colors[i % colors.length];
    r.communities.forEach((c, i) => {
      const rel = c.area && c.area.startsWith(r.path + "/") ? c.area.slice(r.path.length + 1) : "";
      const files = (c.files || []).filter(f => f && f[0]);
      const total = files.reduce((a, f) => a + f[1], 0) || 1;
      const main = files[0] && files[0][1] / total >= 0.55 ? fileName(files[0][0]) : "";
      // Main file, else its sub-folder, else its biggest file, else its busiest member, else the area and a number.
      c.name = main || lastSeg(rel) || (files[0] ? fileName(files[0][0]) : "") || shortLabel(c.top?.[0] || c.label) || `${r.label} #${i + 1}`;
      // What can tell two same-named communities apart: their other files, then their sub-folder.
      const key = t => bareName(String(t || "")).replace(/\/$/, "").toLowerCase();
      const relTail = rel && rel.split("/").length > 1 ? rel.split("/").filter(x => key(x) !== key(c.name)).slice(-1)[0] : "";
      c.quals = [...new Set([...files.map(f => fileName(f[0])), relTail, lastSeg(rel)])]
        .filter(q => q && key(q) !== key(c.name) && !key(q).startsWith(key(c.name) + "/"));
      c.qual = "";
      c.sub = (c.top || []).map(shortLabel).filter(t => t && t !== c.name).slice(0, 3).join(", ");
      c.region = r.key;
    });
    uniqueNames(r.communities);
    for (const c of r.communities) { c.display = c.qual ? `${c.name} · ${c.qual}` : c.name; c.full = c.display + (c.area ? ` — ${c.area}/` : ""); }
  });
  return { regions, common };
}
const prettyLabel = s => String(s || "").replace(/^__init__\.py$/, "package init").replace(/\/__init__\.py$/, "/");
export const lastSeg = p => String(p || "").split("/").filter(x => x && x !== ".").pop() || "";
/** A file's name, or its folder for package entry files (engines/__init__.py → engines/). */
function fileName(path) {
  const parts = String(path || "").split("/"), f = parts.pop() || "";
  return /^(__init__\.py|index\.[jt]sx?|mod\.rs|__main__\.py)$/.test(f) && parts.length ? parts.pop() + "/" : f;
}
/** A member's label without its path. */
const shortLabel = s => { const t = prettyLabel(s); return t.endsWith("/") ? t : t.split("/").pop(); };
/** Within one area, give every same-named community the first qualifier that no other of them shares. */
export function uniqueNames(cs) {
  const groups = new Map();
  for (const c of cs) { if (!groups.has(c.name)) groups.set(c.name, []); groups.get(c.name).push(c); }
  for (const [, g] of groups) {
    if (g.length < 2) continue;
    const used = new Set();
    for (const c of g) {
      const others = g.filter(x => x !== c);
      const q = c.quals.find(q => !used.has(q) && !others.some(o => o.quals[0] === q || (o.qual && o.qual === q)));
      if (q) { c.qual = q; used.add(q); }
    }
    g.forEach((c, i) => { if (!c.qual) c.qual = `#${i + 1}`; });
  }
}
