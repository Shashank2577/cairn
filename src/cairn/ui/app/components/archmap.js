// Dependency layers: first-party folders stacked above the folders they import (FR-026, diagram standard).
// Vendored code is hidden behind a count; a large import cycle is one block that expands; each layer shows
// at most the node budget and folds the rest into "+N"; only edges between visible blocks are drawn, thinned
// to the heaviest until you hover or select a block. Every block opens its evidence below the picture.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html } from "./ui.js";
import { plural } from "../lib/format.js";
import { link, go } from "../router.js";

const NH = 34, GAP = 16, LGAP = 38, PADX = 112, TOP = 16, CH = 7.6, EDGE_BUDGET = 16, LABEL_MAX = 32, VENDOR_SHOWN = 12;

const STYLE = `
.dl .dl-note { color: var(--muted); font-size: 13px; margin: 0 0 12px; max-width: 86ch; }
.dl .dl-bar { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; margin: 0 0 12px; font-size: 13px; }
.dl .dl-bar button, .dl .dl-open button { font: 600 12.5px var(--ui); color: var(--ink); background: var(--raised); border: 1px solid var(--rule-strong); border-radius: 7px; padding: 4px 10px; cursor: pointer; }
.dl .dl-bar button[aria-pressed="true"] { border-color: var(--agent); color: var(--agent); }
.dl .dl-entries { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.dl .dl-entries span.lbl { color: var(--muted); }
.dl svg { display: block; max-width: 100%; }
.dl .b { cursor: pointer; outline: none; }
.dl .b rect.box { fill: var(--raised); stroke: var(--rule-strong); stroke-width: 1.2; }
.dl .b text { font: 600 12.5px var(--mono); fill: var(--ink); }
.dl .b text.n { font: 500 11px var(--ui); fill: var(--muted); }
.dl .b.cycle rect.box { stroke-dasharray: 5 3; stroke: var(--ink); fill: var(--faint); }
.dl .b.more rect.box { stroke-dasharray: 2 3; fill: var(--sheet); }
.dl .b.vend rect.box { stroke-dasharray: 2 3; fill: var(--sheet); }
.dl .b.vend text { fill: var(--muted); }
.dl .b.test rect.box { stroke-dasharray: 1 3; }
.dl .b.cyc3 rect.box { stroke-dasharray: 4 3; }
.dl .b:hover rect.box, .dl .b:focus-visible rect.box, .dl .b.sel rect.box { stroke: var(--agent); stroke-width: 2.2; }
.dl .b.dim { opacity: .35; }
.dl .tag rect { fill: var(--ink); }
.dl .tag text { font: 700 10px var(--ui); fill: var(--on-ink); letter-spacing: .04em; }
.dl .ctr rect { fill: none; stroke: var(--ink); stroke-dasharray: 6 4; stroke-width: 1; }
.dl .ctr text.h { font: 700 12.5px var(--ui); fill: var(--ink); cursor: pointer; }
.dl .ctr text.g { font: 650 11.5px var(--ui); fill: var(--muted); }
.dl .lay { font: 650 12px var(--ui); fill: var(--muted); }
.dl .lay.s { font-weight: 500; font-size: 11px; }
.dl .rule { stroke: var(--contour-strong); stroke-width: 1; }
.dl .e { fill: none; stroke: var(--rule-strong); stroke-opacity: .85; }
.dl .e.act { stroke: var(--agent); stroke-opacity: .9; }
.dl .ew { font: 650 11px var(--ui); fill: var(--muted); paint-order: stroke; stroke: var(--paper); stroke-width: 4px; }
.dl .ew.act { fill: var(--agent); }
.dl .keyline { padding: 10px 4px 4px; }
.dl .keyline i.d { border-style: dashed; }
.dl .dl-ev { margin-top: 14px; background: var(--raised); border: 1px solid var(--rule); border-radius: 10px; padding: 14px 18px; }
.dl .dl-ev h3 { margin: 0 0 4px; font: 700 14px var(--mono); word-break: break-all; }
.dl .dl-ev .meta { color: var(--muted); font-size: 13px; margin: 0 0 8px; }
.dl .dl-ev .cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 6px 28px; }
.dl .dl-ev h4 { margin: 8px 0 4px; font: 700 12px var(--ui); color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
.dl .dl-ev ul { list-style: none; margin: 0; padding: 0; font-size: 13px; }
.dl .dl-ev li { display: flex; justify-content: space-between; gap: 12px; padding: 2px 0; }
.dl .dl-ev li .p { font-family: var(--mono); font-size: 12.5px; overflow-wrap: anywhere; }
.dl .dl-ev li .c { color: var(--muted); white-space: nowrap; }
.dl .dl-ev a { color: var(--agent); }
.dl .dl-ev .lk { background: none; border: 0; padding: 0; font: inherit; color: var(--agent); cursor: pointer; text-align: left; }
.dl .dl-open { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
`;

