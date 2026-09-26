// Memory: conventions, decisions and gotchas every agent should know. New memories are reconciled
// against what is already known: added, merged into a similar one, used to replace one they
// contradict, or recognised as already known.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, Empty, Load, Tag, Skeleton, ConfirmButton, Meter } from "../components/ui.js";
import { useApp, useFetch, useLive } from "../state.js";
import { api } from "../api.js";
import { link, replace } from "../router.js";
import { ago, fmt, plural, rich, stamp, tsOf } from "../lib/format.js";

export const MKINDS = [["convention", "Convention"], ["decision", "Decision"], ["gotcha", "Gotcha"], ["preference", "Preference"], ["fact", "Fact"]];
const MLABEL = Object.fromEntries(MKINDS);
/** Replaced by a newer memory, or forgotten: kept in history, no longer told to agents. */
const retired = m => !!m.superseded_by || !!m.forgotten;
const PROV = { seeded: "Seeded from", agent: "Saved by an agent from", user: "Added by", EXTRACTED: "From", INFERRED: "Inferred from" };
/** History arrives either as a list of {ts, op, old, new, actor} or as {memory, chain, changes}. */
function normHistory(h) {
  if (Array.isArray(h)) return h;
  // A change's own time is its updated_at; created_at is when the memory it belongs to was first saved.
  const changes = (h?.changes || []).map(c => ({ ts: tsOf(c.updated_at ?? c.created_at ?? c.ts), op: c.event || c.op, old: c.old_memory ?? c.old, new: c.new_memory ?? c.new, actor: c.actor || c.role || (c.actor_id ? "agent" : "") }));
  // An edit shows up twice, as an UPDATE change and as the older version in the chain: keep the change only.
  const edited = new Set(changes.filter(c => c.op === "UPDATE" && c.old).map(c => c.old));
  const chain = (h?.chain || []).filter(x => x.id !== h?.memory?.id && !edited.has(x.text)).map(x => ({ ts: tsOf(x.created_at), op: "REPLACED", old: x.text, new: null, actor: x.source || "" }));
  return [...chain, ...changes].sort((a, b) => (a.ts || 0) - (b.ts || 0));
}
const OP = {
  ADD: ["Saved as a new memory", "memory"],
  UPDATE: ["Merged into a similar memory", "code"],
  DELETE: ["Saved, and replaced a memory it contradicts", "risk"],
  NONE: ["Already known. Nothing changed", "ink"],
};

