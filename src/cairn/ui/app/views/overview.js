// Overview: what Cairn has served, the health of each layer, live activity, drift, and the code
// everything else leans on.
import { useEffect, useRef, useState } from "preact/hooks";
import { html, Icon, Empty, Load, ContourField, Tag, Meter, Skeleton } from "../components/ui.js";
import { ScaleBar, Ratio } from "../components/scalebar.js";
import { Stepper } from "../components/stepper.js";
import { useNarration, useModelReady, NarrationPanel } from "../components/narrate.js";
import { useApp, useFetch, useLive } from "../state.js";
import { api } from "../api.js";
import { link, citeHref } from "../router.js";
import { ago, fmt, plural, rich, base, dir, stepNames, listOf, failText, STEP_LABEL } from "../lib/format.js";

export const SURFACE = { mcp: "Agent tool call", hook: "Session start", cli: "Terminal", ui: "This page", api: "API token" };
export const PACK = { impact: "impact", why: "why", context: "context", brief: "briefing", recall: "recall", ask: "answer" };
export const OBS_TONE = { discovery: "agent", bugfix: "risk", feature: "memory", refactor: "spec", change: "code", decision: "" };

export function Overview() {
  const { pid, project, ov, reloadOv, route } = useApp();
  const n = useNarration(pid);
  const sav = useFetch(() => api.p(pid).savings(12), [pid]);
  const feed = useFetch(() => api.p(pid).feed({ limit: 8 }), [pid]);
  const drift = useFetch(() => api.p(pid).drift(), [pid]);
  const wf = useFetch(() => api.p(pid).workflow(), [pid]);
  const specs = useFetch(() => api.p(pid).specs(), [pid]);
  const [live, setLive] = useState([]);
  const fails = useRef({});
  useLive(ev => {
    if (ev.type === "sync" && ev.step !== "sync") fails.current[ev.step] = ev.state === "fail" ? ev.detail || "Failed" : undefined;
    if (ev.type === "observation") setLive(l => [{ k: "obs", ts: ev.observation.ts, o: ev.observation }, ...l].slice(0, 12));
    else if (ev.type === "summary") setLive(l => [{ k: "sum", ts: ev.summary.ts, o: ev.summary }, ...l].slice(0, 12));
    else if (ev.type === "memory") setLive(l => [{ k: "mem", ts: Date.now() / 1000, o: ev.memory, op: ev.op }, ...l].slice(0, 12));
    else if (ev.type === "query") { setLive(l => [{ k: "q", ts: ev.query.ts, o: ev.query }, ...l].slice(0, 12)); sav.reload(); }
    else if (ev.type === "sync" && ev.step === "sync" && ev.state === "done") {
      const failed = Object.fromEntries(Object.entries(fails.current).filter(([, v]) => v));
      fails.current = {};
      setLive(l => [{ k: "sync", ts: Date.now() / 1000, o: { ...ev, failed } }, ...l].slice(0, 12)); drift.reload(); feed.reload();
    }
  });

  if (!ov) return html`<div class="page"><${Skeleton} rows="8"/></div>`;
  const L = ov.layers;
  const never = !ov.last_sync;
  return html`<div class="page view-in overview">
    <header class="ov-head">
      <${ContourField} seed=${pid} w=${1400} h=${260} levels=${22} class="ov-field" accent="var(--code)"/>
      <div class="ov-title">
        <h1>${ov.project}</h1>
        <div class="ov-meta">
          <span class="path" title="Where the repository lives">${project.root || project.git_url || ov.root}</span>
          <span>${never ? "Never synced" : `Synced ${ago(ov.last_sync)}`}</span>
          <span title=${ov.capture?.agents?.length > 3 ? listOf(ov.capture.agents) : null}>${(ov.capture ? ov.capture.on : L.sessions?.connected) ? (ov.capture?.agents?.length ? `Recording sessions from ${fewOf(ov.capture.agents)}` : "Recording agent sessions") : "Session recording is off"}</span>
          <span>${ov.models?.available ? `Models on through ${ov.models.provider}` : "No model: deterministic answers only"}</span>
        </div>
      </div>
    </header>
    ${never ? html`<${FirstSync}/>` : html`<${AskBox} n=${n} initial=${route.q.get("ask") || ""}/>`}
    <div class="ov-grid">
      <section class="sec ov-served" aria-labelledby="h-served">
        <div class="sec-h"><div><h2 id="h-served">Context served</h2><p class="sub">Every impact, why and context answer is a short cited pack instead of the files behind it.</p></div></div>
        <${Load} res=${sav} rows="4">${s => html`<${Served} s=${s}/>`}</${Load}>
      </section>
      <section class="sec ov-attn" aria-labelledby="h-attn">
        <div class="sec-h"><div><h2 id="h-attn">Needs attention</h2></div></div>
        <${SyncFailures} failed=${ov.sync_failed}/>
        <${Load} res=${drift} rows="3">${d => html`<${Attention} findings=${d.findings}/>`}</${Load}>
        ${specs.data?.features?.length && wf.data ? html`<${ActiveSpec} specs=${specs.data} wf=${wf.data}/>` : null}
      </section>
    </div>
    <section class="sec ruled" aria-labelledby="h-layers">
      <div class="sec-h"><div><h2 id="h-layers">Layers</h2><p class="sub">Where the answers come from. Each layer rebuilds on every sync.</p></div></div>
      <${Layers} L=${L} ov=${ov}/>
    </section>
    <div class="ov-grid lower">
      <section class="sec ruled" aria-labelledby="h-live">
        <div class="sec-h"><div><h2 id="h-live">Activity</h2><p class="sub">Agent observations, answers served and memory changes, newest first. New items arrive as they happen.</p></div>
          <a class="btn sm" href=${link.project(pid, "sessions")}>All sessions</a></div>
        <${Load} res=${feed} rows="6">${f => html`<${Activity} items=${f.items} live=${live} queries=${sav.data?.recent || []}/>`}</${Load}>
      </section>
      <section class="sec ruled" aria-labelledby="h-hubs">
        <div class="sec-h"><div><h2 id="h-hubs">Most depended-on code</h2><p class="sub">Change these carefully: the most other code relies on them.</p></div></div>
        <${Hubs} hubs=${ov.hubs}/>
      </section>
    </div>
  </div>`;
}