const shorten = (s, n = LABEL_MAX) => (s.length <= n ? s : "…" + s.slice(s.length - n + 1));
const segs = (id, k) => id.split("/").slice(-k).join("/");

export function ArchMap({ g, pid }) {
  const host = useRef(null);
  const [hw, setHw] = useState(1100);
  const [showV, setShowV] = useState(false);
  const [openLayers, setOpenLayers] = useState(() => new Set());
  const [openCycles, setOpenCycles] = useState(() => new Set());
  const [sel, setSel] = useState(null);
  const [hov, setHov] = useState(null);
  useEffect(() => { const el = host.current; if (!el) return; const ro = new ResizeObserver(([e]) => setHw(Math.round(e.contentRect.width))); ro.observe(el); return () => ro.disconnect(); }, []);

  const unit = g.level === "folder" ? "folder" : "file";
  const T = useMemo(() => tables(g), [g]);
  const L = useMemo(() => layout(g, T, { showV, openLayers, openCycles }, Math.max(520, hw - PADX - 24)), [g, T, showV, openLayers, openCycles, Math.round(hw / 60)]);
  const E = useMemo(() => edges(g, T, L, showV, openCycles), [g, T, L, showV, openCycles]);
  const act = hov || sel;
  const drawn = useMemo(() => {
    if (act) return E.list.filter(e => e.a === act || e.b === act).sort((x, y) => y.w - x.w).slice(0, 40);
    return E.list.filter(e => !e.internal).sort((x, y) => y.w - x.w || (x.a + x.b < y.a + y.b ? -1 : 1)).slice(0, EDGE_BUDGET);
  }, [E, act]);
  const near = useMemo(() => { if (!act) return null; const s = new Set([act]); for (const e of drawn) { s.add(e.a); s.add(e.b); } return s; }, [act, drawn]);

  const toggle = (set, setter, key) => setter(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });
  const pick = id => { const b = L.boxes.get(id); if (!b) return; if (b.more) toggle(openLayers, setOpenLayers, b.layer); else if (b.less) toggle(openLayers, setOpenLayers, b.layer); else setSel(s => s === id ? null : id); };
  const open = id => go(g.level === "folder" ? link.project(pid, "map", [], { scope: "file:" + id }) : link.impact(pid, id));
  const vcount = g.vendored?.count || 0;
  const cycles = g.cycles || [];
  if (!g.layers?.length) return html`<div class="dl"><style>${STYLE}</style><p class="dl-note">No ${unit} here imports another first-party ${unit}${vcount ? `; ${plural(vcount, "vendored folder")} hidden` : ""}, so there are no layers to draw.</p></div>`;

  return html`<div class="dl" ref=${host}><style>${STYLE}</style>
    <p class="dl-note">Dependency layers: ${plural(g.scope.units, unit)} of your own code (${plural(g.scope.files, "file")}), each above the ${unit}s it imports.
      ${cycles.length ? ` ${cycles.map(c => `${c.size} ${unit}s import each other in one cycle, shown as one block`).join("; ")}.` : " There are no large import cycles."}
      ${" "}Lines are drawn only between visible blocks, and only the heaviest ${EDGE_BUDGET} until you hover or select one; the number on a line is how many imports it stands for.</p>
    <div class="dl-bar">
      ${g.entries?.length ? html`<span class="dl-entries"><span class="lbl">Entry points:</span>${g.entries.slice(0, 6).map(e => html`<button key=${e.id} onClick=${() => setSel(e.id)} title=${e.signals.map(s => s.detail).join("; ")}>${labelOf(T, e.id)}</button>`)}${g.entries.length > 6 ? html`<span class="lbl">+${g.entries.length - 6}</span>` : null}</span>` : html`<span class="lbl" style="color:var(--muted)">No entry points found in pyproject scripts, __main__ modules, server modules or system-model containers.</span>`}
      ${vcount ? html`<button aria-pressed=${showV} onClick=${() => setShowV(v => !v)}>${vcount} vendored ${vcount === 1 ? "folder" : "folders"} ${showV ? "shown" : "hidden"} (${showV ? "hide" : "show"})</button>` : null}
      ${cycles.map(c => html`<button key=${c.id} aria-pressed=${openCycles.has(c.id)} onClick=${() => toggle(openCycles, setOpenCycles, c.id)}>${openCycles.has(c.id) ? "Collapse" : "Expand"} the cycle of ${c.size} ${unit}s</button>`)}
    </div>
    <svg width=${L.width} height=${L.height} viewBox=${`0 0 ${L.width} ${L.height}`} role="group" aria-label="Dependency layers">
      <defs>
        <marker id="dl-a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,1 L7,4 L0,7 z" fill="var(--rule-strong)"/></marker>
        <marker id="dl-b" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,1 L7,4 L0,7 z" fill="var(--agent)"/></marker>
      </defs>
      ${L.bands.map(b => html`<g key=${"l" + b.key}>
        <line class="rule" x1="10" x2=${L.width - 10} y1=${b.y - GAP / 2 - 2} y2=${b.y - GAP / 2 - 2}/>
        <text class="lay" x="14" y=${b.y + NH / 2 - 2}>${b.title}</text>
        <text class="lay s" x="14" y=${b.y + NH / 2 + 13}>${b.sub}</text></g>`)}
      ${L.containers.map(c => html`<g key=${c.id} class="ctr">
        <rect x=${c.x} y=${c.y} width=${c.w} height=${c.h} rx="9"/>
        <text class="h" x=${c.x + 12} y=${c.y + 19} tabindex="0" role="button" onClick=${() => toggle(openCycles, setOpenCycles, c.id)} onKeyDown=${e => { if (e.key === "Enter" || e.key === " ") toggle(openCycles, setOpenCycles, c.id); }}>▾ ${c.label} · click to collapse</text>
        ${c.groups.map(gr => html`<text class="g" key=${gr.name} x=${gr.x} y=${gr.y}>${gr.name} · ${gr.n}</text>`)}</g>`)}
      ${drawn.map(e => { const a = L.boxes.get(e.a), b = L.boxes.get(e.b); if (!a || !b) return null; const p = path(a, b); const hot = !!act;
        return html`<g key=${e.a + ">" + e.b}><path class=${"e" + (hot ? " act" : "")} d=${p.d} stroke-width=${(1 + Math.min(2.2, Math.log2(e.w) * 0.3)).toFixed(2)} marker-end=${hot ? "url(#dl-b)" : "url(#dl-a)"}/>
          <text class=${"ew" + (hot ? " act" : "")} x=${p.mx} y=${p.my + 4} text-anchor="middle">${e.w}</text></g>`; })}
      ${[...L.boxes.values()].map(b => { const dim = near && !near.has(b.id);
        const cls = `b${b.cycle ? " cycle" : ""}${b.more || b.less ? " more" : ""}${b.vend ? " vend" : ""}${b.test ? " test" : ""}${b.small ? " cyc3" : ""}${sel === b.id ? " sel" : ""}${dim ? " dim" : ""}`;
        return html`<g key=${b.id} class=${cls} tabindex="0" role="button" aria-pressed=${sel === b.id} aria-label=${b.aria}
          onMouseEnter=${() => setHov(b.id)} onMouseLeave=${() => setHov(null)} onFocus=${() => setHov(b.id)} onBlur=${() => setHov(null)}
          onClick=${() => pick(b.id)} onKeyDown=${e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(b.id); } }}>
          <title>${b.tip}</title>
          <rect class="box" x=${b.x} y=${b.y} width=${b.w} height=${b.h} rx="7"/>
          <text x=${b.x + 12} y=${b.y + b.h / 2 + 4.5}>${b.label}</text>
          ${b.n != null ? html`<text class="n" x=${b.x + b.w - 9} y=${b.y + b.h / 2 + 4} text-anchor="end">${b.n}</text>` : null}
          ${b.entry ? html`<g class="tag"><rect x=${b.x + 8} y=${b.y - 8} width="38" height="14" rx="7"/><text x=${b.x + 27} y=${b.y + 2.5} text-anchor="middle">ENTRY</text></g>` : null}
        </g>`; })}
    </svg>
    <div class="keyline">
      <span><i></i>${unit === "folder" ? "Folder" : "File"} (number: files)</span>
      ${cycles.length ? html`<span><i class="d" style="border-color:var(--ink);background:var(--faint)"></i>Import cycle</span>` : null}
      <span><i style="border-style:dashed"></i>+N more in this layer</span>
      ${showV ? html`<span><i style="border-style:dotted"></i>Vendored (not your code)</span>` : null}
      <span><i style="background:var(--ink);border-color:var(--ink);height:7px;width:22px;border-radius:4px"></i>Entry point (from pyproject scripts, __main__, server modules, containers)</span>
      <span><i class="line" style="border-color:var(--rule-strong)"></i>Imports (arrow points at what is used)</span>
      <span><i class="line" style="border-color:var(--agent)"></i>Imports of the hovered or selected block</span>
    </div>
    <p class="dl-note" style="margin-top:6px">${act ? `Showing every link of ${labelOf(T, act)} that reaches a visible block.` : E.list.filter(e => !e.internal).length > drawn.length ? `${E.list.filter(e => !e.internal).length - drawn.length} lighter links are not drawn; hover or select a block to see its own.` : "All links between visible blocks are drawn."}
      ${g.isolated?.length ? ` ${plural(g.isolated.length, unit)} import${g.isolated.length === 1 ? "s" : ""} no other first-party code and ${g.isolated.length === 1 ? "is" : "are"} not imported: ${g.isolated.slice(0, 6).map(shortId).join(", ")}${g.isolated.length > 6 ? ` and ${g.isolated.length - 6} more` : ""}.` : ""}</p>
    ${sel ? html`<${Evidence} g=${g} T=${T} L=${L} E=${E} id=${sel} unit=${unit} open=${open} pid=${pid} pick=${setSel} toggleCycle=${id => toggle(openCycles, setOpenCycles, id)} openCycles=${openCycles}/>` : html`<p class="dl-note">Select any block to open its files, import counts and what it uses and is used by.</p>`}
  </div>`;
}

