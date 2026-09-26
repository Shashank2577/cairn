// Cairn UI entry: resolves the session, routes, and holds workspace state (current project and
// team, live events, sync progress, toasts, theme).
import { render } from "preact";
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Toasts, Logo, Empty } from "./components/ui.js";
import { TopBar, Rail } from "./components/shell.js";
import { AppCtx, usePref, useApp as useAppBase } from "./state.js";
import { api, bootSession, env, openStream, ApiError } from "./api.js";
import { useRoute, link, go } from "./router.js";
import { canRole, allowed, stepNames, listOf, failText } from "./lib/format.js";
import { Login, Invite, ForcePassword } from "./views/auth.js";
import { Users } from "./views/users.js";
import { Overview } from "./views/overview.js";
import { Impact } from "./views/impact.js";
import { MapView } from "./views/map.js";
import { Specs } from "./views/specs.js";
import { Sessions } from "./views/sessions.js";
import { Timeline } from "./views/timeline.js";
import { Memory } from "./views/memory.js";
import { ProjectSettings } from "./views/settings.js";
import { Team } from "./views/team.js";
import { Account } from "./views/account.js";

const VIEWS = { overview: Overview, impact: Impact, map: MapView, specs: Specs, sessions: Sessions, timeline: Timeline, memory: Memory, settings: ProjectSettings };

function applyTheme(t) {
  const r = document.documentElement;
  if (t === "light" || t === "dark") r.setAttribute("data-theme", t); else r.removeAttribute("data-theme");
}

