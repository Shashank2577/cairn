// Workspace chrome: top bar (team and project switchers, search, sync, theme, user menu) and the
// left rail, which doubles as the map legend: every view keeps its layer colour.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, Logo, SheetMark, Avatar } from "./ui.js";
import { useApp, useDismiss } from "../state.js";
import { api, env } from "../api.js";
import { link, citeHref, go } from "../router.js";
import { ago, fmt, ROLE_LABEL, canRole, allowed, STEP_LABEL, failText } from "../lib/format.js";

/* ---------------------------------------------------------------- top bar */
export function TopBar({ onMenu }) {
  const { session, project, team } = useApp();
  return html`<header class="top">
    <button class="icon-btn menu-btn" onClick=${onMenu} aria-label="Open navigation"><${Icon} name="menu"/></button>
    <a class="brand" href=${project ? link.project(project.id) : "#/"} aria-label="Cairn home"><${Logo}/><b>cairn</b></a>
    <div class="crumbs">
      ${session.mode === "team" && session.teams.length ? html`<span class="team-crumb"><${TeamSwitcher}/></span><span class="slash team-slash" aria-hidden="true">/</span>` : null}
      ${project ? html`<${ProjectSwitcher}/>` : null}
    </div>
    ${project ? html`<${Search}/>` : html`<div class="spacer"></div>`}
    <div class="top-tools">
      ${project ? html`<${SyncControl}/>` : null}
      ${env.mock ? html`<span class="mockbadge" title="No Cairn server answered, so the UI is showing sample data built from the Cairn repository.">Sample data</span>`
        : env.fallback.size ? html`<span class="mockbadge" title=${`The server doesn't answer ${[...env.fallback].join(", ")} yet; those sections show sample data.`}>Partly sample data</span>` : null}
      <${ThemeButton}/>
      <${UserMenu}/>
    </div>
    <${SyncLine}/>
  </header>`;
}

function Pop({ open, onClose, anchor, children, align = "left", class: cls = "", width }) {
  const ref = useRef(null);
  useDismiss(open, onClose, anchor);
  if (!open) return null;
  return html`<div class=${"pop " + cls} ref=${ref} style=${{ top: "calc(100% + 6px)", [align]: 0, width }}>${children}</div>`;
}

function ProjectSwitcher() {
  const { session, project } = useApp();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const wrap = useRef(null);
  const teams = session.teams.length ? session.teams : [{ id: null, name: "This machine" }];
  const list = session.projects.filter(p => !filter || p.name.toLowerCase().includes(filter.toLowerCase()));
  const pick = p => { setOpen(false); setFilter(""); go(link.project(p.id)); };
  return html`<div style="position:relative;min-width:0" ref=${wrap}>
    <button class="switch-btn" aria-haspopup="listbox" aria-expanded=${open} onClick=${() => setOpen(!open)} title="Switch project">
      <${SheetMark} seed=${project.id} size="24"/><span class="nm">${project.name}</span>
      ${project.role ? html`<span class="role">${ROLE_LABEL[project.role]}</span>` : null}
      <${Icon} name="chev" size="15" class="chev"/>
    </button>
    <${Pop} open=${open} onClose=${() => setOpen(false)} anchor=${wrap} class="pswitch">
      ${session.projects.length > 5 ? html`<div class="filter"><input class="input" placeholder="Find a project" value=${filter} onInput=${e => setFilter(e.target.value)} aria-label="Find a project" autofocus/></div>` : null}
      <div class="menu list" role="listbox" aria-label="Projects">
        ${teams.map(t => {
          const ps = list.filter(p => (p.team_id ?? null) === t.id);
          if (!ps.length) return null;
          return html`<div class="mh">${t.name}</div>${ps.map(p => html`
            <button class="prow" role="option" aria-current=${p.id === project.id} aria-selected=${p.id === project.id} onClick=${() => pick(p)}>
              <${SheetMark} seed=${p.id} size="28"/>
              <span style="min-width:0"><span class="pn">${p.name}</span><span class="pp">${p.root || p.git_url || ""}</span></span>
              <span class="pr">${p.last_sync ? ago(p.last_sync) : "Never synced"}<br/>${ROLE_LABEL[p.role] || ""}</span>
            </button>`)}`;
        })}
        ${!list.length ? html`<div class="mh">No project matches “${filter}”.</div>` : null}
        ${session.mode === "team" && session.teams.some(t => canRole(t.role, "admin")) ? html`<hr/>
          <a href=${link.team(project.team_id || session.teams.find(t => canRole(t.role, "admin")).id, "projects")} onClick=${() => setOpen(false)}><${Icon} name="plus" size="16"/>Add a project</a>` : null}
      </div>
    </${Pop}>
  </div>`;
}

