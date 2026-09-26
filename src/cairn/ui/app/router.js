// Hash routes with deep links:
//   #/login  #/invite/<token>  #/account  #/team/<tid>/<tab>  #/p/<pid>/<view>[/<part>…][?query]
import { useEffect, useState } from "preact/hooks";
import { enc } from "./lib/format.js";

export const PROJECT_VIEWS = ["overview", "impact", "map", "specs", "sessions", "timeline", "memory", "settings"];
const ALIAS = { graph: "map", why: "impact", code: "map", agents: "sessions", architecture: "map" };
export const TEAM_TABS = ["members", "tokens", "projects", "audit", "settings"];

export function parse(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "");
  const qi = raw.indexOf("?");
  const path = qi < 0 ? raw : raw.slice(0, qi);
  const q = new URLSearchParams(qi < 0 ? "" : raw.slice(qi + 1));
  const parts = path.split("/").filter(Boolean).map(s => { try { return decodeURIComponent(s); } catch (e) { return s; } });
  const [head, ...rest] = parts;
  if (!head) return { name: "home", q };
  if (head === "login") return { name: "login", q };
  if (head === "invite") return { name: "invite", token: rest[0] || "", q };
  if (head === "account") return { name: "account", q };
  if (head === "admin") return { name: "admin", tab: rest[0] || "users", q };
  if (head === "team") return { name: "team", tid: rest[0], tab: TEAM_TABS.includes(rest[1]) ? rest[1] : "members", q };
  if (head === "p" && rest[0]) {
    let view = rest[1] || "overview";
    view = ALIAS[view] || view;
    return { name: PROJECT_VIEWS.includes(view) ? "project" : "notfound", pid: rest[0], view, rest: rest.slice(2), q };
  }
  return { name: "notfound", q };
}

const qs = q => {
  const e = Object.entries(q || {}).filter(([, v]) => v !== undefined && v !== null && v !== "" && v !== false);
  return e.length ? "?" + e.map(([k, v]) => `${enc(k)}=${enc(v)}`).join("&") : "";
};
export const link = {
  project: (pid, view = "overview", parts = [], q) => `#/p/${enc(pid)}/${view}${parts.filter(x => x != null && x !== "").map(x => "/" + enc(x)).join("")}${qs(q)}`,
  impact: (pid, target, q) => link.project(pid, "impact", target ? [target] : [], q),
  team: (tid, tab = "members") => `#/team/${enc(tid)}/${tab}`,
  account: () => "#/account",
  users: () => "#/admin/users",
  login: next => "#/login" + (next ? qs({ next }) : ""),
};

export function useRoute() {
  const [r, setR] = useState(() => parse(location.hash));
  useEffect(() => {
    const f = () => setR(parse(location.hash));
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return r;
}

export const go = h => { if (location.hash !== h) location.hash = h; };
/** Replace the current hash without adding a history entry (for filters and selections). */
export const replace = h => { if (location.hash !== h) { history.replaceState(null, "", h); window.dispatchEvent(new HashChangeEvent("hashchange")); } };

/** Where a search result or citation leads. */
export function citeHref(pid, cite, extra = {}) {
  if (!cite) return "";
  const i = cite.indexOf(":");
  const kind = i < 0 ? cite : cite.slice(0, i), val = i < 0 ? "" : cite.slice(i + 1);
  switch (kind) {
    case "file": return link.impact(pid, val);
    case "symbol": return link.impact(pid, "symbol:" + val);
    case "task": case "req": case "requirement": case "story": { const [fid, id] = val.split("/"); return link.project(pid, "specs", [fid, kind === "task" ? "tasks" : "overview"], id ? { focus: id } : undefined); }
    case "spec": return link.project(pid, "specs", [val]);
    case "obs": case "observation": return extra.session_id ? link.project(pid, "sessions", [extra.session_id], { obs: val }) : link.project(pid, "sessions", [], { q: val });
    case "session": return link.project(pid, "sessions", [val]);
    case "commit": return link.project(pid, "timeline", [], { focus: cite });
    case "memory": return link.project(pid, "memory", [], { focus: val });
    case "fact": return link.project(pid, "timeline", [], extra.entity ? { entity: extra.entity } : {});
    case "entity": return link.project(pid, "timeline", [], { entity: val });
    case "wiki": return link.project(pid, "map", ["wiki", val]);
    case "drift": return link.project(pid, "specs", [], { tab: "drift" });
    // A rationale comment is a node in the map (its id), or a file:line from older answers.
    case "rationale": return extra.path ? link.impact(pid, extra.path) : /[/:]/.test(val) ? link.impact(pid, val.split(":")[0]) : link.project(pid, "map", [], { scope: `around:${val}`, node: val });
    default: return "";
  }
}