function App() {
  const route = useRoute();
  const [session, setSession] = useState(undefined);
  const [bootErr, setBootErr] = useState(null);
  const [theme, setThemeState] = usePref("theme", "system");
  const [toasts, setToasts] = useState([]);
  const [mustChange, setMustChange] = useState(false);
  const [pwUser, setPwUser] = useState(null);  // who must choose a password (known before the session loads)
  const [, setFallbackTick] = useState(0);
  const setTheme = t => { setThemeState(t); applyTheme(t); };
  useEffect(() => applyTheme(theme), []);

  // Signed out: the sign-in page remembers where you were going, whether the page just opened or a request was refused.
  const toLogin = () => {
    const h = location.hash || "#/";
    if (!h.startsWith("#/login") && !h.startsWith("#/invite")) { history.replaceState(null, "", "#/login" + (h === "#/" ? "" : "?next=" + encodeURIComponent(h))); window.dispatchEvent(new HashChangeEvent("hashchange")); }
  };
  const boot = useCallback(() => { setBootErr(null); bootSession().then(s => { if (s === null) toLogin(); setSession(s); }, setBootErr); }, []);
  useEffect(() => {
    boot();
    const f = () => { toLogin(); setSession(null); };
    const pw = () => setMustChange(true);
    const fb = () => setFallbackTick(n => n + 1);
    window.addEventListener("cairn:unauthorized", f);
    window.addEventListener("cairn:password-required", pw);
    window.addEventListener("cairn:fallback", fb);
    return () => { window.removeEventListener("cairn:unauthorized", f); window.removeEventListener("cairn:password-required", pw); window.removeEventListener("cairn:fallback", fb); };
  }, []);
  useEffect(() => { if (session) setMustChange(!!(session.must_change_password || session.user?.must_change_password)); }, [session]);
  const reloadSession = useCallback(async () => { const s = await api.session(); setSession(s); return s; }, []);
  const toast = useCallback(t => {
    const id = Math.random().toString(36).slice(2);
    setToasts(ts => [...ts.slice(-3), { id, ...t }]);
    setTimeout(() => setToasts(ts => ts.filter(x => x.id !== id)), t.ms || 6000);
  }, []);
  const dismiss = useCallback(id => setToasts(ts => ts.filter(x => x.id !== id)), []);
  const requirePasswordChange = useCallback(user => { setPwUser(user || null); setMustChange(true); }, []);
  const passwordChanged = useCallback(async () => {
    setMustChange(false); setPwUser(null);
    try { await reloadSession(); } catch (e) { /* the sign-in page handles it */ }
    if (/^#\/(login|invite)/.test(location.hash)) location.hash = new URLSearchParams(location.hash.split("?")[1] || "").get("next") || "#/";
  }, []);
  const base = { session, reloadSession, route, theme, setTheme, toast, toasts, dismiss, requirePasswordChange };

  let body;
  if (bootErr) body = html`<div class="boot"><${Logo} size="40"/><div style="max-width:52ch;text-align:center">
      <h2 style="margin-bottom:6px">Can't reach the Cairn server</h2>
      <p class="sub">${bootErr.message} Start it with <code>cairn ui</code>, then try again. To look around without a server, open this page with <code>?mock=1</code>.</p>
      <div class="row" style="justify-content:center"><button class="btn primary" onClick=${boot}>Try again</button><a class="btn" href="?mock=1#/">Open with sample data</a></div></div></div>`;
  else if (session === undefined) body = html`<div class="boot" role="status"><${Logo} size="40"/><span>Opening Cairn…</span></div>`;
  // A forced password change comes first: the server refuses everything else until it's done.
  else if (mustChange) body = html`<${ForcePassword} user=${pwUser || session?.user} onDone=${passwordChanged}/>`;
  else if (route.name === "invite") body = html`<${Invite} token=${route.token}/>`;
  else if (route.name === "login" || session === null) body = html`<${Login} next=${route.q.get("next")}/>`;
  else body = html`<${Workspace}/>`;
  return html`<${AppCtx.Provider} value=${base}>${body}<${Toasts}/></${AppCtx.Provider}>`;
}

function Workspace() {
  const outer = useAppBase();
  const { session, route } = outer;
  const [drawer, setDrawer] = useState(false);
  const [lastPid, setLastPid] = usePref("project", null);

  // Which project and team the page is about.
  // On team and account pages the last project stays in context, so its views and search stay one click away.
  const routePid = route.name === "project" ? route.pid : null;
  const routeProject = routePid ? session.projects.find(p => p.id === routePid) : null;
  const lastProject = session.projects.find(p => p.id === lastPid) || null;
  // A team page keeps only that team's projects in context.
  const project = routePid ? routeProject : route.name !== "team" ? lastProject
    : lastProject?.team_id === route.tid ? lastProject : session.projects.find(p => p.team_id === route.tid) || null;
  const pid = project?.id || null;
  const team = route.name === "team" ? session.teams.find(t => t.id === route.tid) : project ? session.teams.find(t => t.id === project.team_id) : null;
  useEffect(() => { if (project) setLastPid(project.id); }, [project?.id]);
  useEffect(() => {
    if (route.name !== "home") return;
    const target = session.projects.find(p => p.id === lastPid) || session.projects[0];
    if (target) location.replace(link.project(target.id));
    else if (session.teams[0]) location.replace(link.team(session.teams[0].id, "projects"));
  }, [route.name]);
  useEffect(() => { setDrawer(false); }, [route]);
  // A new page starts at the top; filter changes (query string only) keep the scroll position.
  const pageKey = [route.name, route.pid, route.view, (route.rest || []).join("/"), route.tid, route.tab].join("|");
  useEffect(() => { const m = document.getElementById("main"); if (m) m.scrollTop = 0; }, [pageKey]);

  // Overview feeds the rail counts and the sync status.
  const [ov, setOv] = useState(null);
  const ovTimer = useRef(0);
  const loadOv = useCallback(() => { if (!pid) return; api.p(pid).overview().then(o => setOv(o), () => setOv(null)); }, [pid]);
  useEffect(() => { setOv(null); loadOv(); }, [pid]);

  // One live stream per open project; views subscribe through `live`.
  const live = useMemo(() => { const subs = new Set(); return { subs, subscribe(fn) { subs.add(fn); return () => subs.delete(fn); } }; }, [pid]);
  const [sync, setSync] = useState({ running: false, steps: [], justDone: false });
  const [connected, setConnected] = useState(false);
  const [pulse, setPulse] = useState(0);
  const syncFails = useRef({});
  useEffect(() => {
    if (!pid || !project) return;
    setSync({ running: false, steps: [], justDone: false });
    syncFails.current = {};
    const close = openStream(pid, ev => {
      if (ev.type === "_open") setConnected(true);
      else if (ev.type === "_error") setConnected(false);
      else if (ev.type === "sync") {
        if (ev.step !== "sync") syncFails.current[ev.step] = ev.state === "fail" ? ev.detail || "Failed" : undefined;
        setSync(s => {
          if (ev.step === "sync" && ev.state === "start") return { running: true, steps: [], justDone: false };
          if (ev.step === "sync") return { running: false, steps: s.steps, justDone: ev.state === "done" };
          const steps = s.steps.filter(x => x.step !== ev.step).concat([{ step: ev.step, state: ev.state, detail: ev.detail }]);
          return { ...s, running: true, steps };
        });
        if (ev.step === "sync" && ev.state === "done") {
          loadOv(); setTimeout(() => setSync(s => ({ ...s, justDone: false })), 1400);
          const failed = Object.fromEntries(Object.entries(syncFails.current).filter(([, v]) => v));
          syncFails.current = {};
          const names = stepNames(failed);
          if (names.length) outer.toast({ title: `Sync finished, but ${listOf(names)} failed`, body: failText(Object.values(failed)[0]), tone: "risk" });
        }
        if (ev.step === "sync" && ev.state === "error") outer.toast({ title: "Sync stopped", body: ev.detail, tone: "risk" });
      } else if (ev.type === "observation") {
        setPulse(p => p + 1);
        setOv(o => o && ({ ...o, layers: { ...o.layers, sessions: { ...o.layers.sessions, observations: (o.layers.sessions.observations || 0) + 1 } } }));
        clearTimeout(ovTimer.current); ovTimer.current = setTimeout(loadOv, 2500);
      } else if (ev.type === "memory") loadOv();
      for (const f of live.subs) { try { f(ev); } catch (e) { console.error(e); } }
    });
    return () => { close(); setConnected(false); };
  }, [pid, !!project]);

  const startSync = useCallback(async deep => {
    try {
      setSync({ running: true, steps: [], justDone: false });
      const r = await api.p(pid).sync(deep);
      if (r && r.started === false) outer.toast({ title: "A sync is already running", body: r.detail });
    } catch (e) {
      setSync({ running: false, steps: [], justDone: false });
      outer.toast({ title: "Couldn't start a sync", body: e.message, tone: "risk" });
    }
  }, [pid]);
  const signOut = useCallback(async () => {
    try { await api.logout(); } catch (e) { /* signing out of an expired session is fine */ }
    outer.reloadSession().catch(() => go(link.login()));
    go(link.login());
  }, []);

  const role = project?.role || (session.mode === "local" ? "owner" : "viewer");
  const P = project || { role };
  const can = { write: allowed(P, "project.write"), sync: allowed(P, "project.sync"), admin: allowed(P, "project.admin"), owner: canRole(role, "owner") };
  const ctx = { ...outer, pid, project, team, ov, reloadOv: loadOv, live, sync, startSync, connected, pulse, role, can, signOut };

  let content;
  if (route.name === "project" && !routeProject) content = html`<div class="page"><${Empty} title="You can't open this project" tone="risk">
      <p>There is no project called <code>${routePid}</code> in your teams, or your access was removed. Pick one from the project switcher, or ask a team admin to add you.</p></${Empty}></div>`;
  else if (route.name === "project") { const V = VIEWS[route.view]; content = html`<${V} key=${pid + ":" + route.view}/>`; }
  else if (route.name === "team") content = team ? html`<${Team} key=${team.id + route.tab}/>` : html`<div class="page"><${Empty} title="No such team" tone="risk"><p>You're not a member of this team.</p></${Empty}></div>`;
  else if (route.name === "account") content = html`<${Account}/>`;
  else if (route.name === "admin") content = html`<${Users}/>`;
  else if (route.name === "home") content = session.projects.length ? html`<div class="page"></div>` : html`<${Welcome}/>`;
  else content = html`<div class="page"><${Empty} title="This page doesn't exist"><p>Check the link, or pick a view from the left.</p></${Empty}></div>`;

  return html`<${AppCtx.Provider} value=${ctx}>
    <div class="app">
      <button class="skip" onClick=${() => document.getElementById("main")?.focus()}>Skip to content</button>
      <${TopBar} onMenu=${() => setDrawer(true)}/>
      <${Rail} open=${drawer} onClose=${() => setDrawer(false)}/>
      <div class=${"drawer-scrim" + (drawer ? " open" : "")} onClick=${() => setDrawer(false)}></div>
      <main class="main" id="main" tabindex="-1">${content}</main>
    </div>
  </${AppCtx.Provider}>`;
}
function Welcome() {
  const { session } = useAppBase();
  const t = session.teams.find(t => canRole(t.role, "admin"));
  return html`<div class="page view-in"><div class="page-h"><div><h1>No projects yet</h1>
    <p class="lede">A project is one repository Cairn keeps a map, specs, timeline, memory and session history for.</p></div></div>
    <${Empty} title="Add your first project" actions=${t ? html`<a class="btn primary" href=${link.team(t.id, "projects")}>Add a project</a>` : null}>
      <p>${t ? "Register a local folder or a git URL, and Cairn builds every layer on the first sync." : "Ask a team admin to add a project, or run this in a repository on your machine:"}</p>
      ${t ? null : html`<code>cairn init</code>`}
    </${Empty}></div>`;
}

// Invite links look like /?invite=<token>; route them to the invite page and drop the token from the query.
{
  const u = new URL(location.href), token = u.searchParams.get("invite");
  if (token) { u.searchParams.delete("invite"); history.replaceState(null, "", u.pathname + u.search + "#/invite/" + encodeURIComponent(token)); }
}
render(html`<${App}/>`, document.getElementById("app"));
