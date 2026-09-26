// Sign-in and invite acceptance.
import { useState } from "preact/hooks";
import { html, Logo, ContourField, Skeleton } from "../components/ui.js";
import { useApp, useFetch } from "../state.js";
import { api, env, setCsrf } from "../api.js";
import { ROLE_LABEL, date } from "../lib/format.js";
import { link } from "../router.js";

const wait = s => !s ? "a minute" : s < 90 ? `${Math.ceil(s)} seconds` : `${Math.ceil(s / 60)} minutes`;

function Art({ title, body }) {
  return html`<div class="auth-art">
    <${ContourField} seed="cairn-survey-sheet" w=${900} h=${900} class="field" levels=${20} bumps=${18} accent="var(--code)"/>
    <div class="brandline"><${Logo} size="30"/><b>cairn</b></div>
    <div class="claim"><h1>${title}</h1><p>${body}</p></div>
    <div class="legend">
      <span><i class="dotkey" style="background:var(--code)"></i>Code and documents</span>
      <span><i class="dotkey" style="background:var(--spec)"></i>Specs</span>
      <span><i class="dotkey" style="background:var(--agent)"></i>Agent sessions</span>
      <span><i class="dotkey" style="background:var(--memory)"></i>Memory</span>
      <span><i class="dotkey" style="background:var(--ink-2)"></i>Timeline</span>
    </div>
  </div>`;
}

export function Login({ next }) {
  const { reloadSession, requirePasswordChange } = useApp();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async e => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const me = await api.login(email.trim(), password);
      setCsrf(me?.csrf_token);
      // A one-time password: choose a new one before anything else (the server refuses other calls until then).
      if (me?.must_change_password || me?.user?.must_change_password) { requirePasswordChange(me.user || { email: me.email, name: me.name, has_password: true }); return; }
      await reloadSession();
      location.hash = next && next.startsWith("#/") ? next : "#/";
    } catch (err) {
      if (err.status === 403 && /password change required/i.test(err.message)) { requirePasswordChange({ email: email.trim(), has_password: true }); return; }
      setError(err.status === 401 ? "That email and password don't match an account. Check both and try again."
        : err.status === 429 ? `Too many attempts. Try again in ${wait(err.retryAfter)}.`
        : err.status === 400 && env.mode === "local" ? "This server runs in local mode, so there is nothing to sign in to. Reload the page."
        : err.message);
    } finally { setBusy(false); }
  };
  return html`<main class="auth">
    <${Art} title="Your team's memory of the code" body="Sign in to see every project your teams share: the map of the code, the specs behind it, what agents learned, and what changed when."/>
    <div class="auth-form">
      <form onSubmit=${submit} aria-labelledby="signin-h" novalidate>
        <div><h2 id="signin-h">Sign in</h2><p class="sub" style="margin:4px 0 0">Use the email your team invited.</p></div>
        <label class="field"><span>Email</span><input class="input" type="email" autocomplete="username" required value=${email} onInput=${e => setEmail(e.target.value)} autofocus/></label>
        <label class="field"><span>Password</span><input class="input" type="password" autocomplete="current-password" required value=${password} onInput=${e => setPassword(e.target.value)}/></label>
        ${error ? html`<p class="err small" role="alert" style="margin:0">${error}</p>` : null}
        <button class="btn primary" type="submit" disabled=${busy || !email || !password}>${busy ? "Signing in…" : "Sign in"}</button>
        <p class="sub" style="margin:0">Forgot your password? Ask a team admin to send you a new invite. Working alone? Run <code>cairn ui</code> on your machine; local mode needs no sign-in.</p>
      </form>
    </div>
  </main>`;
}

