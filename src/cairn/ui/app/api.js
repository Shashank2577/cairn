// One data layer for the whole UI: every endpoint of the HTTP API v2, the CSRF header, and the
// 401 / 403 handling. With ?mock=1 in the URL (or when the server has no /api/session), the same
// calls are answered by the sample data under /ui/mock/.
import { enc } from "./lib/format.js";

export const env = { mock: false, mockReason: "", csrf: "", mode: "local", fallback: new Set() };
let mock = null;

// While the server is still growing, a per-project route it doesn't have yet (404 "Not Found", as opposed to
// "project not found") is answered from the sample data, and the UI says which sections are samples.
const PROJECT_ROUTE = /^\/api\/p\/[^/]+\/([^/?]+)/;
async function fromSample(method, path, query, body) {
  if (!mock) { mock = await import("/ui/mock/mock.js"); await mock.ready(); }
  const section = path.match(PROJECT_ROUTE)[1];
  if (!env.fallback.has(section)) { env.fallback.add(section); window.dispatchEvent(new CustomEvent("cairn:fallback", { detail: section })); }
  const res = await mock.handle(method, path.replace(/^\/api\/p\/[^/]+/, "/api/p/cairn"), query, body);
  if (res.status >= 400) fail(res.status, res.json?.detail);
  return res.json;
}