const shortId = id => (id === "." ? "(repo root)" : segs(id, 2));

/* ---------------------------------------------------------------- lookups */
function tables(g) {
  const nodes = new Map(g.nodes.map(n => [n.id, n]));
  const members = new Map();      // member folder -> {m, cycle}
  const adj = new Map();          // id -> {out: Map, in: Map}
  const add = (a, b, w) => { for (const [x, y, k] of [[a, b, "out"], [b, a, "in"]]) { if (!adj.has(x)) adj.set(x, { out: new Map(), in: new Map() }); adj.get(x)[k].set(y, (adj.get(x)[k].get(y) || 0) + w); } };
  for (const c of g.cycles || []) for (const m of c.members) members.set(m.id, { m, cycle: c.id });
  for (const l of g.links) add(l.source, l.target, l.weight);
  for (const c of g.cycles || []) for (const l of c.links) add(l.source, l.target, l.weight);
  const vend = new Map((g.vendored?.folders || []).map(v => [v.id, v]));
  for (const l of g.vendored?.links || []) add(l.source, "vendor:" + l.target, l.weight);
  const all = [...g.nodes.map(n => n.id), ...members.keys()];
  const dup = new Map(); const nm = id => segs(id, 2); all.forEach(id => dup.set(nm(id), (dup.get(nm(id)) || 0) + 1));
  return { nodes, members, adj, vend, dup, name: id => (dup.get(nm(id)) > 1 ? segs(id, 3) : nm(id)) };
}
function labelOf(T, id) {
  const n = T.nodes.get(id); if (n) return n.kind === "cycle" ? n.label : shorten(T.name(id) + (n.kind === "folder" ? "/" : ""));
  const m = T.members.get(id); if (m) return shorten(T.name(id) + "/");
  if (id.startsWith("vendor:")) return shorten(segs(id.slice(7), 2) + "/");
  if (id.startsWith("more:")) return "more";
  return id;
}

