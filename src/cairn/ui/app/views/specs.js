// Specs: the spec-driven workflow for each feature — where it stands, what it must do, its tasks,
// its documents and checklists, and where the code disagrees with it.
import { useEffect, useMemo, useState } from "preact/hooks";
import { html, Icon, Empty, Load, Tag, Meter, Skeleton, Tabs, Cmd } from "../components/ui.js";
import { Stepper, STAGES, stageState } from "../components/stepper.js";
import { Markdown } from "../lib/md.js";
import { useApp, useFetch, usePref } from "../state.js";
import { api } from "../api.js";
import { link, citeHref, replace } from "../router.js";
import { fmt, plural, rich, plain, base } from "../lib/format.js";

const TABS = ["overview", "tasks", "docs", "checklists", "drift"];

export function Specs() {
  const { pid, route } = useApp();
  const specs = useFetch(() => api.p(pid).specs(), [pid]);
  const wf = useFetch(() => api.p(pid).workflow(), [pid]);
  const drift = useFetch(() => api.p(pid).drift(), [pid]);
  const fid = route.rest[0];
  useEffect(() => { if (!fid && specs.data?.features?.length) replace(link.project(pid, "specs", [specs.data.features[0].id]) + (route.q.toString() ? "?" + route.q : "")); }, [fid, specs.data]);

  return html`<div class="page view-in specs">
    <div class="page-h"><div><h1>Specs</h1>
      <p class="lede">What this project set out to build, written as Markdown in <code>specs/</code> through the <code>/cairn.*</code> workflow commands in your agent. Cairn reads those files and checks finished work against the code.</p></div></div>
    <${Load} res=${specs} rows="8" what="specs">${s => {
      if (!s.features.length && !s.constitution) return html`<${NoSpecs} commands=${wf.data?.commands}/>`;
      const cur = fid === "constitution" ? null : s.features.find(f => f.id === fid) || (!fid && s.features[0]);
      return html`<div>
        <${FeatureStrip} s=${s} cur=${fid === "constitution" ? "constitution" : cur?.id} findings=${drift.data?.findings || []}/>
        ${fid === "constitution" ? html`<${Constitution} c=${s.constitution}/>`
          : cur ? html`<${Feature} f=${cur} wf=${wf.data} findings=${(drift.data?.findings || []).filter(x => x.spec === cur.id)} key=${cur.id}
              onTask=${(t, done) => specs.setData(d => ({ ...d, features: d.features.map(x => x.id !== cur.id ? x : { ...x, tasks: x.tasks.map(y => y.id === t ? { ...y, done } : y),
                progress: { done: x.tasks.filter(y => (y.id === t ? done : y.done)).length, total: x.tasks.length } }) }))}/>`
          : html`<${Empty} title=${`No feature called ${fid}`}><p>Pick a feature above.</p></${Empty}>`}
      </div>`;
    }}</${Load}>
  </div>`;
}

function NoSpecs({ commands }) {
  return html`<${Empty} title="No specs yet" tone="spec">
    <p>A spec is a plain Markdown description of what to build: user stories, requirements and a task list. Your agent writes it through the workflow commands, one stage at a time:</p>
    <ol class="small" style="margin:6px 0 10px;padding-left:20px">${STAGES.map(([id, label, what]) => html`<li><b>${label}</b>: ${what}${(commands || []).find(c => c.name.endsWith("." + id)) ? html` (<code>${commands.find(c => c.name.endsWith("." + id)).name}</code>)` : null}</li>`)}</ol>
    <p>Start with the first command in your agent:</p><${Cmd}>${(commands || [])[0]?.name || "/cairn.constitution"}</${Cmd}>
  </${Empty}>`;
}