export function Memory() {
  const { pid, route, can, toast, reloadOv } = useApp();
  const q = route.q;
  const kind = q.get("kind") || "", text = q.get("q") || "", all = q.get("all") === "1", focus = q.get("focus");
  const setQ = patch => { const n = new URLSearchParams(q); for (const [k, v] of Object.entries(patch)) v ? n.set(k, v) : n.delete(k); const s = n.toString(); replace(link.project(pid, "memory") + (s ? "?" + s : "")); };
  const res = useFetch(() => api.p(pid).memories({ all: "1" }), [pid]);
  const sources = useFetch(() => api.p(pid).memorySources(), [pid]);
  const [flash, setFlash] = useState(null);
  const changed = () => { res.reload(); reloadOv?.(); };
  useLive(ev => { if (ev.type === "memory") res.reload(); });
  useEffect(() => { if (focus && res.data) setTimeout(() => document.getElementById("mem-" + focus)?.scrollIntoView({ block: "center" }), 80); }, [focus, !!res.data]);
  const onSaved = r => {
    const op = r.op || r.result?.op || r.reconciliation?.op || "ADD";
    const [title, tone] = OP[op] || OP.ADD;
    toast({ title, tone, body: op === "NONE" ? rich(r.reason || r.text, 140) : null,
      diff: op === "UPDATE" && r.previous ? { old: r.previous, new: r.text } : op === "DELETE" && r.replaced ? { old: r.replaced.text, new: r.text } : null, ms: 8000 });
    setFlash(r.id);
    setTimeout(() => setFlash(null), 3000);
    changed();
  };

  return html`<div class="page view-in memory">
    <div class="page-h"><div><h1>Memory</h1>
      <p class="lede">Conventions, decisions and gotchas every agent should know. Agents save them with <code>cairn_remember</code>; they come back in impact and context answers for the code they mention.</p></div></div>
    <div class="memgrid">
      <div>
        ${can.write ? html`<${AddForm} onSaved=${onSaved}/>` : html`<p class="sub readonly"><${Icon} name="lock" size="14"/> Viewers can read memories but can't add or change them.</p>`}
        <${Load} res=${res} rows="8" what="memories">${list => {
          const active = list.filter(m => !retired(m));
          const replaced = list.filter(m => m.superseded_by).length, forgotten = list.filter(m => m.forgotten && !m.superseded_by).length;
          const counts = Object.fromEntries(MKINDS.map(([k]) => [k, active.filter(m => m.kind === k).length]));
          const shown = list.filter(m => (all || !retired(m)) && (!kind || m.kind === kind) && (!text || m.text.toLowerCase().includes(text.toLowerCase()) || (m.source || "").toLowerCase().includes(text.toLowerCase())));
          const byId = new Map(list.map(m => [m.id, m]));
          return html`<div>
            <div class="memtools">
              <input class="input" type="search" placeholder="Search memories" value=${text} aria-label="Search memories" onInput=${e => setQ({ q: e.target.value })}/>
              <div class="chips">${MKINDS.map(([k, l]) => html`<button class="chip" aria-pressed=${kind === k} onClick=${() => setQ({ kind: kind === k ? null : k })}>${l}<span class="n">${counts[k]}</span></button>`)}</div>
              <label class="switch" title="Memories replaced by a newer one, or forgotten. Agents are no longer told about them."><input type="checkbox" checked=${all} onChange=${e => setQ({ all: e.target.checked ? "1" : null })}/>Show retired</label>
            </div>
            ${!list.length ? html`<${Empty} title="Nothing saved yet" tone="memory"><p>The first convention saved here reaches every agent's next impact or context answer for the code it mentions.</p></${Empty}>`
              : !shown.length ? html`<p class="sub">No memory matches these filters.</p>`
              : html`<ol class="mems">${shown.map(m => html`<li key=${m.id}><${MemoryRow} m=${m} byId=${byId} flash=${flash === m.id || focus === m.id} onChanged=${changed}/></li>`)}</ol>`}
            <p class="sub" style="margin-top:12px">${plural(active.length, "active memory", "active memories")}${replaced + forgotten ? `; ${[replaced && `${fmt(replaced)} replaced`, forgotten && `${fmt(forgotten)} forgotten`].filter(Boolean).join(" and ")}${all ? "" : ", hidden"}` : ""}.</p>
          </div>`;
        }}</${Load}>
      </div>
      <aside class="memside">
        <div class="panel"><h3>Where memories come from</h3>
          <p class="sub">Cairn seeds memory on every sync from instruction files, decision records and agent sessions, and keeps what people and agents add.</p>
          <${Load} res=${sources} rows="4">${ss => ss.length ? html`<ul class="sources">${ss.map(s => html`<li><span class="nm"><b>${s.source}</b>${s.path && s.path !== s.source ? html`<span class="path">${s.path}</span>` : null}</span><span class="num">${fmt(s.count)}</span><span class="sub nowrap">${ago(s.last_seen)}</span></li>`)}</ul>`
            : html`<p class="sub">No seeded sources yet. Add a <code>CLAUDE.md</code> or <code>AGENTS.md</code> with your conventions and sync.</p>`}</${Load}>
        </div>
        <div class="panel"><h3>How saving works</h3>
          <ol class="howto">
            <li><b>Added</b> when nothing similar is known.</li>
            <li><b>Merged</b> into a similar memory, keeping the newer wording.</li>
            <li><b>Replaced</b>: a memory it contradicts is retired and kept in history.</li>
            <li><b>Already known</b>: nothing changes.</li>
          </ol>
        </div>
      </aside>
    </div>
  </div>`;
}

function AddForm({ onSaved }) {
  const { pid, toast } = useApp();
  const [text, setText] = useState("");
  const [kind, setKind] = useState("convention");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const submit = async e => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true); setErr("");
    try { const r = await api.p(pid).addMemory({ text: text.trim(), kind }); setText(""); onSaved(r); }
    catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  return html`<form class="memform" onSubmit=${submit}>
    <label class="sr-only" for="memtext">New memory</label>
    <textarea id="memtext" class="textarea" required value=${text} onInput=${e => setText(e.target.value)} placeholder="One precise sentence, for example: Money is stored in integer cents; never use floats in src/billing."
      onKeyDown=${e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(e); }}></textarea>
    <div class="row">
      <select class="select" style="width:auto" value=${kind} onChange=${e => setKind(e.target.value)} aria-label="Kind">${MKINDS.map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
      <span class="sub">Cairn checks it against what it already knows before saving.</span><span class="spacer"></span>
      <button class="btn primary" type="submit" disabled=${busy || !text.trim()}>${busy ? "Saving…" : "Save memory"}</button>
    </div>
    ${err ? html`<p class="err small" role="alert" style="margin:0">${err}</p>` : null}
  </form>`;
}

