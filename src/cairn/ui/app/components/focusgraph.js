// Neighbourhood: one node, file or area and what surrounds it, nearest first and never more than the cap.
// It always says "showing N of M" and offers more, so the picture stays readable whatever the repository's size.
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Load, Empty, Tag } from "./ui.js";
import { FindBox, FIND_STYLE } from "./browse.js";
import { useFetch } from "../state.js";
import { api } from "../api.js";
import { link } from "../router.js";
import { fmt, plural, base } from "../lib/format.js";
import { loadCytoscape, themeColors, onThemeChange } from "../lib/cyto.js";

const STEP = 100, MAX = 400;
const STYLE = `
.fg .fg-bar { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center; margin: 0 0 10px; }
.fg .fg-count { font-size: 13.5px; color: var(--ink); }
.fg .fg-count b { font-weight: 700; }
.fg .fg-stage { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 14px; }
.fg .fg-cy { height: 560px; border: 1px solid var(--rule); border-radius: 10px; background: var(--sheet); }
.fg .fg-side { border: 1px solid var(--rule); border-radius: 10px; background: var(--raised); padding: 12px 14px; font-size: 13px; align-self: start; }
.fg .fg-side h3 { margin: 0 0 6px; font: 700 14px var(--mono); overflow-wrap: anywhere; }
.fg .fg-key { display: flex; gap: 14px; flex-wrap: wrap; margin: 8px 0 0; font-size: 12.5px; color: var(--muted); }
.fg .fg-key i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; }
@media (max-width: 900px) { .fg .fg-stage { grid-template-columns: 1fr; } }
`;
const HOP_KEY = ["The centre", "Directly linked", "Two steps away", "Three steps away"];

export function FocusGraph({ pid, node, file, depth, limit, nav }) {
  const target = node || file || "";
  const res = useFetch(() => target ? api.p(pid).focus(target, depth, limit) : Promise.resolve(null), [pid, target, depth, limit]);
  const areas = useFetch(() => target ? Promise.resolve(null) : api.p(pid).graphData("communities", 12), [pid, target]);
  const go = patch => nav({ view: "focus", node, file, depth, limit, ...patch });
  const pickSymbol = s => nav({ view: "focus", node: s.id, file: "", depth: 2, limit: 150 });
  return html`<div class="fg"><style>${STYLE}${FIND_STYLE}</style>
    <div class="fg-bar bw">
      <${FindBox} pid=${pid} placeholder="Start from a symbol, file or folder  ⌘K" onSymbol=${pickSymbol}
        onFile=${f => nav({ view: "focus", node: "", file: f, depth: 1, limit: 150 })} onFolder=${p => nav({ view: "browse", path: p })}/>
    </div>
    ${!target ? html`<${Empty} title="Pick somewhere to start"><p>This view draws one thing and its surroundings, never the whole repository. Search for a symbol or file above, or start from an area:</p>
        <${Load} res=${areas} rows="2">${d => html`<div class="row tight" style="flex-wrap:wrap;gap:6px">${(d?.nodes || []).slice(0, 12).map(a => html`<button key=${a.id} class="btn sm" onClick=${() => nav({ view: "focus", node: String(a.id), limit: 150 })}>${a.label}</button>`)}</div>`}</${Load}>
      </${Empty}>`
      : html`<${Load} res=${res} rows="8">${d => d ? html`<${Stage} d=${d} pid=${pid} depth=${depth} limit=${limit} go=${go} nav=${nav}/>`
        : html`<${Empty} title="Not in the map"><p>Nothing in the map matches ${target}. Search again above.</p></${Empty}>`}</${Load}>`}
  </div>`;
}

