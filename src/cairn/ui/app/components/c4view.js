// One level of the system model, drawn by the diagram standard's own renderer (spec 002, T030).
//
// This component lays out nothing. The server runs the same layout and renderer `cairn docs export`
// writes to disk and hands back an SVG; we embed it and add the two things a file cannot have —
// hover/focus/click on an element, and an evidence panel beside it. One renderer means the picture on
// screen and the exported file cannot drift apart.
//
// Every element group carries `data-id`, so wiring interactivity is a DOM query, not a redraw.
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html } from "./ui.js";
import { plural } from "../lib/format.js";

const SVG_NS = "http://www.w3.org/2000/svg";

/** Fetch the renderer output and the description behind it. */
export function useDiagram(pid, level, scope) {
  const [state, setState] = useState({ svg: "", desc: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let dead = false;
    setState(s => ({ ...s, loading: true, error: null }));
    const q = new URLSearchParams({ level });
    if (scope) q.set("scope", scope);
    const url = `/api/p/${encodeURIComponent(pid)}/system/svg?${q}`;
    Promise.all([fetch(url).then(r => (r.ok ? r.text() : detailOf(r).then(m => Promise.reject(new Error(m))))),
                 fetch(`/api/p/${encodeURIComponent(pid)}/system/view?${q}`).then(r => r.json())])
      .then(([svg, desc]) => { if (!dead) setState({ svg, desc, error: null, loading: false }); })
      .catch(e => { if (!dead) setState({ svg: "", desc: null, error: e, loading: false }); });
    return () => { dead = true; };
  }, [pid, level, scope, tick]);
  const reload = useCallback(() => setTick(t => t + 1), []);
  return { ...state, reload };
}

/** The server says why there is nothing to draw (an empty model, or a view that would say nothing): show that. */
async function detailOf(r) {
  let said = "";
  try { said = (await r.json()).detail || ""; } catch (e) { /* not JSON */ }
  if (r.status === 404) return said ? said.charAt(0).toUpperCase() + said.slice(1) : "Nothing to draw at this level yet. Run a sync so the model is current.";
  return `The server answered ${r.status}.`;
}

export function SystemDiagram({ pid, level, scope }) {
  const { svg, desc, error, loading, reload } = useDiagram(pid, level, scope);
  const [sel, setSel] = useState(null);
  const [fit, setFit] = useState(true);
  const host = useRef(null);

  // Wire the embedded drawing once per render of it. Delegated, so it survives the SVG being replaced.
  useEffect(() => {
    const root = host.current;
    if (!root || !svg) return;
    const groups = [...root.querySelectorAll("g.node, g.boundary")];
    const on = (g, over, out) => {
      g.style.cursor = "pointer";
      g.setAttribute("tabindex", "0");
      g.setAttribute("role", "button");
      g.addEventListener("mouseenter", over);
      g.addEventListener("mouseleave", out);
      g.addEventListener("focus", over);
      g.addEventListener("blur", out);
      g.addEventListener("click", () => setSel(s => (s === g.dataset.id ? null : g.dataset.id)));
      g.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSel(s => (s === g.dataset.id ? null : g.dataset.id)); }
      });
    };
    const offs = [];
    for (const g of groups) {
      const id = g.dataset.id;
      on(g,
         () => g.style.filter = "drop-shadow(0 0 0 var(--dg-accent))",
         () => { g.style.filter = ""; });
      offs.push(g);
    }
    return () => offs.forEach(g => { g.style.cursor = ""; g.style.filter = ""; g.removeAttribute("tabindex"); });
  }, [svg]);

  const els = useMemo(() => Object.fromEntries((desc?.elements || []).map(e => [e.id, e])), [desc]);
  const rels = desc?.relationships || desc?.flow?.messages || [];
  const chosen = sel ? els[sel] : null;
  const linked = useMemo(() => {
    if (!sel) return [];
    const out = [], seen = new Set();
    for (const r of rels) {
      if (r.from !== sel && r.to !== sel) continue;
      const other = els[r.from === sel ? r.to : r.from];
      if (!other || seen.has(other.id)) continue;
      seen.add(other.id);
      out.push({ name: other.name, kind: other.kind || other.type, dir: r.from === sel ? "→" : "←",
                 label: [r.what, r.how && `[${r.how}]`].filter(Boolean).join(" ") });
    }
    return out;
  }, [sel, rels, els]);

  if (error) return html`<div class="sysdiag">
    <p class="err">${error.message}</p>
    <button class="btn sm" onClick=${reload}>Try again</button>
  </div>`;
  if (loading && !svg) return html`<div class="sysdiag"><${Skeleton} rows="4"/></div>`;

