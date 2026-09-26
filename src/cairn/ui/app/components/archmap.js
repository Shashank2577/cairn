// Architecture layers: every code file stacked above the code it uses, like contour bands on a hill.
// Hover or focus a file to trace what it uses (brown) and what uses it (blue); click to open its impact.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html } from "./ui.js";
import { base, dir, plural } from "../lib/format.js";
import { link, go } from "../router.js";

const RH = 92, NH = 34, PADX = 118, TOP = 26, CH = 7.9;

export function ArchMap({ g, pid }) {
  const [focus, setFocus] = useState(null);
  const host = useRef(null);
  const [hw, setHw] = useState(1100);
  useEffect(() => { const el = host.current; if (!el) return; const ro = new ResizeObserver(([e]) => setHw(Math.round(e.contentRect.width))); ro.observe(el); return () => ro.disconnect(); }, []);
  const L = useMemo(() => layout(g, Math.max(520, hw - PADX - 60)), [g, Math.round(hw / 80)]);
  const on = useMemo(() => {
    if (!focus) return null;
    const s = new Set([focus]);
    for (const l of g.links) { if (l.source === focus) s.add(l.target); if (l.target === focus) s.add(l.source); }
    return s;
  }, [focus, g]);
  if (!L) return null;
  const open = id => go(g.level === "folder" ? link.project(pid, "map", [], { scope: "file:" + id }) : link.impact(pid, id));
  return html`<div class=${"diagram arch" + (focus ? " focus" : "") + (g.links.length > 150 ? " dense" : "")} ref=${host}>
    <svg width=${L.width} height=${L.height} viewBox=${`0 0 ${L.width} ${L.height}`} role="group" aria-label="Architecture layers">
      ${L.rows.map((r, ri) => { const y = r.y + NH / 2; const firstLayer = r.layer === L.rows[0].layer, lastLayer = r.layer === L.rows[L.rows.length - 1].layer;
        const lbl = r.first && firstLayer ? "Entry points" : r.first && lastLayer && L.rows.length > 1 ? "Foundations" : "";
        return html`<g key=${"r" + ri}>${r.first ? html`<line class="contour" x1="12" x2=${L.width - 12} y1=${y} y2=${y}/>` : null}${lbl ? html`<text class="rowlbl" x="14" y=${y - 8}>${lbl}</text>` : null}</g>`; })}
      ${g.links.map(l => { const a = L.pos.get(l.source), b = L.pos.get(l.target); if (!a || !b) return null;
        const x1 = a.x + a.w / 2, y1 = a.y + NH, x2 = b.x + b.w / 2, y2 = b.y, my = (y1 + y2) / 2;
        const d = y2 > y1 ? `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}` : `M${x1},${a.y} C${x1},${a.y - 40} ${x2},${b.y + NH + 40} ${x2},${b.y + NH}`;
        const cls = focus ? (l.source === focus ? " out" : l.target === focus ? " in" : "") : "";
        return html`<path key=${l.source + ">" + l.target} class=${"aedge" + cls} d=${d} stroke-width=${(1 + Math.log2(l.weight || 1) * 0.8).toFixed(2)}/>`; })}
      ${L.nodes.map(n => { const p = L.pos.get(n.id); const agentN = (n.agent_reads || 0) + (n.agent_edits || 0);
        return html`<g key=${n.id} class=${`an${n.test ? " test" : ""}${L.small(n) ? " cycle" : ""}${on && !on.has(n.id) ? " dim" : ""}`} tabindex="0" role="link"
          aria-label=${`${n.id}: ${plural(n.symbols, "symbol")}${n.commits ? `, ${plural(n.commits, "commit")}` : ""}${agentN ? `, ${agentN} agent reads and edits` : ""}. Open impact.`}
          onMouseEnter=${() => setFocus(n.id)} onMouseLeave=${() => setFocus(null)} onFocus=${() => setFocus(n.id)} onBlur=${() => setFocus(null)}
          onClick=${() => open(n.id)} onKeyDown=${e => { if (e.key === "Enter") open(n.id); }}>
          <title>${n.id}: ${n.symbols} symbols${n.commits ? `, ${n.commits} commits` : ""}${agentN ? `, ${agentN} agent reads/edits` : ""}${(n.cycle || []).length ? `, import cycle with ${n.cycle.join(", ")}` : ""}</title>
          <rect x=${p.x} y=${p.y} width=${p.w} height=${NH} rx="7"/><text x=${p.x + 15} y=${p.y + 22}>${L.label(n)}</text>
          ${agentN ? html`<text class="dot" x=${p.x + p.w - 8} y=${p.y - 5} text-anchor="end" style="fill:var(--agent)">● ${agentN}</text>` : null}
          ${n.fixes ? html`<text class="dot" x=${p.x + 8} y=${p.y - 5} style="fill:var(--risk)">● ${n.fixes}</text>` : null}
        </g>`; })}
    </svg>
    <div class="keyline" style="padding:10px 22px 14px">
      <span><i class="line" style="border-color:var(--code)"></i>What it uses</span><span><i class="line" style="border-color:var(--agent)"></i>What uses it</span>
      <span style="color:var(--agent)">● agent reads and edits</span><span style="color:var(--risk)">● fix or revert commits</span>
      <span><i style="border-color:var(--memory)"></i>Tests</span>${L.cycles ? html`<span><i style="border-color:var(--risk);border-style:dashed"></i>Import cycle</span>` : null}
    </div>
  </div>
  ${L.hidden ? html`<p class="sub" style="margin-top:12px">Showing the ${L.nodes.length} most connected ${g.level === "folder" ? "folders" : "files"}; ${L.hidden} smaller ones are left out to keep the map readable.</p>` : null}
  ${L.loose.length ? html`<p class="sub loose" style="margin-top:8px">Not connected to other code: ${L.loose.slice(0, 30).map((n, i) => html`${i ? ", " : ""}<span class="path">${n.id}</span>`)}${L.loose.length > 30 ? ` and ${L.loose.length - 30} more` : ""}</p>` : null}`;
}

