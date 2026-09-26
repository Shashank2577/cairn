// Sample data for the UI when no Cairn server is behind the page (open the UI with ?mock=1, or
// serve it statically). Every route of the HTTP API v2 is answered from fixtures built from this
// repository's own graph, commits and specs; impact, why, path and search are computed here from
// that graph, so every screen behaves like it does against a real server. Nothing here is used when
// a server answers /api/session.

let D = null;   // project fixture (this repository)
let PL = null;  // platform fixture (session, teams, members, tokens, audit)
let IX = null;  // indexes over the current project's graph (set per request)
let ready_ = null;

const TS_KEYS = new Set(["ts", "last_sync", "created_at", "updated_at", "valid_at", "invalid_at", "expired_at", "started", "ended",
  "last_seen", "last_used_at", "expires_at", "built_at", "last_ts"]);
const now = () => Date.now() / 1000;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const clone = x => x === undefined ? null : JSON.parse(JSON.stringify(x));
const tok = s => Math.ceil(String(s || "").length / 4);

function shift(o, dt) {
  if (Array.isArray(o)) { o.forEach(x => shift(x, dt)); return; }
  if (!o || typeof o !== "object") return;
  for (const k of Object.keys(o)) {
    const v = o[k];
    if (TS_KEYS.has(k) && typeof v === "number" && v > 1.6e9) o[k] = v + dt;
    else if (v && typeof v === "object") shift(v, dt);
  }
}

export function ready() {
  if (!ready_) ready_ = (async () => {
    const [c, p] = await Promise.all([
      fetch("/ui/mock/data/cairn.json").then(r => r.json()),
      fetch("/ui/mock/data/platform.json").then(r => r.json()),
    ]);
    const dt = now() - c.anchor - 240;
    shift(c, dt); shift(p, dt);
    D = c; PL = p;
    const url = new URL(location.href);
    for (const [id, spec] of Object.entries(SYNTH)) PL.session.projects.push({ id, team_id: "t-core", slug: id, name: id, kind: "git", root: null, git_url: spec.git_url,
      git_branch: null, data_dir: null, managed: true, status: "ready", status_detail: "", settings: {}, created_at: now() - 86400 * 9, updated_at: now() - 3600,
      last_synced_at: now() - spec.synced, webhook_configured: true, role: "owner", permissions: PERMS.owner });
    const as = url.searchParams.get("as");
    if (as && ["owner", "admin", "member", "viewer"].includes(as)) {
      PL.session.projects.filter(x => x.team_id === "t-core").forEach(x => { x.role = as; x.permissions = PERMS[as]; });
      PL.session.teams[0].role = as;
      PL.members["t-core"].find(m => m.user_id === "u-maya").role = as;
    }
    if (url.searchParams.get("local") === "1") {
      const me = { id: "u-local", email: "owner@localhost", name: "You", is_admin: true, is_local: true, must_change_password: false, disabled: false, has_password: false, created_at: now(), last_login_at: null };
      const team = { id: "t-local", slug: "personal", name: "Personal", settings: {}, created_at: now(), role: "owner" };
      Object.assign(PL.session, { user_id: me.id, email: me.email, name: me.name, kind: "local", mode: "local", csrf_token: null, user: me, teams: [team],
        projects: PL.session.projects.filter(x => x.id === "cairn").map(x => ({ ...x, team_id: team.id })) });
      PL.members["t-local"] = [{ team_id: team.id, user_id: me.id, email: me.email, name: me.name, role: "owner", joined_at: now(), disabled: false, last_login_at: null }];
      PL.invitations["t-local"] = [];
      PL.tokens.forEach(t => { t.team_id = team.id; t.user_id = me.id; });
    }
    if (url.searchParams.get("nomodel") === "1") { D.overview.models.available = false; D.models.available = false; }
    // Every key the server reports, as it reports it. Through the Claude CLI most input arrives as cache tokens.
    for (const r of D.models.ledger) { r.cache_read ??= r.input_tokens * 5; r.cache_write ??= Math.round(r.input_tokens / 2); r.input_total ??= r.input_tokens + r.cache_read + r.cache_write; }
    D.settings.models.base_url ??= null; D.settings.deep.budget_tokens ??= 150000; D.settings.deep.enabled = D.settings.deep.enabled === true ? "auto" : D.settings.deep.enabled;
    // &syncfail=1: the last sync could not rebuild the map, and the next one fails the same way
    if (url.searchParams.get("syncfail") === "1") D.overview.sync_failed = { map: MAP_FAIL };
    if (url.searchParams.get("reset") === "1") { PL.session.must_change_password = true; PL.session.user.must_change_password = true; }
    IX = indexOf(D);
  })();
  return ready_;
}

/* ------------------------------------------------------------------ indexes */
const DEP_RELS = new Set(["calls", "imports", "imports_from", "uses", "inherits", "indirect_call", "references"]);
function indexOf(d) {
  if (d._ix) return d._ix;
  const g = d.graph;
  const byId = new Map(g.nodes.map(n => [n.id, n]));
  const byFile = new Map(), rev = new Map(), fwd = new Map(), byLabel = new Map();
  for (const n of g.nodes) {
    if (n.file) (byFile.get(n.file) || byFile.set(n.file, []).get(n.file)).push(n);
    const k = n.label.toLowerCase();
    (byLabel.get(k) || byLabel.set(k, []).get(k)).push(n);
  }
  for (const l of g.links) {
    (rev.get(l.target) || rev.set(l.target, []).get(l.target)).push(l);
    (fwd.get(l.source) || fwd.set(l.source, []).get(l.source)).push(l);
  }
  const commName = new Map(g.communities.map(c => [c.id, c.name]));
  return (d._ix = { byId, byFile, rev, fwd, byLabel, commName });
}

/* ------------------------------------------------------------------ routing */
const routes = [];
const on = (method, pattern, fn) => routes.push([method, new RegExp("^" + pattern + "$"), fn]);
const err = (status, detail) => ({ __status: status, json: { detail } });

export async function handle(method, path, query, body) {
  await ready();
  await sleep(70 + Math.random() * 160);
  for (const [m, re, fn] of routes) {
    if (m !== method) continue;
    const mm = path.match(re);
    if (!mm) continue;
    try {
      const out = await fn({ p: mm.slice(1).map(decodeURIComponent), q: query || {}, b: body || {} });
      if (out && out.__status) return { status: out.__status, json: out.json, retryAfter: out.retryAfter };
      return { status: 200, json: clone(out) };
    } catch (e) {
      console.error(e);
      return { status: 500, json: { detail: String(e.message || e) } };
    }
  }
  return { status: 404, json: { detail: `No sample data for ${method} ${path}.` } };
}

const PERMS = { owner: ["project.admin", "project.capture", "project.read", "project.sync", "project.write"], admin: ["project.admin", "project.capture", "project.read", "project.sync", "project.write"],
  member: ["project.capture", "project.read", "project.sync", "project.write"], viewer: ["project.read"] };
const TEAM_PERMS = { owner: ["team.read", "team.members", "team.tokens", "team.audit", "team.admin", "team.delete"], admin: ["team.read", "team.members", "team.tokens", "team.audit"], member: ["team.read"], viewer: ["team.read"] };
const projOf = pid => PL.session.projects.find(p => p.id === pid);
const roleOf = pid => projOf(pid)?.role || "viewer";
const RANK = { viewer: 0, member: 1, admin: 2, owner: 3 };
const needs = (pid, role) => RANK[roleOf(pid)] >= RANK[role] ? null :
  err(403, role === "member" ? "Viewers can read this project but not change it. Ask a team admin for member access." : "Only team admins can do this.");
const teamOf = tid => PL.session.teams.find(t => t.id === tid);
const teamRole = tid => teamOf(tid)?.role || "viewer";
const needsTeam = (tid, action) => (TEAM_PERMS[teamRole(tid)] || []).includes(action) || (action === "project.admin" && RANK[teamRole(tid)] >= 2) ? null : err(403, "Your role in this team doesn't allow that.");
const me = () => PL.session.user;
const actor = () => me()?.name || "Local owner";
function audit(action, target, detail, team_id = "t-core", project_id = null) {
  PL.audit.unshift({ id: (PL.audit[0]?.id || 0) + 1, ts: now(), actor_id: me()?.id || null, actor: actor(), action, target, team_id, project_id, ip: "127.0.0.1", detail: detail || {} });
}
const meta = () => { const s = PL.session; return { user_id: s.user_id, email: s.email, name: s.name, kind: s.kind, is_admin: s.is_admin, must_change_password: s.must_change_password, user: s.user, teams: s.teams, mode: s.mode, csrf_token: s.csrf_token }; };
const rnd = (n, abc = "abcdefghijkmnpqrstuvwxyz23456789") => Array.from(crypto.getRandomValues(new Uint8Array(n)), x => abc[x % abc.length]).join("");

/* ------------------------------------------------------------------ global routes (platform API) */
on("GET", "/api/health", () => ({ ok: true, version: "0.2.0-sample" }));
on("GET", "/api/session", () => PL.session);
on("GET", "/api/auth/me", () => meta());
on("POST", "/api/auth/login", ({ b }) => {
  if (PL.session.mode === "local") return err(400, "This server runs in local mode; there is nothing to sign in to.");
  if (!b.email || !b.password) return err(400, "Enter your email and password.");
  if (b.password === "throttle") return { __status: 429, json: { detail: "Too many attempts." }, retryAfter: 42 };
  if (b.password.length < 4) return err(401, "That email and password don't match an account.");
  return meta();
});
on("POST", "/api/auth/logout", () => ({ ok: true }));
on("POST", "/api/auth/password", ({ b }) => {
  if (!b.new_password || b.new_password.length < 10) return err(400, "Use at least 10 characters for the new password.");
  if (me()?.has_password && !PL.session.must_change_password && !b.current_password) return err(403, "The current password isn't right.");
  PL.session.must_change_password = false; if (me()) me().must_change_password = false;
  return { ok: true };
});
on("GET", "/api/auth/sessions", () => PL.web_sessions);
on("DELETE", "/api/auth/sessions/([^/]+)", ({ p }) => { PL.web_sessions = PL.web_sessions.filter(s => s.id !== p[0] || s.current); return { ok: true }; });
on("GET", "/api/invites/([^/]+)", ({ p }) => ({ expired: err(410, "This invite has expired."), used: err(409, "This invite has already been used."), bad: err(404, "No such invite.") }[p[0]])
  || { ...PL.invite, account_exists: p[0] === "existing" });
on("POST", "/api/invites/([^/]+)/accept", ({ p, b }) => {
  if (p[0] === "existing" ? !b.password : (!b.name || !b.password)) return err(400, "Enter your name and a password.");
  if (b.password && b.password.length < 10 && p[0] !== "existing") return err(400, "Use at least 10 characters for your password.");
  return { user: me(), team: PL.invite.team, role: PL.invite.role, created: p[0] !== "existing", csrf_token: PL.session.csrf_token };
});

