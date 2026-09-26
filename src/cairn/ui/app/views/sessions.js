// Sessions: what coding agents did and learned here. A live feed of the observations a model writes
// from each session's tool calls, the summary at the end of each session, the prompts that started
// the work, and what the next session will be told at its start.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, Empty, Load, Tag, Skeleton, CopyButton, Meter, ConfirmButton, Avatar } from "../components/ui.js";
import { ScaleBar } from "../components/scalebar.js";
import { Markdown } from "../lib/md.js";
import { useApp, useFetch, useLive, useMedia } from "../state.js";
import { api } from "../api.js";
import { link, replace } from "../router.js";
import { ago, fmt, plural, rich, base, stamp, clock, day, approxTokens, clip, tsOf, repoPath, inRepo } from "../lib/format.js";

export const TYPES = [["discovery", "Discovery", "agent"], ["decision", "Decision", ""], ["feature", "Feature", "memory"], ["bugfix", "Bug fix", "risk"], ["change", "Change", "code"], ["refactor", "Refactor", "spec"]];
const TONE = Object.fromEntries(TYPES.map(t => [t[0], t[2]]));
const TLABEL = Object.fromEntries(TYPES.map(t => [t[0], t[1]]));
const ikey = it => `${it.kind}-${it.id}`;
/** Session titles are often the first prompt, pasted content and all: keep the words. */
export const cleanTitle = t => String(t || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
/** "Priya's machine" for sessions pushed from a teammate's machine; nothing for local ones. */
const possessive = n => `${n}${/s$/i.test(n) ? "'" : "'s"}`;
const machineOf = name => { const first = String(name || "").trim().split(/\s+/)[0]; return first ? `${possessive(first)} machine` : ""; };
const shortId = id => String(id || "").length > 12 ? String(id).slice(0, 8) : String(id || "");
const CONCEPT = { "how-it-works": "How it works", "why-it-exists": "Why it exists", "what-changed": "What changed", "problem-solution": "Problem and fix", gotcha: "Gotcha", pattern: "Pattern", "trade-off": "Trade-off" };

export function Sessions() {
  const { route } = useApp();
  const sid = route.rest[0];
  return sid ? html`<${SessionDetail} sid=${sid} key=${sid}/>` : html`<${Feed}/>`;
}

function Feed() {
  const { pid, route, ov, project } = useApp();
  const q = route.q;
  const types = (q.get("type") || "").split(",").filter(Boolean);
  const concept = q.get("concept") || "", file = q.get("file") || "", text = q.get("q") || "";
  const setF = patch => { const n = new URLSearchParams(q); for (const [k, v] of Object.entries(patch)) v ? n.set(k, v) : n.delete(k); const s = n.toString(); replace(link.project(pid, "sessions") + (s ? "?" + s : "")); };
  const stats = useFetch(() => api.p(pid).sessionStats(), [pid]);
  const status = useFetch(() => api.p(pid).sessionStatus().catch(() => null), [pid]);
  const ctx = useFetch(() => api.p(pid).sessionContext(), [pid]);
  const [pages, setPages] = useState({ items: [], next: null, loading: true, error: null });
  const [fresh, setFresh] = useState(new Set());
  const [paused, setPaused] = useState(false);
  const [queued, setQueued] = useState([]);
  const [hiddenNew, setHiddenNew] = useState(0);
  const filtered = types.length || concept || file || text;
  const load = async (cursor) => {
    setPages(p => ({ ...p, loading: true, error: null }));
    try {
      let items, next = null;
      if (text) { const r = await api.p(pid).sessionSearch({ q: text, type: types.join(",") || undefined, limit: 40 }); items = r.results; }
      else { const r = await api.p(pid).feed({ cursor, limit: 30, type: types.join(",") || undefined, concept: concept || undefined, file: file || undefined }); items = r.items; next = r.next_cursor; }
      setPages(p => ({ items: cursor ? [...p.items, ...items] : items, next, loading: false, error: null }));
    } catch (e) { setPages(p => ({ ...p, loading: false, error: e })); }
  };
  useEffect(() => { load(null); setHiddenNew(0); }, [pid, types.join(), concept, file, text]);
  const matches = it => (!types.length || types.includes(it.type || it.kind)) && (!concept || (it.concepts || []).includes(concept))
    && (!file || [...(it.files_read || []), ...(it.files_modified || [])].some(f => f.includes(file))) && !text;
  const arrive = it => {
    const k = ikey(it);
    setFresh(s => new Set([...s, k]));
    setPages(p => p.items.some(x => ikey(x) === k) ? p : { ...p, items: [it, ...p.items] });
    setTimeout(() => setFresh(s => { const n = new Set(s); n.delete(k); return n; }), 5000);
  };
  useLive(ev => {
    if (ev.type === "status") { status.setData(d => ({ ...(d || {}), ...(ev.status || ev) })); return; }
    const it = ev.type === "observation" ? { kind: "observation", ...ev.observation } : ev.type === "summary" ? { kind: "summary", ...ev.summary } : ev.type === "prompt" ? { kind: "prompt", ...(ev.prompt || {}) } : null;
    if (!it || it.id == null) return;
    stats.reload();
    if (ev.type === "summary") ctx.reload();
    if (!matches(it)) { setHiddenNew(n => n + 1); return; }
    if (paused) setQueued(qd => [it, ...qd]); else arrive(it);
  }, [paused, types.join(), concept, file, text]);
  const narrow = useMedia("(max-width: 1100px)");
  const st = stats.data;

  const waiting = st?.queue?.pending ?? status.data?.queueDepth ?? 0;
  if (st && !st.observations && !st.prompts && !waiting && !pages.items.length && !pages.loading) return html`<div class="page view-in"><div class="page-h"><div><h1>Sessions</h1></div></div>
    <${Empty} title=${ov?.capture?.on ? "No sessions recorded yet" : "Session recording is off"} tone="agent">
      <p>${ov?.capture?.on ? "Recording is on. After the next agent session in this repository, the observations, prompts and summary appear here as they are written." : "Cairn records what agents read, change and learn through their hooks, and writes observations with a model. Turn it on in this repository:"}</p>
      ${ov?.capture?.on ? null : html`<code>cairn init --agents claude</code>`}
    </${Empty}></div>`;

  return html`<div class="page view-in sessions">
    <div class="page-h"><div><h1>Sessions</h1>
      <p class="lede">What coding agents did and learned in this repository. A model turns each session's tool calls into observations as the agent works, and summarises the session when it ends. The next session starts with the most useful of them.</p></div></div>
    <${Processing} status=${status} stats=${st} onDone=${() => { stats.reload(); status.reload(); load(null); ctx.reload(); }}/>
    ${st ? html`<div class="sstats">
      <dl class="counts">
        <div><dt>Sessions</dt><dd class="num">${fmt(st.sessions)}</dd></div><div><dt>Observations</dt><dd class="num">${fmt(st.observations)}</dd></div>
        <div><dt>Summaries</dt><dd class="num">${fmt(st.summaries)}</dd></div><div><dt>Prompts</dt><dd class="num">${fmt(st.prompts)}</dd></div>
      </dl>
      <div class="econ"><${ScaleBar} sent=${st.tokens.read} source=${st.tokens.discovery} sentLabel="Reading the notes back" sourceLabel="Work it took to learn them" compact/>
        <p class="ratio" style="margin-top:6px">Recalling instead of rediscovering keeps <b>${fmt(st.tokens.saved)} tokens</b> out of future sessions.</p></div>
    </div>` : html`<${Skeleton} rows="3"/>`}
    <div class="sgrid">
      ${narrow ? html`<details class="gfold sfold"><summary>Filters${filtered ? " (on)" : ""}</summary><${Filters} st=${st} types=${types} concept=${concept} file=${file} text=${text} setF=${setF}/></details>`
        : html`<aside class="sfilters"><${Filters} st=${st} types=${types} concept=${concept} file=${file} text=${text} setF=${setF}/></aside>`}
      <section class="sfeed" aria-label="Session feed">
        <div class="row feedbar">
          <span class="livebadge"><span class=${"live" + (paused ? " off" : "")}></span>${paused ? "Paused" : "Live"}</span>
          <button class="btn xs ghost" onClick=${() => { if (paused) { queued.slice().reverse().forEach(arrive); setQueued([]); } setPaused(!paused); }}>${paused ? `Resume${queued.length ? ` and show ${queued.length} new` : ""}` : "Pause"}</button>
          <span class="sub">${text ? `Most relevant to “${text}”` : filtered ? "Filtered" : "Newest first"}</span>
          ${hiddenNew ? html`<button class="btn xs" onClick=${() => { setF({ type: null, concept: null, file: null, q: null }); }}>${plural(hiddenNew, "new item")} hidden by filters. Clear filters</button>` : null}
        </div>
        ${pages.error ? html`<p class="err">${pages.error.message}</p>` : null}
        ${!pages.items.length && pages.loading ? html`<${Skeleton} rows="8" block/>` : !pages.items.length ? html`<p class="sub">Nothing matches these filters.</p>` : html`
          <ol class="feedlist">${pages.items.map(it => html`<li key=${ikey(it)} class=${fresh.has(ikey(it)) ? "fresh" : ""}><${Item} it=${it} showSession onDeleted=${() => { setPages(p => ({ ...p, items: p.items.filter(x => ikey(x) !== ikey(it)) })); stats.reload(); }}/></li>`)}</ol>`}
        ${pages.next ? html`<button class="btn" style="margin-top:14px" disabled=${pages.loading} onClick=${() => load(pages.next)}>${pages.loading ? "Loading…" : "Load older"}</button>` : null}
      </section>
      <aside class="sside">
        <div class="panel ctxpanel">
          <div class="row" style="justify-content:space-between"><h3>What the next session starts with</h3></div>
          <p class="sub">Injected at the start of the next agent session in this repository${ctx.data ? `, about ${fmt(approxTokens(ctx.data.markdown))} tokens` : ""}.</p>
          ${ctx.data ? ctx.data.markdown ? html`<pre class="ctxdoc">${ctx.data.markdown}</pre><div class="row" style="margin-top:8px"><${CopyButton} text=${ctx.data.markdown} label="Copy"/></div>`
            : html`<p class="sub">Nothing yet. It fills in after the first observations.</p>` : html`<${Skeleton} rows="6"/>`}
        </div>
        <${SessionList}/>
        ${st?.top_files?.length ? html`<div class="panel"><h3>Files agents touch most</h3><p class="sub">Edits count double in the ranking. Blue is reads, brown is edits.</p>
          <ul class="heat">${(() => { const max = Math.max(1, ...st.top_files.map(f => f.reads + f.modifies)); return st.top_files.slice(0, 12).map(f => html`<li>
            ${inRepo(f.path, project?.root) ? html`<a class="path clip" href=${link.impact(pid, repoPath(f.path, project?.root))} title=${f.path}>${repoPath(f.path, project?.root)}</a>`
              : html`<span class="path clip muted" title=${`${f.path} (outside the repository)`}>${repoPath(f.path, project?.root)}</span>`}<span class="n num" title=${`${plural(f.reads, "read")}, ${plural(f.modifies, "edit")}`}>${f.reads + f.modifies}</span>
            <span class="b"><b style=${{ width: f.reads / max * 100 + "%" }}></b><s style=${{ width: f.modifies / max * 100 + "%" }}></s></span></li>`); })()}</ul></div>` : null}
      </aside>
    </div>
  </div>`;
}

function Filters({ st, types, concept, file, text, setF }) {
  const [q, setQ] = useState(text);
  const [fl, setFl] = useState(file);
  const t = useRef(0);
  useEffect(() => setQ(text), [text]);
  return html`<div class="filters-in">
    <label class="field"><span>Search</span><input class="input" type="search" value=${q} placeholder="Words from titles, narratives, facts" onInput=${e => { setQ(e.target.value); clearTimeout(t.current); t.current = setTimeout(() => setF({ q: e.target.value.trim() }), 300); }}/></label>
    <div class="fgroup"><h4>Type</h4><div class="chips">${TYPES.map(([k, l, tone]) => { const on = types.includes(k); return html`<button class="chip" aria-pressed=${on} onClick=${() => setF({ type: (on ? types.filter(x => x !== k) : [...types, k]).join(",") })}>
      <i style=${{ background: `var(--${tone || "ink"})` }}></i>${l}<span class="n">${st?.by_type?.[k] || 0}</span></button>`; })}
      <button class="chip" aria-pressed=${types.includes("summary")} onClick=${() => setF({ type: (types.includes("summary") ? types.filter(x => x !== "summary") : [...types, "summary"]).join(",") })}><i style="background:var(--agent)"></i>Summaries<span class="n">${st?.summaries || 0}</span></button>
      <button class="chip" aria-pressed=${types.includes("prompt")} onClick=${() => setF({ type: (types.includes("prompt") ? types.filter(x => x !== "prompt") : [...types, "prompt"]).join(",") })}><i style="background:var(--muted)"></i>Prompts<span class="n">${st?.prompts || 0}</span></button></div></div>
    <div class="fgroup"><h4>Concept</h4><div class="chips">${Object.entries(st?.by_concept || {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => html`<button class="chip" aria-pressed=${concept === k} onClick=${() => setF({ concept: concept === k ? null : k })}>${CONCEPT[k] || k}<span class="n">${n}</span></button>`)}</div></div>
    <label class="field fgroup"><span>File</span><input class="input mono" value=${fl} placeholder="Part of a path, e.g. store.py" onInput=${e => setFl(e.target.value)} onChange=${e => setF({ file: e.target.value.trim() })} onKeyDown=${e => { if (e.key === "Enter") setF({ file: e.target.value.trim() }); }}/></label>
    ${types.length || concept || file || text ? html`<button class="btn sm ghost" onClick=${() => setF({ type: null, concept: null, file: null, q: null })}>Clear filters</button>` : null}
  </div>`;
}

function SessionList() {
  const { pid } = useApp();
  const res = useFetch(() => api.p(pid).sessionList({ limit: 8 }).then(r => r.items || r).catch(() => null), [pid]);
  const feed = useFetch(() => res.data === null ? api.p(pid).feed({ limit: 60 }) : Promise.resolve(null), [pid, res.data === null]);
  const sessions = useMemo(() => {
    if (res.data) return res.data.map(s => ({ id: s.id, title: s.title, n: s.observations || 0, last: s.ended || s.started, status: s.status, pending: s.pending, pushedBy: s.pushed_by_name }));
    const m = new Map();
    for (const it of feed.data?.items || []) { const s = m.get(it.session_id) || { id: it.session_id, n: 0, last: it.ts, title: null }; s.n += it.kind === "observation" ? 1 : 0; s.last = Math.max(s.last, it.ts); if (it.kind === "prompt" && it.prompt_number === 1) s.title = it.text; if (it.kind === "summary") s.title = it.request; m.set(it.session_id, s); }
    return [...m.values()].sort((a, b) => b.last - a.last);
  }, [res.data, feed.data]);
  if (!sessions.length) return null;
  return html`<div class="panel"><h3>Sessions</h3><ul class="slist">${sessions.slice(0, 6).map(s => html`<li><a href=${link.project(pid, "sessions", [s.id])}>
    <b>${rich(cleanTitle(s.title) || s.id, 90)}</b><span class="sub">${plural(s.n, "observation")}${s.pushedBy ? html`, from <span title=${`Pushed to this server from ${possessive(s.pushedBy)} machine`}>${machineOf(s.pushedBy)}</span>` : ""}${s.status === "active" ? ", in progress" : s.last ? `, last ${ago(s.last)}` : ""}${s.pending ? `, ${fmt(s.pending)} events waiting` : ""}</span></a></li>`)}</ul></div>`;
}

/** Captured events wait in a queue until a model turns them into observations. Say so, and offer to process them. */
function Processing({ status, stats, onDone }) {
  const { pid, can, toast } = useApp();
  const settings = useFetch(() => api.p(pid).sessionSettings().catch(() => null), [pid]);
  const [busy, setBusy] = useState("");
  const st = status.data || {};
  const q = stats?.queue || {};
  const pending = q.pending ?? st.queueDepth ?? 0, failed = q.failed ?? st.failed ?? 0, processing = q.processing ?? 0;
  const h = st.health || {};
  const auto = settings.data?.settings ? settings.data.settings.worker_spawn !== false : null;
  if (!pending && !failed && !processing && !h.lastErrorMessage) return null;
  const run = async (label, fn, done) => {
    setBusy(label);
    try { const r = await fn(); toast({ title: done, body: r && typeof r === "object" && (r.processed != null || r.observations != null) ? `${fmt(r.processed ?? r.observations)} processed.` : "It runs in the background; new observations appear in the feed as they are written.", tone: "agent" }); onDone(); }
    catch (e) { toast({ title: `Couldn't ${label.toLowerCase()}`, body: e.message, tone: "risk" }); } finally { setBusy(""); }
  };
  const errAt = h.lastErrorAt ? h.lastErrorAt / (h.lastErrorAt > 1e12 ? 1000 : 1) : null;
  const quota = h.lastErrorKind === "quota_exhausted";
  return html`<div class=${"procbox" + (quota ? " warn" : "")} role="status">
    <div class="procmain">
      <h2>${fmt(pending + processing)} captured ${pending + processing === 1 ? "event is" : "events are"} waiting to become observations</h2>
      <p>${auto === false ? "Automatic processing is off for this project, so nothing uses your model quota until you start it." : st.workerRunning ? "The processor is running and works through them in order." : "They are processed in the background as the agent works."}
        ${failed ? html` <b class="err">${plural(failed, "event")} failed.</b>` : ""}</p>
      ${h.lastErrorMessage ? html`<p class="procerr"><${Icon} name="info" size="15"/><span>${quota ? "The model quota ran out" : "The last model call failed"}${errAt ? ` ${ago(errAt)}` : ""}${h.lastErrorProvider ? ` (${h.lastErrorProvider})` : ""}: ${h.lastErrorMessage.replace(/^model call failed:\s*/i, "")}</span></p>` : null}
    </div>
    ${can.write ? html`<div class="procact">
      <button class="btn primary sm" disabled=${!!busy || !(pending + processing)} onClick=${() => run("Process now", () => api.p(pid).processSessions(false), "Processing started")}>${busy === "Process now" ? "Starting…" : "Process now"}</button>
      <button class="btn sm" disabled=${!!busy || !(pending + processing)} onClick=${() => run("Process without a model", () => api.p(pid).processSessions(true), "Processed without a model")} title="Write simple observations from the tool calls themselves; uses no model quota">${busy === "Process without a model" ? "Working…" : "Without a model"}</button>
      ${failed ? html`<button class="btn sm" disabled=${!!busy} onClick=${() => run("Retry failed", () => api.p(pid).queueAction("retry"), "Failed events queued again")}>Retry failed</button>
        <${ConfirmButton} label="Clear failed" confirm="Drop failed events" onConfirm=${() => run("Clear failed", () => api.p(pid).queueAction("clear-failed"), "Failed events dropped")}/>` : null}
      ${pending ? html`<${ConfirmButton} label="Discard queue" confirm=${`Discard ${fmt(pending)} events`} onConfirm=${() => run("Discard queue", () => api.p(pid).queueAction("clear"), "Queue discarded")}/>` : null}
    </div>` : html`<p class="sub"><${Icon} name="lock" size="14"/> Members can start processing.</p>`}
  </div>`;
}

/** A file an agent read or changed: repository-relative, shortened when it lies outside, linked to its impact when it can be. */
function FileChip({ f, mod, full }) {
  const { pid, project } = useApp();
  const rel = repoPath(f, project?.root), label = full ? rel : (inRepo(f, project?.root) ? base(rel) : rel);
  const cls = "fchip" + (mod ? " mod" : "");
  const title = `${mod ? "Changed" : "Read"}: ${f}`;
  return inRepo(f, project?.root) ? html`<a class=${cls} href=${link.impact(pid, rel)} title=${title}>${label}</a>` : html`<span class=${cls + " outside"} title=${title}>${label}</span>`;
}

/** One feed item: observation, summary or prompt. */
export function Item({ it, showSession, open: startOpen, onDeleted }) {
  const { pid, can, toast } = useApp();
  const [open, setOpen] = useState(!!startOpen);
  const sess = showSession && it.session_id ? html`<a class="sub" href=${link.project(pid, "sessions", [it.session_id])} title=${it.session_id}>session ${shortId(it.session_id)}</a>` : null;
  const del = can.write && onDeleted ? html`<${ConfirmButton} label="" ariaLabel=${`Delete this ${it.kind}`} icon="trash" confirm="Delete" class="icon-btn sm danger-icon" onConfirm=${async () => {
    try { await api.p(pid).deleteSessionItem(it.kind, it.id); toast({ title: `${it.kind === "observation" ? "Observation" : it.kind === "summary" ? "Summary" : "Prompt"} deleted`, body: "Future sessions won't be told about it." }); onDeleted(); }
    catch (e) { toast({ title: "Couldn't delete it", body: e.message, tone: "risk" }); } }}/>` : null;
  if (it.kind === "prompt") return html`<article class="obs prompt">
    <div class="ohead"><span class="tag outline">prompt ${it.prompt_number || ""}</span><span class="spacer"></span>${sess}<time class="sub num" title=${stamp(it.ts)}>${ago(it.ts)}</time>${del}</div>
    <p class="ptext">“${rich(cleanTitle(it.text), 400)}”</p></article>`;
  if (it.kind === "summary") return html`<article class="obs summary">
    <div class="ohead"><${Tag} tone="agent">session summary</${Tag}><span class="spacer"></span>${sess}<time class="sub num" title=${stamp(it.ts)}>${ago(it.ts)}</time>${del}</div>
    <h3>${rich(it.request)}</h3>
    <dl class="sumgrid">
      ${[["Investigated", it.investigated], ["Learned", it.learned], ["Completed", it.completed], ["Next steps", it.next_steps]].filter(r => r[1]).map(([k, v]) => html`<div><dt>${k}</dt><dd>${rich(v)}</dd></div>`)}
    </dl>
    ${(it.files_edited || []).length ? html`<div class="chips" style="margin-top:8px">${it.files_edited.map(f => html`<${FileChip} f=${f} mod full/>`)}</div>` : null}
  </article>`;
  const id = "obs-" + it.id;
  return html`<article class=${"obs" + (open ? " open" : "")} id=${id}>
    <div class="ohead"><${Tag} tone=${TONE[it.type] ?? "agent"}>${TLABEL[it.type] || it.type}</${Tag}>${(it.concepts || []).slice(0, 2).map(c => html`<span class="concept">${CONCEPT[c] || c}</span>`)}
      <span class="spacer"></span>${sess}<time class="sub num" title=${stamp(it.ts)}>${ago(it.ts)}</time></div>
    <h3><button class="otitle" aria-expanded=${open} aria-controls=${id + "-b"} onClick=${() => setOpen(!open)}><span class="ot">${rich(cleanTitle(it.title), 220)}</span><${Icon} name="chev" size="15"/></button></h3>
    ${it.subtitle ? html`<p class="osub">${rich(it.subtitle)}</p>` : null}
    ${!open && ((it.files_modified || []).length || (it.files_read || []).length) ? html`<div class="chips">${(it.files_modified || []).map(f => html`<${FileChip} f=${f} mod/>`)}${(it.files_read || []).filter(f => !(it.files_modified || []).includes(f)).slice(0, 4).map(f => html`<${FileChip} f=${f}/>`)}</div>` : null}
    ${open ? html`<div class="obody" id=${id + "-b"}>
      ${it.narrative ? html`<p class="narr">${rich(it.narrative)}</p>` : null}
      ${(it.facts || []).length ? html`<h4>Facts</h4><ul class="facts">${it.facts.map(f => html`<li>${rich(f)}</li>`)}</ul>` : null}
      ${(it.files_read || []).length || (it.files_modified || []).length ? html`<h4>Files</h4><div class="chips">
        ${(it.files_modified || []).map(f => html`<${FileChip} f=${f} mod full/>`)}
        ${(it.files_read || []).filter(f => !(it.files_modified || []).includes(f)).map(f => html`<${FileChip} f=${f} full/>`)}</div>` : null}
      <div class="row" style="justify-content:space-between;margin-top:10px"><p class="sub tokline" style="margin:0">${it.tokens?.discovery ? `Took about ${fmt(it.tokens.discovery)} tokens of work to learn; reads back in ${fmt(it.tokens.read)}.` : ""}${it.prompt_number ? ` Written during prompt ${it.prompt_number}.` : ""}${it.derived ? " Written without a model." : ""}</p>${del}</div>
    </div>` : null}
  </article>`;
}

function SessionDetail({ sid }) {
  const { pid, route } = useApp();
  const res = useFetch(() => api.p(pid).session(sid), [pid, sid]);
  const focusObs = route.q.get("obs");
  useLive(ev => { if ((ev.type === "observation" && ev.observation.session_id === sid) || (ev.type === "summary" && ev.summary.session_id === sid)) res.reload(); }, [sid]);
  useEffect(() => { if (focusObs && res.data) setTimeout(() => document.getElementById("obs-" + focusObs)?.scrollIntoView({ block: "center" }), 80); }, [focusObs, !!res.data]);
  return html`<div class="page view-in sessions">
    <a class="btn ghost sm backlink" href=${link.project(pid, "sessions")}><${Icon} name="back" size="15"/>All sessions</a>
    <${Load} res=${res} rows="10" what="this session">${d => {
      const s = d.session;
      const items = [...d.prompts.map(p => ({ kind: "prompt", ...p })), ...d.observations.map(o => ({ kind: "observation", ...o }))].sort((a, b) => a.ts - b.ts);
      const byType = {}; d.observations.forEach(o => { byType[o.type] = (byType[o.type] || 0) + 1; });
      const started = tsOf(s.started), ended = tsOf(s.ended);
      const dur = ended ? ended - started : Date.now() / 1000 - started;
      return html`<div>
        <div class="page-h"><div><h1 class="stitle">${clip(cleanTitle(s.title), 140) || s.id}</h1>
          <div class="ov-meta" style="margin-top:8px"><span class="path">${s.id}</span><span>${s.agent || "agent"}${s.model ? `, ${s.model}` : ""}</span>${s.pushed_by_name ? html`<span class="pushed" title="Recorded on a teammate's machine and pushed to this server"><${Avatar} name=${s.pushed_by_name} size="xs"/>from ${possessive(s.pushed_by_name)} machine</span>` : null}${s.branch && s.branch !== "None" ? html`<span>on ${s.branch}</span>` : null}
            <span>${day(started)} ${clock(started)}${ended ? `, ${Math.max(1, Math.round(dur / 60))} min` : ""}</span>
            ${s.status === "active" || !ended ? html`<span class="livebadge"><span class="live"></span>In progress</span>` : null}
            ${+s.pending ? html`<span>${fmt(+s.pending)} events waiting to be processed</span>` : null}</div></div></div>
        <div class="sdetail">
          <section>
            ${d.summaries.map(x => html`<div style="margin-bottom:18px"><${Item} it=${{ kind: "summary", ...x }}/></div>`)}
            <h2 style="margin:4px 0 12px">What happened, in order</h2>
            <ol class="feedlist tl">${items.map(it => html`<li key=${ikey(it)}><${Item} it=${it} open=${it.kind === "observation" && String(it.id) === String(focusObs)} onDeleted=${res.reload}/></li>`)}</ol>
          </section>
          <aside class="panel">
            <h3>This session</h3>
            <dl class="kv" style="margin-top:8px">${s.branch && s.branch !== "None" ? html`<dt>Branch</dt><dd>${s.branch}</dd>` : null}<dt>Prompts</dt><dd class="num">${d.prompts.length}</dd><dt>Observations</dt><dd class="num">${d.observations.length}</dd>
              <dt>Learned with</dt><dd class="num">${fmt(d.observations.reduce((a, o) => a + (o.tokens?.discovery || 0), 0))} tokens</dd>
              <dt>Reads back in</dt><dd class="num">${fmt(d.observations.reduce((a, o) => a + (o.tokens?.read || 0), 0))} tokens</dd></dl>
            <h4 style="margin:14px 0 6px">By type</h4>
            <ul class="kept">${TYPES.filter(([k]) => byType[k]).map(([k, l, tone]) => html`<li><span>${l}</span><span class="c num">${byType[k]}</span><${Meter} value=${byType[k]} max=${d.observations.length} tone=${tone || ""} label=${l}/></li>`)}</ul>
          </aside>
        </div></div>`;
    }}</${Load}>
  </div>`;
}