const MAXN = 90, SMALL_CYCLE = 8;
function layout(g, MAXW = 1100) {
  const deg = new Map();
  g.links.forEach(l => { deg.set(l.source, (deg.get(l.source) || 0) + 1); deg.set(l.target, (deg.get(l.target) || 0) + 1); });
  const linkedAll = g.nodes.filter(n => deg.has(n.id));
  const loose = g.nodes.filter(n => !deg.has(n.id));
  if (!linkedAll.length) return null;
  // Keep the map readable: the most connected, largest files or folders, at most MAXN of them.
  const nodes = linkedAll.length > MAXN ? [...linkedAll].sort((a, b) => (deg.get(b.id) * 3 + Math.log2(1 + (b.symbols || 0))) - (deg.get(a.id) * 3 + Math.log2(1 + (a.symbols || 0)))).slice(0, MAXN) : linkedAll;
  const hidden = linkedAll.length - nodes.length;
  const small = n => (n.cycle || []).length > 0 && (n.cycle || []).length <= SMALL_CYCLE;
  const bigCycle = Math.max(0, ...nodes.map(n => (n.cycle || []).length));
  const segs = g.level === "folder" ? 2 : 1;
  const short = (n, k) => { const parts = n.id.split("/"); return parts.slice(-k).join("/") + (g.level === "folder" ? "/" : ""); };
  const count = new Map(); nodes.forEach(n => { const b = short(n, segs); count.set(b, (count.get(b) || 0) + 1); });
  const label = n => count.get(short(n, segs)) > 1 ? short(n, segs + 1) : short(n, segs);
  const w = n => Math.max(90, label(n).length * CH + 30);
  const layers = [...new Set(nodes.map(n => n.layer))].sort((a, b) => b - a);
  const rows = [];
  for (const l of layers) {
    const inLayer = nodes.filter(n => n.layer === l).sort((a, b) => (a.test - b.test) || dir(a.id).localeCompare(dir(b.id)) || a.id.localeCompare(b.id));
    let row = [], rw = 0, first = true;
    for (const n of inLayer) {
      if (row.length && rw + w(n) + 14 > MAXW) { rows.push({ nodes: row, layer: l, first }); row = []; rw = 0; first = false; }
      row.push(n); rw += w(n) + 14;
    }
    if (row.length) rows.push({ nodes: row, layer: l, first });
  }
  const rowW = r => r.nodes.reduce((a, n) => a + w(n), 0) + (r.nodes.length - 1) * 14;
  const width = Math.max(760, ...rows.map(rowW)) + PADX + 40;
  const RH2 = 64;
  let y = TOP;
  const pos = new Map();
  rows.forEach((r, ri) => {
    if (ri && r.first) y += RH - RH2;
    r.y = y;
    let x = PADX + (width - PADX - 40 - rowW(r)) / 2;
    r.nodes.forEach(n => { pos.set(n.id, { x, y, w: w(n) }); x += w(n) + 14; });
    y += RH2;
  });
  const height = y + 6;
  return { nodes, loose, hidden, rows, pos, width, height, label, small, bigCycle, cycles: nodes.some(small) };
}