function FeatureStrip({ s, cur, findings }) {
  const { pid } = useApp();
  return html`<div class="fstrip" role="list">
    ${s.features.map(f => {
      const pct = f.progress?.total ? f.progress.done / f.progress.total : 0;
      const n = findings.filter(x => x.spec === f.id).length;
      return html`<a role="listitem" class="fcard" href=${link.project(pid, "specs", [f.id])} aria-current=${cur === f.id ? "page" : undefined}>
        <span class="fid">${f.id}</span><b>${f.title}</b>
        <span class="row tight"><${Meter} value=${f.progress?.done || 0} max=${f.progress?.total || 0} tone="spec" label="Tasks done"/><span class="sub num nowrap">${f.progress?.done}/${f.progress?.total}</span></span>
        <span class="row tight"><${Tag} tone="spec">${f.status || "Draft"}</${Tag}>${n ? html`<${Tag} tone="risk">${plural(n, "drift finding")}</${Tag}>` : null}</span>
      </a>`;
    })}
    ${s.constitution ? html`<a role="listitem" class="fcard const" href=${link.project(pid, "specs", ["constitution"])} aria-current=${cur === "constitution" ? "page" : undefined}>
      <span class="fid">Principles</span><b>Constitution</b><span class="sub">v${s.constitution.version}, ${plural(s.constitution.principles.length, "principle")}</span></a>` : null}
  </div>`;
}

function Feature({ f, wf, findings, onTask }) {
  const { pid, route } = useApp();
  const tab = TABS.includes(route.rest[1]) ? route.rest[1] : route.q.get("tab") === "drift" ? "drift" : "overview";
  const docs = (f.artifacts || []).filter(a => a.endsWith(".md") && !a.startsWith("checklists/"));
  const lists = (f.artifacts || []).filter(a => a.startsWith("checklists/") && a.endsWith(".md"));
  const open = f.tasks.filter(t => !t.done).length;
  const { next } = stageState(wf?.stages, f.id);
  const nextCmd = next < STAGES.length ? (wf?.commands || []).find(c => c.name.endsWith("." + STAGES[next][0])) : null;
  return html`<section class="feature">
    <div class="fhead">
      <div style="min-width:0;flex:1 1 420px"><h2>${f.title}</h2><p class="sub path" style="margin:3px 0 0">${f.path}/</p></div>
      <div class="row tight"><${Tag} tone="spec">${f.status || "Draft"}</${Tag}><span class="sub num">${f.progress.done} of ${f.progress.total} tasks done</span></div>
    </div>
    ${wf ? html`<div class="stepwrap"><${Stepper} stages=${wf.stages} fid=${f.id} commands=${wf.commands}/></div>` : html`<${Skeleton} rows="2"/>`}
    ${nextCmd ? html`<div class="nextstep"><${Icon} name="bolt" size="16"/><span><b>Next: ${STAGES[next][1].toLowerCase()}.</b> ${String(nextCmd.description || "").replace(/[.\s]+$/, "")}. Run it in your agent:</span><${Cmd}>${nextCmd.name} ${f.id}</${Cmd}></div>`
      : next >= STAGES.length && open ? html`<div class="nextstep"><${Icon} name="bolt" size="16"/><span><b>Implementing.</b> ${plural(open, "task")} still open. Your agent ticks each one in tasks.md as it lands.</span></div>` : null}
    <${Tabs} label="Feature sections" current=${tab} keyColor="var(--spec)" items=${[
      { id: "overview", label: "Overview", href: link.project(pid, "specs", [f.id]) },
      { id: "tasks", label: "Tasks", n: open ? `${open} open` : f.tasks.length, href: link.project(pid, "specs", [f.id, "tasks"]) },
      { id: "docs", label: "Documents", n: docs.length, href: link.project(pid, "specs", [f.id, "docs"]) },
      { id: "checklists", label: "Checklists", n: lists.length, href: link.project(pid, "specs", [f.id, "checklists"]) },
      { id: "drift", label: "Drift", n: findings.length, href: link.project(pid, "specs", [f.id, "drift"]) },
    ]}/>
    ${tab === "overview" ? html`<${FeatureOverview} f=${f} findings=${findings}/>`
      : tab === "tasks" ? html`<${TaskBoard} f=${f} findings=${findings} onTask=${onTask}/>`
      : tab === "docs" ? html`<${Docs} f=${f} names=${docs}/>`
      : tab === "checklists" ? html`<${Checklists} f=${f} names=${lists}/>`
      : html`<${DriftList} findings=${findings}/>`}
  </section>`;
}

