// Browse: the repository one level at a time. Folders and files directly under a path, each with what makes
// it worth opening (size, best-connected symbols, who depends on it, what Cairn remembers about it). Only
// the current level is ever fetched or drawn, and never more than one page of it, so it opens instantly on
// any repository. A find box jumps straight to a folder, file or symbol (Cmd/Ctrl-K).
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, Load, Tag, Empty } from "./ui.js";
import { useFetch } from "../state.js";
import { api } from "../api.js";
import { link } from "../router.js";
import { fmt, plural } from "../lib/format.js";

export const FIND_STYLE = `
.bw .bw-find { position: relative; flex: 1 1 280px; max-width: 460px; }
.bw .bw-find input { width: 100%; }
.bw .bw-hits { position: absolute; z-index: 20; left: 0; right: 0; top: calc(100% + 4px); background: var(--raised); border: 1px solid var(--rule-strong); border-radius: 9px; box-shadow: 0 8px 24px rgba(0,0,0,.14); max-height: 360px; overflow: auto; padding: 4px; }
.bw .bw-hits h5 { margin: 6px 8px 2px; font: 700 11px var(--ui); color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
.bw .bw-hits button { display: flex; justify-content: space-between; gap: 12px; width: 100%; text-align: left; background: none; border: 0; padding: 5px 8px; border-radius: 6px; font: 500 13px var(--mono); color: var(--ink); cursor: pointer; }
.bw .bw-hits button:hover, .bw .bw-hits button:focus-visible { background: var(--faint); }
.bw .bw-hits .sub { color: var(--muted); font: 500 12px var(--ui); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`;
const STYLE = `
.bw .bw-bar { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center; margin: 0 0 10px; }
.bw .bw-crumbs { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 6px; font: 600 13.5px var(--mono); }
.bw .bw-crumbs a, .bw .bw-crumbs button { color: var(--agent); background: none; border: 0; padding: 0; font: inherit; cursor: pointer; }
.bw .bw-crumbs .sep { color: var(--muted); }
.bw .bw-list { border: 1px solid var(--rule); border-radius: 10px; background: var(--raised); overflow: hidden; }
.bw .bw-row { display: grid; grid-template-columns: minmax(150px, 0.9fr) minmax(0, 1.4fr) minmax(0, 1.5fr); gap: 6px 18px; align-items: center; padding: 9px 14px; border-top: 1px solid var(--rule); }
.bw .bw-row:first-child { border-top: 0; }
.bw .bw-row:hover { background: var(--faint); }
.bw .bw-name { display: flex; align-items: center; gap: 8px; min-width: 0; font: 650 13.5px var(--mono); }
.bw .bw-name button, .bw .bw-name a { color: var(--ink); background: none; border: 0; padding: 0; font: inherit; cursor: pointer; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-decoration: none; }
.bw .bw-name button:hover, .bw .bw-name a:hover { color: var(--agent); }
.bw .bw-meta { color: var(--muted); font-size: 12.5px; display: flex; flex-wrap: wrap; gap: 2px 12px; align-items: center; min-width: 0; }
.bw .bw-meta code { font: 500 12px var(--mono); color: var(--ink-2, var(--ink)); background: var(--faint); border-radius: 5px; padding: 1px 6px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bw .bw-tags { display: flex; flex-wrap: wrap; gap: 4px; justify-content: flex-end; }
.bw .bw-page { display: flex; gap: 10px; align-items: center; margin: 10px 0 0; color: var(--muted); font-size: 13px; }
.bw .hit-row { background: var(--faint); }
@media (max-width: 760px) { .bw .bw-row { grid-template-columns: 1fr; } .bw .bw-tags { justify-content: flex-start; } }
`;

/** The find box, shared by Browse and the neighbourhood view. Opens on Cmd/Ctrl-K while the page is up. */
export function FindBox({ pid, onFolder, onFile, onSymbol, placeholder = "Find a file, folder or symbol  ⌘K" }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState(null);
  const input = useRef(null);
  useEffect(() => {
    const key = e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); input.current?.focus(); input.current?.select(); } };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {
    if (q.trim().length < 2) { setHits(null); return; }
    let dead = false;
    const t = setTimeout(() => api.p(pid).find(q.trim()).then(r => { if (!dead) setHits(r); }, () => { if (!dead) setHits(null); }), 160);
    return () => { dead = true; clearTimeout(t); };
  }, [q, pid]);
  const done = fn => arg => { setQ(""); setHits(null); fn(arg); };
  const none = hits && !hits.folders.length && !hits.files.length && !hits.symbols.length;
  return html`<div class="bw-find"><input class="input" type="search" ref=${input} value=${q} placeholder=${placeholder} aria-label="Find a file, folder or symbol"
      onInput=${e => setQ(e.target.value)} onKeyDown=${e => { if (e.key === "Escape") { setQ(""); setHits(null); } }}/>
    ${hits ? html`<div class="bw-hits" role="listbox">
      ${none ? html`<p class="sub" style="margin:8px">Nothing in the map is named like that.</p>` : null}
      ${hits.folders.length ? html`<h5>Folders</h5>${hits.folders.map(f => html`<button key=${f.path} onClick=${() => done(onFolder)(f.path)}><span>${f.path}/</span></button>`)}` : null}
      ${hits.files.length ? html`<h5>Files</h5>${hits.files.map(f => html`<button key=${f.path} onClick=${() => done(onFile)(f.path)}><span>${f.path}</span></button>`)}` : null}
      ${hits.symbols.length ? html`<h5>Symbols</h5>${hits.symbols.map(s => html`<button key=${s.id} onClick=${() => done(onSymbol)(s)}><span>${s.label}</span><span class="sub">${s.file}</span></button>`)}` : null}
    </div>` : null}</div>`;
}