export function Invite({ token }) {
  const { reloadSession, session } = useApp();
  const inv = useFetch(() => api.invite(token), [token]);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const d = inv.data;
  const signedIn = !!session?.user && session.mode === "team";
  const existing = !!d?.account_exists;
  const submit = async e => {
    e.preventDefault();
    if (!signedIn && !existing && password.length < 10) { setError("Use at least 10 characters for your password."); return; }
    setBusy(true); setError("");
    try {
      const r = await api.acceptInvite(token, signedIn ? {} : existing ? { password } : { name: name.trim(), password });
      setCsrf(r?.csrf_token);
      const s = await reloadSession();
      const first = s?.projects?.find(p => p.team_id === (r?.team?.id || d.team?.id));
      location.hash = first ? link.project(first.id) : r?.team?.id ? link.team(r.team.id) : "#/";
    } catch (err) {
      setError(err.status === 403 || err.status === 401 ? (existing ? "That password isn't right for this account." : err.message)
        : err.status === 429 ? `Too many attempts. Try again in ${wait(err.retryAfter)}.` : err.message);
    } finally { setBusy(false); }
  };
  const gone = inv.error && { 404: ["This invite link isn't valid", "It may have been revoked, or the link was copied incompletely. Ask the person who invited you for a new one."],
    409: ["This invite has already been used", "If it was you, sign in with the account you created."], 410: ["This invite has expired", "Invites work for a limited time. Ask the person who invited you for a new link."] }[inv.error.status];
  return html`<main class="auth">
    <${Art} title=${d ? `Join ${d.team?.name || "the team"}` : "Join your team"} body="Accept the invite to see the team's projects, their specs and sessions, and what the team has learned."/>
    <div class="auth-form">
      ${inv.error ? html`<div style="max-width:380px"><h2>${gone ? gone[0] : "This invite can't be used"}</h2><p class="sub" style="margin:6px 0 14px">${gone ? gone[1] : inv.error.message}</p><a class="btn" href="#/login">Go to sign in</a></div>`
      : !d ? html`<div style="width:min(380px,100%)"><${Skeleton} rows="5"/></div>`
      : html`<form onSubmit=${submit} aria-labelledby="inv-h">
        <div><h2 id="inv-h">You're invited to ${d.team?.name}</h2>
          <p class="sub" style="margin:4px 0 0">as ${(ROLE_LABEL[d.role] || d.role || "").toLowerCase()}${d.expires_at ? `. The invite expires ${date(d.expires_at)}.` : "."}</p></div>
        <label class="field"><span>Email</span><input class="input" value=${d.email} disabled/></label>
        ${signedIn ? html`<p class="sub" style="margin:0">You're signed in as ${session.user.email}. Joining adds ${d.team?.name} to this account${session.user.email !== d.email ? "; the invite is for a different email, so the server may refuse it" : ""}.</p>`
          : existing ? html`<p class="sub" style="margin:0">You already have a Cairn account with this email. Enter its password to join.</p>
            <label class="field"><span>Password</span><input class="input" type="password" autocomplete="current-password" required value=${password} onInput=${e => setPassword(e.target.value)} autofocus/></label>`
          : html`<label class="field"><span>Your name</span><input class="input" autocomplete="name" required value=${name} onInput=${e => setName(e.target.value)} autofocus/></label>
            <label class="field"><span>Choose a password</span><input class="input" type="password" autocomplete="new-password" required minlength="10" value=${password} onInput=${e => setPassword(e.target.value)}/>
              <span class="hint">At least 10 characters.</span></label>`}
        ${error ? html`<p class="err small" role="alert" style="margin:0">${error}</p>` : null}
        <button class="btn primary" type="submit" disabled=${busy || (!signedIn && (!password || (!existing && !name)))}>${busy ? "Joining…" : `Join ${d.team?.name || "team"}`}</button>
      </form>`}
    </div>
  </main>`;
}

/** Shown before anything else when the account must change its password (new account or after a reset). */
export function ForcePassword({ onDone, user }) {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async e => {
    e.preventDefault();
    if (next.length < 10) { setError("Use at least 10 characters."); return; }
    if (next !== again) { setError("The two passwords don't match."); return; }
    setBusy(true); setError("");
    try { await api.changePassword(cur || undefined, next); onDone(); }
    catch (err) { setError(err.status === 403 ? "The temporary password isn't right." : err.status === 429 ? `Too many attempts. Try again in ${wait(err.retryAfter)}.` : err.message); }
    finally { setBusy(false); }
  };
  return html`<main class="auth">
    <${Art} title="Choose your own password" body="Your account was set up with a temporary password. Pick one only you know before you continue."/>
    <div class="auth-form"><form onSubmit=${submit} aria-labelledby="pw-h">
      <div><h2 id="pw-h">Set a new password</h2><p class="sub" style="margin:4px 0 0">${user?.email || ""}</p></div>
      ${user?.has_password !== false ? html`<label class="field"><span>Temporary password</span><input class="input" type="password" autocomplete="current-password" value=${cur} onInput=${e => setCur(e.target.value)}/>
        <span class="hint">The one an admin gave you, if you were given one.</span></label>` : null}
      <label class="field"><span>New password</span><input class="input" type="password" autocomplete="new-password" required value=${next} onInput=${e => setNext(e.target.value)}/><span class="hint">At least 10 characters.</span></label>
      <label class="field"><span>New password again</span><input class="input" type="password" autocomplete="new-password" required value=${again} onInput=${e => setAgain(e.target.value)}/></label>
      ${error ? html`<p class="err small" role="alert" style="margin:0">${error}</p>` : null}
      <button class="btn primary" type="submit" disabled=${busy || !next || !again}>${busy ? "Saving…" : "Save and continue"}</button>
    </form></div>
  </main>`;
}