function TeamSwitcher() {
  const { session, team } = useApp();
  const [open, setOpen] = useState(false);
  const wrap = useRef(null);
  const pick = t => {
    setOpen(false);
    const first = session.projects.find(p => p.team_id === t.id);
    go(first ? link.project(first.id) : link.team(t.id));
  };
  return html`<div style="position:relative;min-width:0" ref=${wrap}>
    <button class="switch-btn team" aria-haspopup="listbox" aria-expanded=${open} onClick=${() => setOpen(!open)} title="Switch team">
      <span class="nm">${team ? team.name : "Choose a team"}</span><${Icon} name="chev" size="15" class="chev"/>
    </button>
    <${Pop} open=${open} onClose=${() => setOpen(false)} anchor=${wrap} width="280px">
      <div class="menu" role="listbox" aria-label="Teams">
        <div class="mh">Your teams</div>
        ${session.teams.map(t => html`<button role="option" aria-selected=${team?.id === t.id} onClick=${() => pick(t)}>
          <${Avatar} name=${t.name} size="sm"/><span class="clip" style="flex:1">${t.name}</span><span class="xsmall muted">${ROLE_LABEL[t.role]}</span></button>`)}
        ${team ? html`<hr/><a href=${link.team(team.id, "members")} onClick=${() => setOpen(false)}><${Icon} name="users" size="16"/>Manage ${team.name}</a>` : null}
      </div>
    </${Pop}>
  </div>`;
}

/* ---------------------------------------------------------------- search */
const KIND = {
  file: ["Files", "code"], symbol: ["Code", "code"], doc: ["Document sections", "code"], spec: ["Specs", "spec"], task: ["Tasks", "spec"],
  requirement: ["Requirements", "spec"], story: ["User stories", "spec"], memory: ["Memory", "memory"], observation: ["Agent sessions", "agent"],
  session: ["Agent sessions", "agent"], commit: ["Commits", "ink-2"], fact: ["Facts", "ink-2"], entity: ["Entities", "ink-2"], wiki: ["Wiki", "code"],
};
const ORDER = Object.keys(KIND);

