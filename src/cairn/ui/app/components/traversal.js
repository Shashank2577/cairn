// The dependency walk, drawn as columns: the changed code, what uses it directly, and what is two
// steps away. One box per file; line width grows with the number of links between two files.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html } from "./ui.js";
import { base, dir, fileKind, plural } from "../lib/format.js";

const PAD = 22, H = 46, VG = 10, TOP = 12, MAXROWS = 18;

export function Traversal({ im, depth, onSelect, selected }) {
  const host = useRef(null);
  const [width, setWidth] = useState(900);
  const [hover, setHover] = useState(null);
  useEffect(() => {
    const el = host.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const L = useMemo(() => layout(im, depth, width), [im, depth, width]);
  const focus = selected || hover;
  const linked = useMemo(() => {
    if (!focus) return null;
    const s = new Set([focus]);
    for (const e of L.edges) if (e.a === focus || e.b === focus) { s.add(e.a); s.add(e.b); }
    return s;
  }, [focus, L]);

  const keyNav = (e, n) => {
    const col = L.cols[n.col], i = col.indexOf(n);
    let t = null;
    if (e.key === "ArrowDown") t = col[i + 1];
    else if (e.key === "ArrowUp") t = col[i - 1];
    else if (e.key === "ArrowRight") t = L.cols[n.col + 1]?.[0];
    else if (e.key === "ArrowLeft") t = n.col > 1 ? L.cols[n.col - 1]?.[0] : null;
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(n.file === selected ? null : n); return; }
    else if (e.key === "Escape") { onSelect(null); return; }
    if (t && !t.more) { e.preventDefault(); host.current.querySelector(`[data-file="${CSS.escape(t.file)}"]`)?.focus(); }
  };

  const heads = ["Changed", "Uses it directly", "Two steps away"].slice(0, depth + 1);
  const tr = (s, n) => s.length > n ? s.slice(0, n - 1) + "…" : s;
  const T = L.target;
  return html`<div class=${"diagram" + (focus ? " focus" : "")} ref=${host}>
    <div class="cols" style=${{ gridTemplateColumns: `repeat(${depth + 1}, ${L.W}px)`, columnGap: L.GAP + "px", paddingLeft: PAD + "px" }}>
      ${heads.map((h, c) => html`<span key=${c}>${h}${c ? html` <span class="num">(${L.counts[c] || 0})</span>` : null}</span>`)}</div>
    <svg width=${L.width} height=${L.height} viewBox=${`0 0 ${L.width} ${L.height}`} role="group" aria-label=${`Dependency walk from ${im.label}`} key=${im.label + depth}>
      ${L.edges.map((e, i) => html`<path key=${e.a + ">" + e.b} class=${"edge draw" + (linked && linked.has(e.a) && linked.has(e.b) && (e.a === focus || e.b === focus) ? " on" : "")}
        d=${e.d} stroke-width=${e.sw} style=${{ "--len": e.len, "--delay": e.delay + "s" }}/>`)}
      <g class="tn target"><rect x=${T.x} y=${T.y} width=${L.W} height=${H} rx="8"/>
        <text class="nm" x=${T.x + 14} y=${T.y + 20}>${tr(base(im.label || ""), L.chars)}</text>
        <text class="dir" x=${T.x + 14} y=${T.y + 37}>${tr((im.files || []).length === 1 && im.label === im.files[0] ? dir(im.label) || "./" : (im.files || []).join(", "), L.chars + 2)}</text></g>
      ${L.cols.slice(1).flat().map(n => n.more
        ? html`<g class="tn more" key=${"more" + n.col}><rect x=${n.x} y=${n.y} width=${L.W} height=${H} rx="8"/><text class="ct" x=${n.x + 14} y=${n.y + 28}>and ${plural(n.more, "more file")}</text></g>`
        : html`<g key=${n.file} class=${`tn ${n.kind}${n.inferredOnly ? " inferred" : ""}${linked && !linked.has(n.file) ? " dim" : ""}${n.file === selected ? " sel" : ""}`}
            data-file=${n.file} tabindex="0" role="button" aria-pressed=${n.file === selected}
            aria-label=${`${n.file}: ${plural(n.entries.length, "symbol")} depend on the change${n.depth === 1 ? " directly" : ", two steps away"}`}
            onMouseEnter=${() => setHover(n.file)} onMouseLeave=${() => setHover(null)} onFocus=${() => setHover(n.file)} onBlur=${() => setHover(null)}
            onClick=${() => onSelect(n.file === selected ? null : n)} onKeyDown=${e => keyNav(e, n)}>
            <rect x=${n.x} y=${n.y} width=${L.W} height=${H} rx="8"/>
            <text class="nm" x=${n.x + 14} y=${n.y + 20}>${tr(base(n.file), L.chars)}</text>
            <text class="dir" x=${n.x + 14} y=${n.y + 37}>${tr(dir(n.file) || "./", L.chars - 2)}</text>
            <text class="ct num" x=${n.x + L.W - 12} y=${n.y + 20} text-anchor="end">${n.entries.length}</text>
            ${n.kind !== "code" ? html`<text class="mark" x=${n.x + L.W - 12} y=${n.y + 37} text-anchor="end">${n.kind}</text>` : null}
          </g>`)}
    </svg>
    ${!L.count ? html`<p class="sub" style="padding:0 22px 14px;margin:0">Nothing else in the map depends on this. It is safe to change in isolation.</p>` : null}
    <div class="keyline" style="padding:10px 22px 14px">
      <span>Line width: links between two files</span><span>Number: symbols in that file that depend on the change</span>
      <span><i style="border-style:dashed"></i>Only inferred links</span><span><i style="border-color:var(--memory)"></i>Test</span><span><i style="border-color:var(--spec)"></i>Doc or spec</span>
    </div>
  </div>`;
}

function layout(im, depth, hostW) {
  const targetFiles = new Set(im.files || []);
  const nodes = new Map();
  for (const e of im.traversal || []) {
    if (!e.file || targetFiles.has(e.file)) continue;
    let n = nodes.get(e.file);
    if (!n) { n = { file: e.file, depth: e.depth, entries: [], inferred: 0, kind: fileKind(e.file) }; nodes.set(e.file, n); }
    n.depth = Math.min(n.depth, e.depth); n.entries.push(e);
    if (e.provenance && e.provenance !== "EXTRACTED") n.inferred++;
  }
  for (const n of nodes.values()) n.inferredOnly = n.inferred === n.entries.length;
  const counts = [1, 0, 0];
  const edgesW = new Map();
  for (const e of im.traversal || []) {
    if (!e.file || targetFiles.has(e.file)) continue;
    const src = !e.via_file || targetFiles.has(e.via_file) ? "__target" : e.via_file;
    if (src === e.file) continue;
    const k = src + "\u0000" + e.file;
    edgesW.set(k, (edgesW.get(k) || 0) + 1);
  }
  const GAP = Math.max(48, Math.min(110, (hostW - 2 * PAD) * 0.09));
  const W = Math.round(Math.max(176, Math.min(260, (hostW - 2 * PAD - depth * GAP) / (depth + 1))));
  const chars = Math.floor((W - 44) / 8.1);
  const cols = [[], [], []];
  const order = n => [n.kind === "test" ? 2 : n.kind === "doc" ? 1 : 0, dir(n.file), -n.entries.length];
  for (const n of nodes.values()) { const c = Math.min(n.depth, depth); cols[c].push(n); counts[c]++; }
  for (const c of cols) c.sort((a, b) => { const x = order(a), y = order(b); for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1; return 0; });
  const shown = cols.map((c, ci) => c.length > MAXROWS ? [...c.slice(0, MAXROWS - 1), { more: c.length - MAXROWS + 1, col: ci }] : c);
  const rows = Math.max(1, ...shown.map(c => c.length));
  const height = TOP + rows * (H + VG) + 6;
  const x0 = c => PAD + c * (W + GAP);
  const pos = new Map();
  const target = { x: x0(0), y: TOP + Math.max(0, (Math.min(rows, shown[1].length || 1) * (H + VG) - VG) / 2 - H / 2) };
  pos.set("__target", target);
  shown.forEach((c, ci) => c.forEach((n, i) => { n.col = ci; n.x = x0(ci); n.y = TOP + i * (H + VG); if (!n.more) pos.set(n.file, n); }));
  const width = PAD * 2 + (depth + 1) * W + depth * GAP;
  const colOf = f => f === "__target" ? 0 : nodes.get(f)?.col ?? 0;
  let i = 0;
  const edges = [...edgesW].map(([k, w]) => { const [a, b] = k.split("\u0000"); return { a, b, w }; })
    .filter(e => pos.has(e.a) && pos.has(e.b) && colOf(e.a) < colOf(e.b)).sort((p, q) => colOf(p.b) - colOf(q.b))
    .map(e => {
      const p = pos.get(e.a), q = pos.get(e.b);
      const x1 = p.x + W, y1 = p.y + H / 2, x2 = q.x, y2 = q.y + H / 2, mx = (x1 + x2) / 2;
      return { ...e, d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, sw: (1.2 + Math.log2(e.w) * 1.3).toFixed(2),
        len: Math.round(Math.hypot(x2 - x1, y2 - y1) * 1.2 + 20), delay: ((colOf(e.b) - 1) * 0.45 + (i++ % 20) * 0.012).toFixed(3) };
    });
  return { cols: shown, edges, width, height, W, GAP, chars, target, counts, count: nodes.size };
}
