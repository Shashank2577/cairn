// Docs: the architecture documents Cairn writes with `cairn docs export` (context, containers, components,
// flows, code), computed live from the current system model and previewed here. Nothing calls a model unless
// the opt-in under "Narrate" is ticked; the evidence in every document links to Impact.
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, Empty, Load } from "../components/ui.js";
import { useApp, useFetch } from "../state.js";
import { api, env } from "../api.js";
import { link } from "../router.js";
import { Markdown } from "../lib/md.js";
import { fmt, plural } from "../lib/format.js";

const LEVELS = [["context", "Context"], ["containers", "Containers"], ["components", "Components"], ["flows", "Flows"], ["code", "Code"]];
const MD_SPECIAL = /([\\`\[\]|*_])/g;
const IMAGE = /^!\[[^\]]*\]\([^)]*\.svg\)\s*$/m;

/** Make every evidence reference in a document's Markdown a link to Impact for its file. */
function linkEvidence(md, evidence, pid) {
  const refs = evidence.filter(e => e.file).sort((a, b) => b.ref.length - a.ref.length);
  if (!refs.length) return md;
  const by = new Map(refs.map(e => [e.ref.replace(MD_SPECIAL, "\\$1"), e]));
  const re = new RegExp([...by.keys()].map(k => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "g");
  return md.replace(re, m => `[${m}](${link.impact(pid, by.get(m).file).replace(/\(/g, "%28").replace(/\)/g, "%29")})`);
}

function Freshness({ list, sync, startSync, canSync }) {
  const behind = list.behind;
  const running = sync.running;
  return html`<div class=${"docs-stale" + (behind ? " behind" : "")} role="status">
    <div class="grow">${behind
      ? html`<b>Model is from commit <code>${list.commit}</code>, ${plural(behind, "commit")} behind.</b> These documents describe that commit, not the code as it is now.`
      : behind === 0 ? html`<b>Up to date.</b> The model is from commit <code>${list.commit}</code>, the current commit.`
      : html`<b>Model is from commit <code>${list.commit}</code>.</b> Cairn could not tell how far the code has moved since.`}
      <span class="sub"> Computed from code, manifests and deploy files with 0 model calls.</span></div>
    ${behind ? html`<button class="btn sm primary" onClick=${() => startSync(false)} disabled=${running || !canSync} title=${canSync ? "Rebuild the model from the latest code" : "Your role can't start a sync"}>
      <${Icon} name="sync" size="15" class=${running ? "spinning" : ""}/><span>${running ? "Syncing…" : "Sync"}</span></button>` : null}
  </div>`;
}

/** A relative link between exported documents ("flows.md") opens that document in this view. */
const docLink = pid => href => /^[\w.-]+\.md$/.test(href) ? link.project(pid, "docs", [href.slice(0, -3)]) : null;

/** A document from the repository's docs/ folder: its styled page in a sandboxed frame, else its Markdown. */
function WrittenDoc({ pid, doc }) {
  const res = useFetch(() => doc.html ? Promise.resolve(null) : api.p(pid).docsFile(doc.name), [pid, doc.name]);
  if (doc.html) return html`<article class="docs-doc written"><iframe class="docs-frame" title=${doc.title} sandbox="allow-same-origin" src=${api.p(pid).docsFileUrl(doc.html)}></iframe></article>`;
  return html`<${Load} res=${res} rows="10" what="this document">${d => html`<article class="docs-doc"><${Markdown} src=${d.md} links=${docLink(pid)} linksKey=${pid}/></article>`}</${Load}>`;
}

function Doc({ pid, slug }) {
  const res = useFetch(() => api.p(pid).docsOne(slug), [pid, slug]);
  return html`<${Load} res=${res} rows="10" what="this document">${d => {
    const md = linkEvidence(d.md, d.evidence, pid);
    const m = IMAGE.exec(md);
    const before = m ? md.slice(0, m.index) : md, after = m ? md.slice(m.index + m[0].length) : "";
    return html`<article class="docs-doc">
      <${Markdown} src=${before} links=${docLink(pid)} linksKey=${pid}/>
      ${d.svg ? html`<div class="docs-diagram" role="img" aria-label=${"Diagram: " + d.title} dangerouslySetInnerHTML=${{ __html: d.svg }}></div>` : null}
      ${after ? html`<${Markdown} src=${after} links=${docLink(pid)} linksKey=${pid}/>` : null}
    </article>`;
  }}</${Load}>`;
}

export function Docs() {
  const { pid, route, can, toast, ov, sync, startSync } = useApp();
  const res = useFetch(() => api.p(pid).docs(), [pid]);
  const [narrate, setNarrate] = useState(false);
  const [busy, setBusy] = useState("");
  const wasRunning = useRef(false);
  // A finished sync changes the model: re-read the list (and with it the open document).
  const [rev, setRev] = useState(0);
  useEffect(() => {
    if (wasRunning.current && !sync.running) { res.reload(); setRev(r => r + 1); }
    wasRunning.current = sync.running;
  }, [sync.running]);

  const modelsOn = !!ov?.models?.available;
  const doExport = async () => {
    setBusy("export");
    try {
      const r = await api.p(pid).docsExport(narrate && modelsOn);
      toast({ title: `Wrote ${r.written} files to ${r.dir}`, tone: "memory", body: `Describing commit ${r.commit.slice(0, 12)}${r.narrated ? ", with narrative" : ", 0 model calls"}.`, ms: 6000 });
    } catch (e) { toast({ title: "Export failed", tone: "risk", body: e.message }); }
    setBusy("");
  };
  const doZip = async () => {
    setBusy("zip");
    try {
      const headers = { "content-type": "application/json", accept: "application/zip" };
      if (env.csrf) headers["X-CSRF-Token"] = env.csrf;
      const r = await fetch(api.p(pid).docsZipUrl, { method: "POST", headers, credentials: "same-origin", body: JSON.stringify({ narrate: narrate && modelsOn }) });
      if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch (e) { /* not JSON */ } throw new Error(d || `The server answered ${r.status}.`); }
      const url = URL.createObjectURL(await r.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: "architecture.zip" });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) { toast({ title: "Download failed", tone: "risk", body: e.message }); }
    setBusy("");
  };

  return html`<div class="page view-in docs-page">
    <div class="page-h"><div><h1>Docs</h1>
      <p class="lede">The documentation in this repository's <code>docs/</code> folder, then the architecture diagrams Cairn computes from the system model, each with the evidence behind every claim. Nothing calls a model unless you ask.</p></div></div>
    <${Load} res=${res} rows="8" what="the documents">${list => {
      const written = list.written || [];
      const wname = route.rest[0] === "file" ? route.rest[1] : !route.rest[0] && written.length ? written[0].name : null;
      const wdoc = written.find(d => d.name === wname);
      const slug = !wdoc && route.rest[0] && list.docs.some(d => d.slug === route.rest[0]) ? route.rest[0] : list.docs[0]?.slug;
      if (!list.docs.length && !written.length) return html`<${Empty} title="Nothing to write yet">The system model is empty. Run a sync so Cairn can read the code, manifests and deploy files, then come back.</${Empty}>`;
      const groups = LEVELS.map(([k, label]) => [label, list.docs.filter(d => d.level === k)]).filter(([, ds]) => ds.length);
      return html`
        <${Freshness} list=${list} sync=${sync} startSync=${startSync} canSync=${can.sync}/>
        <div class="docs-actions">
          <button class="btn sm" onClick=${doZip} disabled=${!!busy}><${Icon} name="ext" size="15"/><span>${busy === "zip" ? "Building…" : "Download as zip"}</span></button>
          <button class="btn sm" onClick=${doExport} disabled=${!!busy || !can.write} title=${can.write ? "Write the same files into the repository" : "Your role can't write to the repository"}><${Icon} name="doc" size="15"/><span>${busy === "export" ? "Writing…" : `Export to ${list.export_dir}`}</span></button>
          <span class="sub">${plural(list.docs.length, "document")}${list.flow_entries > list.flow_shapes ? ` · ${fmt(list.flow_entries)} entry points condensed into ${plural(list.flow_shapes, "distinct flow")}` : ""}</span>
        </div>
        <div class="docs-narrate"><label><input type="checkbox" checked=${narrate && modelsOn} disabled=${!modelsOn} onChange=${e => setNarrate(e.target.checked)}/>
          <span><b>Narrate (opt-in, uses a model).</b> Add model-written names and short descriptions to the files you export or download, each labelled inferred. Capped at ${fmt(list.narrate_tokens)} tokens per export on the fast model, and recorded in the ledger.
          ${modelsOn ? " Off by default: without it nothing here calls a model." : " Unavailable: no model is set up (Settings)."}</span></label></div>
        <div class="docs" style="margin-top:16px">
          <nav class="docs-list" aria-label="Documents">
            <h4>Documentation</h4>
            ${written.length ? written.map(d => html`<a href=${link.project(pid, "docs", ["file", d.name])} aria-current=${wdoc?.name === d.name ? "page" : undefined}>${d.index ? "Overview" : d.title}</a>`)
              : html`<p class="sub" style="margin:0 10px 8px">Nothing in <code>docs/</code> yet. Documents written there by a documentation run show up here.</p>`}
            <h4 style="margin-top:18px">Computed from the model</h4>
            ${groups.map(([label, ds]) => html`<h4 class="sub2">${label}</h4>
            ${ds.map(d => html`<a href=${link.project(pid, "docs", [d.slug])} aria-current=${!wdoc && d.slug === slug ? "page" : undefined}>${d.title}</a>`)}`)}</nav>
          ${wdoc ? html`<${WrittenDoc} key=${wdoc.name} pid=${pid} doc=${wdoc}/>` : html`<${Doc} key=${slug + ":" + rev} pid=${pid} slug=${slug}/>`}
        </div>`;
    }}</${Load}>
  </div>`;
}