function Stage({ d, pid, depth, limit, go, nav }) {
  const el = useRef(null), cy = useRef(null);
  const [sel, setSel] = useState(null);
  const [err, setErr] = useState(null);
  const [tick, setTick] = useState(0);
  useEffect(() => onThemeChange(() => setTick(t => t + 1)), []);
  const centre = d.nodes.find(n => n.id === d.center);
  useEffect(() => {
    let dead = false;
    setSel(null);
    loadCytoscape().then(cytoscape => {
      if (dead || !el.current) return;
      const V = themeColors();
      cy.current?.destroy();
      const hopColor = h => (h === 0 ? V.agent : h === 1 ? V.code : h === 2 ? V.c[2] || V.other : V.other);
      const c = cy.current = cytoscape({
        container: el.current, wheelSensitivity: 0.3, minZoom: 0.2, maxZoom: 3,
        elements: [...d.nodes.map(n => ({ data: { id: n.id, label: n.label, short: n.label.length > 26 ? n.label.slice(0, 25) + "…" : n.label, hop: n.hop,
            size: n.hop === 0 ? 30 : 12 + Math.min(14, Math.sqrt(n.degree || 1) * 2.4), color: hopColor(n.hop) } })),
          ...d.links.map((l, i) => ({ data: { id: "e" + i, source: l.source, target: l.target } }))],
        style: [
          { selector: "node", style: { "background-color": "data(color)", width: "data(size)", height: "data(size)", label: "data(short)", "font-size": 10.5, "font-weight": "bold", color: V.ink,
            "text-outline-color": V.paper, "text-outline-width": 3, "text-valign": "bottom", "text-margin-y": 3, "overlay-opacity": 0 } },
          { selector: "node[hop > 1]", style: { label: "" } },
          { selector: "edge", style: { width: 1, "line-color": V.rule, opacity: 0.7, "curve-style": "haystack", "haystack-radius": 0 } },
          { selector: "node:selected", style: { "border-width": 3, "border-color": V.ink, label: "data(label)", "z-index": 20 } },
        ],
        layout: { name: "fcose", animate: false, quality: "default", randomize: true, nodeRepulsion: 9000, idealEdgeLength: 70, fit: true, padding: 24 },
      });
      c.on("tap", "node", e => setSel(d.nodes.find(n => n.id === e.target.id())));
      c.on("tap", e => { if (e.target === c) setSel(null); });
    }).catch(e => !dead && setErr(e));
    return () => { dead = true; cy.current?.destroy(); cy.current = null; };
  }, [d, tick]);
  const more = d.shown < d.total && limit < MAX;
  const hops = Object.entries(d.by_hop || {}).filter(([h]) => h !== "0");
  return html`<div>
    <div class="fg-bar">
      <span class="fg-count">${d.shown < d.total ? html`Showing <b>${fmt(d.shown)}</b> of <b>${fmt(d.total)}</b>` : html`Showing all <b>${fmt(d.total)}</b>`}
        ${centre ? html` around <b>${centre.label}</b>` : d.file ? html` around the code in <b>${base(d.file)}</b>` : html` of this area, best-connected first`} · ${d.center || d.file ? `${d.depth} step${d.depth > 1 ? "s" : ""} out` : ""}</span>
      ${d.center || d.file ? html`<div class="seg" role="tablist" aria-label="How far out">
        ${[1, 2, 3].map(n => html`<button role="tab" aria-selected=${depth === n} onClick=${() => go({ depth: n, limit: 150 })}>${n} ${n === 1 ? "step" : "steps"}</button>`)}</div>` : null}
      ${more ? html`<button class="btn sm" onClick=${() => go({ limit: Math.min(MAX, limit + STEP) })}>Show ${Math.min(STEP, d.total - d.shown)} more</button>` : null}
    </div>
    <div class="fg-stage">
      ${err ? html`<${Empty} title="The graph library did not load"><p>${err.message}</p></${Empty}>` : html`<div class="fg-cy" ref=${el} role="img" aria-label=${"Neighbourhood of " + (centre?.label || d.file || "the area") + ", " + d.shown + " nodes"}></div>`}
      <aside class="fg-side">${sel ? html`<div><h3>${sel.label}</h3>
          <div class="row tight" style="margin:0 0 8px"><${Tag} tone="code">${sel.kind}</${Tag}><span class="sub">${plural(sel.degree, "link")}</span></div>
          ${sel.file ? html`<a class="path small" href=${link.impact(pid, sel.file)}>${sel.file}${sel.location ? ":" + String(sel.location).replace(/^L/, "") : ""}</a>` : null}
          <div class="row tight" style="margin:10px 0 0;gap:6px;flex-wrap:wrap">
            ${sel.id !== d.center ? html`<button class="btn sm primary" onClick=${() => nav({ view: "focus", node: sel.id, file: "", depth: 2, limit: 150 })}>Centre here</button>` : null}
            ${sel.file ? html`<a class="btn sm" href=${link.impact(pid, sel.file)}>Impact</a>` : null}
            ${sel.file ? html`<button class="btn sm" onClick=${() => nav({ view: "browse", path: sel.file.includes("/") ? sel.file.slice(0, sel.file.lastIndexOf("/")) : "", mark: sel.file })}>Find in Browse</button>` : null}
          </div></div>`
        : html`<div><p style="margin:0 0 8px">Click a node to see what it is. <b>Centre here</b> redraws the picture around it.</p>
          <p class="sub" style="margin:0">${hops.map(([h, n]) => `${fmt(n)} ${HOP_KEY[h].toLowerCase()}`).join(" · ")}</p></div>`}
        <div class="fg-key">${[0, 1, 2].filter(h => d.by_hop?.[h]).map(h => html`<span key=${h}><i style=${"background:" + (h === 0 ? "var(--agent)" : h === 1 ? "var(--code)" : "var(--c2)")}></i>${HOP_KEY[h]}</span>`)}</div>
      </aside>
    </div>
  </div>`;
}