function AskBox({ n, initial }) {
  const { pid } = useApp();
  const [q, setQ] = useState(initial);
  const ready = useModelReady(n);
  // A question arriving in the address (from search) is asked once: it leaves the address as soon as it starts, so a
  // reload or Back doesn't ask the model again. The question stays in the box.
  useEffect(() => { if (initial && ready) { setQ(initial); n.start("ask", initial); dropAsk(); } }, [initial]);
  const submit = e => { e.preventDefault(); if (q.trim() && ready && !n.busy) n.start("ask", q.trim()); };
  return html`<section class="ov-ask" aria-label="Ask about this project">
    <form class="askbox" onSubmit=${submit} role="search">
      <${Icon} name="bolt" size="17"/>
      <input class="input" value=${q} onInput=${e => setQ(e.target.value)} placeholder="Ask anything about this project, e.g. how does sync reach the read model?" aria-label="Question"/>
      ${ready ? html`<button class="btn primary sm" type="submit" disabled=${!q.trim() || n.busy}>${n.busy ? "Answering…" : "Ask"}</button>`
        : html`<button class="btn sm" type="button" aria-disabled="true" title="Explaining needs a model. Set one up in Settings.">Ask</button>`}
    </form>
    ${ready ? html`<p class="sub hint">Cairn gathers cited evidence from the code, specs, history, memory and sessions, and a model writes the answer from it.</p>`
      : html`<p class="sub hint">Answers need a model. <a href=${link.project(pid, "settings")}>Set one up in Settings</a>; everything else on this page works without one.</p>`}
    <${NarrationPanel} n=${n} title=${n.subject ? `“${n.subject}”` : "Answer"} packLabel="Evidence the answer was written from"/>
  </section>`;
}

