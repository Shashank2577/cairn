// Formatting helpers shared by every view.
import { h } from "preact";

export const fmt = n => Math.round(Number(n) || 0).toLocaleString("en-US");
export function fmtK(n) {
  n = Number(n) || 0;
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "k";
  return String(Math.round(n));
}
export const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : (many || one + "s")}`;
export const base = p => String(p || "").replace(/\/$/, "").split("/").pop();
export const dir = p => { const s = String(p || ""); const i = s.lastIndexOf("/"); return i < 0 ? "" : s.slice(0, i + 1); };
export const isTest = p => /(^|\/)tests?\/|(^|\/)test_|_test\.|\.test\.|\.spec\./.test(p || "");
export const isDoc = p => /\.(md|mdx|rst|txt)$/i.test(p || "");
export const fileKind = p => isTest(p) ? "test" : isDoc(p) ? "doc" : "code";

const toSec = ts => typeof ts === "string" ? Date.parse(ts) / 1000 : Number(ts);
export function ago(ts) {
  if (ts == null || ts === "") return "";
  const d = Date.now() / 1000 - toSec(ts);
  if (d < 0) return "in " + until(-d);
  for (const [u, s] of [["y", 31536000], ["mo", 2592000], ["d", 86400], ["h", 3600], ["m", 60]]) if (d >= s) return Math.floor(d / s) + u + " ago";
  return "just now";
}
function until(d) {
  for (const [u, s] of [["y", 31536000], ["mo", 2592000], ["d", 86400], ["h", 3600], ["m", 60]]) if (d >= s) return Math.floor(d / s) + u;
  return "moments";
}
export const inTime = ts => { const d = toSec(ts) - Date.now() / 1000; return d <= 0 ? "expired" : "in " + until(d); };
export const day = ts => new Date(toSec(ts) * 1000).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
export const date = ts => new Date(toSec(ts) * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
export const clock = ts => new Date(toSec(ts) * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
export const stamp = ts => `${date(ts)}, ${clock(ts)}`;
export const isoDay = ts => new Date(toSec(ts) * 1000).toISOString().slice(0, 10);
export const secs = toSec;

// Text from engines can carry emoji markers and `code` spans. Strip the first, render the second.
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F000}-\u{1F2FF}]️?\s*/gu;
// Some engines store text HTML-escaped; it is always rendered as text here, so show the characters.
const ENT = { lt: "<", gt: ">", amp: "&", quot: '"', "#39": "'" };
export const plain = s => String(s ?? "").replace(EMOJI, "").replace(/&(lt|gt|amp|quot|#39);/g, (_, e) => ENT[e]).replace(/\s{2,}/g, " ").trim();
export function clip(s, n) {
  s = plain(s);
  if (!n || s.length <= n) return s;
  s = s.slice(0, n - 1);
  if ((s.match(/`/g) || []).length % 2) s = s.replace(/`([^`]*)$/, "$1");
  return s.replace(/\s+\S*$/, "") + "…";
}
export function rich(text, n = 0) {
  const s = clip(text, n);
  const out = [];
  s.split(/(`[^`]+`)/g).forEach((part, i) => {
    if (!part) return;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) out.push(h("code", { key: i }, part.slice(1, -1)));
    else out.push(part);
  });
  return out;
}

export const initials = name => String(name || "?").split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join("");
const AV = ["#9A6434", "#2F6FA3", "#4F7F3F", "#6E4A96", "#8A5A9E", "#3E7C8C", "#A0566F", "#6B8E3E"];
export const avatarColor = s => { let x = 0; for (const c of String(s || "")) x = (x * 31 + c.charCodeAt(0)) >>> 0; return AV[x % AV.length]; };

export const ROLE_LABEL = { owner: "Owner", admin: "Admin", member: "Member", viewer: "Viewer" };
export const ROLE_RANK = { viewer: 0, member: 1, admin: 2, owner: 3 };
export const canRole = (role, need) => (ROLE_RANK[role] ?? -1) >= (ROLE_RANK[need] ?? 9);

export const STEP_LABEL = { map: "Code and document graph", history: "Git history", specs: "Specs and workflow", sessions: "Agent sessions",
  temporal: "Timeline facts", timeline: "Timeline facts", memory: "Memory", links: "Memory", drift: "Drift check", sync: "Sync" };
export const stepNames = failed => [...new Set(Object.keys(failed || {}).map(k => STEP_LABEL[k] || k))];
// A failed step's message without the engine's log prefix ("[graph] WARNING: ...").
export const failText = s => String(s || "Failed").replace(/^\[[^\]]*\]\s*(?:warning|error)?:?\s*/i, "").trim() || "Failed";
export const listOf = xs => xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

export const approxTokens = s => Math.ceil(String(s || "").length / 4);
export const enc = encodeURIComponent;
export function copyText(t) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(t).then(() => true, () => fallbackCopy(t));
  return Promise.resolve(fallbackCopy(t));
}
function fallbackCopy(t) {
  const ta = document.createElement("textarea");
  ta.value = t; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  let ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  ta.remove(); return ok;
}

// Permissions: the server sends a `permissions` array per project and team; the role table is the fallback.
const LOWEST = {
  "project.read": "viewer", "project.write": "member", "project.sync": "member", "project.capture": "member", "project.admin": "admin",
  "team.read": "viewer", "team.members": "admin", "team.tokens": "admin", "team.audit": "admin", "team.admin": "owner", "team.delete": "owner",
};
export const allowed = (thing, action) => Array.isArray(thing?.permissions) ? thing.permissions.includes(action) || thing.permissions.includes("*") : canRole(thing?.role, LOWEST[action]);
/** Unix seconds from seconds, milliseconds-free floats or ISO strings. */
export const tsOf = v => v == null || v === "" ? null : typeof v === "number" ? v : secs(v);

/**
 * A path as the reader wants it: relative to the repository when it lies inside it, otherwise shortened to its
 * last folders (the full path goes in a title). Paths the agent recorded are often absolute.
 */
export function repoPath(p, root) {
  const s = String(p || "");
  const r = String(root || "").replace(/\/+$/, "");
  if (r && (s === r || s.startsWith(r + "/"))) return s.slice(r.length + 1) || ".";
  if (!s.startsWith("/") && !/^[A-Za-z]:[\\/]/.test(s) && !s.startsWith("~")) return s;
  const home = s.match(/^\/(Users|home)\/[^/]+\//);
  const t = home ? "~/" + s.slice(home[0].length) : s;
  const parts = t.split("/");
  return parts.length > 4 ? "…/" + parts.slice(-3).join("/") : t;
}
/** True when a path points inside the repository, so it can open an impact report. */
export const inRepo = (p, root) => { const s = String(p || ""), r = String(root || "").replace(/\/+$/, ""); return !s.startsWith("/") && !s.startsWith("~") || (r && s.startsWith(r + "/")); };
