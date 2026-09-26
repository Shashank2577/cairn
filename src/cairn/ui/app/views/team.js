// Team administration: members and invitations, API tokens, projects (with per-project roles,
// refresh and push webhooks), the audit log and team settings. Controls follow the team's and each
// project's `permissions` from the server.
import { useState } from "preact/hooks";
import { html, Icon, Empty, Load, Tag, Skeleton, Tabs, Avatar, Modal, CopyButton, ConfirmButton, SheetMark, RoleBadge } from "../components/ui.js";
import { useApp, useFetch } from "../state.js";
import { api, env } from "../api.js";
import { link, go } from "../router.js";
import { ago, date, stamp, inTime, plural, ROLE_LABEL, ROLE_RANK, allowed, tsOf } from "../lib/format.js";

const TABS = [["members", "Members"], ["tokens", "API tokens"], ["projects", "Projects"], ["audit", "Audit log"], ["settings", "Settings"]];
const ROLE_HELP = { owner: "Everything, including renaming and deleting the team", admin: "Manage members, projects, tokens and the audit log", member: "Sync, edit tasks, save memories", viewer: "Read everything, change nothing" };

export function Team() {
  const { team, route, session } = useApp();
  const detail = useFetch(() => api.team(team.id), [team.id]);
  // The session is reloaded after every change, so its name, slug and role win over the first fetch.
  const T = { ...(detail.data || {}), ...team };
  const local = session.mode === "local";
  const tabs = TABS.filter(([id]) => (!local || !["members", "settings"].includes(id)) && (id !== "audit" || allowed(T, "team.audit")));
  const tab = tabs.some(t => t[0] === route.tab) ? route.tab : tabs[0][0];
  const props = { T, reloadTeam: detail.reload };
  return html`<div class="page view-in team">
    <div class="page-h"><div>
      <div class="row" style="gap:12px"><${Avatar} name=${T.name} size="lg"/><div><h1>${T.name}</h1>
        <p class="sub" style="margin:2px 0 0">${local ? "Local mode: every project on this machine, and you own them all." : html`Your role here: <b>${ROLE_LABEL[T.role]}</b>. ${ROLE_HELP[T.role]}.`}</p></div></div>
    </div></div>
    <${Tabs} label="Team sections" current=${tab} items=${tabs.map(([id, label]) => ({ id, label, href: link.team(team.id, id) }))}/>
    ${tab === "members" ? html`<${Members} ...${props}/>` : tab === "tokens" ? html`<${Tokens} ...${props}/>` : tab === "projects" ? html`<${Projects} ...${props}/>` : tab === "audit" ? html`<${Audit} ...${props}/>` : html`<${TeamSettings} ...${props}/>`}
  </div>`;
}