function MemoryRow({ m, byId, flash, onChanged }) {
  const { pid, can, toast } = useApp();
  const [mode, setMode] = useState(null);
  const [text, setText] = useState(m.text);
  const [kind, setKind] = useState(m.kind);
  const [busy, setBusy] = useState(false);
  const hist = useFetch(() => mode === "history" ? api.p(pid).memoryHistory(m.id).then(normHistory) : Promise.resolve(null), [pid, m.id, mode]);
  const save = async () => {
    setBusy(true);
    try { await api.p(pid).editMemory(m.id, { text: text.trim(), kind }); setMode(null); toast({ title: "Memory updated", tone: "memory" }); onChanged(); }
    catch (e) { toast({ title: "Couldn't update the memory", body: e.message, tone: "risk" }); } finally { setBusy(false); }
  };
  const forget = async () => {
    try { await api.p(pid).deleteMemory(m.id); toast({ title: "Memory forgotten", body: "Agents are no longer told about it. Turn on Show retired to see it again.", tone: "ink" }); onChanged(); }
    catch (e) { toast({ title: "Couldn't forget the memory", body: e.message, tone: "risk" }); }
  };
  const replacedBy = m.superseded_by ? byId.get(m.superseded_by) : null;
  const src = m.citation || m.source || "";
  const srcLink = /[/.]/.test(src) && !src.startsWith("session") ? link.impact(pid, src) : null;
  return html`<article class=${"mem" + (retired(m) ? " replaced" : "") + (flash ? " flash" : "")} id=${"mem-" + m.id}>
    <${Tag} tone="memory">${MLABEL[m.kind] || m.kind}</${Tag}>
    <div class="mbody">
      ${mode === "edit" ? html`<div class="stack">
        <textarea class="textarea" value=${text} onInput=${e => setText(e.target.value)} aria-label="Memory text"></textarea>
        <div class="row"><select class="select" style="width:auto" value=${kind} onChange=${e => setKind(e.target.value)} aria-label="Kind">${MKINDS.map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
          <button class="btn sm primary" disabled=${busy || !text.trim()} onClick=${save}>${busy ? "Saving…" : "Save changes"}</button><button class="btn sm ghost" onClick=${() => { setMode(null); setText(m.text); setKind(m.kind); }}>Cancel</button></div></div>`
      : html`<p class="mtext">${rich(m.text)}</p>`}
      <div class="mmeta">
        <span>${PROV[m.provenance] || "From"} ${srcLink ? html`<a class="path" href=${srcLink}>${src}</a>` : html`<span>${src || "unknown"}</span>`}</span>
        <span title=${stamp(m.updated_at || m.created_at)}>${m.updated_at && tsOf(m.updated_at) - tsOf(m.created_at) > 5 ? `updated ${ago(m.updated_at)}` : ago(m.created_at)}</span>
        ${m.confidence != null ? html`<span class="conf" title=${`Confidence ${Math.round(m.confidence * 100)}%`}><${Meter} value=${m.confidence} max=${1} tone="memory" label="Confidence"/>${Math.round(m.confidence * 100)}%</span>` : null}
        ${m.forgotten && !m.superseded_by ? html`<span class="err">Forgotten; agents are no longer told about it</span>` : null}
        ${replacedBy ? html`<span class="err">Replaced by: <a href=${link.project(pid, "memory", [], { focus: replacedBy.id, all: "1" })}>${rich(replacedBy.text, 70)}</a></span>` : null}
      </div>
      ${mode === "history" ? html`<div class="mhist">${hist.data ? html`<ol>${hist.data.map(h => html`<li><span class=${"op " + h.op}>${{ ADD: "Added", UPDATE: "Updated", DELETE: "Retired", NONE: "Unchanged", REPLACED: "Replaced" }[h.op] || h.op}</span>
          <span class="sub nowrap">${ago(h.ts)}${h.actor ? `, ${h.actor}` : ""}</span>
          <div class="hdiff">${h.old && h.op !== "ADD" ? html`<del>${h.old}</del>` : null}${h.new ? html`<ins>${h.new}</ins>` : null}</div></li>`)}</ol>` : html`<${Skeleton} rows="2"/>`}</div>` : null}
    </div>
    <div class="mact">
      <button class="icon-btn sm" aria-expanded=${mode === "history"} onClick=${() => setMode(mode === "history" ? null : "history")} title="History" aria-label="Show history"><${Icon} name="history" size="16"/></button>
      ${can.write && !retired(m) ? html`<button class="icon-btn sm" onClick=${() => setMode(mode === "edit" ? null : "edit")} title="Edit" aria-label="Edit memory"><${Icon} name="edit" size="16"/></button>
        <${ConfirmButton} label="" ariaLabel="Forget this memory" icon="trash" confirm="Forget" class="icon-btn sm danger-icon" onConfirm=${forget}/>` : null}
    </div>
  </article>`;
}