/* ---------------------------------------------------------------- layout */
function commonPrefix(ids) {
  const parts = ids.map(i => i.split("/")); let k = 0;
  while (parts.every(p => p.length > k + 1 && p[k] === parts[0][k])) k++;
  return parts[0].slice(0, k).join("/");
}

function layout(g, T, st, MAXW) {
  const folder = g.level === "folder";
  const boxes = new Map(), bands = [], containers = [];
  const wOf = (label, extra = 30) => Math.max(96, Math.round(label.length * CH + extra));
  let y = TOP, widest = 0;
  const flow = (items, x0) => {   // wrap boxes into rows; returns the y after the last row
    let x = x0, rowTop = y, first = true;
    for (const it of items) {
      if (!first && x + it.w > x0 + MAXW) { x = x0; rowTop += NH + GAP; first = true; }
      boxes.set(it.id, { ...it, x, y: rowTop, h: NH }); x += it.w + 12; first = false; widest = Math.max(widest, x - x0);
    }
    return rowTop + NH;
  };
  const box = (id, extra) => {
    const n = T.nodes.get(id), label = labelOf(T, id);
    const ent = (n.entry || []).length > 0;
    return { id, label, w: wOf(label, 58), n: n.files, cycle: n.kind === "cycle", entry: ent, test: n.test, small: n.cycle?.length > 1,
      aria: `${label}: ${plural(n.files, "file")}${ent ? ", entry point" : ""}. Open its evidence.`,
      tip: `${n.kind === "cycle" ? n.label : n.id}: ${plural(n.files, "file")}, ${plural(n.symbols, "symbol")}${ent ? `; entry point (${n.entry.map(e => e.detail).join("; ")})` : ""}${n.cycle?.length > 1 ? `; import cycle with ${n.cycle.filter(c => c !== n.id).join(", ")}` : ""}` };
  };
  for (const lay of g.layers) {
    const open = st.openLayers.has(lay.layer);
    const top = y;
    bands.push({ key: lay.layer, y: top, title: `Layer ${lay.layer}`, sub: `${plural(lay.total, folder ? "folder" : "file")}` });
    let pending = [];
    const flush = () => { if (pending.length) { y = flow(pending, PADX) + GAP; pending = []; } };
    const ids = open ? [...lay.shown, ...lay.rest] : lay.shown;
    for (const id of ids) {
      const n = T.nodes.get(id);
      if (n.kind === "cycle" && st.openCycles.has(id)) { flush(); y = expand(id, n, y) + GAP; } else pending.push(box(id));
    }
    if (!open && lay.rest.length) pending.push({ id: "more:" + lay.layer, more: true, layer: lay.layer, label: `+${lay.rest.length} more`, w: 112, rest: lay.rest,
      aria: `${lay.rest.length} more in layer ${lay.layer}. Show them.`, tip: `${lay.rest.length} more ${folder ? "folders" : "files"} in layer ${lay.layer} (budget ${g.budget} per layer). Click to show them.` });
    if (open && lay.rest.length) pending.push({ id: "less:" + lay.layer, less: true, layer: lay.layer, label: "show fewer", w: 112, aria: "Fold this layer back to the budget.", tip: "Fold this layer back to its budget." });
    flush();
    y += LGAP - GAP;
  }
  function expand(id, n, y0) {
    const c = (g.cycles || []).find(k => k.id === id);
    const pre = commonPrefix(c.members.map(m => m.id));
    const rel = m => (pre ? m.id.slice(pre.length + 1) : m.id);
    const key = (m, k) => rel(m).split("/").slice(0, k).join("/");
    let k = 1;   // group at the deepest level that still gives a dozen headings or fewer
    for (let t = 2; t <= 6; t++) if (new Set(c.members.map(m => key(m, t))).size <= 12) k = t;
    const groups = new Map();
    for (const m of c.members) { const kk = key(m, k); (groups.get(kk) || groups.set(kk, []).get(kk)).push(m); }
    const gx = PADX + 10, gw = MAXW - 20;
    let yy = y0 + 30; const gl = [];
    for (const [name, ms] of [...groups].sort((a, b) => a[0].localeCompare(b[0]))) {
      gl.push({ name: (pre ? pre + "/" : "") + name, n: ms.length, x: gx, y: yy + 12 }); yy += 22;
      let x = gx, top = yy;
      for (const m of ms.sort((a, b) => a.id.localeCompare(b.id))) {
        const r = rel(m), inGroup = r === name ? "·" : r.startsWith(name + "/") ? r.slice(name.length + 1) : r;
        const label = shorten(inGroup + (folder && inGroup !== "·" ? "/" : ""));
        const w = wOf(label, 58);
        if (x + w > gx + gw && x > gx) { x = gx; top += NH + 10; }
        const ent = (m.entry || []).length > 0;
        boxes.set(m.id, { id: m.id, label, w, x, y: top, h: NH, n: m.files, entry: ent, test: m.test, member: true, aria: `${label}: ${plural(m.files, "file")}. Open its evidence.`,
          tip: `${m.id}: ${plural(m.files, "file")}, ${plural(m.symbols, "symbol")}${ent ? `; entry point (${m.entry.map(e => e.detail).join("; ")})` : ""}` });
        x += w + 10;
      }
      yy = top + NH + 14;
    }
    containers.push({ id, label: n.label, x: PADX, y: y0, w: MAXW, h: yy - y0, groups: gl });
    widest = Math.max(widest, MAXW);
    return yy;
  }
  if (st.showV && g.vendored?.count) {
    const vf = g.vendored.folders, open = st.openLayers.has("v");
    bands.push({ key: "v", y, title: "Vendored", sub: `${plural(vf.length, "folder")}, not your code` });
    const items = (open ? vf : vf.slice(0, VENDOR_SHOWN)).map(v => {
      const id = "vendor:" + v.id, label = shorten(segs(v.id, 2) + "/");
      return { id, label, w: wOf(label, 58), n: v.files, vend: true, aria: `${v.id}: vendored, ${plural(v.files, "file")}. Open its evidence.`, tip: `${v.id}: ${plural(v.files, "file")}; vendored (${v.reason})` };
    });
    if (!open && vf.length > VENDOR_SHOWN) items.push({ id: "more:v", more: true, layer: "v", label: `+${vf.length - VENDOR_SHOWN} more`, w: 112, aria: "Show every vendored folder.", tip: "Show every vendored folder." });
    if (open && vf.length > VENDOR_SHOWN) items.push({ id: "less:v", less: true, layer: "v", label: "show fewer", w: 112, aria: "Fold back.", tip: "Fold back." });
    y = flow(items, PADX) + GAP;
  }
  const width = Math.max(760, PADX + Math.min(MAXW, widest) + 24);
  return { boxes, bands, containers, width, height: y + 8 };
}