function FeatureOverview({ f, findings }) {
  const { pid, route } = useApp();
  const focus = route.q.get("focus");
  const cover = useMemo(() => {
    const m = new Map();
    for (const t of f.tasks) for (const r of t.reqs || []) { if (!m.has(r)) m.set(r, []); m.get(r).push(t); }
    return m;
  }, [f]);
  const traced = f.tasks.some(t => (t.reqs || []).length);
  const uncovered = new Set(findings.filter(x => x.kind === "uncovered-requirement" || x.kind === "uncovered").map(x => String(x.cite || "").split("/").pop()));
  useEffect(() => { if (focus) document.getElementById("item-" + focus)?.scrollIntoView({ block: "center" }); }, [focus]);
  return html`<div class="fov">
    <div>
      <h3>What it must do</h3>
      <ul class="stories">${f.stories.map(s => html`<li id=${"item-" + s.id} class=${focus === s.id ? "focus" : ""}><span class="pri">${s.priority || ""}</span><span class="sid">${s.id}</span><span>${rich(s.title)}</span>
        <span class="sub num nowrap">${plural(f.tasks.filter(t => t.story === s.id).length, "task")}</span></li>`)}</ul>
      <h3 style="margin-top:26px">Requirements <span class="sub num" style="font-weight:500">${f.requirements.length}</span></h3>
      ${!traced && f.requirements.length ? html`<p class="sub">No task cites a requirement id yet, so Cairn can't tell which requirements are built. Add ids such as <code>FR-003</code> to task lines to get coverage.</p>` : null}
      <ul class="reqlist">${f.requirements.map(r => { const ts = cover.get(r.id) || []; const un = uncovered.has(r.id) || (traced && !ts.length && r.id.startsWith("FR"));
        return html`<li id=${"item-" + r.id} class=${focus === r.id ? "focus" : ""}><span class="id">${r.id}</span><span>${rich(r.text)}</span>
          <span class="cov">${ts.length ? html`<span class="sub" title=${ts.map(t => t.id).join(", ")}>${ts.filter(t => t.done).length}/${ts.length} tasks</span>` : un ? html`<${Tag} tone="risk">no task</${Tag}>` : null}</span></li>`; })}</ul>
    </div>
    <aside>
      ${f.clarifications?.length ? html`<div class="panel"><h3>Clarifications</h3><p class="sub">Questions answered back into the spec.</p>
        <dl class="qa">${f.clarifications.map(c => typeof c === "string" ? html`<dd>${rich(c)}</dd>` : html`<dt>${rich(c.q)}</dt><dd>${rich(c.a)}</dd>`)}</dl></div>` : null}
      <div class="panel"><h3>Needs attention</h3>
        ${findings.length ? html`<ul class="list">${findings.slice(0, 6).map(x => html`<li><a href=${citeHref(pid, x.cite) || "#"}><${Tag} tone=${x.severity === "high" ? "risk" : "code"}>${x.severity}</${Tag}><span>${rich(x.title, 110)}</span></a></li>`)}</ul>
          <a class="small" href=${link.project(pid, "specs", [f.id, "drift"])}>All ${plural(findings.length, "finding")}</a>`
        : html`<p class="sub">Every finished task's files exist and every requirement has a task. Nothing to fix.</p>`}</div>
    </aside>
  </div>`;
}

function TaskBoard({ f, findings, onTask }) {
  const { pid, can, toast, route } = useApp();
  const [mode, setMode] = usePref("tasks.mode", "board");
  const [show, setShow] = useState("all");
  const [story, setStory] = useState(null);
  const [busy, setBusy] = useState(null);
  const [expanded, setExpanded] = useState(() => new Set());
  const focus = route.q.get("focus");
  const byCite = useMemo(() => { const m = {}; findings.forEach(x => (m[x.cite] ||= []).push(x)); return m; }, [findings]);
  const bad = t => byCite[`task:${f.id}/${t.id}`] || [];
  const tasks = f.tasks.filter(t => (show === "all" || (show === "open" ? !t.done : show === "done" ? t.done : bad(t).length)) && (!story || t.story === story));
  const phases = [];
  tasks.forEach(t => { let ph = phases.find(p => p.name === (t.phase || "Tasks")); if (!ph) phases.push(ph = { name: t.phase || "Tasks", tasks: [], all: f.tasks.filter(x => (x.phase || "Tasks") === (t.phase || "Tasks")) }); ph.tasks.push(t); });
  const toggle = async t => {
    if (!can.write) return;
    setBusy(t.id); onTask(t.id, !t.done);
    try { await api.p(pid).setTask(f.id, t.id, !t.done); }
    catch (e) { onTask(t.id, t.done); toast({ title: `Couldn't update ${t.id}`, body: e.message, tone: "risk" }); }
    finally { setBusy(null); }
  };
  useEffect(() => { if (focus) setTimeout(() => document.getElementById("task-" + focus)?.scrollIntoView({ block: "center", inline: "center" }), 60); }, [focus]);
  const stories = [...new Set(f.tasks.map(t => t.story).filter(Boolean))];
  const card = t => {
    const probs = bad(t);
    return html`<li id=${"task-" + t.id} class=${`task${t.done ? " done" : ""}${probs.length ? " bad" : ""}${focus === t.id ? " focus" : ""}`}>
      <label class="tcheck" title=${can.write ? (t.done ? "Mark as not done" : "Mark as done") : "Viewers can't change tasks"}>
        <input type="checkbox" checked=${t.done} disabled=${!can.write || busy === t.id} onChange=${() => toggle(t)} aria-label=${`${t.id} done`}/></label>
      <div class="tbody">
        <div class="tmeta"><span class="tid">${t.id}</span>${t.parallel ? html`<span class="tag outline" title="Can run in parallel with other [P] tasks">parallel</span>` : null}
          ${t.story ? html`<${Tag} tone="spec">${t.story}</${Tag}>` : null}${(t.reqs || []).map(r => html`<span class="tag outline">${r}</span>`)}</div>
        <p>${rich(t.text)}</p>
        ${(t.files || []).length ? html`<div class="chips">${t.files.map(([p, ok]) => ok ? html`<a class="fchip" href=${link.impact(pid, p)} title="Open impact">${p}</a>` : html`<span class="fchip missing" title="This file doesn't exist">${p}</span>`)}</div>` : null}
        ${probs.map(x => html`<p class="why-bad"><${Icon} name="info" size="14"/>${x.kind === "missing-file" ? "Marked done, but a file it names doesn't exist." : rich(x.title)}</p>`)}
      </div></li>`;
  };
  return html`<div class="tasks">
    <div class="row" style="margin:-6px 0 16px">
      <span class="seg" role="radiogroup" aria-label="Layout"><button role="radio" aria-checked=${mode === "board"} aria-pressed=${mode === "board"} onClick=${() => setMode("board")}>Board</button><button role="radio" aria-checked=${mode === "list"} aria-pressed=${mode === "list"} onClick=${() => setMode("list")}>List</button></span>
      <span class="seg" role="radiogroup" aria-label="Show">${[["all", "All"], ["open", "Open"], ["done", "Done"], ["drift", "Drifted"]].map(([k, l]) => html`<button role="radio" aria-checked=${show === k} aria-pressed=${show === k} onClick=${() => setShow(k)}>${l}</button>`)}</span>
      ${stories.length ? html`<div class="chips">${stories.map(s => html`<button class="chip" aria-pressed=${story === s} onClick=${() => setStory(story === s ? null : s)}>${s}</button>`)}</div>` : null}
      <span class="spacer"></span>
      ${!can.write ? html`<span class="sub"><${Icon} name="lock" size="14"/> Read only for viewers</span>` : null}
    </div>
    ${!tasks.length ? html`<p class="sub">No task matches these filters.</p>` : mode === "list" ? html`<div class="tlist">${phases.map(ph => html`<details class="phase" open>
        <summary><span>${rich(ph.name)}</span><span class="sub num">${ph.all.filter(t => t.done).length}/${ph.all.length}</span></summary><ul>${ph.tasks.map(card)}</ul></details>`)}</div>`
    : html`<div class="board hscroll">${phases.map(ph => {
        const done = ph.all.filter(t => t.done).length, complete = done === ph.all.length && !ph.tasks.some(t => bad(t).length);
        const folded = complete && !expanded.has(ph.name) && show === "all";
        return html`<section class=${"col" + (folded ? " folded" : "")} aria-label=${plain(ph.name)}>
          <header><h4>${rich(ph.name.replace(/^Phase \d+:\s*/, ""))}</h4><span class="sub num">${done}/${ph.all.length}</span>
            <${Meter} value=${done} max=${ph.all.length} tone="spec" label=${`${plain(ph.name)} progress`}/></header>
          ${folded ? html`<button class="btn xs ghost" onClick=${() => setExpanded(new Set([...expanded, ph.name]))}><${Icon} name="check" size="14"/>All ${ph.all.length} done. Show</button>`
            : html`<ul>${ph.tasks.map(card)}</ul>`}
        </section>`;
      })}</div>`}
  </div>`;
}