on("GET", "/api/teams", () => PL.session.teams.map(t => ({ ...t, members: (PL.members[t.id] || []).length, projects: PL.session.projects.filter(p => p.team_id === t.id).length })));
on("POST", "/api/teams", ({ b }) => {
  if (!me()?.is_admin) return err(403, "Only server admins can create teams.");
  const t = { id: "t-" + rnd(6), slug: (b.slug || b.name || "team").toLowerCase().replace(/[^a-z0-9]+/g, "-"), name: b.name, settings: {}, created_at: now(), role: "owner" };
  PL.session.teams.push(t); PL.members[t.id] = [{ team_id: t.id, user_id: me().id, email: me().email, name: me().name, role: "owner", joined_at: now(), disabled: false, last_login_at: now() }]; PL.invitations[t.id] = [];
  audit("team.create", t.name, {}, t.id); return { __status: 201, json: t };
});
on("GET", "/api/teams/([^/]+)", ({ p }) => { const t = teamOf(p[0]); return t ? { ...t, permissions: TEAM_PERMS[t.role], members: (PL.members[t.id] || []).length, projects: PL.session.projects.filter(x => x.team_id === t.id).length } : err(404, "team not found"); });
on("PATCH", "/api/teams/([^/]+)", ({ p, b }) => {
  const d = needsTeam(p[0], "team.admin"); if (d) return d;
  const t = teamOf(p[0]); Object.assign(t, { name: b.name ?? t.name, slug: b.slug ?? t.slug });
  audit("team.update", t.name, { name: t.name, slug: t.slug }, p[0]); return t;
});
on("DELETE", "/api/teams/([^/]+)", ({ p, q }) => {
  const d = needsTeam(p[0], "team.delete"); if (d) return d;
  if (q.confirm !== teamOf(p[0]).slug) return err(400, "Confirm with the team's short name.");
  PL.session.teams = PL.session.teams.filter(t => t.id !== p[0]);
  PL.session.projects = PL.session.projects.filter(x => x.team_id !== p[0]); return { deleted: true };
});
on("GET", "/api/teams/([^/]+)/members", ({ p }) => ({ members: PL.members[p[0]] || [], invitations: needsTeam(p[0], "team.members") ? [] : PL.invitations[p[0]] || [] }));
on("POST", "/api/teams/([^/]+)/members", ({ p, b }) => {
  const d = needsTeam(p[0], "team.members"); if (d) return d;
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(b.email || "")) return err(400, "Enter a valid email address.");
  if ((PL.members[p[0]] || []).some(m => m.email === b.email)) return err(409, `${b.email} is already a member.`);
  const token = rnd(40, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789");
  const inv = { id: "inv-" + rnd(6), team_id: p[0], email: b.email, role: b.role || "member", invited_by: me()?.id, created_at: now(), expires_at: now() + (b.days || 7) * 86400,
    accepted_at: null, accepted_by: null, revoked_at: null, status: "pending" };
  (PL.invitations[p[0]] ||= []).unshift(inv);
  audit("invitation.create", b.email, { role: inv.role, days: b.days || 7 }, p[0]);
  return { __status: 201, json: { invitation: inv, token, url: `${location.origin}/?invite=${token}` } };
});
on("PATCH", "/api/teams/([^/]+)/members/([^/]+)", ({ p, b }) => {
  const d = needsTeam(p[0], "team.members"); if (d) return d;
  const m = (PL.members[p[0]] || []).find(m => m.user_id === p[1]); if (!m) return err(404, "No such member.");
  if (m.role === "owner" && b.role !== "owner" && PL.members[p[0]].filter(x => x.role === "owner").length === 1) return err(409, "A team needs at least one owner. Make someone else an owner first.");
  audit("member.role", m.name, { from: m.role, to: b.role }, p[0]); m.role = b.role; return { ok: true };
});
on("DELETE", "/api/teams/([^/]+)/members/([^/]+)", ({ p }) => {
  const self = p[1] === me()?.id;
  if (!self) { const d = needsTeam(p[0], "team.members"); if (d) return d; }
  const m = (PL.members[p[0]] || []).find(m => m.user_id === p[1]); if (!m) return err(404, "No such member.");
  PL.members[p[0]] = PL.members[p[0]].filter(x => x !== m); audit(self ? "member.leave" : "member.remove", m.name, {}, p[0]);
  if (self) { PL.session.teams = PL.session.teams.filter(t => t.id !== p[0]); PL.session.projects = PL.session.projects.filter(x => x.team_id !== p[0]); }
  return { ok: true };
});
on("DELETE", "/api/teams/([^/]+)/invitations/([^/]+)", ({ p }) => {
  const d = needsTeam(p[0], "team.members"); if (d) return d;
  const i = (PL.invitations[p[0]] || []).find(i => i.id === p[1]); if (!i) return err(404, "No such invitation.");
  i.status = "revoked"; i.revoked_at = now(); audit("invitation.revoke", i.email, {}, p[0]); return { ok: true };
});

on("GET", "/api/tokens", ({ q }) => PL.tokens.filter(t => (!q.team || t.team_id === q.team) && (q.all === "1" || t.user_id === me()?.id) && (q.include_revoked === "1" || t.status === "active")));
on("POST", "/api/tokens", ({ b }) => {
  if (!b.name) return err(400, "Give the token a name so you can recognise it later.");
  const prefix = rnd(8), secret = `cairn_${prefix}_${rnd(40, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")}`;
  const t = { id: "tok-" + rnd(6), prefix, name: b.name, user_id: me()?.id, team_id: b.team_id || PL.session.teams[0]?.id, project_id: b.project_id || null, scopes: b.scopes || ["agent"],
    created_by: me()?.id, created_at: now(), last_used_at: null, expires_at: b.expires_days ? now() + b.expires_days * 86400 : null, revoked_at: null, user_email: me()?.email, status: "active", display: `cairn_${prefix}_…` };
  PL.tokens.unshift(t); audit("token.create", t.name, { scopes: t.scopes }, t.team_id, t.project_id);
  return { __status: 201, json: { token: t, secret } };
});
on("DELETE", "/api/tokens/([^/]+)", ({ p }) => { const t = PL.tokens.find(t => t.id === p[0]); if (!t) return err(404, "No such token."); t.status = "revoked"; t.revoked_at = now(); audit("token.revoke", t.name, {}, t.team_id); return t; });

on("GET", "/api/projects", ({ q }) => PL.session.projects.filter(p => !q.team || p.team_id === q.team));
on("POST", "/api/projects", ({ b }) => {
  const tid = b.team_id || PL.session.teams[0]?.id;
  const d = needsTeam(tid, "project.admin"); if (d) return d;
  if (!b.path && !b.git_url) return err(400, "Give a folder path or a git URL.");
  if (b.path && PL.session.mode === "team" && !me()?.is_admin) return err(403, "Only server admins can register folders on the server.");
  if (b.path && !b.path.startsWith("/") && !/^[A-Za-z]:\\/.test(b.path)) return err(400, "Use an absolute path, for example /srv/code/app.");
  const name = b.name || String(b.path || b.git_url).replace(/\/+$/, "").replace(/\.git$/, "").split(/[\\/:]/).pop();
  const id = (b.slug || name).toLowerCase().replace(/[^a-z0-9]+/g, "-");
  if (PL.session.projects.some(p => p.id === id)) return err(409, `A project called ${name} already exists.`);
  const git = !!b.git_url;
  const pr = { id, team_id: tid, slug: id, name, kind: git ? "git" : "local", root: b.path || null, git_url: b.git_url || null, git_branch: b.branch || null, data_dir: null, managed: git,
    status: git ? "cloning" : "ready", status_detail: "", settings: {}, created_at: now(), updated_at: now(), last_synced_at: null, webhook_configured: false, role: "owner", permissions: PERMS.owner };
  PL.session.projects.push(pr); audit("project.create", name, { kind: pr.kind }, tid, id);
  if (git) setTimeout(() => { pr.status = "ready"; pr.last_synced_at = now(); }, 4000);
  return { __status: 201, json: pr };
});
on("GET", "/api/projects/([^/]+)", ({ p }) => projOf(p[0]) || err(404, "project not found"));
on("PATCH", "/api/projects/([^/]+)", ({ p, b }) => { const pr = projOf(p[0]); if (!pr) return err(404, "project not found"); const d = needs(p[0], "admin"); if (d) return d;
  Object.assign(pr, { name: b.name ?? pr.name, slug: b.slug ?? pr.slug, git_branch: b.branch ?? pr.git_branch, updated_at: now() }); return pr; });
on("DELETE", "/api/projects/([^/]+)", ({ p }) => {
  const pr = projOf(p[0]); if (!pr) return err(404, "project not found");
  const d = needs(p[0], "admin"); if (d) return d;
  PL.session.projects = PL.session.projects.filter(x => x.id !== p[0]); audit("project.delete", pr.name, {}, pr.team_id, pr.id); return { deleted: true };
});
on("POST", "/api/projects/([^/]+)/refresh", ({ p }) => {
  const pr = projOf(p[0]); if (!pr) return err(404, "project not found");
  const d = needs(p[0], "member"); if (d) return d;
  runSync(p[0], data(p[0]), false); audit("project.refresh", pr.name, {}, pr.team_id, pr.id);
  return { __status: 202, json: { accepted: true, queued_after_current: false } };
});
on("POST", "/api/projects/([^/]+)/webhook", ({ p }) => {
  const pr = projOf(p[0]); if (!pr) return err(404, "project not found");
  const d = needs(p[0], "admin"); if (d) return d;
  pr.webhook_configured = true; audit("project.webhook", pr.name, {}, pr.team_id, pr.id);
  return { secret: rnd(40, "abcdef0123456789"), url: `${location.origin}/api/projects/${pr.id}/hooks/git`, content_type: "application/json", events: ["push"],
    note: "GitHub and Gitea sign deliveries with this secret; for GitLab paste it as the secret token." };
});
on("GET", "/api/projects/([^/]+)/members", ({ p }) => {
  const pr = projOf(p[0]); if (!pr) return err(404, "project not found");
  const ov = (PL.overrides[p[0]] ||= {});
  return (PL.members[pr.team_id] || []).map(m => ({ user_id: m.user_id, email: m.email, name: m.name, disabled: m.disabled, team_role: m.role, override: m.role === "owner" ? null : ov[m.user_id] || null,
    role: m.role === "owner" ? "owner" : ov[m.user_id] || m.role }));
});
on("PUT", "/api/projects/([^/]+)/members/([^/]+)", ({ p, b }) => { const d = needs(p[0], "admin"); if (d) return d; (PL.overrides[p[0]] ||= {})[p[1]] = b.role || undefined; if (!b.role) delete PL.overrides[p[0]][p[1]]; audit("project.role", p[1], { role: b.role }, projOf(p[0]).team_id, p[0]); return { ok: true }; });
on("DELETE", "/api/projects/([^/]+)/members/([^/]+)", ({ p }) => { const d = needs(p[0], "admin"); if (d) return d; delete (PL.overrides[p[0]] ||= {})[p[1]]; return { ok: true }; });