export class ApiError extends Error {
  constructor(status, detail, retryAfter) {
    super(detail || (status ? `The server answered ${status}.` : "Can't reach the Cairn server."));
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/** Projects come from the server as records; give every view the same field names. */
export function normProject(p) {
  return { ...p, last_sync: p.last_sync ?? p.last_synced_at ?? null, branch: p.branch ?? p.git_branch ?? null };
}
export function normSession(s) {
  if (!s) return s;
  return { ...s, csrf: s.csrf_token ?? s.csrf ?? "", teams: s.teams || [], projects: (s.projects || []).map(normProject) };
}

async function useMock(reason) {
  env.mock = true; env.mockReason = reason;
  mock = await import("/ui/mock/mock.js");
  await mock.ready();
}

/** Resolve the session at start-up, switching to sample data when there is no server behind the page. */
export async function bootSession() {
  const url = new URL(location.href);
  if (url.searchParams.get("mock") === "1") await useMock("requested");
  if (!env.mock) {
    let r;
    // optional=1: a signed-out visit gets {signed_in: false} instead of a 401, so the console stays clean.
    try { r = await fetch("/api/session?optional=1", { credentials: "same-origin", headers: { accept: "application/json" } }); }
    catch (e) { throw new ApiError(0); }
    if (r.status === 404) {
      // No convenience route: build the session from /api/auth/me and /api/projects.
      let me;
      try { me = await fetch("/api/auth/me", { credentials: "same-origin", headers: { accept: "application/json" } }); }
      catch (e) { throw new ApiError(0); }
      if (me.status === 404) await useMock("no-server");
      else if (me.status === 401) return null;
      else if (!me.ok) throw new ApiError(me.status, await detailOf(me));
      else {
        const m = await me.json();
        env.csrf = m.csrf_token || ""; env.mode = m.mode || "local";
        const projects = await request("GET", "/api/projects");
        return adopt({ mode: m.mode, user: m.user || { id: m.user_id, email: m.email, name: m.name, is_admin: m.is_admin, must_change_password: m.must_change_password }, csrf_token: m.csrf_token, teams: m.teams, projects });
      }
    }
    else if (r.status === 401) return null;
    else if (r.status === 403) {
      // Signed in with a one-time password: only /api/auth/* answers until a new password is chosen.
      const detail = await detailOf(r);
      if (!/password change required/i.test(detail)) throw new ApiError(403, detail);
      const me = await request("GET", "/api/auth/me");
      return adopt({ ...me, projects: [], must_change_password: true });
    }
    else if (!r.ok) throw new ApiError(r.status, await detailOf(r));
    else {
      const body = await r.json();
      if (body.signed_in === false) { env.mode = body.mode || env.mode; return null; }
      return adopt(body);
    }
  }
  return adopt(await request("GET", "/api/session"));
}
function adopt(raw) {
  const s = normSession(raw);
  env.csrf = s.csrf || ""; env.mode = s.mode || "local";
  return s;
}
/** Keep the CSRF token current after sign-in, invite acceptance or a key rotation. */
export function setCsrf(token) { if (token !== undefined) env.csrf = token || ""; }

async function detailOf(r) {
  try { const j = await r.json(); return typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) { return ""; }
}

function fail(status, detail, retryAfter) {
  if (status === 401) window.dispatchEvent(new CustomEvent("cairn:unauthorized"));
  if (status === 403 && /password change required/i.test(detail || "")) window.dispatchEvent(new CustomEvent("cairn:password-required"));
  throw new ApiError(status, detail, retryAfter);
}

export async function request(method, path, { query, body, signal } = {}) {
  const q = query ? Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== "") : [];
  if (env.mock) {
    const res = await mock.handle(method, path, Object.fromEntries(q), body);
    if (res.status >= 400) fail(res.status, res.json?.detail, res.retryAfter);
    return res.json;
  }
  const headers = { accept: "application/json" };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (!/^(GET|HEAD)$/.test(method) && env.csrf) headers["X-CSRF-Token"] = env.csrf;
  const qs = q.length ? "?" + new URLSearchParams(q.map(([k, v]) => [k, String(v)])) : "";
  let r;
  try { r = await fetch(path + qs, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin", signal }); }
  catch (e) { if (e.name === "AbortError") throw e; throw new ApiError(0); }
  if (!r.ok) {
    const detail = await detailOf(r);
    if (r.status === 404 && detail === "Not Found" && PROJECT_ROUTE.test(path)) return fromSample(method, path, Object.fromEntries(q), body);
    fail(r.status, detail, Number(r.headers.get("retry-after")) || undefined);
  }
  if (r.status === 204) return null;
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

const get = (p, query) => request("GET", p, { query });
const post = (p, body = {}) => request("POST", p, { body });
const patch = (p, body = {}) => request("PATCH", p, { body });
const del = (p, query) => request("DELETE", p, { query });
const put = (p, body = {}) => request("PUT", p, { body });
const docPath = name => String(name).split("/").map(enc).join("/");

export const api = {
  health: () => get("/api/health"),
  session: () => get("/api/session").then(adopt),
  me: () => get("/api/auth/me"),
  login: (email, password) => post("/api/auth/login", { email, password }),
  logout: () => post("/api/auth/logout"),
  changePassword: (current, next) => post("/api/auth/password", current ? { current_password: current, new_password: next } : { new_password: next }),
  sessions: () => get("/api/auth/sessions"),
  endSession: sid => del(`/api/auth/sessions/${enc(sid)}`),
  invite: token => get(`/api/invites/${enc(token)}`),
  acceptInvite: (token, body) => post(`/api/invites/${enc(token)}/accept`, body),

  teams: () => get("/api/teams"),
  createTeam: body => post("/api/teams", body),
  team: tid => get(`/api/teams/${enc(tid)}`),
  updateTeam: (tid, body) => patch(`/api/teams/${enc(tid)}`, body),
  deleteTeam: (tid, slug) => del(`/api/teams/${enc(tid)}`, { confirm: slug }),
  members: tid => get(`/api/teams/${enc(tid)}/members`),
  inviteMember: (tid, email, role, days) => post(`/api/teams/${enc(tid)}/members`, { email, role, days }),
  setRole: (tid, uid, role) => patch(`/api/teams/${enc(tid)}/members/${enc(uid)}`, { role }),
  removeMember: (tid, uid) => del(`/api/teams/${enc(tid)}/members/${enc(uid)}`),
  revokeInvitation: (tid, iid) => del(`/api/teams/${enc(tid)}/invitations/${enc(iid)}`),

  tokens: query => get("/api/tokens", query),
  createToken: body => post("/api/tokens", body),
  revokeToken: id => del(`/api/tokens/${enc(id)}`),

  projects: team => get("/api/projects", { team }),
  project: pid => get(`/api/projects/${enc(pid)}`).then(normProject),
  createProject: body => post("/api/projects", body),
  updateProject: (pid, body) => patch(`/api/projects/${enc(pid)}`, body),
  deleteProject: (pid, purge) => del(`/api/projects/${enc(pid)}`, purge ? { purge: "true" } : undefined),
  refreshProject: pid => post(`/api/projects/${enc(pid)}/refresh`),
  webhook: pid => post(`/api/projects/${enc(pid)}/webhook`),
  projectMembers: pid => get(`/api/projects/${enc(pid)}/members`),
  setProjectRole: (pid, uid, role) => put(`/api/projects/${enc(pid)}/members/${enc(uid)}`, { role }),
  clearProjectRole: (pid, uid) => del(`/api/projects/${enc(pid)}/members/${enc(uid)}`),

  audit: query => get("/api/audit", query),
  reposMap: team => get("/api/repos/map", { team }),
  users: () => get("/api/users"),
  disableUser: uid => post(`/api/users/${enc(uid)}/disable`),
  enableUser: uid => post(`/api/users/${enc(uid)}/enable`),
  resetPassword: uid => post(`/api/users/${enc(uid)}/reset-password`),

  /** Every per-project endpoint, under /api/p/{pid}. */
  p(pid) {
    const P = `/api/p/${enc(pid)}`;
    return {
      overview: () => get(P + "/overview"),
      search: (q, kinds, limit = 24) => get(P + "/search", { q, kinds, limit }),
      sync: deep => post(P + "/sync", deep ? { deep: true } : {}),
      savings: limit => get(P + "/savings", { limit }),
      models: () => get(P + "/models"),
      settings: () => get(P + "/settings"),
      saveSettings: body => patch(P + "/settings", body),

      impact: (target, depth = 2, budget) => get(P + "/impact", { target, depth, budget }),
      why: (target, budget) => get(P + "/why", { target, budget }),

      graphSummary: () => get(P + "/graph/summary"),
      graphData: (scope = "all", limit = 600) => get(P + "/graph/data", { scope, limit }),
      architecture: () => get(P + "/graph/architecture"),
      node: id => get(P + "/graph/node", { id }),
      path: (a, b) => get(P + "/graph/path", { a, b }),
      query: q => get(P + "/graph/query", { q }),
      views: () => get(P + "/graph/views"),
      viewUrl: kind => env.mock || env.fallback.has("graph") ? `/ui/mock/views/${enc(kind)}.html` : `${P}/graph/views/${enc(kind)}`,
      wiki: () => get(P + "/graph/wiki"),
      wikiPage: slug => get(`${P}/graph/wiki/${enc(slug)}`),
      writeWiki: () => post(P + "/graph/wiki", {}),
      report: () => get(P + "/graph/report"),

      specs: () => get(P + "/specs"),
      doc: (fid, name) => get(`${P}/specs/${enc(fid)}/doc/${docPath(name)}`),
      setTask: (fid, tid, done) => patch(`${P}/specs/${enc(fid)}/tasks/${enc(tid)}`, { done }),
      drift: spec => get(P + "/drift", { spec }),
      workflow: () => get(P + "/workflow"),

      feed: query => get(P + "/sessions/feed", query),
      sessionStats: () => get(P + "/sessions/stats"),
      session: sid => get(`${P}/sessions/${enc(sid)}`),
      sessionSearch: query => get(P + "/sessions/search", query),
      sessionContext: () => get(P + "/sessions/context"),
      sessionList: query => get(P + "/sessions/list", query),
      sessionStatus: () => get(P + "/sessions/status"),
      sessionQueue: () => get(P + "/sessions/queue"),
      sessionSettings: () => get(P + "/sessions/settings"),
      processSessions: noModel => post(P + "/sessions/process", noModel ? { no_model: true } : {}),
      queueAction: action => post(`${P}/sessions/queue/${action}`),
      deleteSessionItem: (kind, id) => del(`${P}/sessions/${enc(kind)}/${enc(id)}`),

      timeline: query => get(P + "/timeline", query),
      facts: query => get(P + "/facts", query),
      entities: query => get(P + "/facts/entities", query),
      entity: id => get(`${P}/facts/entities/${enc(id)}`),
      factCommunities: () => get(P + "/facts/communities"),
      episodes: limit => get(P + "/facts/episodes", { limit }),

      memories: query => get(P + "/memories", query),
      addMemory: body => post(P + "/memories", body),
      editMemory: (id, body) => patch(`${P}/memories/${enc(id)}`, body),
      deleteMemory: id => del(`${P}/memories/${enc(id)}`),
      memoryHistory: id => get(`${P}/memories/${enc(id)}/history`),
      memorySources: () => get(P + "/memories/sources"),
    };
  },
};

/**
 * Stream a model's explanation: POST /api/p/{pid}/narrate answers with one JSON event per line
 * (start, text…, then done or error). Calls onEvent for each; resolves when the stream ends.
 * Abort with the signal to stop; the server stops the model call when the connection closes.
 */
export async function narrate(pid, body, { signal, onEvent }) {
  const sample = async () => {
    if (!mock) { mock = await import("/ui/mock/mock.js"); await mock.ready(); }
    return mock.narrate(body, { signal, onEvent });
  };
  if (env.mock) return sample();
  const headers = { accept: "application/x-ndjson", "content-type": "application/json" };
  if (env.csrf) headers["X-CSRF-Token"] = env.csrf;
  let r;
  try { r = await fetch(`/api/p/${enc(pid)}/narrate`, { method: "POST", headers, body: JSON.stringify(body), credentials: "same-origin", signal }); }
  catch (e) { if (e.name === "AbortError") throw e; throw new ApiError(0); }
  if (!r.ok) {
    const detail = await detailOf(r);
    if (r.status === 404 && detail === "Not Found") {
      if (!env.fallback.has("narrate")) { env.fallback.add("narrate"); window.dispatchEvent(new CustomEvent("cairn:fallback", { detail: "narrate" })); }
      return sample();
    }
    fail(r.status, detail);
  }
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  const emit = line => { line = line.trim(); if (!line) return; try { onEvent(JSON.parse(line)); } catch (e) { /* skip a malformed line */ } };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) { emit(buf.slice(0, i)); buf = buf.slice(i + 1); }
  }
  emit(buf + dec.decode());
}

/** Live events for one project. Reconnects with backoff; returns a function that closes it. */
export function openStream(pid, onEvent) {
  if (env.mock) return mock.stream(pid, onEvent);
  let es = null, closed = false, retry = 1000, timer = 0;
  const deliver = ev => { try { const m = JSON.parse(ev.data); if (!m.type && ev.type !== "message") m.type = ev.type; onEvent(m); } catch (e) { /* ignore malformed frames */ } };
  const connect = () => {
    es = new EventSource(`/api/p/${enc(pid)}/stream`, { withCredentials: true });
    es.onmessage = deliver;
    for (const t of ["sync", "observation", "summary", "prompt", "status", "memory", "query", "ping"]) es.addEventListener(t, deliver);
    es.onopen = () => { retry = 1000; onEvent({ type: "_open" }); };
    es.onerror = () => {
      onEvent({ type: "_error" });
      es.close();
      if (!closed) timer = setTimeout(connect, retry = Math.min(retry * 2, 30000));
    };
  };
  connect();
  return () => { closed = true; clearTimeout(timer); es && es.close(); };
}
