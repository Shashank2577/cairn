// Impact and why: what depends on a file or symbol, what an agent receives when it asks, and the
// reasons the code is the way it is.
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, Empty, Load, Tag, Meter, Skeleton, CopyButton } from "../components/ui.js";
import { Traversal } from "../components/traversal.js";
import { useNarration, ExplainButton, NarrationPanel } from "../components/narrate.js";
import { ScaleBar, Ratio } from "../components/scalebar.js";
import { useApp, useFetch, usePref } from "../state.js";
import { api } from "../api.js";
import { link, citeHref, go } from "../router.js";
import { ago, fmt, plural, rich, base, dir } from "../lib/format.js";

export function Impact() {
  const { route } = useApp();
  const target = route.rest[0];
  return target ? html`<${Report} target=${target} key=${target}/>` : html`<${Picker}/>`;
}

function Picker() {
  const { pid, ov } = useApp();
  const sav = useFetch(() => api.p(pid).savings(30), [pid]);
  const recent = [...new Map((sav.data?.recent || []).filter(r => r.target).map(r => [r.target, r])).values()].slice(0, 8);
  return html`<div class="page view-in">
    <div class="page-h"><div><h1>Impact</h1>
      <p class="lede">Pick a file or symbol to see everything that depends on it one and two steps away, why it is the way it is, and exactly what an agent receives when it asks.</p></div></div>
    <${TargetInput}/>
    <div class="impact-start">
      <section class="sec"><div class="sec-h"><div><h2>Most depended-on code</h2><p class="sub">Changes here reach the most other code.</p></div></div>
        ${ov?.hubs?.length ? html`<ul class="list pick">${ov.hubs.map(h => html`<li><a href=${link.impact(pid, h.file && base(h.file) === h.label ? h.file : "symbol:" + h.id)}>
          <code>${h.label}</code><span class="sub path">${h.file}</span><span class="spacer"></span><span class="sub num">${h.degree} links</span></a></li>`)}</ul>`
        : html`<p class="sub">Nothing mapped yet. Run a sync first.</p>`}</section>
      <section class="sec"><div class="sec-h"><div><h2>Asked recently</h2><p class="sub">Targets agents and people looked up.</p></div></div>
        ${recent.length ? html`<ul class="list pick">${recent.map(r => html`<li><a href=${link.impact(pid, r.target)}><code>${r.target}</code><span class="spacer"></span><span class="sub nowrap">${ago(r.ts)}</span></a></li>`)}</ul>`
        : html`<p class="sub">No impact or why answers served yet.</p>`}</section>
    </div>
  </div>`;
}

function TargetInput({ initial = "" }) {
  const { pid } = useApp();
  const [q, setQ] = useState(initial);
  const [hits, setHits] = useState([]);
  const [sel, setSel] = useState(0);
  const t = useRef(0);
  const run = v => {
    clearTimeout(t.current);
    if (v.trim().length < 2) { setHits([]); return; }
    t.current = setTimeout(() => api.p(pid).search(v.trim(), "file,symbol", 8).then(r => { setHits(r); setSel(0); }, () => setHits([])), 120);
  };
  const pick = h => { setHits([]); go(link.impact(pid, h ? (h.kind === "file" ? h.title : h.id) : q.trim())); };
  return html`<form class="target-input" onSubmit=${e => { e.preventDefault(); pick(hits[sel]); }} role="search">
    <${Icon} name="target" size="18"/>
    <input class="input" value=${q} placeholder="A file path such as src/cairn/store.py, or a symbol such as Brain" aria-label="File or symbol"
      role="combobox" aria-expanded=${!!hits.length} aria-controls="target-hits" autocomplete="off" spellcheck="false"
      onInput=${e => { setQ(e.target.value); run(e.target.value); }}
      onKeyDown=${e => { if (e.key === "ArrowDown" && hits.length) { e.preventDefault(); setSel((sel + 1) % hits.length); } if (e.key === "ArrowUp" && hits.length) { e.preventDefault(); setSel((sel - 1 + hits.length) % hits.length); } if (e.key === "Escape") setHits([]); }}/>
    <button class="btn primary" type="submit" disabled=${!q.trim()}>Show impact</button>
    ${hits.length ? html`<div class="pop hits" id="target-hits" role="listbox" style="top:calc(100% + 4px);left:0;right:0">
      ${hits.map((h, i) => html`<a href=${link.impact(pid, h.kind === "file" ? h.title : h.id)} role="option" aria-selected=${i === sel} onMouseEnter=${() => setSel(i)}
        onClick=${e => { e.preventDefault(); pick(h); }}><span class="t">${h.title}</span>${h.path && h.path !== h.title ? html`<span class="p">${h.path}</span>` : null}<span class="k">${h.kind === "file" ? "File" : "Symbol"}</span></a>`)}
    </div>` : null}
  </form>`;
}