on("GET", "/api/audit", ({ q }) => PL.audit.filter(a => (!q.team || a.team_id === q.team) && (!q.project || a.project_id === q.project) && (!q.before || a.ts < +q.before)).slice(0, +q.limit || 200));
on("GET", "/api/users", () => me()?.is_admin ? PL.users : err(403, "Only server admins can list users."));
on("POST", "/api/users/([^/]+)/disable", ({ p }) => { const u = PL.users.find(u => u.id === p[0]); if (!u) return err(404, "No such user."); u.disabled = true; audit("user.disable", u.email, {}); return { ok: true }; });
on("POST", "/api/users/([^/]+)/enable", ({ p }) => { const u = PL.users.find(u => u.id === p[0]); if (!u) return err(404, "No such user."); u.disabled = false; audit("user.enable", u.email, {}); return { ok: true }; });
on("POST", "/api/users/([^/]+)/reset-password", ({ p }) => { const u = PL.users.find(u => u.id === p[0]); if (!u) return err(404, "No such user."); u.must_change_password = true; audit("user.reset_password", u.email, {}); return { password: rnd(16, "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"), must_change: true }; });

/* ------------------------------------------------------------------ per-project data */
const EMPTY = {};
function data(pid) {
  if (pid === "cairn") return D;
  if (!projOf(pid)) return null;
  if (!EMPTY[pid]) {
    const pr = projOf(pid);
    EMPTY[pid] = {
      overview: { project: pr.name, root: pr.root, last_sync: pr.last_synced_at, syncing: false, drift: 0, active_spec: null, hubs: [],
        layers: { map: { connected: !!pr.last_synced_at, nodes: 0, edges: 0, files: 0, areas: 0, languages: {} },
          specs: { connected: false, features: 0, tasks: 0, done: 0 }, timeline: { connected: !!pr.last_synced_at, commits: 0, warnings: 0, facts: 0, deep: false },
          memory: { connected: true, memories: 0, semantic: false }, sessions: { connected: false, observations: 0, sessions: 0 } },
        models: { available: false, provider: "auto", deep: false }, capture: { on: false, agents: [] } },
      queries: [], graph: { nodes: [], links: [], communities: [], languages: {}, built_at: null }, architecture: { level: "file", nodes: [], links: [] },
      report: "", wiki: [], specs: { constitution: null, features: [] }, docs: {}, drift: [], rationale: {}, filestats: {}, cochange: [], file_tokens: {},
      workflow: { stages: ["constitution", "specify", "clarify", "plan", "tasks", "analyze", "implement"].map(id => ({ id, done_for: [] })), commands: D.workflow.commands, agents: [] },
      sessions: [], feed: [], session_stats: { sessions: 0, observations: 0, summaries: 0, prompts: 0, by_type: {}, by_concept: {}, tokens: { discovery: 0, read: 0, saved: 0 }, top_files: [] },
      session_context: "", timeline: [], facts: [], entities: [], episodes: [], fact_communities: [], memories: [], memory_history: {}, memory_sources: [],
      models: { available: false, provider: "auto", table: D.models.table, ledger: [] },
      settings: { models: { provider: "auto" }, sessions: { capture: false }, deep: { enabled: false }, context: { budget: 1800 } },
    };
    if (SYNTH[pid]) addSynthGraph(EMPTY[pid], pid);
  }
  return EMPTY[pid];
}
function P(pattern, fn, write) {
  const wrap = ctx => {
    const pid = ctx.p[0];
    const d = data(pid);
    if (!d) return err(404, `No project called ${pid}.`);
    if (write) { const deny = needs(pid, write); if (deny) return deny; }
    IX = indexOf(d);
    return fn(d, { ...ctx, p: ctx.p.slice(1), pid });
  };
  return wrap;
}
const PG = (pattern, fn) => on("GET", "/api/p/([^/]+)" + pattern, P(pattern, fn));
const PW = (method, pattern, fn, role = "member") => on(method, "/api/p/([^/]+)" + pattern, P(pattern, fn, role));

/* overview, savings, models, settings */
PG("/overview", d => ({ ...d.overview, syncing: !!SYNC[d.overview.project] }));
PG("/savings", (d, { q }) => {
  const groups = new Map();
  for (const r of d.queries) {
    const k = r.surface + "|" + r.kind;
    const g = groups.get(k) || { surface: r.surface, kind: r.kind, n: 0, sent: 0, source: 0, sent_with_source: 0 };
    g.n++; g.sent += r.sent_tokens;
    if (r.source_tokens != null) { g.source += r.source_tokens; g.sent_with_source += r.sent_tokens; }
    groups.set(k, g);
  }
  return { totals: [...groups.values()], recent: d.queries.slice(0, +q.limit || 20) };
});
PG("/models", d => d.models);
// What the server reports alongside the values: operator-set keys (team servers) and each key's rule.
const LIMITS = { "context.budget": [200, 20000], "deep.budget_tokens": [1000, 5000000] };
const RULES = { "models.provider": "one of auto, anthropic, openai, claude-code", "models.fast": "a model name", "models.balanced": "a model name", "models.deep": "a model name",
  "models.frontier": "a model name", "models.base_url": "empty, or an http(s) URL", "deep.enabled": "true, false or \"auto\"",
  ...Object.fromEntries(Object.entries(LIMITS).map(([k, [lo, hi]]) => [k, `a whole number from ${fmtN(lo)} to ${fmtN(hi)}`])) };