function Docs({ f, names }) {
  const { pid, route } = useApp();
  const cur = route.rest.slice(2).join("/") || names.find(n => n === "spec.md") || names[0];
  const doc = useFetch(() => cur ? api.p(pid).doc(f.id, cur) : Promise.resolve(null), [pid, f.id, cur]);
  if (!names.length) return html`<${Empty} title="No documents yet" tone="spec"><p>Documents appear as the workflow runs: <code>spec.md</code> after specify, <code>plan.md</code> and friends after plan, <code>tasks.md</code> after tasks.</p></${Empty}>`;
  const label = n => ({ "spec.md": "Spec", "plan.md": "Plan", "tasks.md": "Tasks", "research.md": "Research", "data-model.md": "Data model", "quickstart.md": "Quickstart", "analysis.md": "Analysis" }[n] || n.replace(/\.md$/, "").replace(/^contracts\//, "Contract: "));
  const order = ["spec.md", "plan.md", "research.md", "data-model.md", "quickstart.md", "tasks.md", "analysis.md"];
  const sorted = [...names].sort((a, b) => ((order.indexOf(a) + 1) || 99) - ((order.indexOf(b) + 1) || 99) || a.localeCompare(b));
  return html`<div class="docs" style="--tab-key:var(--spec)">
    <nav class="doclist" aria-label="Documents"><ol>${sorted.map(n => html`<li><a href=${link.project(pid, "specs", [f.id, "docs", ...n.split("/")])} aria-current=${n === cur ? "page" : undefined}>${label(n)}<span class="sub path" style="display:block">${n}</span></a></li>`)}</ol></nav>
    <article class="docbody">${doc.error ? html`<p class="err">${doc.error.message}</p>` : doc.data ? html`<${Markdown} src=${doc.data.markdown} links=${docLink(pid, f.id, cur, names)} linksKey=${f.id + "/" + cur}/>` : html`<${Skeleton} rows="12"/>`}</article>
  </div>`;
}

/** Links between a feature's documents ("./plan.md", "../spec.md", "contracts/cli.md") open in the Documents tab. */
function docLink(pid, fid, cur, names) {
  return href => {
    const [p] = href.split(/[?#]/);
    if (!/\.md$/i.test(p)) return null;
    const parts = (cur || "").split("/").slice(0, -1);
    for (const seg of p.split("/")) { if (seg === "..") parts.pop(); else if (seg && seg !== ".") parts.push(seg); }
    const path = parts.join("/");
    return names.includes(path) ? link.project(pid, "specs", [fid, "docs", ...path.split("/")]) : null;
  };
}

function Checklists({ f, names }) {
  const { pid } = useApp();
  const res = useFetch(() => Promise.all(names.map(n => api.p(pid).doc(f.id, n).catch(() => ({ name: n, markdown: "" })))), [pid, f.id, names.join()]);
  if (!names.length) return html`<${Empty} title="No checklists yet" tone="spec"><p>Checklists test the quality of the requirements themselves, one domain at a time. Generate one in your agent:</p><${Cmd}>/cairn.checklist ux</${Cmd}></${Empty}>`;
  return html`<${Load} res=${res} rows="8">${docs => html`<div class="checklists">${docs.map(d => {
    const done = (d.markdown.match(/^\s*[-*] \[[xX]\]/gm) || []).length, total = (d.markdown.match(/^\s*[-*] \[[ xX]\]/gm) || []).length;
    return html`<details class="cl" open=${done < total}>
      <summary><span class="nm">${base(d.name).replace(/\.md$/, "")}</span><${Meter} value=${done} max=${total} tone=${done === total ? "memory" : "spec"} label="Checklist progress"/><span class="sub num nowrap">${done} of ${total} pass</span></summary>
      <${Markdown} src=${d.markdown.replace(/^# .*\n/, "")}/></details>`;
  })}</div>`}</${Load}>`;
}

function DriftList({ findings }) {
  const { pid } = useApp();
  if (!findings.length) return html`<${Empty} title="No drift" tone="memory"><p>Every finished task's files exist, every requirement has a task, and the plan matches the code.</p></${Empty}>`;
  const KIND = { "missing-file": "Done, but a file it names is missing", "uncovered-requirement": "Requirement with no task", uncovered: "Requirement with no task",
    "changed-after-done": "Code changed after the task was marked done", status: "Spec status disagrees with its tasks", semantic: "Code does something the spec doesn't say",
    "plan-mismatch": "Plan disagrees with the code" };
  return html`<ul class="drift">${findings.map(x => html`<li>
    <${Tag} tone=${x.severity === "high" ? "solid-risk" : x.severity === "medium" ? "risk" : "code"}>${x.severity}</${Tag}>
    <div><div class="sub">${KIND[x.kind] || x.kind}</div><b>${rich(x.title)}</b>
      ${(x.evidence || []).length ? html`<ul class="ev">${x.evidence.map(e => html`<li>${rich(e, 200)}</li>`)}</ul>` : null}</div>
    ${citeHref(pid, x.cite) ? html`<a class="btn xs" href=${citeHref(pid, x.cite)}>Open</a>` : html`<span></span>`}
  </li>`)}</ul>`;
}

function Constitution({ c }) {
  if (!c) return html`<${Empty} title="No constitution yet" tone="spec"><p>The constitution holds the principles every spec and plan is checked against.</p><${Cmd}>/cairn.constitution</${Cmd}></${Empty}>`;
  return html`<section class="feature">
    <div class="fhead"><div><h2>Constitution</h2><p class="sub" style="margin:3px 0 0">Version ${c.version}${c.ratified ? `, ratified ${c.ratified}` : ""}${c.amended && c.amended !== c.ratified ? `, amended ${c.amended}` : ""}. Every plan runs a constitution check against these.</p></div></div>
    <ol class="principles">${c.principles.map(p => { const m = p.match(/^([IVXLC]+)\.\s*(.*)$/); return html`<li><span class="rn">${m ? m[1] : ""}</span><span>${m ? m[2] : p}</span></li>`; })}</ol>
    ${c.markdown ? html`<details class="fulltext"><summary>Read the full constitution</summary><${Markdown} src=${c.markdown}/></details>` : null}
  </section>`;
}