const BUDGETS = [800, 1500, 3000];
function Report({ target }) {
  const { pid, route } = useApp();
  const depth = route.q.get("d") === "1" ? 1 : 2;
  const [budget, setBudget] = usePref("impact.budget", 1500);
  const im = useFetch(() => api.p(pid).impact(target, depth, budget), [pid, target, depth, budget]);
  const why = useFetch(() => api.p(pid).why(target), [pid, target]);
  const [sel, setSel] = useState(null);
  useEffect(() => setSel(null), [target, depth]);
  const nImpact = useNarration(pid), nWhy = useNarration(pid);
  const qs = d => link.impact(pid, target, { d: d === 2 ? undefined : 1 });

  if (im.error) return html`<div class="page"><${Load} res=${im} what="the impact report">${() => null}</${Load}></div>`;
  const d = im.data;
  if (!d) return html`<div class="page"><div class="page-h"><div><h1 class="mono-title">${target.replace(/^symbol:/, "")}</h1><p class="sub">Walking the map…</p></div></div><${Skeleton} rows="10" block/></div>`;
  if (!d.traversal) return html`<div class="page view-in"><div class="page-h"><div><h1>Impact</h1></div></div>
    <${TargetInput} initial=${target}/>
    <${Empty} title=${`Nothing in the map matches “${target}”`}><p>${(d.header || []).join(" ")} Try a file path such as <code>src/cairn/store.py</code>, or pick a symbol from the suggestions as you type.</p></${Empty}></div>`;

  const secs = d.sections || {};
  const acc = d.tokens || { used: 0, budget, sections: {} };
  const kept = Object.entries(acc.sections || {}).sort((a, b) => b[1].total - a[1].total);
  const W = why.data?.sections || {};
  const others = [
    ["Changes together", "var(--ink-2)", "Files that usually change with it", secs["Changes together"]],
    ["Historical warnings", "var(--risk)", "Past fixes and reverts here", secs["Historical warnings"]],
    ["Intent", "var(--spec)", "The spec tasks that own it", secs["Intent"]],
    ["Agent sessions", "var(--agent)", "What agents learned working on it", secs["Agent sessions"]],
    ["Memory", "var(--memory)", "What the team recorded about it", secs["Memory"]],
    ["Tests likely affected", "var(--memory)", "Tests that reach it", secs["Tests likely affected"]],
  ].filter(r => r[3]?.length);
  const whyRows = [
    ["Rationale", "var(--code)", "Comments that explain why", W["Rationale"]],
    ["Origin", "var(--ink-2)", "Commits that wrote it", W["Origin"]],
    ["Decisions", "var(--spec)", "Decisions recorded about it", W["Decisions"]],
  ].filter(r => r[3]?.length);
  const riskTone = { HIGH: "solid-risk", MEDIUM: "code", LOW: "memory" }[d.risk] || "";

  return html`<div class="page view-in impact">
    <div class="page-h"><div>
      <div class="row" style="gap:6px 14px;align-items:baseline"><h1 class="mono-title">${d.label || target}</h1><${Tag} tone=${riskTone}>${d.risk} risk</${Tag}></div>
      <p class="sub" style="margin:6px 0 0">${(d.reasons || []).join("; ") || "No dependents or warnings recorded"}${d.files?.length && d.files[0] !== d.label ? html` <span class="path">${d.files.join(", ")}</span>` : null}</p>
    </div></div>
    <div class="controls">
      <span class="sub">Walk</span>
      <span class="seg"><a href=${qs(1)} aria-current=${depth === 1}>1 step</a><a href=${qs(2)} aria-current=${depth === 2}>2 steps</a></span>
      <span class="sub">${plural(d.dependents || 0, "dependent symbol")} in ${plural((d.dependent_files || []).length, "file")}</span>
      <span class="spacer"></span>
      <${ExplainButton} n=${nImpact} kind="impact" subject=${target} label="Explain the impact" budget=${budget}/>
      <a class="btn sm" href=${mapHref(pid, d.target_nodes?.[0])}><${Icon} name="route" size="15"/>Show in the map</a>
    </div>
    <${NarrationPanel} n=${nImpact} title=${`What changing ${base(d.label || target)} means`}/>
    <div class="impact-split">
      <div class="impact-main">
        <${Traversal} im=${d} depth=${depth} selected=${sel?.file} onSelect=${setSel}/>
        ${sel ? html`<div class="detail" aria-live="polite">
          <div class="row" style="justify-content:space-between"><h3><code>${sel.file}</code></h3><button class="icon-btn sm" onClick=${() => setSel(null)} aria-label="Close"><${Icon} name="x" size="15"/></button></div>
          <p class="sub" style="margin:0">${plural(sel.entries.length, "symbol")} here ${sel.entries.length === 1 ? "depends" : "depend"} on the change, ${sel.depth === 1 ? "directly" : "through another file"}.</p>
          <ul>${sel.entries.slice(0, 14).map(e => html`<li><code>${e.label}</code> ${e.rel.replace(/_/g, " ")} <code>${e.via}</code>${e.location ? html` <span class="sub">line ${String(e.location).replace(/^L/, "")}</span>` : null}${e.provenance && e.provenance !== "EXTRACTED" ? html` <${Tag}>inferred</${Tag}>` : null}</li>`)}
            ${sel.entries.length > 14 ? html`<li class="sub">and ${sel.entries.length - 14} more</li>` : null}</ul>
          <a class="btn sm" href=${link.impact(pid, sel.file)}>See what depends on ${base(sel.file)}</a>
        </div>` : html`<p class="sub" style="margin:10px 2px 0">Hover a file to trace its links; click it, or press Enter on it, to see which symbols depend on the change.</p>`}
      </div>
      <aside class="receives">
        <div class="panel">
          <h2>What the agent receives</h2>
          <p class="sub">The pack <code>cairn_impact</code> returns for this target, cut to the budget below.</p>
          <div class="row" style="margin:10px 0 2px"><span class="sub">Budget</span>
            <span class="seg" role="radiogroup" aria-label="Token budget">${BUDGETS.map(b => html`<button role="radio" aria-checked=${budget === b} aria-pressed=${budget === b} onClick=${() => setBudget(b)}>${fmt(b)}</button>`)}</span>
            ${im.loading ? html`<span class="sub">…</span>` : null}</div>
          <${ScaleBar} sent=${acc.used} source=${d.source?.tokens || 0} files=${d.source?.files || 0} sentLabel="Sent to the agent" sourceLabel="Source files it summarises" compact key=${budget}/>
          <${Ratio} sent=${acc.used} source=${d.source?.tokens || 0}/>
          <ul class="kept">${kept.map(([s, v]) => html`<li><span>${s}</span><span class="c num">${v.kept} of ${v.total}</span><${Meter} value=${v.kept} max=${v.total} label=${`${s}: ${v.kept} of ${v.total} kept`}/></li>`)}</ul>
          <details><summary>Show the exact text</summary>
            <div class="row" style="justify-content:flex-end;margin:8px 0 6px"><${CopyButton} text=${d.markdown || ""} label="Copy pack"/></div>
            <pre class="pack">${d.markdown}</pre></details>
        </div>
      </aside>
    </div>
    <section class="sec ruled" style="margin-top:34px" id="why"><div class="sec-h"><div><h2>Why it is this way</h2>
        <p class="sub">What <code>cairn_why</code> returns: the comments, commits and decisions behind this code${why.data?.tokens ? `, in ${fmt(why.data.tokens.used)} tokens` : ""}.</p></div>
        <${ExplainButton} n=${nWhy} kind="why" subject=${target} label="Explain why"/></div>
      <${NarrationPanel} n=${nWhy} title=${`Why ${base(d.label || target)} is this way`}/>
      ${why.error ? html`<p class="sub">${why.error.message}</p>` : !why.data ? html`<${Skeleton} rows="4"/>` : whyRows.length ? html`<${LayerLists} rows=${whyRows}/>`
        : html`<p class="sub">No comments, commits or decisions explain this code yet.</p>`}</section>
    ${others.length ? html`<section class="sec ruled"><div class="sec-h"><div><h2>What the other layers know</h2><p class="sub">Everything Cairn linked to this code beyond the dependency walk.</p></div></div>
      <${LayerLists} rows=${others}/></section>` : null}
  </div>`;
}

/** The map around the changed node. Target nodes arrive as ids or as node objects. */
function mapHref(pid, t) {
  const id = t && typeof t === "object" ? t.id : t;
  return id ? link.project(pid, "map", [], { scope: `around:${id}`, node: id }) : link.project(pid, "map");
}

function LayerLists({ rows }) {
  const { pid } = useApp();
  return html`<div class="layerlists">${rows.map(([name, color, what, items]) => html`<div class="ll">
    <h3><i style=${{ background: color }}></i>${what}<span class="sub num" style="font-weight:500">${items.length}</span></h3>
    <ul>${items.slice(0, 6).map(it => { const h = citeHref(pid, it.cite); return html`<li>${h ? html`<a href=${h}>${rich(it.text, 180)}</a>` : rich(it.text, 180)}</li>`; })}
      ${items.length > 6 ? html`<li class="sub">and ${items.length - 6} more</li>` : null}</ul></div>`)}</div>`;
}
