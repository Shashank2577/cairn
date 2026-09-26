// Account: who you are signed in as, where you're signed in, your password and theme.
import { useState } from "preact/hooks";
import { html, Icon, Avatar, Empty, Load, Tag, ConfirmButton } from "../components/ui.js";
import { useApp, useFetch } from "../state.js";
import { api } from "../api.js";
import { link } from "../router.js";
import { ROLE_LABEL, ago } from "../lib/format.js";

const browser = ua => { const s = String(ua || ""); const b = /Edg\//.test(s) ? "Edge" : /Chrome\//.test(s) ? "Chrome" : /Firefox\//.test(s) ? "Firefox" : /Safari\//.test(s) ? "Safari" : s ? "Browser" : "Unknown browser";
  const o = /Mac OS X|Macintosh/.test(s) ? "macOS" : /Windows/.test(s) ? "Windows" : /Android/.test(s) ? "Android" : /iPhone|iPad/.test(s) ? "iOS" : /Linux/.test(s) ? "Linux" : ""; return o ? `${b} on ${o}` : b; };

export function Account() {
  const { session, theme, setTheme, toast, signOut } = useApp();
  const u = session.user || { name: session.name, email: session.email };
  const local = session.mode === "local";
  return html`<div class="page view-in account">
    <div class="page-h"><div><h1>Account</h1></div></div>
    <div class="acct">
      <section class="panel"><div class="row" style="gap:14px"><${Avatar} name=${u.name || u.email} size="lg"/><div><h2>${u.name || u.email}</h2><p class="sub" style="margin:0">${u.email}</p></div></div>
        ${u.is_admin && !local ? html`<p style="margin:12px 0 0"><${Tag} tone="spec">server admin</${Tag}></p>` : null}
        <h3 style="margin-top:18px">Teams</h3>
        <ul class="list">${session.teams.map(t => html`<li><a href=${link.team(t.id, local ? "projects" : "members")}><span>${t.name}</span><span class="spacer"></span><span class="sub">${ROLE_LABEL[t.role]}</span></a></li>`)}</ul>
        ${local ? html`<p class="sub" style="margin:10px 0 0">Local mode: this server runs on your machine without sign-in, and you own every project on it. Start it with <code>cairn serve</code> in team mode to share projects.</p>`
          : html`<button class="btn" style="margin-top:6px" onClick=${signOut}><${Icon} name="out" size="15"/>Sign out</button>`}
      </section>
      ${local ? null : html`<${PasswordForm}/>`}
      <section class="panel"><h2>Appearance</h2><p class="sub">Stored in this browser only.</p>
        <div class="seg" role="radiogroup" aria-label="Theme" style="margin-top:8px">${[["system", "Match system"], ["light", "Light"], ["dark", "Dark"]].map(([k, l]) => html`<button role="radio" aria-checked=${theme === k} aria-pressed=${theme === k} onClick=${() => setTheme(k)}>${l}</button>`)}</div>
      </section>
    </div>
    ${local ? null : html`<${Sessions}/>`}
  </div>`;
}

function PasswordForm() {
  const { toast } = useApp();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const change = async e => {
    e.preventDefault();
    if (next.length < 10) { setErr("Use at least 10 characters for the new password."); return; }
    if (next !== again) { setErr("The two new passwords don't match."); return; }
    setBusy(true); setErr("");
    try { await api.changePassword(cur, next); setCur(""); setNext(""); setAgain(""); toast({ title: "Password changed", body: "Every other browser signed in as you has been signed out.", tone: "memory" }); }
    catch (x) { setErr(x.status === 403 ? "Your current password isn't right." : x.status === 429 ? "Too many attempts. Wait a minute and try again." : x.message); } finally { setBusy(false); }
  };
  return html`<form class="panel stack" onSubmit=${change}>
    <h2>Change password</h2>
    <label class="field"><span>Current password</span><input class="input" type="password" autocomplete="current-password" value=${cur} onInput=${e => setCur(e.target.value)}/></label>
    <label class="field"><span>New password</span><input class="input" type="password" autocomplete="new-password" value=${next} onInput=${e => setNext(e.target.value)}/><span class="hint">At least 10 characters. Other browsers get signed out.</span></label>
    <label class="field"><span>New password again</span><input class="input" type="password" autocomplete="new-password" value=${again} onInput=${e => setAgain(e.target.value)}/></label>
    ${err ? html`<p class="err small" role="alert" style="margin:0">${err}</p>` : null}
    <div><button class="btn primary" type="submit" disabled=${busy || !cur || !next || !again}>${busy ? "Changing…" : "Change password"}</button></div>
  </form>`;
}

function Sessions() {
  const { toast } = useApp();
  const res = useFetch(() => api.sessions(), []);
  return html`<section class="sec ruled" style="margin-top:30px"><div class="sec-h"><div><h2>Where you're signed in</h2><p class="sub">Sign out a browser you no longer use.</p></div></div>
    <${Load} res=${res} rows="3" what="your sessions">${ss => ss.length ? html`<div class="table-wrap"><table class="table stack"><thead><tr><th>Browser</th><th>From</th><th>Last active</th><th>Signed in</th><th></th></tr></thead><tbody>
      ${ss.map(s => html`<tr key=${s.id}><td class="lead" data-label="Browser">${browser(s.user_agent)}${s.current ? html` <${Tag} tone="memory">this browser</${Tag}>` : null}</td>
        <td data-label="From">${s.ip || "—"}</td><td data-label="Last active" class="nowrap">${s.last_seen_at ? ago(s.last_seen_at) : "—"}</td><td data-label="Signed in" class="nowrap">${ago(s.created_at)}</td>
        <td class="n" data-label="">${s.current ? null : html`<${ConfirmButton} label="Sign out" confirm="Sign it out" onConfirm=${async () => { try { await api.endSession(s.id); res.reload(); } catch (e) { toast({ title: "Couldn't sign it out", body: e.message, tone: "risk" }); } }}/>`}</td></tr>`)}</tbody></table></div>`
      : html`<p class="sub">No other sessions.</p>`}</${Load}></section>`;
}
