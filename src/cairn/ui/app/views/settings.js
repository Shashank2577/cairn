// Project settings, in four tabs: models and cost, recording and the deep tier, limits and budgets, the repository.
import { useEffect, useMemo, useState } from "preact/hooks";
import { html, Icon, Load, Tag, Skeleton, Empty, Tabs } from "../components/ui.js";
import { useApp, useFetch } from "../state.js";
import { api } from "../api.js";
import { link } from "../router.js";
import { ago, fmt, plural, stamp } from "../lib/format.js";
import { usd, rateLabel } from "../lib/cost.js";

const PROVIDERS = [["auto", "Automatic", "An API key if one is set, otherwise your signed-in Claude Code"], ["anthropic", "Anthropic API", "Uses ANTHROPIC_API_KEY"],
  ["openai", "OpenAI-compatible endpoint", "Any endpoint that speaks the OpenAI API, including local models"], ["claude-code", "Claude Code sign-in", "Your subscription, no key needed"]];
const TIER = { fast: "Fast", balanced: "Balanced", deep: "Deep", frontier: "Frontier" };
// What the server's messages call each setting, in the page's words.
const LABEL = { "models.provider": "Provider", "models.base_url": "Endpoint", "models.fast": "Fast model", "models.balanced": "Balanced model", "models.deep": "Deep model",
  "models.frontier": "Frontier model", "sessions.capture": "Session recording", "deep.enabled": "Deep tier", "deep.budget_tokens": "Token budget per full sync", "context.budget": "Context budget" };
const plainMsg = m => String(m || "").replace(/\b(models|sessions|deep|context)\.[a-z_]+\b/g, k => LABEL[k] || k).replace(/([^.!?])$/, "$1.");
const TASK = { brief: "session briefings", classify: "classifying", label: "labelling communities", summarize: "session summaries", episode: "reading episodes", extract: "extracting facts",
  link: "linking entities", memory: "reconciling memory", ask: "answering questions", drift: "explaining drift", impact: "narrating impact", why: "narrating why", review: "whole-system review", observe: "writing observations",
  recall_observe: "writing observations", recall_summarize: "session summaries", recall_compress: "compressing sessions", recall_corpus: "building recall collections",
  "graph-label": "labelling communities", "graph-dedup": "merging duplicate nodes", "graph-extract": "reading documents into the map", "graph-triage": "triaging pull requests",
  "temporal.extract": "extracting facts", "temporal.dedupe": "merging duplicate entities", "temporal.resolve": "resolving contradictions", "temporal.summarize": "summarising entities",
  "temporal.attributes": "entity details", "temporal.community": "grouping facts", "temporal.rerank": "ranking facts", "temporal.saga": "linking episodes", "temporal.timestamps": "dating facts",
  memory_instructions: "memory rules", memory_rerank: "ranking memories", memory_chat: "memory chat", memory_procedural: "how-to memories" };

const GROUPS = [["models", "Models and cost"], ["recording", "Recording and deep tier"], ["limits", "Limits and budgets"], ["repo", "Repository"]];