function dropAsk() {
  const [path, qs] = location.hash.split("?");
  const q = new URLSearchParams(qs || "");
  q.delete("ask");
  history.replaceState(history.state, "", path + (q.toString() ? "?" + q : ""));
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function FirstSync() {
  const { startSync, can, sync } = useApp();
  return html`<div style="margin:0 0 30px"><${Empty} title="Build this project's layers" actions=${can.sync ? html`<button class="btn primary" disabled=${sync.running} onClick=${() => startSync(false)}><${Icon} name="sync" size="15"/>${sync.running ? "Syncing…" : "Run the first sync"}</button>` : null}>
    <p>Cairn hasn't read this repository yet. The first sync parses the code and documents, reads git history and specs, and takes a minute or two on a large repository.</p>
    ${can.sync ? null : html`<p>Ask a member of the team to run the first sync; your role can't start one.</p>`}
  </${Empty}></div>`;
}

function Served({ s }) {
  const { pid } = useApp();
  const rows = s.totals.filter(r => r.kind !== "brief");
  const agentRows = rows.filter(r => r.surface === "mcp");
  const use = agentRows.length ? agentRows : rows;
  const sum = (rs, k) => rs.reduce((a, r) => a + (r[k] || 0), 0);
  const briefs = s.totals.filter(r => r.kind === "brief");
  if (!rows.length) return html`<${Empty} title="No context has been served yet">
    <p>Each time an agent calls <code>cairn_impact</code>, <code>cairn_why</code> or <code>cairn_context</code>, Cairn records the tokens it sent and the size of the files behind the answer. Start an agent session in this repository, or <a href=${link.project(pid, "impact")}>look up an impact yourself</a>.</p></${Empty}>`;
  const sent = sum(use, "sent_with_source"), source = sum(use, "source"), n = sum(use, "n");
  const files = s.recent.filter(r => use.some(u => u.surface === r.surface && u.kind === r.kind)).reduce((a, r) => a + (r.source_files || 0), 0);
  const who = agentRows.length ? "Agents" : "You";
  return html`<div>
    <p class="lede" style="margin:0 0 6px">${agentRows.length ? `Agents asked Cairn ${plural(n, "time")}` : `No agent has asked yet. Answers were looked up ${plural(n, "time")}`}, and got a pack a fraction of the size of the code it cites.</p>
    <${ScaleBar} sent=${sent} source=${source} files=${files || null} sentLabel=${agentRows.length ? "Sent to agents" : "Sent"}/>
    <${Ratio} sent=${sent} source=${source} who=${who}/>
    <div class="surfaces">
      ${s.totals.sort((a, b) => b.n - a.n).map(r => html`<div class="surf">
        <span class="k">${SURFACE[r.surface] || r.surface}</span><span class="t">${cap(PACK[r.kind] || r.kind)}</span>
        <span class="n num">${fmt(r.n)}×</span><span class="n num">${fmt(r.sent / Math.max(1, r.n))} <small>avg tokens</small></span></div>`)}
    </div>
    ${briefs.length ? html`<p class="sub" style="margin-top:10px">Session briefings add about ${fmt(sum(briefs, "sent") / Math.max(1, sum(briefs, "n")))} tokens at the start of each agent session. They have no file baseline, so they count as cost, not savings.</p>` : null}
  </div>`;
}

function Attention({ findings }) {
  const { pid } = useApp();
  const high = findings.filter(f => f.severity === "high"), other = findings.filter(f => f.severity !== "high");
  if (!findings.length) return html`<p class="sub">The code matches its specs. No drift found.</p>`;
  return html`<div class="attn">
    <p class="attn-n"><b class="num">${findings.length}</b> place${findings.length === 1 ? "" : "s"} where the code and its specs disagree${high.length ? html`, <span class="err">${high.length} high</span>` : ""}.</p>
    <ul class="list">${[...high, ...other].slice(0, 4).map(f => html`<li><a href=${citeHref(pid, f.cite) || link.project(pid, "specs", [], { tab: "drift" })}>
      <${Tag} tone=${f.severity === "high" ? "risk" : "code"}>${f.severity}</${Tag}> <span>${rich(f.title, 110)}</span></a></li>`)}</ul>
    <a class="small" href=${link.project(pid, "specs", [f0(findings)], { tab: "drift" })}>See all ${findings.length} findings</a>
  </div>`;
}
const f0 = fs => fs[0]?.spec;
const fewOf = xs => xs.length > 3 ? `${xs.slice(0, 2).join(", ")} and ${xs.length - 2} more agents` : listOf(xs);

function SyncFailures({ failed }) {
  const { startSync, can, sync } = useApp();
  const rows = Object.entries(failed || {});
  if (!rows.length) return null;
  return html`<div class="attn sync-fail">
    <p class="attn-n"><span class="err">The last sync couldn't rebuild ${listOf(stepNames(failed))}.</span> ${rows.length === 1 ? "That layer still shows" : "Those layers still show"} what the sync before built.</p>
    <ul class="list">${rows.map(([k, msg]) => html`<li><${Tag} tone="risk">${STEP_LABEL[k] || k}</${Tag}> <span class="fail-msg">${failText(msg)}</span></li>`)}</ul>
    ${can.sync ? html`<button class="btn sm" disabled=${sync.running} onClick=${() => startSync(false)}>${sync.running ? "Syncing…" : "Sync again"}</button>` : null}
  </div>`;
}
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);

function ActiveSpec({ specs, wf }) {
  const { pid } = useApp();
  const { ov } = useApp();
  const activeId = String(ov?.active_spec?.id || "").replace(/^spec:/, "");
  const f = specs.features.find(x => x.id === activeId) || specs.features.find(x => x.progress && x.progress.done < x.progress.total) || specs.features[0];
  return html`<div class="active-spec">
    <div class="row" style="justify-content:space-between"><h3 style="margin:0">Active spec</h3><a class="small" href=${link.project(pid, "specs", [f.id])}>Open</a></div>
    <a class="as-title" href=${link.project(pid, "specs", [f.id])}>${f.title}</a>
    <${Stepper} stages=${wf.stages} fid=${f.id} compact/>
    <div class="row tight" style="margin-top:8px"><${Meter} value=${f.progress.done} max=${f.progress.total} tone="spec" label="Tasks done"/><span class="sub num nowrap">${f.progress.done} of ${f.progress.total} tasks</span></div>
  </div>`;
}

function Layers({ L, ov }) {
  const { pid } = useApp();
  const rows = [
    ["var(--code)", "Code and documents", "What the code is", L.map.connected !== false && L.map.nodes,
      `${fmt(L.map.nodes)} nodes`, `${fmt(L.map.edges)} links across ${fmt(L.map.files)} files in ${fmt(L.map.areas)} communities`, link.project(pid, "map"), "Open the map",
      "Run a sync to parse the code and documents."],
    ["var(--spec)", "Specs", "What was intended", L.specs.features,
      `${fmt(L.specs.done)}/${fmt(L.specs.tasks)} tasks`, `${plural(L.specs.features, "feature")} in specs/${L.specs.constitution ? `, constitution v${L.specs.constitution.version}` : ""}`, link.project(pid, "specs"), "Open specs",
      "No specs yet. Run /cairn.specify in your agent to write the first one."],
    ["var(--ink-2)", "Timeline", "What happened, and what was true when", L.timeline.connected,
      `${fmt(L.timeline.commits)} commits`, `${L.timeline.facts ? `${plural(L.timeline.facts, "fact")} with validity windows` : "No temporal facts yet"}${L.timeline.warnings ? `, ${plural(L.timeline.warnings, "fix or revert warning")}` : ""}`, link.project(pid, "timeline"), "Open timeline",
      "Git history isn't readable yet. Run a sync."],
    ["var(--memory)", "Memory", "What the team learned", true,
      `${fmt(L.memory.memories)} saved`, L.memory.memories ? `Conventions, decisions and gotchas${L.memory.semantic ? ", reconciled by meaning" : ""}` : "Empty. Agents save learnings with cairn_remember; you can add one here.", link.project(pid, "memory"), "Open memory"],
    ["var(--agent)", "Agent sessions", "What agents did and learned", L.sessions.connected || L.sessions.observations,
      `${fmt(L.sessions.observations)} observations`, `${plural(L.sessions.sessions || 0, "session")}${ov.capture?.on ? `, recording from ${(ov.capture.agents || []).join(", ")}` : ""}`, link.project(pid, "sessions"), "Open sessions",
      "Recording is off. Run cairn init --agents claude to capture sessions here."],
  ];
  return html`<div class="ledger">${rows.map(([color, name, what, on, count, detail, href, cta, off]) => html`
    <a class=${"lrow" + (on ? "" : " off")} href=${href}>
      <i style=${{ background: color }}></i>
      <span class="nm"><b>${name}</b><span class="what">${what}</span></span>
      <span class="ct num">${on ? count : "Off"}</span>
      <span class="dt">${on ? detail : off}</span>
      <span class="go">${cta}<${Icon} name="chevr" size="14"/></span>
    </a>`)}</div>`;
}

function Activity({ items, live, queries }) {
  const { pid } = useApp();
  const seen = new Set(live.map(x => x.o?.id).filter(Boolean));
  const rows = [
    ...live.map(x => ({ ...x, fresh: true })),
    ...items.filter(i => i.kind !== "prompt" && !seen.has(i.id)).map(i => ({ k: i.kind === "observation" ? "obs" : "sum", ts: i.ts, o: i })),
    ...queries.slice(0, 5).map(q => ({ k: "q", ts: q.ts, o: q })),
  ].sort((a, b) => b.ts - a.ts).slice(0, 10);
  if (!rows.length) return html`<${Empty} title="Nothing yet" tone="agent"><p>When an agent works in this repository, what it learns shows up here as it happens.</p></${Empty}>`;
  return html`<ol class="activity">${rows.map((r, i) => {
    const o = r.o;
    let tag, title, href;
    if (r.k === "obs") { tag = html`<${Tag} tone=${OBS_TONE[o.type] ?? "agent"}>${o.type}</${Tag}>`; title = rich(o.title, 120); href = link.project(pid, "sessions", [o.session_id], { obs: o.id }); }
    else if (r.k === "sum") { tag = html`<${Tag} tone="agent">summary</${Tag}>`; title = rich(o.request || o.completed, 120); href = link.project(pid, "sessions", [o.session_id]); }
    else if (r.k === "q") { tag = html`<${Tag} tone="memory">${PACK[o.kind] || o.kind}</${Tag}>`; title = o.target ? html`${SURFACE[o.surface] || o.surface}: <code>${o.target}</code>, ${fmt(o.sent_tokens)} tokens${o.source_tokens ? ` for ${fmt(o.source_tokens)} in files` : ""}` : `Briefing at session start: ${fmt(o.sent_tokens)} tokens`; href = o.target ? link.impact(pid, o.target) : null; }
    else if (r.k === "mem") { tag = html`<${Tag} tone="memory">memory</${Tag}>`; title = html`${{ ADD: "Remembered", UPDATE: "Updated", DELETE: "Replaced an old memory with", NONE: "Already known" }[r.op] || "Remembered"}: ${rich(o.text, 100)}`; href = link.project(pid, "memory", [], { focus: o.id }); }
    else {
      const names = stepNames(o.failed);
      tag = html`<${Tag} tone=${names.length ? "risk" : ""}>sync</${Tag}>`; href = null;
      title = names.length ? html`Sync finished, but <span class="err">${listOf(names)} failed</span>: ${rich(failText(Object.values(o.failed)[0]), 110)}` : "Sync finished: every layer is up to date";
    }
    const body = html`<span class="when num">${ago(r.ts)}</span><span class="tg">${tag}</span><span class="tt">${title}</span>`;
    return html`<li key=${(o.id || "") + r.k + r.ts} class=${r.fresh ? "fresh" : ""}>${href ? html`<a href=${href}>${body}</a>` : html`<div>${body}</div>`}</li>`;
  })}</ol>`;
}

function Hubs({ hubs }) {
  const { pid } = useApp();
  if (!hubs?.length) return html`<p class="sub">The map has no dependencies yet. Run a sync after adding code.</p>`;
  const max = Math.max(...hubs.map(h => h.degree));
  return html`<ol class="hubs">${hubs.slice(0, 9).map(h => {
    const target = h.file && base(h.file) === h.label ? h.file : "symbol:" + h.id;
    return html`<li><a href=${link.impact(pid, target)}>
      <span class="hn"><code>${h.label}</code><span class="hp">${h.label === base(h.file) ? dir(h.file) : h.file}</span></span>
      <span class="hb"><b style=${{ width: `${h.degree / max * 100}%` }}></b></span><span class="hd num">${h.degree}</span></a></li>`;
  })}</ol>
  <p class="sub" style="margin-top:8px">Bar length: links to other code. Click one to see everything that depends on it.</p>`;
}