/* ---------------------------------------------------------------- edges */
function edges(g, T, L, showV, openCycles) {
  const hidden = new Map();       // id -> the "+N" box that stands for it
  for (const lay of g.layers) { const m = L.boxes.get("more:" + lay.layer); if (m) lay.rest.forEach(id => hidden.set(id, m.id)); }
  const mv = L.boxes.get("more:v");
  const res = id => (L.boxes.has(id) ? id : hidden.get(id) || null);
  const agg = new Map();
  const put = (s, t, w, internal) => {
    const a = res(s), b = res(t); if (!a || !b || a === b) return;
    const k = a + "\u0000" + b, e = agg.get(k);
    if (e) { e.w += w; e.internal = e.internal && internal; } else agg.set(k, { a, b, w, internal });
  };
  for (const l of g.links) if (!openCycles.has(l.source) && !openCycles.has(l.target)) put(l.source, l.target, l.weight, false);
  for (const c of g.cycles || []) if (openCycles.has(c.id)) {
    const mine = new Set(c.members.map(m => m.id));
    for (const l of c.links) put(l.source, l.target, l.weight, mine.has(l.source) && mine.has(l.target));
  }
  if (showV) for (const l of g.vendored?.links || []) {
    const t = "vendor:" + l.target; const b = L.boxes.has(t) ? t : mv?.id;
    const a = res(l.source); if (a && b) { const k = a + "\u0000" + b, e = agg.get(k); if (e) e.w += l.weight; else agg.set(k, { a, b, w: l.weight, internal: false }); }
  }
  return { list: [...agg.values()] };
}