function fmtN(n) { return n.toLocaleString("en-US"); }
const withRules = d => ({ ...d.settings, locked: PL.session.mode === "local" ? [] : ["models.base_url", "models.provider"], rules: RULES });
function settingProblem(k, v) {
  if (!(k in RULES) && !["sessions.capture"].includes(k)) return `unknown setting: ${k}`;
  if (k === "models.provider" && !["auto", "anthropic", "openai", "claude-code"].includes(v)) return `${k} must be ${RULES[k]}`;
  if (/^models\.(fast|balanced|deep|frontier)$/.test(k) && !String(v ?? "").trim()) return `${k} needs a model name`;
  if (k === "models.base_url" && v && !/^https?:\/\//.test(v)) return `${k} must be empty or start with http:// or https://`;
  if (LIMITS[k] && !(Number.isInteger(v) && v >= LIMITS[k][0] && v <= LIMITS[k][1])) return `${k} must be ${RULES[k]}`;
  return null;
}
PG("/settings", d => withRules(d));
PW("PATCH", "/settings", (d, { b }) => {
  const locked = withRules(d).locked;
  for (const [k, v] of Object.entries(b)) {
    if (locked.includes(k)) return err(403, `${k} is set by the server's operator (server.toml), not per project`);
    const problem = settingProblem(k, v); if (problem) return err(400, problem);
  }
  for (const [k, v] of Object.entries(b)) {
    const [s, key] = k.includes(".") ? k.split(".") : [k, null];
    if (key) (d.settings[s] ||= {})[key] = v; else if (typeof v === "object") Object.assign(d.settings[s] ||= {}, v);
  }
  if (d.settings.sessions) d.overview.capture.on = !!d.settings.sessions.capture;
  audit("project.settings", d.overview.project, Object.keys(b).join(", "));
  return withRules(d);
}, "admin");

/* search */
function searchDocs(d) {
  if (d._search) return d._search;
  const out = [];
  const files = new Set();
  for (const n of d.graph.nodes) {
    if (n.file && !files.has(n.file)) { files.add(n.file); out.push({ id: "file:" + n.file, kind: "file", title: n.file, path: n.file }); }
    if (["class", "function", "method", "module"].includes(n.kind)) out.push({ id: "symbol:" + n.id, kind: "symbol", title: n.label, path: n.file + (n.loc ? ":" + n.loc.replace(/^L/, "") : ""), community: n.community });
    else if (n.kind === "section") out.push({ id: "symbol:" + n.id, kind: "doc", title: n.label, path: n.file });
  }
  for (const f of d.specs.features) {
    out.push({ id: "spec:" + f.id, kind: "spec", title: f.title, path: f.path });
    for (const t of f.tasks) out.push({ id: `task:${f.id}/${t.id}`, kind: "task", title: `${t.id} ${t.text.replace(/[`]/g, "")}`, path: f.id });
    for (const r of f.requirements) out.push({ id: `req:${f.id}/${r.id}`, kind: "requirement", title: `${r.id} ${r.text.replace(/[`]/g, "")}`, path: f.id });
    for (const s of f.stories) out.push({ id: `story:${f.id}/${s.id}`, kind: "story", title: `${s.id} ${s.title}`, path: f.id });
  }
  for (const m of d.memories) if (!m.superseded_by && !m.forgotten) out.push({ id: "memory:" + m.id, kind: "memory", title: m.text.replace(/[`]/g, ""), path: m.kind });
  for (const o of d.feed) if (o.kind === "observation") out.push({ id: "obs:" + o.id, kind: "observation", title: o.title, path: o.session_id, session_id: o.session_id });
  for (const e of d.timeline) if (e.kind === "commit") out.push({ id: e.id, kind: "commit", title: e.title, path: e.meta?.sha || "" });
  for (const f of d.facts) out.push({ id: "fact:" + f.id, kind: "fact", title: f.fact, path: f.source_entity, entity: f.source_id });
  for (const e of d.entities) out.push({ id: "entity:" + e.id, kind: "entity", title: e.name, path: e.labels.join(", ") });
  for (const w of d.wiki) out.push({ id: "wiki:" + w.slug, kind: "wiki", title: w.title, path: "wiki" });
  return (d._search = out);
}
function score(docTitle, docPath, q) {
  const t = docTitle.toLowerCase(), p = (docPath || "").toLowerCase();
  if (t === q) return 10;
  if (t.startsWith(q)) return 6;
  if (t.split(/[\s/._()-]+/).some(w => w.startsWith(q))) return 4;
  if (t.includes(q)) return 3;
  if (p.includes(q)) return 1.5;
  const words = q.split(/\s+/).filter(w => w.length > 2);
  if (words.length > 1 && words.every(w => t.includes(w) || p.includes(w))) return 2;
  return 0;
}
PG("/search", (d, { q }) => {
  const s = String(q.q || "").trim().toLowerCase();
  if (!s) return [];
  const kinds = q.kinds ? new Set(String(q.kinds).split(",")) : null;
  return searchDocs(d).filter(x => !kinds || kinds.has(x.kind))
    .map(x => ({ ...x, score: score(x.title, x.path, s) + (x.kind === "file" ? .4 : x.kind === "symbol" ? .3 : 0) }))
    .filter(x => x.score > 0).sort((a, b) => b.score - a.score).slice(0, +q.limit || 20);
});

/* sync — progress arrives on the live stream */
const SYNC = {};
const MAP_FAIL = "map build failed: the parser stopped on src/cairn/engines/graph/extract.py (line 412): unexpected indent";
const STEPS = [["map", "Code and document graph", 1300, "793 nodes, 1,546 edges; 3 files changed"], ["history", "Git history", 700, "14 commits; 1 new"],
  ["specs", "Specs and workflow", 500, "2 features, 59 tasks"], ["sessions", "Agent sessions", 600, "1 new observation"],
  ["temporal", "Timeline facts", 1100, "2 facts updated, 1 invalidated"], ["memory", "Memory", 450, "13 active memories"], ["drift", "Drift check", 500, "13 findings"]];
function runSync(pid, d, deep) {
  const name = d.overview.project;
  if (SYNC[name]) return false;
  SYNC[name] = true;
  (async () => {
    emit(pid, { type: "sync", step: "sync", state: "start", detail: deep ? "Full sync with models" : "Quick sync" });
    const failing = !!d.overview.sync_failed?.map, failed = {};
    for (const [step, label, ms, detail] of STEPS) {
      if (step === "temporal" && !d.overview.models?.available) continue;
      emit(pid, { type: "sync", step, state: "start", detail: label });
      await sleep(ms);
      if (step === "map" && failing) { failed.map = MAP_FAIL; emit(pid, { type: "sync", step, state: "fail", detail: MAP_FAIL }); continue; }
      emit(pid, { type: "sync", step, state: "done", detail: pid === "cairn" ? detail : "Nothing to index yet" });
    }
    d.overview.sync_failed = failed;
    d.overview.last_sync = now();
    const pr = projOf(pid); if (pr) pr.last_synced_at = now();
    SYNC[name] = false;
    emit(pid, { type: "sync", step: "sync", state: "done", detail: "Every layer is up to date" });
  })();
  return true;
}
PW("POST", "/sync", (d, { pid, b }) => {
  if (!runSync(pid, d, b.deep)) return { started: false, detail: "A sync is already running." };
  audit("project.sync", d.overview.project, { reason: "manual", deep: !!b.deep }, projOf(pid)?.team_id, pid);
  return { started: true };
});

/* impact & why, computed from the graph */
function resolveTarget(d, target) {
  const t = String(target || "").trim();
  if (!t) return { nodes: [], files: [], label: "" };
  if (t.startsWith("symbol:")) { const n = IX.byId.get(t.slice(7)); return n ? { nodes: [n], files: [n.file], label: n.label } : { nodes: [], files: [], label: t }; }
  const f = t.startsWith("file:") ? t.slice(5) : t;
  if (IX.byFile.has(f)) return { nodes: IX.byFile.get(f), files: [f], label: f };
  const byL = IX.byLabel.get(t.toLowerCase()) || IX.byLabel.get(t.toLowerCase() + "()") || [];
  if (byL.length) { const n = byL.sort((a, b) => b.degree - a.degree)[0]; return { nodes: [n], files: [n.file], label: n.label }; }
  const fuzzy = [...IX.byFile.keys()].find(k => k.endsWith("/" + f) || k.endsWith(f));
  if (fuzzy) return { nodes: IX.byFile.get(fuzzy), files: [fuzzy], label: fuzzy };
  return { nodes: [], files: [], label: t };
}
function walk(start, depth, exclude) {
  const seen = new Set(start.map(n => n.id));
  const out = [];
  let frontier = start;
  for (let dep = 1; dep <= depth; dep++) {
    const next = [];
    for (const n of frontier) for (const l of IX.rev.get(n.id) || []) {
      if (!DEP_RELS.has(l.rel) || seen.has(l.source)) continue;
      const s = IX.byId.get(l.source);
      if (!s || ["rationale", "concept"].includes(s.kind)) continue;
      seen.add(s.id);
      if (exclude.has(s.file)) { next.push(s); continue; }
      out.push({ id: s.id, label: s.label, file: s.file, depth: dep, rel: l.rel, via: n.label, via_id: n.id, via_file: n.file, provenance: l.prov, location: l.loc || s.loc });
      next.push(s);
    }
    frontier = next;
  }
  return out;
}
function tasksFor(d, files) {
  const out = [];
  for (const f of d.specs.features) for (const t of f.tasks) if ((t.files || []).some(([p]) => files.some(x => x === p || x.startsWith(p) && p.endsWith("/")))) out.push({ f, t });
  return out;
}
function obsFor(d, files) { return d.feed.filter(o => o.kind === "observation" && [...o.files_read, ...o.files_modified].some(p => files.includes(p))); }
function memFor(d, files, label) {
  const keys = [...files.map(f => f.split("/").pop().replace(/\.\w+$/, "")), label].filter(k => k && k.length > 2).map(k => k.toLowerCase());
  return d.memories.filter(m => !m.superseded_by && !m.forgotten && keys.some(k => m.text.toLowerCase().includes(k)));
}
function commitsFor(d, files) { return d.timeline.filter(e => e.kind === "commit" && (e.meta?.files || []).some(f => files.includes(f))); }
function pack(sections, budget) {
  const names = Object.keys(sections);
  const kept = Object.fromEntries(names.map(n => [n, []]));
  let used = 40;
  const maxLen = Math.max(0, ...names.map(n => sections[n].length));
  for (let r = 0; r < maxLen; r++) for (const n of names) {
    const it = sections[n][r]; if (!it) continue;
    const cost = tok(it.text) + 8;
    if (used + cost <= budget) { kept[n].push(it); used += cost; }
  }
  const acc = {};
  for (const n of names) if (sections[n].length) acc[n] = { kept: kept[n].length, total: sections[n].length, tokens: kept[n].reduce((a, it) => a + tok(it.text) + 8, 0) };
  return { kept, used, acc };
}
function record(d, pid, kind, target, sent, source, files) {
  const row = { ts: now(), surface: "ui", kind, target, sent_tokens: sent, source_tokens: source, source_files: files };
  d.queries.unshift(row);
  emit(pid, { type: "query", query: row });
}
PG("/impact", (d, { q, pid }) => {
  const depth = +q.depth === 1 ? 1 : 2, budget = +q.budget || d.settings?.context?.budget || 1500;
  const T = resolveTarget(d, q.target);
  if (!T.nodes.length) return { title: `Impact: ${q.target}`, header: [`Nothing in the map matches “${q.target}”.`], sections: {}, markdown: "", target: q.target, label: q.target };
  const exclude = new Set(T.files);
  const trav = walk(T.nodes, depth, exclude);
  const depFiles = [...new Set(trav.map(e => e.file))];
  const fixes = T.files.reduce((a, f) => a + (d.filestats[f]?.fixes || 0), 0);
  const risk = depFiles.length >= 15 || fixes >= 2 ? "HIGH" : depFiles.length >= 5 || fixes ? "MEDIUM" : "LOW";
  const reasons = [`${trav.length} dependents in ${depFiles.length} other files`];
  if (fixes) reasons.push(`${fixes} fix commit${fixes === 1 ? "" : "s"} touched it`);
  const tests = trav.filter(e => /(^|\/)tests?\/|test_/.test(e.file));
  const line = e => `${e.label} ${e.file}${e.location ? ":" + String(e.location).replace(/^L/, "") : ""} — ${e.rel.replace(/_/g, " ")} ${e.via} (depth ${e.depth}, ${e.provenance})`;
  const cochange = d.cochange.filter(c => T.files.includes(c.a) || T.files.includes(c.b)).slice(0, 8)
    .map(c => ({ text: `${T.files.includes(c.a) ? c.b : c.a} changed with it in ${c.count} commit${c.count === 1 ? "" : "s"}`, cite: "file:" + (T.files.includes(c.a) ? c.b : c.a) }));
  const warn = commitsFor(d, T.files).filter(e => /^(fix|revert)/i.test(e.title)).map(e => ({ text: `${e.meta.sha} ${e.title}`, cite: e.id }));
  const sections = {
    "Dependents": trav.filter(e => !tests.includes(e)).sort((a, b) => a.depth - b.depth).map(e => ({ text: line(e), cite: "symbol:" + e.id, score: e.depth === 1 ? 1 : .6 })),
    "Tests likely affected": tests.map(e => ({ text: line(e), cite: "symbol:" + e.id })),
    "Historical warnings": warn,
    "Intent": tasksFor(d, T.files).map(({ f, t }) => ({ text: `${t.id} ${t.text.replace(/^\W+/, "")} (${f.id}, ${t.done ? "done" : "open"})`, cite: `task:${f.id}/${t.id}` })),
    "Agent sessions": obsFor(d, T.files).map(o => ({ text: `${o.type}: ${o.title} — session ${o.session_id}`, cite: "obs:" + o.id })),
    "Memory": memFor(d, T.files, T.label).map(m => ({ text: `${m.kind}: ${m.text}`, cite: "memory:" + m.id })),
    "Changes together": cochange,
  };
  const { kept, used, acc } = pack(sections, budget);
  const evidence = [...new Set([...T.files, ...depFiles])];
  const source = evidence.reduce((a, f) => a + (d.file_tokens[f] || 300), 0);
  const header = [`**Risk: ${risk}** — ${reasons.join("; ")}`];
  const md = [`## Impact: ${T.label}`, ...header, ...Object.entries(kept).filter(([, v]) => v.length).flatMap(([k, v]) => [`### ${k}`, ...v.map(it => `- ${it.text} [${it.cite}]`)])].join("\n");
  record(d, pid, "impact", T.label, used, source, evidence.length);
  return { title: `Impact: ${T.label}`, header, sections, markdown: md, tokens: { used, budget, sections: acc }, source: { tokens: source, files: evidence.length },
    risk, reasons, files: T.files, dependents: trav.length, dependent_files: depFiles, target: q.target, label: T.label,
    target_nodes: T.nodes.map(n => n.id), traversal: trav, evidence_files: evidence };
});
PG("/why", (d, { q, pid }) => {
  const T = resolveTarget(d, q.target);
  const budget = +q.budget || 1200;
  const files = T.files;
  const rat = files.flatMap(f => (d.rationale[f] || []).map(r => ({ text: `${f}${r.loc ? ":" + r.loc.replace(/^L/, "") : ""} — “${r.text}”`, cite: `rationale:${f}:${r.loc}` })));
  const origin = commitsFor(d, files).map(e => ({ text: `${e.meta.sha} ${e.title} (${e.actor || "unknown"})`, cite: e.id }));
  const intent = tasksFor(d, files).map(({ f, t }) => ({ text: `${t.id} ${t.text.replace(/^\W+/, "")}`, cite: `task:${f.id}/${t.id}` }));
  const decisions = [...d.memories.filter(m => m.kind === "decision" && !m.superseded_by && memFor(d, files, T.label).includes(m)).map(m => ({ text: m.text, cite: "memory:" + m.id })),
    ...obsFor(d, files).filter(o => o.type === "decision").map(o => ({ text: `${o.title} — ${o.subtitle}`, cite: "obs:" + o.id }))];
  const sections = { "Rationale": rat, "Origin": origin, "Intent": intent, "Decisions": decisions };
  const { kept, used, acc } = pack(sections, budget);
  const source = files.reduce((a, f) => a + (d.file_tokens[f] || 300), 0);
  record(d, pid, "why", T.label, used, source, files.length);
  return { title: `Why: ${T.label}`, sections, markdown: Object.entries(kept).filter(([, v]) => v.length).map(([k, v]) => `### ${k}\n` + v.map(it => `- ${it.text}`).join("\n")).join("\n"),
    tokens: { used, budget, sections: acc }, source: { tokens: source, files: files.length }, files };
});

/* graph */
PG("/graph/summary", d => ({ nodes: d.graph.nodes.length, edges: d.graph.links.length, files: new Set(d.graph.nodes.map(n => n.file).filter(Boolean)).size,
  communities: d.graph.communities, languages: d.graph.languages, built_at: d.graph.built_at }));
PG("/graph/data", (d, { q }) => {
  const limit = +q.limit || 600, scope = String(q.scope || "all");
  if (scope === "communities") {
    const comm = new Map();
    for (const n of d.graph.nodes) { const c = comm.get(n.community) || { n: [], dirs: new Map(), files: new Map() }; c.n.push(n);
      if (n.file) { const dir = n.file.includes("/") ? n.file.slice(0, n.file.lastIndexOf("/")) : ""; c.dirs.set(dir, (c.dirs.get(dir) || 0) + 1); c.files.set(n.file, (c.files.get(n.file) || 0) + 1); }
      comm.set(n.community, c); }
    const top = [...comm].sort((a, b) => b[1].n.length - a[1].n.length).slice(0, limit);
    const keep = new Set(top.map(([id]) => id));
    const nodes = top.map(([id, c]) => ({ id: "community:" + id, community: id, kind: "community", label: IX.commName.get(id) || `community ${id}`, size: c.n.length,
      top: [...c.n].sort((a, b) => b.degree - a.degree).slice(0, 5).map(n => n.label), degree: 0,
      area: [...c.dirs].sort((a, b) => b[1] - a[1])[0]?.[0] || "", files: [...c.files].sort((a, b) => b[1] - a[1]).slice(0, 3) }));
    const w = new Map();
    for (const l of d.graph.links) {
      if (!DEP_RELS.has(l.rel)) continue;
      const a = IX.byId.get(l.source)?.community, b = IX.byId.get(l.target)?.community;
      if (a == null || b == null || a === b || !keep.has(a) || !keep.has(b)) continue;
      const k = a + ">" + b; w.set(k, (w.get(k) || 0) + 1);
    }
    const links = [...w].map(([k, weight]) => { const [a, b] = k.split(">"); return { source: "community:" + a, target: "community:" + b, rel: "depends_on", weight }; });
    nodes.forEach(n => { n.degree = links.filter(l => l.source === n.id || l.target === n.id).length; });
    return { level: "communities", nodes, links, truncated: comm.size > limit, total: comm.size };
  }
  let pick;
  if (scope.startsWith("community:")) pick = d.graph.nodes.filter(n => String(n.community) === scope.slice(10));
  else if (scope.startsWith("file:")) {
    const own = d.graph.nodes.filter(n => n.file === scope.slice(5)); const ids = new Set(own.map(n => n.id));
    for (const n of own) for (const l of [...(IX.fwd.get(n.id) || []), ...(IX.rev.get(n.id) || [])]) { ids.add(l.source); ids.add(l.target); }
    pick = [...ids].map(i => IX.byId.get(i));
  } else if (scope.startsWith("around:")) {
    const ids = new Set([scope.slice(7)]);
    for (let k = 0; k < 2; k++) for (const i of [...ids]) for (const l of [...(IX.fwd.get(i) || []), ...(IX.rev.get(i) || [])]) { ids.add(l.source); ids.add(l.target); }
    pick = [...ids].map(i => IX.byId.get(i)).filter(Boolean);
  } else pick = d.graph.nodes;
  const total = pick.length;
  pick = [...pick].sort((a, b) => b.degree - a.degree).slice(0, limit);
  const ids = new Set(pick.map(n => n.id));
  return { nodes: pick.map(({ id, label, kind, file, community, degree }) => ({ id, label, kind, file, community, degree })),
    links: d.graph.links.filter(l => ids.has(l.source) && ids.has(l.target)).map(({ source, target, rel, weight, prov }) => ({ source, target, rel, weight, provenance: prov })),
    truncated: total > limit, total };
});
PG("/graph/architecture", d => d.architecture);
PG("/graph/node", (d, { q }) => {
  const n = IX.byId.get(q.id); if (!n) return err(404, "No such node.");
  const nb = [];
  for (const l of IX.fwd.get(n.id) || []) if (l.rel !== "rationale_for") { const m = IX.byId.get(l.target); nb.push({ id: m.id, label: m.label, kind: m.kind, rel: l.rel, direction: "out", file: m.file, provenance: l.prov }); }
  for (const l of IX.rev.get(n.id) || []) if (l.rel !== "rationale_for") { const m = IX.byId.get(l.source); nb.push({ id: m.id, label: m.label, kind: m.kind, rel: l.rel, direction: "in", file: m.file, provenance: l.prov }); }
  const rat = (IX.rev.get(n.id) || []).filter(l => l.rel === "rationale_for").map(l => IX.byId.get(l.source)?.label).filter(Boolean);
  const count = (dir, rels) => nb.filter(x => x.direction === dir && rels.includes(x.rel)).length;
  const callers = count("in", ["calls", "indirect_call"]), importers = count("in", ["imports", "imports_from"]), refs = count("in", ["references"]);
  const calls = count("out", ["calls", "indirect_call"]);
  const cname = IX.commName.get(n.community) || "an unnamed group";
  const parts = [`${n.label} is a ${n.kind}${n.file ? ` in ${n.file}${n.loc ? " at line " + n.loc.replace(/^L/, "") : ""}` : ""}, part of the “${cname}” group.`];
  const ins = [callers && `${callers} caller${callers === 1 ? "" : "s"}`, importers && `${importers} importer${importers === 1 ? "" : "s"}`, refs && `${refs} document reference${refs === 1 ? "" : "s"}`].filter(Boolean);
  if (ins.length) parts.push(`It has ${ins.join(", ")}.`);
  if (calls) parts.push(`It calls ${calls} other function${calls === 1 ? "" : "s"}.`);
  if (!nb.length) parts.push("Nothing else in the graph connects to it.");
  if (rat.length) parts.push(`Its comments say: “${rat[0]}”`);
  return { node: { ...n, community_name: cname }, neighbours: nb, rationale: rat, explain_text: parts.join(" ") };
});
PG("/graph/path", (d, { q }) => {
  const a = q.a, b = q.b;
  if (!IX.byId.has(a) || !IX.byId.has(b)) return err(404, "Pick two nodes that are in the graph.");
  const prev = new Map([[a, null]]); const Q = [a];
  while (Q.length && !prev.has(b)) {
    const x = Q.shift();
    for (const l of [...(IX.fwd.get(x) || []), ...(IX.rev.get(x) || [])]) {
      const y = l.source === x ? l.target : l.source;
      if (!prev.has(y)) { prev.set(y, { from: x, l }); Q.push(y); }
    }
  }
  if (!prev.has(b)) return { path: [], nodes: [], links: [], text: `No path connects ${IX.byId.get(a).label} and ${IX.byId.get(b).label}.` };
  const path = [], links = [];
  for (let x = b; x; x = prev.get(x)?.from) { path.unshift(x); const s = prev.get(x); if (s) links.unshift({ source: s.l.source, target: s.l.target, rel: s.l.rel }); }
  const nodes = path.map(i => IX.byId.get(i));
  const text = nodes.map((n, i) => i === 0 ? n.label : `${links[i - 1].rel.replace(/_/g, " ")} ${n.label}`).join(" → ");
  return { path, nodes, links, text: `${path.length - 1} step${path.length === 2 ? "" : "s"}: ${text}` };
});
const STOP = new Set(["the", "and", "what", "which", "how", "does", "where", "who", "that", "this", "with", "from", "for", "into", "are", "why", "use", "uses", "used", "code", "file", "files"]);
PG("/graph/query", (d, { q }) => {
  const words = String(q.q || "").toLowerCase().split(/[^a-z0-9_.]+/).filter(w => w.length > 2 && !STOP.has(w));
  if (!words.length) return { text: "Ask about a part of the code, for example “how does sync reach the read model”.", nodes: [] };
  const hit = d.graph.nodes.filter(n => words.some(w => n.label.toLowerCase().includes(w) || n.file.toLowerCase().includes(w))).sort((a, b) => b.degree - a.degree).slice(0, 6);
  const ids = new Set(hit.map(n => n.id));
  for (const n of hit) for (const l of (IX.fwd.get(n.id) || []).slice(0, 6)) if (DEP_RELS.has(l.rel)) ids.add(l.target);
  const files = new Set([...ids].map(i => IX.byId.get(i).file));
  const text = hit.length ? `Started from ${hit.slice(0, 3).map(n => n.label).join(", ")}${hit.length > 3 ? ` and ${hit.length - 3} more` : ""} and followed what they use: ${ids.size} nodes across ${files.size} files. The most connected is ${hit[0].label} (${hit[0].degree} links) in ${hit[0].file}.`
    : `Nothing in the graph matches ${words.join(", ")}.`;
  const out = [...ids].slice(0, 60);
  return { summary: hit.length ? text.split(":")[0] + "." : text, text, nodes: out, node_communities: Object.fromEntries(out.map(i => [i, IX.byId.get(i)?.community])) };
});
PG("/graph/views", d => d.graph.nodes.length ? [{ kind: "graph", title: "Full graph" }, { kind: "callflow", title: "Call flow" }, { kind: "tree", title: "File tree" }] : []);
PG("/graph/wiki", d => d.wiki.map(({ slug, title, community }) => ({ slug, title, community })));
PW("POST", "/graph/wiki", d => {
  if (!d.graph.nodes.length) return { ok: false, log: "[graph] error: the graph is empty. Run a sync first." };
  if (!d.wiki.length) {
    const byComm = new Map();
    for (const n of d.graph.nodes) (byComm.get(n.community) || byComm.set(n.community, []).get(n.community)).push(n);
    d.wiki = [...byComm].map(([c, ns]) => {
      const title = IX.commName.get(c) || `Community ${c}`;
      return { slug: "community-" + c, title, community: c, markdown: `# ${title}\n\n${ns.length} nodes, in ${[...new Set(ns.map(n => n.file).filter(Boolean))].map(f => "`" + f + "`").join(", ")}.\n\n${ns.slice(0, 12).map(n => `- **${n.label}** (${n.kind})`).join("\n")}\n` };
    });
  }
  return { ok: true, log: `Wiki: ${d.wiki.length} articles written` };
});
PG("/graph/wiki/([^/]+)", (d, { p }) => d.wiki.find(w => w.slug === p[0]) || err(404, "No such wiki page."));
PG("/graph/report", d => ({ markdown: d.report }));

/* specs */
PG("/specs", d => d.specs);
PG("/specs/([^/]+)/doc/(.+)", (d, { p }) => {
  const doc = d.docs[p[0]]?.[p[1]];
  return doc == null ? err(404, `${p[1]} doesn't exist in this feature yet.`) : { name: p[1], markdown: doc };
});
PW("PATCH", "/specs/([^/]+)/tasks/([^/]+)", (d, { p, b }) => {
  const f = d.specs.features.find(x => x.id === p[0]); const t = f?.tasks.find(x => x.id === p[1]);
  if (!t) return err(404, "No such task.");
  t.done = !!b.done; f.progress = { done: f.tasks.filter(x => x.done).length, total: f.tasks.length };
  const doc = d.docs[f.id]?.["tasks.md"];
  if (doc) d.docs[f.id]["tasks.md"] = doc.replace(new RegExp(`- \\[[ xX]\\] ${t.id}\\b`), `- [${t.done ? "x" : " "}] ${t.id}`);
  audit("task.update", d.overview.project, `${f.id} ${t.id} marked ${t.done ? "done" : "not done"}`);
  return t;
});
PG("/drift", (d, { q }) => ({ findings: d.drift.filter(x => !q.spec || x.spec === q.spec) }));
PG("/workflow", d => d.workflow);

/* sessions */
PG("/sessions/feed", (d, { q }) => {
  let items = d.feed;
  if (q.type) { const ts = String(q.type).split(","); items = items.filter(x => x.kind === "observation" ? ts.includes(x.type) : ts.includes(x.kind)); }
  if (q.kind) { const ks = String(q.kind).split(","); items = items.filter(x => ks.includes(x.kind)); }
  if (q.concept) items = items.filter(x => (x.concepts || []).includes(q.concept));
  if (q.file) items = items.filter(x => [...(x.files_read || []), ...(x.files_modified || []), ...(x.files_edited || [])].some(f => f.includes(q.file)));
  if (q.session) items = items.filter(x => x.session_id === q.session);
  if (q.q) { const s = String(q.q).toLowerCase(); items = items.filter(x => JSON.stringify(x).toLowerCase().includes(s)); }
  const start = +q.cursor || 0, lim = +q.limit || 50;
  return { items: items.slice(start, start + lim), next_cursor: start + lim < items.length ? String(start + lim) : null };
});
PG("/sessions/stats", d => ({ ...d.session_stats, queue: (({ pending, processing, failed }) => ({ pending, processing, failed }))(demoQueue()) }));
PG("/sessions/context", d => ({ markdown: d.session_context }));
PG("/sessions/search", (d, { q }) => {
  const words = String(q.q || "").toLowerCase().split(/\s+/).filter(Boolean);
  const res = d.feed.filter(x => x.kind !== "prompt" && (!q.type || x.type === q.type)).map(x => {
    const s = JSON.stringify([x.title, x.subtitle, x.narrative, x.facts, x.request, x.learned, x.completed]).toLowerCase();
    return { ...x, score: words.reduce((a, w) => a + (s.split(w).length - 1), 0) };
  }).filter(x => x.score > 0).sort((a, b) => b.score - a.score).slice(0, +q.limit || 20);
  return { results: res };
});
const QUEUE = { pending: 0, processing: 0, failed: 0 };
const demoQueue = () => { if (new URL(location.href).searchParams.get("queue") === "1" && !QUEUE._set) { Object.assign(QUEUE, { pending: 1412, processing: 5, failed: 3, _set: true }); } return QUEUE; };
PG("/sessions/list", d => ({ items: d.sessions.map(s => ({ ...s, prompts: d.feed.filter(x => x.kind === "prompt" && x.session_id === s.id).length, summaries: d.feed.filter(x => x.kind === "summary" && x.session_id === s.id).length,
  pending: s.status === "active" ? demoQueue().pending : 0 })).sort((a, b) => b.started - a.started), has_more: false }));
PG("/sessions/status", () => { const q = demoQueue(); return { isProcessing: q.processing > 0, queueDepth: q.pending + q.processing, failed: q.failed, workerRunning: false, parkedSessions: 0,
  health: q.pending ? { consecutiveFailures: 1, lastErrorAt: Date.now() - 42 * 60000, lastErrorProvider: "claude-code", lastErrorKind: "quota_exhausted", lastErrorMessage: "model call failed: You've hit your session limit · resets 3:50am" } : {} }; });
PG("/sessions/queue", () => { const q = demoQueue(); return { totals: { pending: q.pending, processing: q.processing, failed: q.failed }, groups: [] }; });
PG("/sessions/settings", () => ({ settings: { capture: true, worker_spawn: !demoQueue().pending, mode: "code" } }));
PW("POST", "/sessions/process", (d, { b }) => { const q = demoQueue(); const n = q.pending + q.processing; q.pending = 0; q.processing = 0; return { processed: n, no_model: !!b.no_model }; });
PW("POST", "/sessions/queue/([^/]+)", (d, { p }) => { const q = demoQueue(); if (p[0] === "clear") q.pending = 0; else if (p[0] === "clear-failed") q.failed = 0; else if (p[0] === "retry") { q.pending += q.failed; q.failed = 0; } return { ok: true }; });
PW("DELETE", "/sessions/(observation|summary|prompt)/([^/]+)", (d, { p }) => { d.feed = d.feed.filter(x => !(x.kind === p[0] && String(x.id) === p[1])); d._search = null; return { ok: true }; });
PG("/sessions/([^/]+)", (d, { p }) => {
  const s = d.sessions.find(x => x.id === p[0]); if (!s) return err(404, "No such session.");
  const items = d.feed.filter(x => x.session_id === s.id);
  return { session: s, prompts: items.filter(x => x.kind === "prompt"), observations: items.filter(x => x.kind === "observation"), summaries: items.filter(x => x.kind === "summary") };
});

/* timeline & facts */
PG("/timeline", (d, { q }) => {
  let ev = d.timeline;
  if (q.kinds) { const k = String(q.kinds).split(","); ev = ev.filter(e => k.includes(e.kind)); }
  if (q.since) ev = ev.filter(e => e.ts >= +q.since);
  return ev.slice(0, +q.limit || 400);
});
const validAt = (f, t) => f.valid_at <= t && (!f.invalid_at || f.invalid_at > t);
PG("/facts", (d, { q }) => {
  let fs = d.facts;
  if (q.at) { const t = Date.parse(q.at) / 1000 || +q.at; fs = fs.filter(f => validAt(f, t)); }
  if (q.q) { const s = String(q.q).toLowerCase(); fs = fs.filter(f => (f.fact + f.source_entity + f.target_entity).toLowerCase().includes(s)); }
  return fs.slice(0, +q.limit || 500);
});
PG("/facts/entities", (d, { q }) => d.entities.filter(e => !q.q || e.name.toLowerCase().includes(String(q.q).toLowerCase())).slice(0, +q.limit || 100));
PG("/facts/entities/([^/]+)", (d, { p }) => {
  const e = d.entities.find(x => x.id === p[0]); if (!e) return err(404, "No such entity.");
  const facts = d.facts.filter(f => f.source_id === e.id || f.target_id === e.id);
  const eps = new Set(facts.flatMap(f => f.episodes));
  return { entity: e, facts, episodes: d.episodes.filter(x => eps.has(x.id)) };
});
PG("/facts/communities", d => d.fact_communities);
PG("/facts/episodes", (d, { q }) => d.episodes.slice(0, +q.limit || 50));

/* memory with reconciliation */
const words = s => new Set(String(s).toLowerCase().replace(/[`.,;:()"“”]/g, " ").split(/\s+/).filter(w => w.length > 2));
function similarity(a, b) { const A = words(a), B = words(b); let i = 0; for (const w of A) if (B.has(w)) i++; return i / Math.max(1, Math.min(A.size, B.size)); }
const liveMem = m => !m.superseded_by && !m.forgotten;
const countMem = d => { d.overview.layers.memory.memories = d.memories.filter(liveMem).length; };
PG("/memories", (d, { q }) => d.memories.filter(m => (q.all === "1" || q.all === "true" || liveMem(m)) && (!q.kind || m.kind === q.kind) && (!q.q || m.text.toLowerCase().includes(String(q.q).toLowerCase())))
  .sort((a, b) => b.updated_at - a.updated_at));
PG("/memories/sources", d => d.memory_sources);
PG("/memories/([^/]+)/history", (d, { p }) => d.memory_history[p[0]] || []);
PW("POST", "/memories", (d, { b, pid }) => {
  const text = String(b.text || "").trim();
  if (text.length < 8) return err(400, "Write the memory as one full sentence.");
  const active = d.memories.filter(liveMem);
  const best = active.map(m => ({ m, s: similarity(text, m.text) })).sort((x, y) => y.s - x.s)[0];
  const who = actor();
  if (best && best.s >= .9) return { ...best.m, op: "NONE", reason: "Cairn already knows this." };
  const NEG = /\b(no longer|not|never|instead|stopped|replaced|don't|doesn't)\b/gi;
  const negs = t => new Set((t.match(NEG) || []).map(w => w.toLowerCase()));
  const differs = (a, b) => { const A = negs(a), B = negs(b); return A.size !== B.size || [...A].some(w => !B.has(w)); };
  const contradicts = best && best.s >= .45 && differs(text, best.m.text) && /\b(no longer|instead|stopped|replaced)\b/i.test(text);
  if (best && best.s >= .5 && !contradicts && best.m.kind === (b.kind || best.m.kind)) {
    const old = best.m.text; best.m.text = text; best.m.updated_at = now();
    (d.memory_history[best.m.id] ||= []).push({ ts: now(), op: "UPDATE", old, new: text, actor: who });
    emit(pid, { type: "memory", memory: best.m, op: "UPDATE" });
    audit("memory.update", d.overview.project, `${best.m.id} rewritten after reconciliation`);
    return { ...best.m, op: "UPDATE", previous: old };
  }
  const m = { id: "mem-" + Math.random().toString(36).slice(2, 7), text, kind: b.kind || "fact", scope: "project", source: who, provenance: "user",
    confidence: 1, created_at: now(), updated_at: now(), superseded_by: null, citation: null };
  d.memories.unshift(m);
  d.memory_history[m.id] = [{ ts: now(), op: "ADD", old: null, new: text, actor: who }];
  countMem(d); d._search = null;
  if (contradicts) {
    best.m.superseded_by = m.id; best.m.updated_at = now();
    (d.memory_history[best.m.id] ||= []).push({ ts: now(), op: "DELETE", old: best.m.text, new: null, actor: who });
    emit(pid, { type: "memory", memory: m, op: "DELETE" });
    audit("memory.delete", d.overview.project, `${best.m.id} replaced by ${m.id}`);
    return { ...m, op: "DELETE", replaced: clone(best.m) };
  }
  emit(pid, { type: "memory", memory: m, op: "ADD" });
  audit("memory.add", d.overview.project, text.slice(0, 80));
  return { ...m, op: "ADD" };
});
PW("PATCH", "/memories/([^/]+)", (d, { p, b }) => {
  const m = d.memories.find(x => x.id === p[0]); if (!m) return err(404, "No such memory.");
  const old = m.text; if (b.text) m.text = b.text; if (b.kind) m.kind = b.kind; m.updated_at = now();
  (d.memory_history[m.id] ||= []).push({ ts: now(), op: "UPDATE", old, new: m.text, actor: actor() });
  audit("memory.update", d.overview.project, m.id);
  return m;
});
PW("DELETE", "/memories/([^/]+)", (d, { p }) => {
  const m = d.memories.find(x => x.id === p[0]); if (!m) return err(404, "No such memory.");
  // Like the server: forgetting keeps the row (forgotten = 1) so history and "all" still show it.
  m.forgotten = 1; m.updated_at = now(); d._search = null;
  (d.memory_history[m.id] ||= []).push({ ts: now(), op: "DELETE", old: m.text, new: null, actor: actor() });
  countMem(d);
  audit("memory.delete", d.overview.project, m.id);
  return { ok: true };
});

/* ------------------------------------------------------------------ live stream */
const subs = new Map();
function emit(pid, ev) { for (const cb of subs.get(pid) || []) setTimeout(() => cb(clone(ev)), 0); }
const LIVE = [
  ["feature", "Graph view loads its layout library only when opened", "cytoscape and the force layout arrive on first visit to Map, not with the page",
    "The graph libraries are 800 KB together, so they load the first time someone opens the Map view. Every other page starts with Preact, htm and the page's own modules only.",
    ["Lazy script injection", "Other views stay under 100 KB of JavaScript"], ["pattern", "trade-off"], ["src/cairn/ui/app/lib/cyto.js"], ["src/cairn/ui/app/views/map.js"]],
  ["discovery", "Temporal facts need their invalidated windows to draw history", "/facts without `at` must return invalidated facts too",
    "Drawing validity bars needs every fact, including the ones a later episode invalidated. With `at`, the API returns only what was true then; without it, the UI expects everything.",
    ["Bars run from valid_at to invalid_at, or to now", "The as-of rule filters client-side"], ["gotcha", "how-it-works"], ["src/cairn/ui/app/views/timeline.js"], []],
  ["change", "Memory form explains what reconciliation did", "The toast says whether a memory was added, rewritten, replaced or already known",
    "Saving a memory can rewrite or replace an existing one. The form now reports the operation the memory store chose and shows the old text next to the new one.",
    ["ADD, UPDATE, DELETE or NONE", "Old and new text shown for updates"], ["what-changed"], [], ["src/cairn/ui/app/views/memory.js"]],
  ["bugfix", "Impact diagram overflowed on phones", "Columns now scroll inside the diagram instead of widening the page",
    "At 390 px the three traversal columns pushed the page sideways. The diagram keeps its own horizontal scroll and the page body never scrolls sideways.",
    ["Diagram min-width is the SVG's width", "Page body stays at viewport width"], ["problem-solution"], [], ["src/cairn/ui/app/components/traversal.js", "src/cairn/ui/styles/views.css"]],
  ["decision", "API token secrets are shown exactly once", "The create dialog is the only place a secret appears; the list shows its prefix",
    "Tokens are stored as hashes, so the UI cannot show a secret again. The dialog makes that explicit and offers a copy button before it closes.",
    ["Only the prefix is listed", "Revoking is immediate"], ["why-it-exists"], [], ["src/cairn/ui/app/views/team.js"]],
];
let liveIx = 0;
function pushLive() {
  const [type, title, subtitle, narrative, facts, concepts, read, mod] = LIVE[liveIx % LIVE.length];
  liveIx++;
  const o = { kind: "observation", id: "obs-live-" + liveIx, ts: now(), session_id: "s-b20d77", type, title, subtitle, narrative, facts, concepts,
    files_read: read, files_modified: mod, prompt_number: 1, tokens: { discovery: 4000 + Math.round(Math.random() * 9000), read: 120 + Math.round(Math.random() * 90) } };
  D.feed.unshift(o);
  const st = D.session_stats; st.observations++; st.by_type[type] = (st.by_type[type] || 0) + 1;
  st.tokens.discovery += o.tokens.discovery; st.tokens.read += o.tokens.read; st.tokens.saved = st.tokens.discovery - st.tokens.read;
  const s = D.sessions.find(x => x.id === "s-b20d77"); if (s) s.observations++;
  D.overview.layers.sessions.observations++;
  D._search = null;
  emit("cairn", { type: "observation", observation: o });
}
let liveTimer = 0;
export function stream(pid, cb) {
  if (!subs.has(pid)) subs.set(pid, new Set());
  subs.get(pid).add(cb);
  setTimeout(() => cb({ type: "_open" }), 50);
  if (pid === "cairn" && !liveTimer && new URL(location.href).searchParams.get("live") !== "0") {
    liveTimer = setTimeout(function tick() { pushLive(); liveTimer = setTimeout(tick, 17000 + Math.random() * 8000); }, 9000);
  }
  const ping = setInterval(() => cb({ type: "ping" }), 25000);
  return () => { subs.get(pid)?.delete(cb); clearInterval(ping); };
}

/* ------------------------------------------------------------------ streamed explanations */
const abortable = (ms, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"));
  const t = setTimeout(resolve, ms);
  signal?.addEventListener("abort", () => { clearTimeout(t); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
});
const baseName = p => String(p || "").split("/").pop();
function sentenceList(xs) { return xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1]; }

function impactAnswer(d, target) {
  const T = resolveTarget(d, target);
  if (!T.nodes.length) return null;
  const trav = walk(T.nodes, 2, new Set(T.files));
  const byFile = new Map(); trav.forEach(e => byFile.set(e.file, (byFile.get(e.file) || 0) + 1));
  const files = [...byFile].sort((a, b) => b[1] - a[1]);
  const code = files.filter(([f]) => !/(^|\/)tests?\/|test_/.test(f) && !/\.md$/.test(f)).slice(0, 3);
  const tests = files.filter(([f]) => /(^|\/)tests?\/|test_/.test(f)).slice(0, 2);
  const direct = new Set(trav.filter(e => e.depth === 1).map(e => e.file));
  const mem = memFor(d, T.files, T.label)[0];
  const task = tasksFor(d, T.files)[0];
  const name = baseName(T.label);
  let md = `Changing **${name}** reaches ${files.length} other file${files.length === 1 ? "" : "s"} within two steps, ${direct.size} of them directly.`;
  if (code.length) md += ` The code that leans on it most is ${sentenceList(code.map(([f, n]) => `[file:${f}] (${n} link${n === 1 ? "" : "s"})`))}.`;
  md += "\n\n";
  if (tests.length) md += `${tests.length === 1 ? "One test tells" : "Two tests tell"} you quickly if something broke: ${sentenceList(tests.map(([f]) => `[file:${f}]`))}. Run ${tests.length === 1 ? "it" : "those"} first.\n\n`;
  if (mem) md += `The team recorded a rule that applies here: ${mem.text} [memory:${mem.id}]\n\n`;
  if (task) md += `It was written for ${task.t.id} in spec ${task.f.id}, “${task.t.text.replace(/^\W+/, "").replace(/`/g, "")}” [task:${task.f.id}/${task.t.id}].\n\n`;
  md += `**Before you merge**\n\n- Keep its public names stable; ${direct.size} file${direct.size === 1 ? "" : "s"} import or call them directly.\n- Re-run the impact check after the change to see whether the reach grew.\n`;
  if (tests.length) md += `- Run \`${baseName(tests[0][0])}\`, which reaches it in ${tests[0][1]} place${tests[0][1] === 1 ? "" : "s"}.\n`;
  return md;
}
function whyAnswer(d, target) {
  const T = resolveTarget(d, target);
  if (!T.nodes.length) return null;
  const f = T.files[0], rat = (d.rationale[f] || []).slice(0, 2);
  const commits = commitsFor(d, T.files).slice(-2);
  const dec = memFor(d, T.files, T.label).find(m => m.kind === "decision") || d.memories.find(m => m.kind === "decision" && !m.superseded_by);
  let md = `**${baseName(T.label)}** is shaped by ${rat.length ? "what its own comments say" : "the history around it"} and by the decisions the team wrote down.\n\n`;
  if (rat.length) md += `Its comments explain the intent: ${rat.map(r => `“${r.text}” [file:${f}]`).join(" ")}\n\n`;
  if (commits.length) md += `It came in with ${commits.map(e => `“${e.title}” [commit:${String(e.id).replace(/^commit:/, "")}]`).join(", then ")}.\n\n`;
  if (dec) md += `The decision behind it: ${dec.text} [memory:${dec.id}]\n\n`;
  md += "If you change how it works, update the comment or record a new decision so the next person gets the same answer.\n";
  return md;
}
function askAnswer(d, question) {
  const words = String(question).toLowerCase().split(/[^a-z0-9_.]+/).filter(w => w.length > 2 && !STOP.has(w));
  const docs = searchDocs(d).map(x => ({ ...x, s: words.reduce((a, w) => a + score(x.title, x.path, w), 0) })).filter(x => x.s > 0).sort((a, b) => b.s - a.s);
  const pick = kind => docs.filter(x => kind.includes(x.kind)).slice(0, 3);
  const files = pick(["file"]), code = pick(["symbol"]), specs = pick(["task", "requirement", "spec"]), mems = pick(["memory"]), sess = pick(["observation"]);
  const cite = x => `[${x.id.replace(/^obs:/, "obs:")}]`;
  const packLines = [
    files.length && `### Files\n${files.map(x => `- \`${x.path}\` ${cite(x)}`).join("\n")}`,
    code.length && `### Code\n${code.map(x => `- \`${x.title}\` in \`${x.path}\` ${cite(x)}`).join("\n")}`,
    specs.length && `### Specs\n${specs.map(x => `- ${x.title.replace(/`/g, "")} ${cite(x)}`).join("\n")}`,
    mems.length && `### Memory\n${mems.map(x => `- ${x.title} ${cite(x)}`).join("\n")}`,
    sess.length && `### Agent sessions\n${sess.map(x => `- ${x.title} ${cite(x)}`).join("\n")}`,
  ].filter(Boolean);
  if (!packLines.length) return { pack: "", md: `Cairn found nothing in this project that matches “${question}”. Try naming a file, a function or a feature.` };
  const lead = files[0] || code[0];
  let md = lead ? `The place to start is ${cite(lead)}` : "Here is what the project's own records say";
  md += code[0] && code[0] !== lead ? `, and in particular ${cite(code[0])}.` : ".";
  if (files.length > 1) md += ` It works together with ${sentenceList(files.slice(1).map(cite))}.`;
  md += "\n\n";
  if (specs[0]) md += `The spec that asked for this is ${specs[0].title.replace(/`/g, "").split(" ")[0]}: ${specs[0].title.replace(/`/g, "").split(" ").slice(1).join(" ")} ${cite(specs[0])}.\n\n`;
  if (mems[0]) md += `Keep in mind what the team recorded: ${mems[0].title} ${cite(mems[0])}\n\n`;
  if (sess[0]) md += `An earlier agent session covered this too: “${sess[0].title}” ${cite(sess[0])}.\n\n`;
  md += "Open any of the linked items to see the evidence in full.\n";
  return { pack: `Evidence for “${question}”\n\n` + packLines.join("\n\n"), md };
}

/** What the server's `start` event carries: a readable name for every citation in the evidence. */
function citeLabels(d, text) {
  const out = {};
  for (const [, kind, val] of String(text).matchAll(/\[([a-z]+):([^\]\s]+)\]/g)) {
    const k = `${kind}:${val}`;
    if (kind === "symbol") out[k] = IX.byId.get(val)?.label || val;
    else if (kind === "memory") out[k] = (d.memories.find(m => m.id === val)?.text || "a memory").slice(0, 80);
    else if (kind === "fact") out[k] = (d.facts.find(f => String(f.id) === val)?.fact || "a timeline fact").slice(0, 80);
    else if (kind === "file") out[k] = val;
  }
  return out;
}
/** Simulate POST /narrate: a start event, markdown in small chunks, then done (or an error with ?narrate=error). */
export async function narrate(body, { signal, onEvent }) {
  await ready();
  IX = indexOf(D);
  const url = new URL(location.href);
  const fail = (status, detail) => Object.assign(new Error(detail), { status });
  if (!D.overview.models?.available) throw fail(409, "Explaining needs a model. Set one up in Settings.");
  const kind = body?.kind, text = String(body?.text || "").trim();
  if (!["ask", "impact", "why"].includes(kind) || !text) throw fail(422, "Give a question, or a file or symbol to explain.");
  let md, pack = null;
  if (kind === "ask") ({ md, pack } = askAnswer(D, text));
  else md = (kind === "impact" ? impactAnswer(D, text) : whyAnswer(D, text)) || `Nothing in the map matches “${text}”, so there is nothing to explain yet.`;
  await abortable(700, signal);
  onEvent({ type: "start", model: "claude-opus-5-5", tier: "deep", ...(pack ? { pack } : {}), labels: citeLabels(D, md + "\n" + (pack || "")) });
  await abortable(900, signal);
  const chunks = md.match(/\S+\s*|\s+/g) || [];
  const failAt = url.searchParams.get("narrate") === "error" ? Math.floor(chunks.length * 0.4) : -1;
  for (let i = 0; i < chunks.length; i += 3) {
    if (i >= failAt && failAt >= 0) { onEvent({ type: "error", message: "RuntimeError: model call failed: You've hit your session limit · resets 3:50am (Asia/Calcutta)" }); return; }
    onEvent({ type: "text", text: chunks.slice(i, i + 3).join("") });
    await abortable(45 + Math.random() * 60, signal);
  }
  onEvent({ type: "done" });
}

/* ------------------------------------------------------------------ other repositories (cross-repository map) */
// Two small repositories so the repository map, and clicking through it, work end to end:
// shop imports acme-core, and lists cairn in its manifest without importing it.
const SYNTH = {
  "acme-core": {
    git_url: "https://git.example.com/core/acme-core.git", synced: 5400, description: "Money, currencies, tax rules and a double-entry ledger, shared by every Acme service.",
    packages: [{ ecosystem: "python", name: "acme-core" }], languages: { ".py": 7, ".md": 1 },
    communities: [[0, "money"], [1, "tax"], [2, "ledger"], [3, "tests"]],
    files: [
      ["acme_core/__init__.py", 0, []],
      ["acme_core/money.py", 0, ["Money", ".add()", ".convert()", "round_half_even()"]],
      ["acme_core/currency.py", 0, ["Currency", "rates()", "fetch_rates()"]],
      ["acme_core/tax.py", 1, ["TaxRule", "vat_for()", "apply_tax()", "exempt()"]],
      ["acme_core/ledger.py", 2, ["Ledger", ".post()", ".balance()", "Entry", "reconcile()"]],
      ["tests/test_money.py", 3, ["test_add()", "test_convert()"]],
      ["tests/test_tax.py", 3, ["test_vat()"]],
      ["README.md", 3, ["Money", "Tax rules"]],
    ],
    calls: [["money.py#.convert()", "currency.py#rates()"], ["currency.py#rates()", "currency.py#fetch_rates()"], ["money.py#.add()", "money.py#round_half_even()"],
      ["tax.py#apply_tax()", "tax.py#vat_for()"], ["tax.py#apply_tax()", "money.py#Money"], ["ledger.py#.post()", "money.py#Money"], ["ledger.py#reconcile()", "ledger.py#.balance()"],
      ["test_money.py#test_add()", "money.py#.add()"], ["test_money.py#test_convert()", "money.py#.convert()"], ["test_tax.py#test_vat()", "tax.py#vat_for()"]],
    imports: [["acme_core/__init__.py", "acme_core/money.py"], ["acme_core/__init__.py", "acme_core/tax.py"], ["acme_core/tax.py", "acme_core/money.py"], ["acme_core/ledger.py", "acme_core/money.py"]],
  },
  "shop": {
    git_url: "https://git.example.com/core/shop.git", synced: 1800, description: "The Acme web shop: catalogue, cart, checkout and billing.",
    packages: [{ ecosystem: "python", name: "acme-shop" }], languages: { ".py": 7, ".html": 3 },
    communities: [[0, "checkout"], [1, "catalog"], [2, "billing"], [3, "web app"], [4, "acme_core (imported)"]],
    files: [
      ["shop/app.py", 3, ["create_app()", "routes()", "error_page()"]],
      ["shop/catalog.py", 1, ["Product", "search()", "by_category()"]],
      ["shop/cart.py", 0, ["Cart", ".add_item()", ".total()", ".remove()"]],
      ["shop/checkout.py", 0, ["checkout()", "validate_address()", "confirm()"]],
      ["shop/billing.py", 2, ["Invoice", "charge()", "refund()", "tax_lines()"]],
      ["tests/test_checkout.py", 0, ["test_checkout_total()", "test_refund()"]],
    ],
    external: [["shop_ext_acme_core_money", "acme_core.money", 4], ["shop_ext_acme_core_tax", "acme_core.tax", 4]],
    calls: [["app.py#routes()", "catalog.py#search()"], ["app.py#routes()", "checkout.py#checkout()"], ["checkout.py#checkout()", "cart.py#.total()"], ["checkout.py#checkout()", "billing.py#charge()"],
      ["checkout.py#checkout()", "checkout.py#validate_address()"], ["billing.py#charge()", "billing.py#tax_lines()"], ["billing.py#refund()", "billing.py#Invoice"], ["cart.py#.add_item()", "catalog.py#Product"],
      ["test_checkout.py#test_checkout_total()", "checkout.py#checkout()"], ["test_checkout.py#test_refund()", "billing.py#refund()"]],
    imports: [["shop/app.py", "shop/checkout.py"], ["shop/checkout.py", "shop/billing.py"], ["shop/checkout.py", "shop/cart.py"], ["shop/cart.py", "shop/catalog.py"]],
    ext_imports: [["shop/billing.py", "shop_ext_acme_core_money", "L3"], ["shop/billing.py", "shop_ext_acme_core_tax", "L4"], ["shop/cart.py", "shop_ext_acme_core_money", "L2"], ["shop/checkout.py", "shop_ext_acme_core_tax", "L5"]],
  },
};
const idOf = (pid, s) => (pid + "_" + s).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/_+$/, "");
function addSynthGraph(d, pid) {
  const spec = SYNTH[pid];
  const nodes = [], links = [], byKey = new Map();
  for (const [path, comm, syms] of spec.files) {
    const fid = idOf(pid, path), doc = path.endsWith(".md");
    nodes.push({ id: fid, label: path.split("/").pop() === "__init__.py" ? path.split("/").slice(-2)[0] + "/__init__.py" : path.split("/").pop(), kind: doc ? "document" : "file", file: path, community: comm, loc: "L1" });
    syms.forEach((sym, i) => {
      const id = idOf(pid, path + "_" + sym);
      nodes.push({ id, label: sym, kind: doc ? "section" : sym.endsWith("()") ? (sym.startsWith(".") ? "method" : "function") : "class", file: path, community: comm, loc: "L" + (8 + i * 14) });
      byKey.set(path.split("/").pop() + "#" + sym, id);
      links.push({ source: fid, target: id, rel: "contains", prov: "EXTRACTED", loc: "", weight: 1 });
    });
  }
  for (const [id, label, comm] of spec.external || []) nodes.push({ id, label, kind: "module", file: "", community: comm, loc: "" });
  for (const [a, b] of spec.calls) if (byKey.has(a) && byKey.has(b)) links.push({ source: byKey.get(a), target: byKey.get(b), rel: "calls", prov: "EXTRACTED", loc: "", weight: 1 });
  for (const [a, b] of spec.imports) links.push({ source: idOf(pid, a), target: idOf(pid, b), rel: "imports_from", prov: "EXTRACTED", loc: "L1", weight: 1 });
  for (const [f, ext, loc] of spec.ext_imports || []) links.push({ source: idOf(pid, f), target: ext, rel: "imports", prov: "EXTRACTED", loc, weight: 1 });
  const deg = new Map(); links.forEach(l => { deg.set(l.source, (deg.get(l.source) || 0) + 1); deg.set(l.target, (deg.get(l.target) || 0) + 1); });
  nodes.forEach(n => { n.degree = deg.get(n.id) || 0; });
  const communities = spec.communities.map(([id, name]) => { const ms = nodes.filter(n => n.community === id); return { id, name, size: ms.length, top: ms.sort((a, b) => b.degree - a.degree).slice(0, 5).map(n => n.label) }; });
  d.graph = { nodes, links, communities, languages: spec.languages, built_at: now() - spec.synced };
  const files = new Set(nodes.map(n => n.file).filter(Boolean)).size;
  Object.assign(d.overview.layers.map, { connected: true, nodes: nodes.length, edges: links.length, files, areas: communities.length, languages: spec.languages });
  d.overview.last_sync = now() - spec.synced;
  d.overview.hubs = [...nodes].sort((a, b) => b.degree - a.degree).slice(0, 6).map(n => ({ id: n.id, label: n.label, file: n.file, degree: n.degree }));
}
function repoNode(pid) {
  const pr = projOf(pid), d = data(pid), g = d.graph;
  if (!g.nodes.length) return null;
  const comm = new Map();
  for (const n of g.nodes) { const c = comm.get(n.community) || { size: 0, dirs: new Map() }; c.size++; if (n.file) { const dir = n.file.includes("/") ? n.file.slice(0, n.file.lastIndexOf("/")) : ""; c.dirs.set(dir, (c.dirs.get(dir) || 0) + 1); } comm.set(n.community, c); }
  const name = new Map(g.communities.map(c => [c.id, c.name]));
  const top = [...comm].sort((a, b) => b[1].size - a[1].size).slice(0, 3).map(([id, c]) => ({ id, label: name.get(id) || `community ${id}`, area: [...c.dirs].sort((a, b) => b[1] - a[1])[0]?.[0] || "", size: c.size }));
  const spec = SYNTH[pid];
  return { id: pid, name: pr.name, root: pr.root, nodes: g.nodes.length, edges: g.links.length, files: new Set(g.nodes.map(n => n.file).filter(Boolean)).size,
    languages: g.languages || {}, communities: top,
    packages: spec ? spec.packages : [{ ecosystem: "python", name: "cairn-brain" }],
    description: spec ? spec.description : "Cairn gives coding agents a map of this repository, the intent behind it, its history, and what earlier sessions learned.",
    built_at_commit: pid === "cairn" ? "cba3cf9" : pid === "shop" ? "4e1d0a2" : "9b77c31" };
}
const REPO_LINKS = [
  { source: "shop", target: "acme-core", kind: "imports", weight: 4, packages: ["acme-core"], declared: true, evidence: [
    { file: "shop/billing.py", line: 3, name: "acme_core.money", package: "acme-core", node: "shop_ext_acme_core_money" },
    { file: "shop/billing.py", line: 4, name: "acme_core.tax", package: "acme-core", node: "shop_ext_acme_core_tax" },
    { file: "shop/cart.py", line: 2, name: "acme_core.money", package: "acme-core", node: "shop_ext_acme_core_money" },
    { file: "shop/checkout.py", line: 5, name: "acme_core.tax", package: "acme-core", node: "shop_ext_acme_core_tax" }] },
  { source: "shop", target: "cairn", kind: "depends", weight: 1, packages: ["cairn-brain"], declared: true, evidence: [] },
];
on("GET", "/api/repos/map", ({ q }) => {
  const visible = PL.session.projects.filter(p => !q.team || p.team_id === q.team).map(p => p.id);
  const nodes = visible.map(repoNode).filter(Boolean);
  const ids = new Set(nodes.map(n => n.id));
  return { level: "repos", nodes, links: REPO_LINKS.filter(l => ids.has(l.source) && ids.has(l.target)) };
});