function Search() {
  const { pid } = useApp();
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const wrap = useRef(null), input = useRef(null), timer = useRef(0), seq = useRef(0);
  useDismiss(open || expanded, () => { setOpen(false); setExpanded(false); }, wrap);
  useEffect(() => {
    const onKey = e => {
      const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
      if ((e.key === "/" && !typing) || (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey))) { e.preventDefault(); setExpanded(true); setTimeout(() => input.current?.focus(), 0); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { setQ(""); setRes(null); setOpen(false); }, [pid]);
  const run = v => {
    clearTimeout(timer.current);
    if (v.trim().length < 2) { setRes(null); setOpen(false); return; }
    timer.current = setTimeout(async () => {
      const my = ++seq.current;
      try {
        const r = await api.p(pid).search(v.trim(), undefined, 30);
        if (my !== seq.current) return;
        setRes(r); setSel(0); setOpen(true);
      } catch (e) { if (my === seq.current) { setRes([]); setOpen(true); } }
    }, 130);
  };
  const groups = useMemo(() => {
    if (!res) return [];
    const by = new Map();
    for (const r of res) { const k = KIND[r.kind] ? r.kind : "file"; if (!by.has(k)) by.set(k, []); by.get(k).push(r); }
    return ORDER.filter(k => by.has(k)).map(k => [k, by.get(k).slice(0, 6)]);
  }, [res]);
  // Last row: ask the question instead; it opens the Overview, which answers it.
  const askRow = q.trim().length >= 3 ? { kind: "ask", id: "ask", title: q.trim() } : null;
  const flat = [...groups.flatMap(([, rs]) => rs), ...(askRow ? [askRow] : [])];
  const hrefOf = r => r.kind === "ask" ? link.project(pid, "overview", [], { ask: r.title }) : citeHref(pid, r.id, r) || link.impact(pid, r.path || r.title);
  const choose = r => { go(r ? hrefOf(r) : link.impact(pid, q.trim())); setOpen(false); setExpanded(false); setQ(""); input.current?.blur(); };
  const onKey = e => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (!flat.length) return; setSel((sel + (e.key === "ArrowDown" ? 1 : -1) + flat.length) % flat.length); }
    else if (e.key === "Enter") { e.preventDefault(); choose(flat[sel]); }
    else if (e.key === "Escape") { setOpen(false); setExpanded(false); input.current?.blur(); }
  };
  let i = -1;
  return html`<div class=${"find" + (expanded ? " expanded" : "")} ref=${wrap}>
    <${Icon} name="search" size="15"/>
    <button class="icon-btn find-open" aria-label="Search" onClick=${() => { setExpanded(true); setTimeout(() => input.current?.focus(), 0); }}><${Icon} name="search"/></button>
    <input ref=${input} type="search" autocomplete="off" spellcheck="false" value=${q} placeholder="Search files, code, specs, sessions, memory"
      role="combobox" aria-expanded=${open} aria-controls="search-hits" aria-autocomplete="list" aria-label="Search this project"
      aria-activedescendant=${open && flat[sel] ? "hit-" + sel : undefined}
      onInput=${e => { setQ(e.target.value); run(e.target.value); }} onKeyDown=${onKey} onFocus=${() => res && setOpen(true)}/>
    <kbd aria-hidden="true">/</kbd>
    ${expanded ? html`<button class="icon-btn" aria-label="Close search" onClick=${() => { setExpanded(false); setOpen(false); }}><${Icon} name="x"/></button>` : null}
    ${open ? html`<div class="pop hits" id="search-hits" role="listbox" aria-label="Search results">
      ${groups.map(([k, rs]) => html`<div class="grp"><span class="dotkey" style=${{ background: `var(--${KIND[k][1]})` }}></span>${KIND[k][0]}</div>
        ${rs.map(r => { i++; const me = i; return html`<a id=${"hit-" + me} href=${hrefOf(r)} role="option" aria-selected=${me === sel} onMouseEnter=${() => setSel(me)} onClick=${e => { e.preventDefault(); choose(r); }}>
          <span class="t">${r.title}</span>${r.path && r.path !== r.title ? html`<span class="p">${r.path}</span>` : null}
          <span class="k">${r.kind === "file" || r.kind === "symbol" ? "Impact" : ""}</span></a>`; })}`)}
      ${!groups.length ? html`<div class="none">Nothing in the project is named like “${q}”.</div>` : null}
      ${askRow ? (() => { i++; const me = i; return html`<a id=${"hit-" + me} class="askrow" href=${hrefOf(askRow)} role="option" aria-selected=${me === sel} onMouseEnter=${() => setSel(me)} onClick=${e => { e.preventDefault(); choose(askRow); }}>
        <span class="t"><${Icon} name="bolt" size="14"/> Ask Cairn: “${askRow.title}”</span><span class="p">A model answers from the project's cited evidence</span><span class="k">Answer</span></a>`; })() : null}
    </div>` : null}
  </div>`;
}