function path(a, b) {
  const ax = a.x + a.w / 2, bx = b.x + b.w / 2;
  if (Math.abs(a.y - b.y) < 4) {          // same row: arc under both
    const y = a.y + a.h, d = `M${ax},${y} C${ax},${y + 26} ${bx},${y + 26} ${bx},${y + 1}`;
    return { d, mx: (ax + bx) / 2, my: y + 20 };
  }
  if (b.y > a.y) { const y1 = a.y + a.h, y2 = b.y, m = (y1 + y2) / 2; return { d: `M${ax},${y1} C${ax},${m} ${bx},${m} ${bx},${y2}`, mx: (ax + bx) / 2, my: m }; }
  const y1 = a.y, y2 = b.y + b.h, m = (y1 + y2) / 2;   // a depends on something above it (inside an expanded cycle)
  return { d: `M${ax},${y1} C${ax},${m} ${bx},${m} ${bx},${y2}`, mx: (ax + bx) / 2, my: m };
}

/* ---------------------------------------------------------------- evidence */
function Evidence({ g, T, L, E, id, unit, open, pid, pick, toggleCycle, openCycles }) {
  const box = L.boxes.get(id); if (!box) return null;
  const n = T.nodes.get(id), mem = T.members.get(id), v = id.startsWith("vendor:") ? T.vend.get(id.slice(7)) : null;
  const d = mem ? mem.m : n;
  if (!d && !v) return html`<section class="dl-ev"><h3>${box.label}</h3><p class="meta">${box.tip}</p>${box.rest ? html`<ul>${box.rest.map(r => html`<li key=${r}><span class="p">${r}</span></li>`)}</ul>` : null}</section>`;
  const nb = (k) => [...(T.adj.get(id)?.[k] || new Map())].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
  const row = ([o, w]) => html`<li key=${o}><span class="p">${(T.nodes.has(o) || T.members.has(o) || o.startsWith("vendor:")) && L.boxes.has(o) ? html`<button class="lk" onClick=${() => pick(o)}>${o.startsWith("vendor:") ? o.slice(7) : o}</button>` : (o.startsWith("vendor:") ? o.slice(7) : o)}</span><span class="c">${plural(w, "import")}</span></li>`;
  const uses = nb("out"), usedBy = nb("in");
  const ev = d?.evidence;
  const entry = d?.entry || [];
  const isFolder = g.level === "folder";
  const title = n?.kind === "cycle" ? n.label : v ? v.id : id;
  return html`<section class="dl-ev" aria-label=${"Evidence for " + title}>
    <h3>${title}</h3>
    <p class="meta">${v ? `Vendored code (${v.reason}); ${plural(v.files, "file")}, ${plural(v.symbols, "symbol")}. Not analysed as part of your own layers.`
      : n?.kind === "cycle" ? `${plural(n.evidence.members, unit)} that import each other, ${plural(n.files, "file")} in all. ${plural(n.evidence.imports_in, "import")} come in from outside the cycle, ${plural(n.evidence.imports_out, "import")} go out.`
      : `${plural(d.files, "file")}, ${plural(d.symbols, "symbol")}${ev ? `; ${plural(ev.imports_out, "import")} out, ${plural(ev.imports_in, "import")} in` : ""}${d.commits ? `; ${plural(d.commits, "commit")}` : ""}${d.fixes ? `, ${d.fixes} fix or revert` : ""}${d.agent_reads || d.agent_edits ? `; agents read ${d.agent_reads || 0}, edited ${d.agent_edits || 0}` : ""}${d.tasks ? `; owned by ${plural(d.tasks, "task")}` : ""}.`}</p>
    ${entry.length ? html`<h4>Why it is an entry point</h4><ul>${entry.map(s => html`<li key=${s.detail + s.file}><span class="p">${s.detail}</span><span class="c">${s.file}</span></li>`)}</ul>` : null}
    <div class="cols">
      ${uses.length ? html`<div><h4>Uses (${uses.length})</h4><ul>${uses.slice(0, 8).map(row)}${uses.length > 8 ? html`<li><span class="c">and ${uses.length - 8} more</span></li>` : null}</ul></div>` : null}
      ${usedBy.length ? html`<div><h4>Used by (${usedBy.length})</h4><ul>${usedBy.slice(0, 8).map(row)}${usedBy.length > 8 ? html`<li><span class="c">and ${usedBy.length - 8} more</span></li>` : null}</ul></div>` : null}
      ${ev?.files?.length ? html`<div><h4>Files (${ev.files_total})</h4><ul>${ev.files.map(f => html`<li key=${f.path}><span class="p"><a href=${link.impact(pid, f.path)}>${f.path}</a></span><span class="c">${plural(f.symbols, "symbol")}</span></li>`)}${ev.files_total > ev.files.length ? html`<li><span class="c">and ${ev.files_total - ev.files.length} more</span></li>` : null}</ul></div>` : null}
    </div>
    <div class="dl-open">
      ${n?.kind === "cycle" ? html`<button onClick=${() => toggleCycle(id)}>${openCycles.has(id) ? "Collapse the cycle" : `Expand the ${n.evidence.members} ${unit}s`}</button>` : null}
      ${!v && n?.kind !== "cycle" ? html`<button onClick=${() => open(id)}>${isFolder ? "Explore it in the graph" : "See what breaks if it changes"}</button>` : null}
    </div>
  </section>`;
}
