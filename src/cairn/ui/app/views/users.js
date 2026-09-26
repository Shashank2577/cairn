// Server users: for server admins only. Disable, enable, or reset a password (shown once).
import { useState } from "preact/hooks";
import { html, Empty, Load, Tag, Avatar, Modal, CopyButton, ConfirmButton } from "../components/ui.js";
import { useApp, useFetch } from "../state.js";
import { api } from "../api.js";
import { ago, date } from "../lib/format.js";

export function Users() {
  const { session, toast } = useApp();
  const admin = !!session.user?.is_admin;
  const res = useFetch(() => admin ? api.users() : Promise.resolve([]), [admin]);
  const [otp, setOtp] = useState(null);
  const [q, setQ] = useState("");
  if (!admin) return html`<div class="page"><${Empty} title="Only server admins can manage users" tone="risk"><p>Team roles are managed on each team's Members page.</p></${Empty}></div>`;
  const act = async (fn, u, done) => { try { const r = await fn(u.id); done(r); res.reload(); } catch (e) { toast({ title: "That didn't work", body: e.message, tone: "risk" }); } };
  return html`<div class="page view-in users">
    <div class="page-h"><div><h1>Server users</h1><p class="lede">Everyone with an account on this Cairn server. Disabling someone signs them out everywhere and stops their tokens; their memberships are kept.</p></div></div>
    <input class="input" type="search" style="max-width:320px;margin:0 0 14px" placeholder="Find by name or email" value=${q} onInput=${e => setQ(e.target.value)} aria-label="Find a user"/>
    <${Load} res=${res} rows="6" what="users">${us => { const shown = us.filter(u => !q || `${u.name} ${u.email}`.toLowerCase().includes(q.toLowerCase()));
      return html`<div class="table-wrap"><table class="table stack"><thead><tr><th>Person</th><th>Status</th><th>Created</th><th>Last signed in</th><th></th></tr></thead><tbody>
      ${shown.map(u => { const self = u.id === session.user?.id; return html`<tr key=${u.id} class=${u.disabled ? "expired" : ""}>
        <td class="lead" data-label="Person"><div class="person"><${Avatar} name=${u.name || u.email} size="sm"/><span><b>${u.name || u.email}${self ? html` <span class="sub">(you)</span>` : null}</b><span class="sub">${u.email}</span></span></div></td>
        <td data-label="Status"><div class="chips">${u.disabled ? html`<${Tag} tone="risk">disabled</${Tag}>` : html`<${Tag} tone="memory">active</${Tag}>`}${u.is_admin ? html`<${Tag} tone="spec">server admin</${Tag}>` : null}${u.must_change_password ? html`<${Tag} tone="code">must change password</${Tag}>` : null}${!u.has_password ? html`<${Tag}>no password</${Tag}>` : null}</div></td>
        <td data-label="Created" class="nowrap">${u.created_at ? date(u.created_at) : "—"}</td>
        <td data-label="Last signed in" class="nowrap">${u.last_login_at ? ago(u.last_login_at) : html`<span class="sub">Never</span>`}</td>
        <td class="n" data-label=""><div class="row tight" style="justify-content:flex-end">${self ? null : html`
          <${ConfirmButton} class="btn sm" label="Reset password" confirm="Reset it" onConfirm=${() => act(api.resetPassword, u, r => setOtp({ user: u, ...r }))}/>
          ${u.disabled ? html`<button class="btn sm" onClick=${() => act(api.enableUser, u, () => toast({ title: `${u.name || u.email} enabled`, tone: "memory" }))}>Enable</button>`
            : html`<${ConfirmButton} label="Disable" confirm="Disable account" onConfirm=${() => act(api.disableUser, u, () => toast({ title: `${u.name || u.email} disabled`, body: "They are signed out and their tokens stop working." }))}/>`}`}</div></td>
      </tr>`; })}</tbody></table></div>`; }}</${Load}>
    ${otp ? html`<${Modal} title=${`Temporary password for ${otp.user.name || otp.user.email}`} onClose=${() => setOtp(null)} actions=${html`<button class="btn primary" onClick=${() => setOtp(null)}>Done</button>`}>
      <p class="ink2">Give this to them through a channel you trust. They must choose a new password when they next sign in. It is shown only once.</p>
      <div class="secret"><code>${otp.password}</code><${CopyButton} text=${otp.password} label="Copy"/></div>
    </${Modal}>` : null}
  </div>`;
}