/* ---------------------------------------------------------------- members */
function Members({ T }) {
  const { session, toast, reloadSession } = useApp();
  const res = useFetch(() => api.members(T.id), [T.id]);
  const manage = allowed(T, "team.members");
  const myRank = ROLE_RANK[T.role] ?? 0;
  const [invited, setInvited] = useState(null);
  const me = session.user?.id || session.user_id;
  return html`<div>
    ${manage ? html`<${InviteForm} T=${T} onInvited=${r => { setInvited(r); res.reload(); }}/>` : null}
    ${invited ? html`<div class="panel invited" role="status"><h3><${Icon} name="check" size="16"/> Invite ready for ${invited.invitation?.email}</h3>
      <p class="sub">Send them this link. It works once and expires ${invited.invitation?.expires_at ? date(invited.invitation.expires_at) : "soon"}. They join as ${(ROLE_LABEL[invited.invitation?.role] || "member").toLowerCase()}. This is the only time the link is shown.</p>
      <div class="secret" style="margin-top:8px"><code>${invited.url}</code><${CopyButton} text=${invited.url} label="Copy link"/></div>
      <button class="btn ghost sm" style="margin-top:8px" onClick=${() => setInvited(null)}>Done</button></div>` : null}
    <${Load} res=${res} rows="6" what="members">${d => {
      const members = Array.isArray(d) ? d : d.members || [];
      const invites = (Array.isArray(d) ? [] : d.invitations || d.invites || []).filter(i => !i.status || i.status === "pending");
      const owners = members.filter(m => m.role === "owner" && !m.disabled).length;
      return html`<div>
        <div class="sec-h" style="margin-top:6px"><div><h2>Members <span class="sub num" style="font-weight:500">${members.length}</span></h2></div></div>
        <div class="table-wrap"><table class="table stack"><thead><tr><th>Person</th><th>Role</th><th>Joined</th><th>Last signed in</th><th></th></tr></thead><tbody>
          ${members.map(m => { const uid = m.user_id || m.id; const self = uid === me; const lastOwner = m.role === "owner" && owners <= 1;
            const canEdit = manage && !self && (T.role === "owner" || (ROLE_RANK[m.role] ?? 0) <= myRank && m.role !== "owner");
            return html`<tr key=${uid}>
              <td class="lead" data-label="Person"><div class="person"><${Avatar} name=${m.name || m.email} size="sm"/><span><b>${m.name || m.email}${self ? html` <span class="sub">(you)</span>` : null}${m.disabled ? html` <${Tag} tone="risk">disabled</${Tag}>` : null}</b><span class="sub">${m.email}</span></span></div></td>
              <td data-label="Role">${canEdit ? html`<${RoleSelect} value=${m.role} max=${T.role} disabled=${lastOwner} onChange=${async role => {
                try { await api.setRole(T.id, uid, role); toast({ title: `${m.name || m.email} is now ${ROLE_LABEL[role].toLowerCase()}`, tone: "memory" }); res.reload(); }
                catch (e) { toast({ title: "Couldn't change the role", body: e.message, tone: "risk" }); res.reload(); } }}/>` : html`<${RoleBadge} role=${m.role}/>`}</td>
              <td data-label="Joined" class="nowrap">${m.joined_at || m.created_at ? date(m.joined_at || m.created_at) : "—"}</td>
              <td data-label="Last signed in" class="nowrap">${m.last_login_at || m.last_seen ? ago(m.last_login_at || m.last_seen) : html`<span class="sub">Never</span>`}</td>
              <td data-label="" class="n">${self && !lastOwner ? html`<${ConfirmButton} label="Leave team" confirm=${`Leave ${T.name}`} onConfirm=${async () => {
                  try { await api.removeMember(T.id, uid); await reloadSession(); go("#/"); } catch (e) { toast({ title: "Couldn't leave the team", body: e.message, tone: "risk" }); } }}/>`
                : canEdit && !lastOwner ? html`<${ConfirmButton} label="Remove" confirm=${`Remove ${(m.name || m.email).split(" ")[0]}`} onConfirm=${async () => {
                  try { await api.removeMember(T.id, uid); toast({ title: `${m.name || m.email} removed`, body: "Their tokens for this team were revoked too." }); res.reload(); } catch (e) { toast({ title: "Couldn't remove", body: e.message, tone: "risk" }); } }}/>` : null}</td>
            </tr>`; })}</tbody></table></div>
        ${invites.length ? html`<div class="sec-h" style="margin-top:28px"><div><h2>Pending invites <span class="sub num" style="font-weight:500">${invites.length}</span></h2></div></div>
          <div class="table-wrap"><table class="table stack"><thead><tr><th>Email</th><th>Role</th><th>Sent</th><th>Expires</th><th></th></tr></thead><tbody>
          ${invites.map(i => html`<tr key=${i.id}><td class="lead" data-label="Email">${i.email}</td><td data-label="Role"><${RoleBadge} role=${i.role}/></td>
            <td data-label="Sent" class="nowrap">${i.created_at ? ago(i.created_at) : "—"}</td>
            <td data-label="Expires" class="nowrap">${i.expires_at ? inTime(i.expires_at) : "—"}</td>
            <td class="n" data-label="">${manage ? html`<${ConfirmButton} label="Revoke" confirm="Revoke invite" onConfirm=${async () => { try { await api.revokeInvitation(T.id, i.id); toast({ title: `Invite for ${i.email} revoked` }); res.reload(); } catch (e) { toast({ title: "Couldn't revoke", body: e.message, tone: "risk" }); } }}/>` : null}</td></tr>`)}</tbody></table></div>` : null}
        <div class="roles"><h3>What each role can do</h3><dl>${Object.entries(ROLE_HELP).map(([r, h]) => html`<div><dt>${ROLE_LABEL[r]}</dt><dd>${h}</dd></div>`)}</dl></div>
      </div>`;
    }}</${Load}>
  </div>`;
}
function RoleSelect({ value, onChange, max, disabled }) {
  const roles = ["viewer", "member", "admin", "owner"].filter(r => r === value || (ROLE_RANK[r] <= (ROLE_RANK[max] ?? 0) && (r !== "owner" || max === "owner")));
  return html`<select class="select" style="width:auto;height:30px;font-size:13.5px" value=${value} disabled=${disabled} aria-label="Role" title=${disabled ? "A team needs at least one owner" : undefined} onChange=${e => onChange(e.target.value)}>
    ${roles.map(r => html`<option value=${r}>${ROLE_LABEL[r]}</option>`)}</select>`;
}
function InviteForm({ T, onInvited }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [days, setDays] = useState("7");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const submit = async e => {
    e.preventDefault(); setBusy(true); setErr("");
    try { const r = await api.inviteMember(T.id, email.trim(), role, +days); onInvited(r); setEmail(""); }
    catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  const roles = ["viewer", "member", "admin", "owner"].filter(r => ROLE_RANK[r] <= (ROLE_RANK[T.role] ?? 0));
  return html`<form class="inlineform" onSubmit=${submit}>
    <label class="field grow"><span>Invite by email</span><input class="input" type="email" required placeholder="name@company.com" value=${email} onInput=${e => setEmail(e.target.value)}/></label>
    <label class="field"><span>Role</span><select class="select" value=${role} onChange=${e => setRole(e.target.value)}>${roles.map(r => html`<option value=${r}>${ROLE_LABEL[r]}</option>`)}</select></label>
    <label class="field"><span>Link works for</span><select class="select" value=${days} onChange=${e => setDays(e.target.value)}><option value="1">1 day</option><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option></select></label>
    <button class="btn primary" type="submit" disabled=${busy || !email.trim()}>${busy ? "Creating…" : "Create invite link"}</button>
    ${err ? html`<p class="err small" role="alert" style="flex-basis:100%;margin:0">${err}</p>` : html`<p class="sub" style="flex-basis:100%;margin:0">${ROLE_HELP[role]}.</p>`}
  </form>`;
}

/* ---------------------------------------------------------------- tokens */
const PRESETS = [["read", "Read", "Impact, why, search, specs, sessions and memory"], ["agent", "Agent", "Read and write, record sessions: what a coding agent on another machine needs"],
  ["ci", "CI", "Read and start syncs: what a pipeline needs"], ["all", "Everything", "Every action your role allows, including admin"]];
function Tokens({ T }) {
  const { session, toast } = useApp();
  const seeAll = allowed(T, "team.tokens");
  const [all, setAll] = useState(false);
  const [revoked, setRevoked] = useState(false);
  const res = useFetch(() => api.tokens({ team: T.id, all: all ? "1" : undefined, include_revoked: revoked ? "1" : undefined }), [T.id, all, revoked]);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState(null);
  const projName = id => session.projects.find(p => p.id === id)?.name || id;
  const canMint = session.kind !== "token";
  return html`<div>
    <div class="sec-h"><div><h2>${all ? "Everyone's API tokens" : "Your API tokens"}</h2><p class="sub">For CI, scripts and agents on other machines. Send a token as <code>Authorization: Bearer cairn_…</code>. A token never does more than its owner's role allows, and it stops working when they leave the team.</p></div>
      ${canMint ? html`<button class="btn primary" onClick=${() => setCreating(true)}><${Icon} name="plus" size="15"/>New token</button>` : null}</div>
    <div class="row" style="margin:-4px 0 12px">
      ${seeAll ? html`<label class="switch"><input type="checkbox" checked=${all} onChange=${e => setAll(e.target.checked)}/>Show everyone's tokens in ${T.name}</label>` : null}
      <label class="switch"><input type="checkbox" checked=${revoked} onChange=${e => setRevoked(e.target.checked)}/>Include revoked and expired</label>
    </div>
    <${Load} res=${res} rows="4" what="tokens">${ts => ts.length ? html`<div class="table-wrap"><table class="table stack"><thead><tr><th>Name</th><th>Token</th><th>Scopes</th><th>Project</th>${all ? html`<th>Owner</th>` : null}<th>Last used</th><th>Expires</th><th></th></tr></thead><tbody>
      ${ts.map(t => { const st = t.status || (t.revoked_at ? "revoked" : t.expires_at && tsOf(t.expires_at) < Date.now() / 1000 ? "expired" : "active"); return html`<tr key=${t.id} class=${st !== "active" ? "expired" : ""}>
        <td class="lead" data-label="Name"><b>${t.name}</b><div class="sub">created ${date(t.created_at)}</div></td>
        <td data-label="Token"><code>${t.display || `${t.prefix}…`}</code></td>
        <td data-label="Scopes"><div class="chips">${(t.scopes || []).map(s => html`<${Tag} tone=${/admin|all|\*/.test(s) ? "risk" : /write|agent|sync|ci/.test(s) ? "code" : ""}>${s}</${Tag}>`)}</div></td>
        <td data-label="Project">${t.project_id ? projName(t.project_id) : html`<span class="sub">Whole team</span>`}</td>
        ${all ? html`<td data-label="Owner">${t.user_email || "—"}</td>` : null}
        <td data-label="Last used" class="nowrap">${t.last_used_at ? ago(t.last_used_at) : html`<span class="sub">Never</span>`}</td>
        <td data-label="Expires" class="nowrap">${st === "revoked" ? html`<${Tag} tone="risk">revoked</${Tag}>` : st === "expired" ? html`<${Tag} tone="risk">expired</${Tag}>` : !t.expires_at ? html`<span class="sub">Never</span>` : inTime(t.expires_at)}</td>
        <td class="n" data-label="">${st === "active" ? html`<${ConfirmButton} label="Revoke" confirm="Revoke now" onConfirm=${async () => { try { await api.revokeToken(t.id); toast({ title: `${t.name} revoked`, body: "Requests with it are refused from now on." }); res.reload(); } catch (e) { toast({ title: "Couldn't revoke", body: e.message, tone: "risk" }); } }}/>` : null}</td>
      </tr>`; })}</tbody></table></div>`
      : html`<${Empty} title="No tokens yet"><p>Create a token to let CI ask for impact on a pull request, or to connect an agent on another machine.</p></${Empty}>`}</${Load}>
    ${creating ? html`<${NewToken} T=${T} onClose=${() => setCreating(false)} onCreated=${r => { setCreating(false); setSecret(r); res.reload(); }}/>` : null}
    ${secret ? html`<${Modal} title="Copy your new token" onClose=${() => setSecret(null)} actions=${html`<button class="btn primary" onClick=${() => setSecret(null)}>I've saved it</button>`}>
      <p class="ink2">This is the only time the token is shown. Cairn keeps only a hash, so it can't show it again. If you lose it, revoke it and create another.</p>
      <div class="secret"><code>${secret.secret}</code><${CopyButton} text=${secret.secret} label="Copy"/></div>
      <p class="sub" style="margin-top:12px">Use it as a header:</p><div class="cmd"><code>Authorization: Bearer ${secret.secret}</code></div>
    </${Modal}>` : null}
  </div>`;
}
function NewToken({ T, onClose, onCreated }) {
  const { session } = useApp();
  const [name, setName] = useState("");
  const [preset, setPreset] = useState("agent");
  const [project, setProject] = useState("");
  const [days, setDays] = useState("90");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const submit = async e => {
    e?.preventDefault(); setBusy(true); setErr("");
    try { const r = await api.createToken({ name: name.trim(), team_id: T.id, project_id: project || undefined, scopes: [preset], expires_days: days === "never" ? undefined : +days }); onCreated(r); }
    catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  return html`<${Modal} title="New API token" onClose=${onClose} actions=${html`<button class="btn ghost" onClick=${onClose}>Cancel</button><button class="btn primary" disabled=${busy || !name.trim()} onClick=${submit}>${busy ? "Creating…" : "Create token"}</button>`}>
    <form class="stack" onSubmit=${submit}>
      <label class="field"><span>Name</span><input class="input" required placeholder="CI impact check" value=${name} onInput=${e => setName(e.target.value)}/><span class="hint">So you can recognise it in this list later.</span></label>
      <fieldset class="field" style="border:0;padding:0;margin:0"><span>What it can do</span>
        <div class="scopes" role="radiogroup">${PRESETS.map(([k, l, d]) => html`<label class=${"radio" + (preset === k ? " on" : "")}><input type="radio" name="preset" checked=${preset === k} onChange=${() => setPreset(k)}/><span><b>${l}</b><span class="sub">${d}</span></span></label>`)}</div></fieldset>
      <div class="row" style="align-items:flex-start">
        <label class="field" style="flex:1 1 200px"><span>Project</span><select class="select" value=${project} onChange=${e => setProject(e.target.value)}><option value="">Every project in ${T.name}</option>${session.projects.filter(p => p.team_id === T.id).map(p => html`<option value=${p.id}>${p.name}</option>`)}</select></label>
        <label class="field" style="flex:1 1 140px"><span>Expires</span><select class="select" value=${days} onChange=${e => setDays(e.target.value)}><option value="30">In 30 days</option><option value="90">In 90 days</option><option value="365">In a year</option><option value="never">Never</option></select></label>
      </div>
      ${err ? html`<p class="err small" role="alert" style="margin:0">${err}</p>` : null}
    </form></${Modal}>`;
}

/* ---------------------------------------------------------------- projects */
const STATUS = { ready: ["Ready", "memory"], pending: ["Waiting", "code"], cloning: ["Cloning", "agent"], error: ["Error", "risk"] };
function Projects({ T }) {
  const { toast, reloadSession, session } = useApp();
  const res = useFetch(() => api.projects(T.id), [T.id]);
  const canAdd = allowed({ role: T.role, permissions: undefined }, "project.admin");
  const [adding, setAdding] = useState(false);
  const [hook, setHook] = useState(null);
  const [people, setPeople] = useState(null);
  const refresh = async p => { try { await api.refreshProject(p.id); toast({ title: `Refreshing ${p.name}`, body: p.kind === "git" ? "Pulling the latest commits, then syncing every layer." : "Syncing every layer in the background.", tone: "code" }); setTimeout(res.reload, 1500); } catch (e) { toast({ title: "Couldn't refresh", body: e.message, tone: "risk" }); } };
  return html`<div>
    <div class="sec-h"><div><h2>Projects</h2><p class="sub">Repositories ${session.mode === "local" ? "on this machine" : "this team"} keeps a map, specs, timeline, memory and sessions for. One server serves all of them.</p></div>
      ${canAdd ? html`<button class="btn primary" onClick=${() => setAdding(true)}><${Icon} name="plus" size="15"/>Add a project</button>` : null}</div>
    <${Load} res=${res} rows="4" what="projects">${all => { const ps = all.filter(p => !p.team_id || p.team_id === T.id);
      return ps.length ? html`<ul class="projlist">${ps.map(p => { const st = STATUS[p.status] || [p.status || "Ready", "memory"]; const pa = allowed(p, "project.admin");
        return html`<li key=${p.id}>
        <${SheetMark} seed=${p.id} size="40"/>
        <div class="pinfo"><div class="row tight"><a href=${link.project(p.id)}><b>${p.name}</b></a><${Tag} tone=${st[1]}>${st[0]}</${Tag}><span class="tag outline">${p.kind === "git" ? "Git" : "Local folder"}</span></div>
          <span class="path">${p.root || p.git_url || p.slug}${p.kind === "git" ? ` on ${p.git_branch || "the default branch"}` : ""}</span>
          <span class="sub">${p.last_synced_at || p.last_sync ? `Synced ${ago(p.last_synced_at || p.last_sync)}` : "Never synced"}${p.kind === "git" ? `, ${p.webhook_configured ? "syncs on every push" : "no push webhook"}` : ""}${p.status === "error" && p.status_detail ? html`. <span class="err">${p.status_detail}</span>` : ""}</span></div>
        <div class="row tight pact">
          <a class="btn sm" href=${link.project(p.id)}>Open</a>
          ${allowed(p, "project.sync") ? html`<button class="btn sm" onClick=${() => refresh(p)} title=${p.kind === "git" ? "Pull and sync" : "Sync now"}><${Icon} name="sync" size="14"/>Refresh</button>` : null}
          <button class="btn sm" onClick=${() => setPeople(p)}><${Icon} name="users" size="14"/>People</button>
          ${pa && p.kind === "git" ? html`<button class="btn sm" onClick=${async () => { try { setHook({ ...(await api.webhook(p.id)), project: p }); res.reload(); } catch (e) { toast({ title: "Couldn't create a webhook secret", body: e.message, tone: "risk" }); } }}>${p.webhook_configured ? "New webhook secret" : "Set up webhook"}</button>` : null}
          ${pa ? html`<${ConfirmButton} label="Remove" confirm=${`Remove ${p.name}`} onConfirm=${async () => { try { await api.deleteProject(p.id, p.managed || p.kind === "git"); toast({ title: `${p.name} removed`, body: p.kind === "git" ? "Its clone and data on the server were deleted." : "Cairn's data for it was removed; the folder itself is untouched." }); res.reload(); reloadSession(); } catch (e) { toast({ title: "Couldn't remove", body: e.message, tone: "risk" }); } }}/>` : null}
        </div></li>`; })}</ul>`
        : html`<${Empty} title="No projects yet" actions=${canAdd ? html`<button class="btn primary" onClick=${() => setAdding(true)}>Add a project</button>` : null}><p>Add a repository by its git URL${session.mode === "local" || session.user?.is_admin ? " or its folder on the server" : ""}. The first sync builds every layer.</p></${Empty}>`; }}</${Load}>
    ${adding ? html`<${AddProject} T=${T} onClose=${() => setAdding(false)} onAdded=${p => { setAdding(false); res.reload(); reloadSession().then(() => toast({ title: `${p.name} added`, body: p.kind === "git" ? "Cloning now; the first sync starts when it finishes." : "Open it to run the first sync.", tone: "memory" })); }}/>` : null}
    ${hook ? html`<${Modal} title=${`Push webhook for ${hook.project.name}`} onClose=${() => setHook(null)} actions=${html`<button class="btn primary" onClick=${() => setHook(null)}>I've saved it</button>`}>
      <p class="ink2">Add a push webhook in your git host with these values, and Cairn pulls and syncs on every push. The secret is shown only once.</p>
      <dl class="kv" style="margin:12px 0">${hook.url ? html`<dt>Payload URL</dt><dd><div class="cmd"><code>${hook.url}</code><${CopyButton} text=${hook.url} iconOnly label="Copy URL"/></div></dd>` : null}
        ${hook.content_type ? html`<dt>Content type</dt><dd><code>${hook.content_type}</code></dd>` : null}
        ${hook.events ? html`<dt>Events</dt><dd>${[].concat(hook.events).join(", ")}</dd>` : null}</dl>
      <div class="secret"><code>${hook.secret}</code><${CopyButton} text=${hook.secret} label="Copy secret"/></div>
      ${hook.note ? html`<p class="sub" style="margin-top:10px">${hook.note}</p>` : null}
    </${Modal}>` : null}
    ${people ? html`<${ProjectPeople} p=${people} onClose=${() => setPeople(null)}/>` : null}
  </div>`;
}
const OVERRIDES = [["", "Team role"], ["none", "No access"], ["viewer", "Viewer"], ["member", "Member"], ["admin", "Admin"]];
function ProjectPeople({ p, onClose }) {
  const { toast } = useApp();
  const res = useFetch(() => api.projectMembers(p.id), [p.id]);
  const admin = allowed(p, "project.admin");
  const set = async (m, v) => {
    try { v ? await api.setProjectRole(p.id, m.user_id, v) : await api.clearProjectRole(p.id, m.user_id); toast({ title: `${m.name || m.email}: ${v ? OVERRIDES.find(o => o[0] === v)[1].toLowerCase() : "team role"} on ${p.name}`, tone: "memory" }); res.reload(); }
    catch (e) { toast({ title: "Couldn't change access", body: e.message, tone: "risk" }); res.reload(); }
  };
  return html`<${Modal} title=${`People on ${p.name}`} onClose=${onClose} wide actions=${html`<button class="btn" onClick=${onClose}>Close</button>`}>
    <p class="ink2" style="margin-top:0">Everyone gets their team role on every project. Override it here for this project only: give a viewer member access, or keep someone out. Owners can't be overridden.</p>
    <${Load} res=${res} rows="4">${ms => html`<div class="table-wrap"><table class="table stack"><thead><tr><th>Person</th><th>Team role</th><th>On this project</th><th>Effective</th></tr></thead><tbody>
      ${ms.map(m => html`<tr key=${m.user_id}><td class="lead" data-label="Person"><div class="person"><${Avatar} name=${m.name || m.email} size="sm"/><span><b>${m.name || m.email}</b><span class="sub">${m.email}</span></span></div></td>
        <td data-label="Team role"><${RoleBadge} role=${m.team_role}/></td>
        <td data-label="On this project">${admin && m.team_role !== "owner" ? html`<select class="select" style="width:auto;height:30px;font-size:13.5px" value=${m.override || ""} onChange=${e => set(m, e.target.value)} aria-label=${`Access for ${m.name || m.email}`}>
          ${OVERRIDES.map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select>` : html`<span class="sub">${m.override ? OVERRIDES.find(o => o[0] === m.override)?.[1] : "Team role"}</span>`}</td>
        <td data-label="Effective">${m.role && m.role !== "none" ? html`<${RoleBadge} role=${m.role}/>` : html`<${Tag} tone="risk">no access</${Tag}>`}</td></tr>`)}</tbody></table></div>`}</${Load}>
  </${Modal}>`;
}
function AddProject({ T, onClose, onAdded }) {
  const { session } = useApp();
  const pathOk = session.mode === "local" || !!session.user?.is_admin;
  const [src, setSrc] = useState(pathOk ? "path" : "git");
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [git, setGit] = useState("");
  const [branch, setBranch] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const guess = v => { const m = String(v).replace(/\/+$/, "").replace(/\.git$/, "").split(/[\\/:]/).pop(); if (!name && m) setName(m); };
  const submit = async e => {
    e?.preventDefault(); setBusy(true); setErr("");
    try { const p = await api.createProject({ team_id: T.id, name: name.trim() || undefined, ...(src === "path" ? { path: path.trim() } : { git_url: git.trim(), branch: branch.trim() || undefined }) }); onAdded(p); }
    catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  return html`<${Modal} title="Add a project" onClose=${onClose} actions=${html`<button class="btn ghost" onClick=${onClose}>Cancel</button><button class="btn primary" disabled=${busy || (src === "path" ? !path.trim() : !git.trim())} onClick=${submit}>${busy ? "Adding…" : "Add project"}</button>`}>
    <form class="stack" onSubmit=${submit}>
      <div class="seg" role="radiogroup" aria-label="Where the code is">
        <button type="button" role="radio" aria-checked=${src === "git"} aria-pressed=${src === "git"} onClick=${() => setSrc("git")}>Git URL</button>
        <button type="button" role="radio" aria-checked=${src === "path"} aria-pressed=${src === "path"} disabled=${!pathOk} title=${pathOk ? undefined : "Only server admins can register folders on the server"} onClick=${() => setSrc("path")}>Folder on ${session.mode === "local" ? "this machine" : "the server"}</button></div>
      ${src === "path" ? html`<label class="field"><span>Folder</span><input class="input mono" placeholder="/srv/code/billing-api" value=${path} onInput=${e => setPath(e.target.value)} onBlur=${e => guess(e.target.value)}/><span class="hint">An absolute path to a git repository the Cairn server can read. Cairn never deletes it.</span></label>`
        : html`<label class="field"><span>Git URL</span><input class="input mono" placeholder="https://git.example.com/team/app.git" value=${git} onInput=${e => setGit(e.target.value)} onBlur=${e => guess(e.target.value)}/><span class="hint">Cairn clones it on the server and pulls before each sync.</span></label>
          <label class="field"><span>Branch</span><input class="input mono" placeholder="The repository's default branch" value=${branch} onInput=${e => setBranch(e.target.value)}/></label>`}
      <label class="field"><span>Name</span><input class="input" placeholder="Taken from the folder or URL" value=${name} onInput=${e => setName(e.target.value)}/></label>
      ${!pathOk ? html`<p class="sub" style="margin:0">Registering a folder on the server needs a server admin.</p>` : null}
      ${err ? html`<p class="err small" role="alert" style="margin:0">${err}</p>` : null}
    </form></${Modal}>`;
}

/* ---------------------------------------------------------------- audit */
const AGROUPS = [["", "Everything"], ["member", "Members"], ["invit", "Invites"], ["token", "Tokens"], ["project", "Projects"], ["memory", "Memory"], ["task", "Tasks"], ["team", "Team"], ["auth", "Sign-ins"]];
const detailText = d => d == null || d === "" ? "" : typeof d === "string" ? d : Object.entries(d).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ");
function Audit({ T }) {
  const { session } = useApp();
  const [group, setGroup] = useState("");
  const [q, setQ] = useState("");
  const [project, setProject] = useState("");
  const [pages, setPages] = useState([]);
  const can = allowed(T, "team.audit");
  const res = useFetch(() => can ? api.audit({ team: T.id, project: project || undefined, limit: 100 }) : Promise.resolve([]), [T.id, project, can]);
  if (!can) return html`<${Empty} title="The audit log is for admins" tone="risk"><p>Ask a team admin if you need to know who changed something.</p></${Empty}>`;
  const more = async () => { const all = [...(res.data || []), ...pages.flat()]; const last = all[all.length - 1]; if (!last) return; setPages([...pages, await api.audit({ team: T.id, project: project || undefined, limit: 100, before: last.ts })]); };
  const pname = id => session.projects.find(p => p.id === id)?.name;
  return html`<div>
    <div class="sec-h"><div><h2>Audit log</h2><p class="sub">Every change made through Cairn in this team: who, what, when, and from where.</p></div></div>
    <div class="row" style="margin:0 0 12px"><div class="chips">${AGROUPS.map(([k, l]) => html`<button class="chip" aria-pressed=${group === k} onClick=${() => setGroup(k)}>${l}</button>`)}</div>
      <select class="select" style="width:auto" value=${project} onChange=${e => { setProject(e.target.value); setPages([]); }} aria-label="Project"><option value="">All projects</option>${session.projects.filter(p => p.team_id === T.id).map(p => html`<option value=${p.id}>${p.name}</option>`)}</select>
      <input class="input" type="search" style="max-width:240px" placeholder="Filter by person or detail" value=${q} onInput=${e => setQ(e.target.value)} aria-label="Filter the audit log"/></div>
    <${Load} res=${res} rows="8" what="the audit log">${first => { const rows = [...first, ...pages.flat()];
      const shown = rows.filter(r => (!group || String(r.action).startsWith(group)) && (!q || JSON.stringify(r).toLowerCase().includes(q.toLowerCase())));
      return html`${shown.length ? html`<div class="table-wrap"><table class="table stack audit"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead><tbody>
        ${shown.map((r, i) => html`<tr key=${r.id || i}><td data-label="When" class="nowrap" title=${stamp(r.ts)}>${ago(r.ts)}</td><td data-label="Who" class="lead">${r.actor || "system"}${r.ip ? html`<div class="sub small">${r.ip}</div>` : null}</td>
          <td data-label="Action"><code class=${"act " + String(r.action).split(".")[0]}>${r.action}</code></td><td data-label="Target">${pname(r.project_id) || r.target}</td><td data-label="Detail">${detailText(r.detail)}</td></tr>`)}</tbody></table></div>`
        : html`<p class="sub">No entries match.</p>`}
        ${rows.length && (pages.length ? pages[pages.length - 1].length : first.length) >= 100 ? html`<button class="btn" style="margin-top:12px" onClick=${more}>Show older entries</button>` : null}`; }}</${Load}>
  </div>`;
}

/* ---------------------------------------------------------------- team settings */
function TeamSettings({ T, reloadTeam }) {
  const { toast, reloadSession, session } = useApp();
  const canRename = allowed(T, "team.admin"), canDelete = allowed(T, "team.delete");
  const [name, setName] = useState(T.name);
  const [slug, setSlug] = useState(T.slug);
  const [busy, setBusy] = useState(false);
  const [confirmSlug, setConfirmSlug] = useState("");
  const save = async e => {
    e.preventDefault(); setBusy(true);
    try {
      const t = await api.updateTeam(T.id, { name: name.trim(), slug: slug.trim() });
      await reloadSession(); reloadTeam();
      setName(t?.name ?? name.trim()); setSlug(t?.slug ?? slug.trim());
      toast({ title: "Team updated", tone: "memory" });
    }
    catch (x) { toast({ title: "Couldn't update the team", body: x.message, tone: "risk" }); } finally { setBusy(false); }
  };
  const remove = async () => {
    try { await api.deleteTeam(T.id, T.slug); const s = await reloadSession(); toast({ title: `${T.name} deleted` }); go(s.projects[0] ? link.project(s.projects[0].id) : "#/"); }
    catch (x) { toast({ title: "Couldn't delete the team", body: x.message, tone: "risk" }); }
  };
  const projects = session.projects.filter(p => p.team_id === T.id).length;
  return html`<div class="teamset">
    <form class="panel stack" onSubmit=${save}>
      <h2>Name and address</h2>
      <label class="field"><span>Team name</span><input class="input" value=${name} disabled=${!canRename} onInput=${e => setName(e.target.value)}/></label>
      <label class="field"><span>Short name</span><input class="input mono" value=${slug} disabled=${!canRename} onInput=${e => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))}/><span class="hint">Lowercase letters, numbers and dashes.</span></label>
      ${canRename ? html`<div><button class="btn primary" type="submit" disabled=${busy || (name === T.name && slug === T.slug)}>${busy ? "Saving…" : "Save changes"}</button></div>` : html`<p class="sub"><${Icon} name="lock" size="14"/> Only team owners can rename the team.</p>`}
    </form>
    ${canDelete ? html`<div class="panel danger">
      <h2>Delete this team</h2>
      <p class="sub">Removes the team, its ${plural(projects, "project")} and everything Cairn stored for them on this server. Repositories themselves are not touched. This can't be undone.</p>
      <label class="field"><span>Type <b>${T.slug}</b> to confirm</span><input class="input mono" value=${confirmSlug} onInput=${e => setConfirmSlug(e.target.value)}/></label>
      <div style="margin-top:10px"><button class="btn danger solid" disabled=${confirmSlug !== T.slug} onClick=${remove}>Delete ${T.name}</button></div>
    </div>` : null}
  </div>`;
}