export function ProjectSettings() {
  const { pid, can, toast, project, reloadOv, ov, route } = useApp();
  const tab = GROUPS.some(([k]) => k === route.q.get("tab")) ? route.q.get("tab") : "models";
  const st = useFetch(() => api.p(pid).settings(), [pid]);
  const models = useFetch(() => api.p(pid).models(), [pid]);
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => { setDraft({}); setError(null); }, [st.data]);
  const val = (k, d) => k in draft ? draft[k] : get(st.data, k) ?? d;
  const set = (k, v) => { setError(null); setDraft(x => { const n = { ...x }; if (get(st.data, k) === v) delete n[k]; else n[k] = v; return n; }); };
  const dirty = Object.keys(draft).length > 0;
  const save = async () => {
    setBusy(true); setError(null);
    try { const r = await api.p(pid).saveSettings(draft); st.setData(r); setDraft({}); reloadOv(); models.reload(); toast({ title: "Settings saved", tone: "memory", body: "They apply from the next sync or agent session." }); }
    catch (e) {
      // The server names the setting it refused; point at that field and keep every change for another try.
      setError({ key: Object.keys(draft).find(k => e.message.includes(k)), text: plainMsg(e.message) });
    } finally { setBusy(false); }
  };
  const ro = !can.admin;
  const locked = new Set(st.data?.locked || []), rules = st.data?.rules || {};
  const off = k => ro || locked.has(k);
  const bad = k => error?.key === k;
  const hint = (k, text) => locked.has(k) ? html`<span class="hint locked"><${Icon} name="lock" size="12"/> Set by the server's operator</span>`
    : html`<span class="hint">${text}${text && rules[k] ? " " : ""}${rules[k] ? `Must be ${rules[k]}.` : ""}</span>`;
  return html`<div class="page view-in settings">
    <div class="page-h"><div><h1>Project settings</h1><p class="lede">How Cairn works for <b>${project.name}</b>. Changes are stored in the project's <code>.cairn/config.toml</code> and apply from the next sync or agent session.</p></div>
      ${ro ? html`<span class="sub"><${Icon} name="lock" size="14"/> Only team admins can change these.</span>` : null}</div>
    <${Tabs} label="Settings groups" current=${tab} items=${GROUPS.map(([id, label]) => ({ id, label, href: link.project(pid, "settings", [], { tab: id }) }))}/>
    <${Load} res=${st} rows="10" what="settings">${() => html`<div class="setgrid">
      ${tab === "models" ? html`
      <section class="setsec">
        <div class="seth"><h2>Models</h2><p class="sub">Everything works without a model. With one, Cairn also writes session observations, extracts timeline facts, reconciles memory by meaning and narrates answers.</p></div>
        <div class="setbody">
          <div class="field"><span>Provider</span>
            <div class="radios" role="radiogroup" aria-label="Model provider">${PROVIDERS.map(([k, l, d]) => html`<label class=${"radio" + (val("models.provider", "auto") === k ? " on" : "")}>
              <input type="radio" name="provider" value=${k} checked=${val("models.provider", "auto") === k} disabled=${off("models.provider")} onChange=${() => set("models.provider", k)}/><span><b>${l}</b><span class="sub">${d}</span></span></label>`)}</div>
            ${locked.has("models.provider") ? hint("models.provider") : null}</div>
          ${val("models.provider", "auto") === "openai" ? html`<label class="field" style="margin:12px 0 0;max-width:460px"><span>Endpoint</span>
            <input class="input mono" placeholder="https://api.example.com/v1" value=${val("models.base_url", "") || ""} disabled=${off("models.base_url")} aria-invalid=${bad("models.base_url")} onInput=${e => set("models.base_url", e.target.value || null)}/>
            ${hint("models.base_url", "Any OpenAI-compatible base URL; the key comes from OPENAI_API_KEY.")}</label>` : null}
          <p class="modelstate">${models.data ? models.data.available ? html`<${Tag} tone="memory">available</${Tag}> Models are reachable through ${models.data.provider}.` : html`<${Tag} tone="risk">not available</${Tag}> No model can be reached. Cairn stays deterministic until one is configured.` : null}</p>
          ${models.data ? html`<div class="table-wrap"><table class="table stack"><thead><tr><th>Tier</th><th>Model</th><th>List price</th><th>Used for</th></tr></thead><tbody>
            ${models.data.table.map(r => html`<tr><td class="lead" data-label="Tier"><b>${TIER[r.tier] || r.tier}</b></td>
              <td data-label="Model">${ro ? html`<code>${r.model}</code>` : html`<input class="input mono" style="height:30px;font-size:13px;min-width:230px" value=${val("models." + r.tier, r.model)} disabled=${off("models." + r.tier)} aria-invalid=${bad("models." + r.tier)}
                title=${locked.has("models." + r.tier) ? "Set by the server's operator" : rules["models." + r.tier] ? `Must be ${rules["models." + r.tier]}` : null} onInput=${e => set("models." + r.tier, e.target.value.trim())} aria-label=${`${TIER[r.tier]} model`}/>`}</td>
              <td data-label="List price" class="small nowrap" title="USD per million tokens (list price)">${r.price ? rateLabel(r.price, true) : html`<span class="sub" title="Cairn only shows prices it has confirmed">no confirmed price</span>`}</td>
              <td data-label="Used for" class="small">${[...new Set(r.tasks.map(t => TASK[t] || t))].join(", ")}</td></tr>`)}</tbody></table></div>` : html`<${Skeleton} rows="4"/>`}
        </div>
      </section>
      <section class="setsec">
        <div class="seth"><h2>Model spend</h2><p class="sub">Recent model calls made for this project, with an estimate of what they cost at list prices (${models.data?.priced_as_of ? `as of ${models.data.priced_as_of}` : "price table"}). Subscriptions such as Claude Code bill by plan usage, not per call, so treat dollars as a size comparison, not an invoice. Cache reads and writes are priced at their own rates.</p></div>
        <div class="setbody">${models.data?.ledger?.length ? html`<p class="spendtotal"><b class="num">${usd(models.data.cost_usd) ?? "No priced calls"}</b> across ${plural(models.data.totals.reduce((a, t) => a + t.calls, 0), "call")} since the ledger began${models.data.unpriced_models?.length ? html`; no confirmed price for ${models.data.unpriced_models.map(m => html`<code>${m}</code>`).reduce((a, x) => a ? [a, ", ", x] : x, null)}, so those calls are in tokens only` : ""}.</p><div class="table-wrap"><table class="table stack"><thead><tr><th>When</th><th>Task</th><th>Model</th><th class="n" title="Input tokens, including those read from or written to the prompt cache">In</th><th class="n">Out</th><th class="n" title="List-price estimate; blank when the model has no confirmed price">Cost</th></tr></thead><tbody>
          ${[...models.data.ledger].sort((a, b) => b.ts - a.ts).slice(0, 12).map(r => html`<tr><td data-label="When" class="nowrap">${ago(r.ts)}</td><td data-label="Task">${TASK[r.task] || r.task}${r.ok === false ? html` <${Tag} tone="risk">failed</${Tag}>` : null}</td>
            <td data-label="Model"><code>${r.model}</code></td><td data-label="In" class="n" title=${r.input_total != null ? `${fmt(r.input_tokens)} new, ${fmt((r.cache_read || 0) + (r.cache_write || 0))} from the prompt cache` : null}>${fmt(r.input_total ?? r.input_tokens)}</td><td data-label="Out" class="n">${fmt(r.output_tokens)}</td><td data-label="Cost" class="n">${usd(r.cost_usd) ?? "–"}</td></tr>`)}</tbody></table></div>`
          : html`<p class="sub">No model calls yet.</p>`}</div>
      </section>
` : null}
      ${tab === "recording" ? html`
      <section class="setsec">
        <div class="seth"><h2>Session recording</h2><p class="sub">Agents' hooks send each tool call to Cairn, which writes observations and a summary per session into <code>.cairn/sessions.db</code>.</p></div>
        <div class="setbody">
          <label class="switch"><input type="checkbox" checked=${val("sessions.capture", false)} disabled=${off("sessions.capture")} onChange=${e => set("sessions.capture", e.target.checked)}/>Record agent sessions in this project</label>
          ${locked.has("sessions.capture") ? html`<div class="field">${hint("sessions.capture")}</div>` : null}
          <p class="modelstate">Now: <b>${get(st.data, "sessions.capture") ? "recording" : "off"}</b>.</p>
          <p class="sub" style="margin:8px 0 0">${ov?.capture?.agents?.length ? `Hooks installed for ${ov.capture.agents.join(", ")}.`
            : ov?.layers?.sessions?.observations ? `${fmt(ov.layers.sessions.observations)} observations recorded so far.` : "No agent hooks installed yet. Run cairn init --agents claude in the repository."}</p>
        </div>
      </section>
      <section class="setsec">
        <div class="seth"><h2>Deep tier</h2><p class="sub">Full syncs read new commits, sessions and documents as episodes and keep the fact graph on the Timeline current.</p></div>
        <div class="setbody">
          ${ov?.models?.deep_state ? html`<p class="modelstate"><${Tag} tone=${ov.models.deep_state.enabled ? "memory" : "risk"}>${ov.models.deep_state.enabled ? "on" : "off"}</${Tag}> ${ov.models.deep_state.reason}.${ov.models.deep_state.enabled ? "" : html` Fix: ${ov.models.deep_state.fix}.`}</p>` : null}
          <div class="seg" role="radiogroup" aria-label="Deep tier">${[["auto", "When a model is available"], [true, "Always"], [false, "Never"]].map(([k, l]) => {
            const cur = val("deep.enabled", "auto"); const on = cur === k || (k === true && cur === "on") || (k === false && cur === "off");
            return html`<button role="radio" aria-checked=${on} aria-pressed=${on} disabled=${off("deep.enabled")} onClick=${() => set("deep.enabled", k)}>${l}</button>`; })}</div>
          ${locked.has("deep.enabled") ? html`<div class="field">${hint("deep.enabled")}</div>` : null}
        </div>
      </section>
` : null}
      ${tab === "limits" ? html`
      <section class="setsec">
        <div class="seth"><h2>Limits and budgets</h2><p class="sub">Every limit Cairn applies: what it caps, the value in effect now, how much has been used and what happens at the limit. The two you can change have their control inside the card.</p></div>
        <div class="setbody limits">
          ${(ov?.budgets || []).map(b => html`<div class="limit"><h3>${b.name}</h3><p>${b.meaning}</p>
            <dl class="kv"><dt>In effect</dt><dd>${fmt(b.value)} ${b.unit}</dd>
              ${b.used != null ? html`<dt>Used</dt><dd>${fmt(b.used)} ${b.used_note ? `(${b.used_note})` : ""}</dd>` : null}
              <dt>At the limit</dt><dd>${b.when_exceeded}</dd></dl>
            ${b.name === "Context size" ? html`<div class="budget"><input type="range" min="600" max="4000" step="100" value=${val("context.budget", 1800)} disabled=${off("context.budget")} onInput=${e => set("context.budget", +e.target.value)} aria-label="Context budget in tokens"/>
            <b class="num">${fmt(val("context.budget", 1800))} tokens</b></div>
          <p class="sub" style="margin:6px 0 0">About ${fmt(val("context.budget", 1800) * 4 / 6)} words of cited evidence per answer.</p>
          ${locked.has("context.budget") ? html`<div class="field">${hint("context.budget")}</div>` : null}` : b.name === "Deep sync allowance" ? html`<label class="field" style="margin-top:10px;max-width:320px"><span>Token budget per full sync</span>
            <input class="input num" type="number" min="1000" step="10000" value=${val("deep.budget_tokens", 150000)} disabled=${off("deep.budget_tokens")} aria-invalid=${bad("deep.budget_tokens")} onInput=${e => set("deep.budget_tokens", Math.round(+e.target.value))}/>
            ${hint("deep.budget_tokens", "Model work stops for the sync once this is spent.")}</label>` : null}</div>`)}
          ${ov?.budgets?.length ? null : html`<div class="limit"><h3>Context size</h3><p>The most tokens any single impact, why or context answer may send to an agent. Lower-ranked sections are dropped first.</p><div class="budget"><input type="range" min="600" max="4000" step="100" value=${val("context.budget", 1800)} disabled=${off("context.budget")} onInput=${e => set("context.budget", +e.target.value)} aria-label="Context budget in tokens"/>
            <b class="num">${fmt(val("context.budget", 1800))} tokens</b></div>
          <p class="sub" style="margin:6px 0 0">About ${fmt(val("context.budget", 1800) * 4 / 6)} words of cited evidence per answer.</p>
          ${locked.has("context.budget") ? html`<div class="field">${hint("context.budget")}</div>` : null}</div>
            <div class="limit"><h3>Deep sync allowance</h3><p>The most model tokens one deep sync may spend.</p><label class="field" style="margin-top:10px;max-width:320px"><span>Token budget per full sync</span>
            <input class="input num" type="number" min="1000" step="10000" value=${val("deep.budget_tokens", 150000)} disabled=${off("deep.budget_tokens")} aria-invalid=${bad("deep.budget_tokens")} onInput=${e => set("deep.budget_tokens", Math.round(+e.target.value))}/>
            ${hint("deep.budget_tokens", "Model work stops for the sync once this is spent.")}</label></div>`}
        </div>
      </section>
` : null}
      ${tab === "repo" ? html`
      <section class="setsec">
        <div class="seth"><h2>Repository</h2><p class="sub">Where Cairn reads this project from.</p></div>
        <div class="setbody"><dl class="kv">
          <dt>Source</dt><dd class="path">${project.root || project.git_url || ov?.root || "—"}</dd>
          ${project.branch ? html`<dt>Branch</dt><dd>${project.branch}</dd>` : null}
          <dt>Last sync</dt><dd>${project.last_sync || ov?.last_sync ? stamp(ov?.last_sync || project.last_sync) : "Never"}</dd>
        </dl>${project.team_id && can.admin ? html`<a class="btn sm" style="margin-top:12px" href=${link.team(project.team_id, "projects")}>Manage in team projects</a>` : null}</div>
      </section>
` : null}
    </div>`}</${Load}>
    ${dirty ? html`<div class=${"savebar" + (error ? " bad" : "")} role="region" aria-label="Unsaved changes">${error ? html`<p class="err" role="alert">Not saved. ${error.text}</p>` : null}<span>${plural(Object.keys(draft).length, "unsaved change")}</span>
      <button class="btn ghost sm" onClick=${() => setDraft({})}>Discard</button><button class="btn primary sm" disabled=${busy} onClick=${save}>${busy ? "Saving…" : "Save changes"}</button></div>` : null}
  </div>`;
}
function get(o, path) { return String(path).split(".").reduce((a, k) => a == null ? a : a[k], o); }