const figure = svg
    ? html`<div class=${"sysfig" + (loading ? " stale" : "")} dangerouslySetInnerHTML=${{ __html: svg }}/>
           ${loading ? html`<div class="sysbusy" role="status"><span class="spin" aria-hidden="true"></span>Redrawing…</div>` : null}`
    : html`<${Skeleton} rows="4"/>`;
  const aside = chosen
    ? html`<${Evidence} el=${chosen} rels=${linked}/>`
    : html`<p class="sub syshint">Select any element to see its evidence and what it connects to.
        The picture is drawn by the same renderer as <code>cairn docs export</code>.</p>`;

  return html`<div class="sysdiag">
    <div class="sysbar">
      <span class="sub">${loading ? "Drawing…" : "Fit to width, or read at actual size"}</span>
      <span class="spacer"></span>
      <button class="btn xs" aria-pressed=${!fit} onClick=${() => setFit(f => !f)}>
        ${fit ? "Actual size" : "Fit to width"}</button>
    </div>
    <div class=${"sysstage" + (fit ? " fit" : " natural")} ref=${host} aria-busy=${loading}>${figure}</div>
    ${aside}
  </div>`;
}

function Evidence({ el, rels }) {
  const ev = el.evidence || [];
  return html`<aside class="panel sys-ev" aria-label=${"Evidence for " + (el.name || el.id)}>
    <div class="row" style="justify-content:space-between;align-items:flex-start;gap:10px">
      <h3 class="insp-t">${el.name || el.id}</h3>
      <span class="tag ${el.provenance === "inferred" ? "agent" : "outline"}">${el.provenance || "extracted"}</span>
    </div>
    <p class="sub" style="margin:2px 0 10px">${[el.kind || el.type, el.tech].filter(Boolean).join(" · ")}</p>
    ${el.desc ? html`<p>${el.desc}</p>` : null}
    ${ev.length ? html`<h4>Evidence</h4><ul class="plain">${ev.map(r => html`<li><code class="path">${r}</code></li>`)}</ul>` : null}
    ${rels.length ? html`<h4>${plural(rels.length, "connection")}</h4>
      <ul class="plain">${rels.map(r => html`<li>
        <span class="sub">${r.dir}</span> <b>${r.name}</b>${r.label ? html` <span class="sub">${r.label}</span>` : null}
      </li>`)}</ul>` : null}
  </aside>`;
}

const Skeleton = ({ rows = 4 }) => html`<div class="skel" aria-busy="true" aria-label="Loading">
  ${Array.from({ length: rows }, (_, i) => html`<i key=${i}></i>`)}</div>`;

const STYLE = `
.sysdiag { display: grid; gap: 10px; }
@media (min-width: 1100px) { .sysdiag { grid-template-columns: minmax(0,1fr) 320px; align-items: start; } }
/* A wide diagram must scroll inside its own box, never widen the grid column it sits in. */
.sysdiag > * { min-width: 0; }
.sysbar { display: flex; align-items: center; gap: 10px; grid-column: 1 / -1; }
.sysbar .spacer { flex: 1; }
.sysstage { background: var(--paper); border: 1px solid var(--rule); border-radius: 12px; overflow: auto; padding: 4px; }
.sysstage.fit .sysfig svg { max-width: 100%; height: auto; }
.sysstage.natural .sysfig svg { max-width: none; }
.sysfig { position: relative; }
.sysfig.stale { opacity: .35; filter: saturate(.4); }
.sysfig svg { display: block; max-width: 100%; height: auto; }
.sysbusy { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; gap: 8px;
  font: 600 12.5px var(--ui); color: var(--muted); background: color-mix(in srgb, var(--paper) 55%, transparent); }
.sysbusy .spin { width: 13px; height: 13px; border: 2px solid var(--rule-strong); border-top-color: var(--agent);
  border-radius: 50%; animation: sysspin .7s linear infinite; }
@keyframes sysspin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .sysbusy .spin { animation: none; } }
.sysfig g.node:focus-visible, .sysfig g.boundary:focus-visible { outline: 2px solid var(--agent); outline-offset: 2px; }
.syshint { margin: 0; }
.sys-ev h4 { margin: 12px 0 4px; font: 700 11.5px var(--ui); color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
.sys-ev ul { margin: 0; padding: 0; list-style: none; font-size: 13px; }
.sys-ev li { padding: 2px 0; }
.sys-ev code { font-size: 12px; overflow-wrap: anywhere; }
`;
export { STYLE as systemDiagramStyle };