export function Browse({ pid, path, mark, sort, offset, nav }) {
  const res = useFetch(() => api.p(pid).browse(path, offset, sort), [pid, path, offset, sort]);
  const go = patch => nav({ path, mark: "", sort, offset: 0, ...patch });
  return html`<div class="bw"><style>${STYLE}${FIND_STYLE}</style>
    <div class="bw-bar">
      <nav class="bw-crumbs" aria-label="Where you are">
        <button onClick=${() => go({ path: "" })}>repository</button>
        ${(res.data?.crumbs || path.split("/").filter(Boolean).map((n, i, a) => ({ name: n, path: a.slice(0, i + 1).join("/") }))).map(c => html`<span key=${c.path}><span class="sep">/ </span><button onClick=${() => go({ path: c.path })}>${c.name}</button></span>`)}
      </nav>
      <${FindBox} pid=${pid} onFolder=${p => go({ path: p })} onFile=${f => go({ path: f.includes("/") ? f.slice(0, f.lastIndexOf("/")) : "", mark: f })}
        onSymbol=${s => nav({ view: "focus", node: s.id })}/>
      <div class="seg" role="tablist" aria-label="Sort">
        ${[["name", "By name"], ["risk", "Riskiest first"]].map(([id, label]) => html`<button role="tab" aria-selected=${sort === id} onClick=${() => go({ sort: id })}>${label}</button>`)}
      </div>
    </div>
    <${Load} res=${res} rows="8">${d => {
      if (!d.rows.length) return html`<${Empty} title="Nothing here"><p>This folder has no files in the map.</p></${Empty}>`;
      const last = Math.min(d.offset + d.rows.length, d.total);
      return html`<div>
        <div class="bw-list" role="list">${d.rows.map(r => html`<${Row} key=${r.path} r=${r} pid=${pid} mark=${mark} go=${go} nav=${nav}/>`)}</div>
        <div class="bw-page">
          <span>${d.total > d.rows.length ? `Rows ${fmt(d.offset + 1)}–${fmt(last)} of ${fmt(d.total)}` : plural(d.total, "item")} here · ${plural(d.files, "file")} in all</span>
          ${d.offset > 0 ? html`<button class="btn sm" onClick=${() => go({ offset: Math.max(0, d.offset - d.rows.length) })}>Previous</button>` : null}
          ${last < d.total ? html`<button class="btn sm" onClick=${() => go({ offset: last })}>Next ${fmt(Math.min(200, d.total - last))}</button>` : null}
        </div></div>`;
    }}</${Load}></div>`;
}

function Row({ r, pid, mark, go, nav }) {
  const folder = r.kind === "folder";
  const riskTone = r.risk === "high" ? "risk" : r.risk === "medium" ? "spec" : "";
  return html`<div class=${"bw-row" + (mark === r.path ? " hit-row" : "")} role="listitem">
    <div class="bw-name"><${Icon} name=${folder ? "folder" : "doc"} size="16"/>
      ${folder ? html`<button onClick=${() => go({ path: r.path })} title=${"Open " + r.path}>${r.name}/</button>`
        : html`<a href=${link.impact(pid, r.path)} title="What breaks if this changes">${r.name}</a>`}</div>
    <div class="bw-meta">
      <span>${folder ? plural(r.files, "file") + " · " : ""}${plural(r.symbols, "symbol")}</span>
      ${r.top.slice(0, 3).map(t => html`<code key=${t} title="Best-connected symbol">${t}</code>`)}
    </div>
    <div class="bw-tags">
      ${r.dependents ? html`<${Tag} tone=${riskTone} title="Other files that depend on this">${plural(r.dependents, "dependent")}</${Tag}>` : null}
      ${r.fixes ? html`<${Tag} tone="risk" title="Commits that fixed or reverted something here">${plural(r.fixes, "past fix", "past fixes")}</${Tag}>` : null}
      ${r.specs ? html`<${Tag} tone="spec" title="Spec tasks that own code here">${plural(r.specs, "spec task")}</${Tag}>` : null}
      ${r.memories ? html`<${Tag} tone="memory" title="Remembered decisions and gotchas that mention this">${plural(r.memories, "memory", "memories")}</${Tag}>` : null}
      ${r.commits ? html`<${Tag} title="Commits that touched this">${plural(r.commits, "commit")}</${Tag}>` : null}
      ${folder ? html`<a class="btn sm" href=${link.project(pid, "map", ["explore"], { scope: "file:" + r.path })}>Graph</a>` : html`<button class="btn sm" onClick=${() => nav({ view: "focus", node: "", file: r.path })}>Neighbours</button>`}
    </div>
  </div>`;
}