/* ---------------------------------------------------------------- sync */
function SyncControl() {
  const { sync, startSync, can, ov, connected } = useApp();
  const [open, setOpen] = useState(false);
  const wrap = useRef(null);
  const running = sync.running;
  const cur = sync.steps.find(s => s.state === "start");
  const last = ov?.last_sync;
  // This page's own run when there is one, else what the server recorded for the last sync.
  const steps = sync.steps.length ? sync.steps.filter(s => s.step !== "sync")
    : Object.entries(ov?.sync_failed || {}).map(([step, detail]) => ({ step, state: "fail", detail }));
  const failed = running ? 0 : steps.filter(s => s.state === "fail").length;
  const label = running ? (cur ? `Syncing ${STEP_LABEL[cur.step]?.toLowerCase() || cur.step}…` : "Starting sync…")
    : last ? `Synced ${ago(last)}${failed ? `, ${failed} ${failed === 1 ? "layer" : "layers"} failed` : ""}` : "Never synced";
  return html`<div class="syncbox" ref=${wrap}>
    <button class=${"btn ghost sm syncstate" + (failed ? " failed" : "")} onClick=${() => setOpen(!open)} aria-expanded=${open} aria-haspopup="dialog" title=${failed ? label : connected ? "Live updates connected" : "Live updates reconnecting"}>
      <span class=${"live" + (connected ? "" : " off")}></span><span class=${"txt num" + (failed ? " err" : "")} aria-live="polite">${label}</span>
    </button>
    <button class="btn sm" onClick=${() => startSync(false)} disabled=${running || !can.sync} title=${can.sync ? "Rebuild every layer from the latest code, history and sessions" : "Your role can't start a sync"}>
      <${Icon} name="sync" size="15" class=${running ? "spinning" : ""}/><span class="lbl">${running ? "Syncing" : "Sync"}</span>
    </button>
    <${Pop} open=${open} onClose=${() => setOpen(false)} anchor=${wrap} align="right" width="310px">
      <div style="padding:8px 10px 4px"><b style="font-size:14px">${running ? "Sync in progress" : last ? `Last sync ${ago(last)}` : "This project has never been synced"}</b>
        <p class="sub" style="margin:2px 0 6px">${running ? "Each layer rebuilds in turn. Pages refresh when it finishes."
          : failed ? `${failed === 1 ? "This layer failed to rebuild and still holds" : "These layers failed to rebuild and still hold"} what the sync before built. The others are up to date.`
          : "A sync rebuilds each layer from the code, git history, specs and agent sessions."}</p></div>
      ${steps.length ? html`<ol class="syncsteps">${steps.map(s => html`<li class=${s.state === "start" ? "running" : s.state === "fail" ? "error" : s.state}>
        <span class="ic"></span><span class="clip">${STEP_LABEL[s.step] || s.step}</span>${s.state === "fail"
          ? html`<span class="st msg">${failText(s.detail)}</span>`
          : html`<span class="st clip">${s.state === "start" ? "running" : s.detail || s.state}</span>`}</li>`)}</ol>` : null}
      ${can.sync ? html`<div class="row" style="padding:8px 8px 6px">
        <button class="btn sm" disabled=${running} onClick=${() => { startSync(false); }}>Quick sync</button>
        <button class="btn sm ghost" disabled=${running || !ov?.models?.available} onClick=${() => startSync(true)} title=${ov?.models?.available ? "Also extract timeline facts and narration with a model" : "Needs a model: set one up in Settings"}>Full sync with models</button></div>` : html`<p class="sub" style="padding:0 10px 8px">You can see sync progress, but your role can't start one.</p>`}
    </${Pop}>
  </div>`;
}
function SyncLine() {
  const { sync } = useApp();
  const total = 7, done = sync.steps.filter(s => s.state === "done" && s.step !== "sync").length;
  const on = sync.running;
  return html`<span class="syncline" aria-hidden="true" style=${{ width: on ? `${Math.max(4, done / total * 100)}%` : sync.justDone ? "100%" : "0%", opacity: on || sync.justDone ? 1 : 0 }}></span>`;
}

/* ---------------------------------------------------------------- theme & user */
const THEMES = [["system", "Match system"], ["light", "Light"], ["dark", "Dark"]];
function ThemeButton() {
  const { theme, setTheme } = useApp();
  const cur = Math.max(0, THEMES.findIndex(t => t[0] === theme));
  const next = THEMES[(cur + 1) % 3];
  return html`<button class="icon-btn theme-btn" onClick=${() => setTheme(next[0])} title=${`Theme: ${THEMES[cur][1]}. Switch to ${next[1].toLowerCase()}`} aria-label=${`Theme: ${theme}. Switch to ${next[1]}`}><${Icon} name="theme"/></button>`;
}
function UserMenu() {
  const { session, theme, setTheme, team, signOut } = useApp();
  const [open, setOpen] = useState(false);
  const wrap = useRef(null);
  const name = session.user?.name || "Local owner";
  return html`<div style="position:relative" ref=${wrap}>
    <button class="icon-btn" style="width:auto;padding:0 3px" aria-haspopup="menu" aria-expanded=${open} onClick=${() => setOpen(!open)} aria-label="Account menu"><${Avatar} name=${name} size="sm"/></button>
    <${Pop} open=${open} onClose=${() => setOpen(false)} anchor=${wrap} align="right" width="270px">
      <div class="menu" role="menu">
        <div style="padding:8px 10px 6px"><b style="font-size:14px">${name}</b><div class="sub clip">${session.user?.email || "Local mode: no sign-in needed on this machine"}</div></div>
        <hr/>
        ${session.mode === "team" ? html`<a role="menuitem" href=${link.account()} onClick=${() => setOpen(false)}><${Icon} name="user" size="16"/>Account and password</a>` : null}
        ${session.mode === "local" ? html`<a role="menuitem" href=${link.account()} onClick=${() => setOpen(false)}><${Icon} name="user" size="16"/>Account</a>` : null}
        ${team ? html`<a role="menuitem" href=${link.team(team.id, "tokens")} onClick=${() => setOpen(false)}><${Icon} name="key" size="16"/>API tokens</a>` : null}
        ${session.mode === "team" && session.user?.is_admin ? html`<a role="menuitem" href=${link.users()} onClick=${() => setOpen(false)}><${Icon} name="lock" size="16"/>Server users</a>` : null}
        <div class="mh">Theme</div>
        <div style="padding:0 8px 6px"><div class="seg" role="radiogroup" aria-label="Theme">${THEMES.map(([k, l]) => html`<button role="radio" aria-checked=${theme === k} aria-pressed=${theme === k} onClick=${() => setTheme(k)}>${l}</button>`)}</div></div>
        ${session.mode === "team" ? html`<hr/><button role="menuitem" onClick=${signOut}><${Icon} name="out" size="16"/>Sign out</button>` : null}
      </div>
    </${Pop}>
  </div>`;
}

/* ---------------------------------------------------------------- rail */
export function Rail({ open, onClose }) {
  const { route, project, team, ov, session, pulse } = useApp();
  // As a drawer (narrow screens): focus moves into it, and Escape closes it and returns focus to the menu button.
  const nav = useRef(null);
  useEffect(() => {
    if (!open) return;
    nav.current?.querySelector("a[href], button")?.focus();
    const onKey = e => { if (e.key === "Escape") { onClose(); document.querySelector(".menu-btn")?.focus(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);
  const pid = project?.id;
  const L = ov?.layers;
  const view = route.name === "project" ? route.view : null;
  const tab = route.name === "team" ? route.tab : null;
  const items = pid ? [
    ["overview", "Overview", "var(--ink)", null],
    ["impact", "Impact", "var(--code)", null, true],
    ["map", "Map", "var(--code)", L?.map?.nodes ? fmt(L.map.nodes) : null],
    ["specs", "Specs", "var(--spec)", L?.specs?.tasks ? `${fmt(L.specs.done)}/${fmt(L.specs.tasks)}` : null],
    ["sessions", "Sessions", "var(--agent)", L?.sessions?.observations ? fmt(L.sessions.observations) : null],
    ["timeline", "Timeline", "var(--ink-2)", L?.timeline?.facts ? `${fmt(L.timeline.facts)} facts` : L?.timeline?.commits ? fmt(L.timeline.commits) : null],
    ["memory", "Memory", "var(--memory)", L?.memory?.memories ? fmt(L.memory.memories) : null],
  ] : [];
  return html`<nav class=${"rail" + (open ? " open" : "")} aria-label="Views" ref=${nav}>
    ${pid ? html`
      <div class="rh"><b>${project.name}</b></div>
      ${items.map(([v, label, color, n, ring]) => html`<a href=${link.project(pid, v)} aria-current=${view === v ? "page" : undefined} onClick=${onClose}>
        <span class=${"key" + (ring ? " ring" : "")} style=${ring ? { color } : { background: color }}></span><span>${label}</span><small>${n || ""}</small>
        ${v === "sessions" ? html`<span class=${"pulse" + (pulse ? " on" : "")} key=${pulse}></span>` : null}</a>`)}
      <a href=${link.project(pid, "settings")} aria-current=${view === "settings" ? "page" : undefined} onClick=${onClose}><${Icon} name="gear" size="14"/><span>Project settings</span><small></small></a>` : null}
    ${team ? html`
      <div class="rh" style="margin-top:14px"><span>${session.mode === "local" ? "This machine" : "Team"}</span><b>${team.name}</b></div>
      ${[["members", "Members", "users"], ["tokens", "API tokens", "key"], ["projects", "Projects", "folder"], ["audit", "Audit log", "list"], ["settings", "Team settings", "gear"]]
        .filter(([t]) => (session.mode === "team" || !["members", "settings"].includes(t)) && (t !== "audit" || allowed(team, "team.audit"))).map(([t, label, ic]) => html`
        <a href=${link.team(team.id, t)} aria-current=${tab === t && route.tid === team.id ? "page" : undefined} onClick=${onClose}><${Icon} name=${ic} size="14"/><span>${label}</span><small></small></a>`)}` : null}
    ${session.mode === "team" && session.user?.is_admin ? html`<div class="rh" style="margin-top:14px"><span>Server</span></div>
      <a href=${link.users()} aria-current=${route.name === "admin" ? "page" : undefined} onClick=${onClose}><${Icon} name="lock" size="14"/><span>Users</span><small></small></a>` : null}
    <div class="foot">
      ${env.mock ? html`<p><span class="mockbadge">Sample data</span></p><p>${env.mockReason === "requested" ? "You opened the UI with ?mock=1." : "No Cairn server answered."} Every page shows sample data built from the Cairn repository; changes last until you reload.</p>`
        : env.fallback.size ? html`<p><span class="mockbadge">Partly sample data</span></p><p>This server doesn't answer ${[...env.fallback].map(f => ({ graph: "the map explorer", sessions: "sessions" }[f] || f)).join(" or ")} yet, so ${env.fallback.size === 1 ? "that section shows" : "those sections show"} sample data from the Cairn repository.</p>` : null}
      ${session.mode === "local" ? html`<p>Local mode: no sign-in, and nothing leaves this machine.</p>` : null}
    </div>
  </nav>`;
}
