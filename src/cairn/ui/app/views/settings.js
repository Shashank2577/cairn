// Project settings, in five sections: models and tiers, recall and observation, deep tier and memory, context and limits,
// team and repository. Every control names its config key and its default, so a person can tell what to change and why.
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

const GROUPS = [["models", "Models and tiers"], ["recall", "Recall and observation"], ["deep", "Deep tier and memory"], ["context", "Context and limits"], ["team", "Team and repository"]];
// Links and bookmarks from before the regrouping keep landing on the right section.
const OLD_TAB = { recording: "recall", limits: "context", repo: "team" };
const tabOf = q => { const t = OLD_TAB[q] || q; return GROUPS.some(([k]) => k === t) ? t : "models"; };

export function ProjectSettings() {
  const { pid, can, toast, project, reloadOv, ov, route } = useApp();
  const tab = tabOf(route.q.get("tab"));
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
  const defaults = st.data?.defaults || {};
  const show = d => d === "" || d == null ? "empty" : String(d);
  // The key line under every control: which config key it writes and what it is when never set.
  const keyline = k => html`<span class="cfgkey"><code>${k}</code>${k in defaults ? html` · default <code>${show(defaults[k])}</code>` : null}${k in draft ? html` · <b>changed</b>` : null}</span>`;
  const toggle = (k, label, why) => html`<div class="setting"><label class="switch"><input type="checkbox" checked=${!!val(k, defaults[k])} disabled=${off(k)} onChange=${e => set(k, e.target.checked)}/>${label}</label>
    <p class="sub">${why}</p>${locked.has(k) ? html`<div class="field">${hint(k)}</div>` : null}${keyline(k)}</div>`;
  const number = (k, label, why, step = 1) => html`<div class="setting"><label class="field" style="max-width:320px"><span>${label}</span>
    <input class="input num" type="number" min=${(st.data?.rules?.[k] || "").match(/from ([\d,]+)/)?.[1]?.replace(/,/g, "") || 0} step=${step} value=${val(k, defaults[k])} disabled=${off(k)} aria-invalid=${bad(k)}
      onInput=${e => set(k, Math.round(+e.target.value))}/>${hint(k, "")}</label><p class="sub">${why}</p>${keyline(k)}</div>`;
  return html`<div class="page view-in settings">
    <div class="page-h"><div><h1>Project settings</h1><p class="lede">How Cairn works for <b>${project.name}</b>. Changes are stored in the project's <code>.cairn/config.toml</code> and apply from the next sync or agent session.</p></div>
      ${ro ? html`<span class="sub"><${Icon} name="lock" size="14"/> Only team admins can change these.</span>` : null}</div>
    <${Tabs} label="Settings groups" current=${tab} items=${GROUPS.map(([id, label]) => ({ id, label, href: link.project(pid, "settings", [], { tab: id }) }))}/>
    <${Load} res=${st} rows="10" what="settings">${() => html`<div class="setgrid">
      ${tab === "models" ? html`
      <section class="setsec">
        <div class="seth"><h2>Models and tiers</h2><p class="sub">Everything works without a model. Cairn sends each job to the cheapest tier that does it well: fast for classifying, balanced for extraction, deep for answers, frontier for opt-in reviews only. With one, Cairn also writes session observations, extracts timeline facts, reconciles memory by meaning and narrates answers.</p></div>
        <div class="setbody">
          <div class="field"><span>Provider</span>
            <div class="radios" role="radiogroup" aria-label="Model provider">${PROVIDERS.map(([k, l, d]) => html`<label class=${"radio" + (val("models.provider", "auto") === k ? " on" : "")}>
              <input type="radio" name="provider" value=${k} checked=${val("models.provider", "auto") === k} disabled=${off("models.provider")} onChange=${() => set("models.provider", k)}/><span><b>${l}</b><span class="sub">${d}</span></span></label>`)}</div>
            ${locked.has("models.provider") ? hint("models.provider") : null}${keyline("models.provider")}</div>
          ${val("models.provider", "auto") === "openai" ? html`<label class="field" style="margin:12px 0 0;max-width:460px"><span>Endpoint</span>
            <input class="input mono" placeholder="https://api.example.com/v1" value=${val("models.base_url", "") || ""} disabled=${off("models.base_url")} aria-invalid=${bad("models.base_url")} onInput=${e => set("models.base_url", e.target.value || null)}/>
            ${hint("models.base_url", "Any OpenAI-compatible base URL; the key comes from OPENAI_API_KEY.")}${keyline("models.base_url")}</label>` : null}
          <p class="modelstate">${models.data ? models.data.available ? html`<${Tag} tone="memory">available</${Tag}> Models are reachable through ${models.data.provider}.` : html`<${Tag} tone="risk">not available</${Tag}> No model can be reached. Cairn stays deterministic until one is configured.` : null}</p>
          ${models.data ? html`<div class="table-wrap"><table class="table stack"><thead><tr><th>Tier</th><th>Model and config key</th><th>List price</th><th>Used for</th></tr></thead><tbody>
            ${models.data.table.map(r => html`<tr><td class="lead" data-label="Tier"><b>${TIER[r.tier] || r.tier}</b></td>
              <td data-label="Model">${ro ? html`<code>${r.model}</code>` : html`<input class="input mono" style="height:30px;font-size:13px;min-width:230px" value=${val("models." + r.tier, r.model)} disabled=${off("models." + r.tier)} aria-invalid=${bad("models." + r.tier)}
                title=${locked.has("models." + r.tier) ? "Set by the server's operator" : rules["models." + r.tier] ? `Must be ${rules["models." + r.tier]}` : null} onInput=${e => set("models." + r.tier, e.target.value.trim())} aria-label=${`${TIER[r.tier]} model`}/>`}${html`<br/>${keyline("models." + r.tier)}`}</td>
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
      ${tab === "recall" ? html`
      <section class="setsec">
        <div class="seth"><h2>Recording agent sessions</h2><p class="sub">Agents' hooks send each tool call to Cairn, which turns them into observations and a summary per session, kept in <code>.cairn/sessions.db</code> on this machine. This is what lets the next session start knowing what the last one did.</p></div>
        <div class="setbody">
          ${toggle("sessions.capture", "Record agent sessions in this project", "Off stops all recording here; nothing already recorded is deleted.")}
          <p class="modelstate">Now: <b>${get(st.data, "sessions.capture") ? "recording" : "off"}</b>. ${ov?.capture?.agents?.length ? `Hooks installed for ${ov.capture.agents.join(", ")}.`
            : ov?.layers?.sessions?.observations ? `${fmt(ov.layers.sessions.observations)} observations recorded so far.` : "No agent hooks installed yet. Run cairn init --agents claude in the repository."}</p>
        </div>
      </section>
      <section class="setsec">
        <div class="seth"><h2>Writing observations</h2><p class="sub">After each agent turn a background worker condenses the raw tool calls into short observations. With a model they read well; without one Cairn records plain derived entries instead.</p></div>
        <div class="setbody">
          ${toggle("recall.worker_spawn", "Write observations after each agent turn", "Uses your model provider. Turn it off to keep the raw record only and spend nothing.")}
          ${number("recall.observe_batch", "Tool calls per model call", "Higher is cheaper but each observation covers more work. 1 writes one observation per tool call; 10 is the measured economy setting.")}
        </div>
      </section>
      <section class="setsec">
        <div class="seth"><h2>What a new session is told</h2><p class="sub">The briefing an agent gets when it starts.</p></div>
        <div class="setbody">${number("recall.context_observations", "Observations in the briefing", "How many recent observations are injected at the start of each session. More is more context for the agent and more tokens spent on every session start.")}</div>
      </section>
` : null}
      ${tab === "deep" ? html`
      <section class="setsec">
        <div class="seth"><h2>Deep tier</h2><p class="sub">Full syncs read new commits, sessions and documents as episodes and keep the fact graph on the Timeline current. It needs a model; without one it stays off and everything else still works.</p></div>
        <div class="setbody">
          ${ov?.models?.deep_state ? html`<p class="modelstate"><${Tag} tone=${ov.models.deep_state.enabled ? "memory" : "risk"}>${ov.models.deep_state.enabled ? "on" : "off"}</${Tag}> ${ov.models.deep_state.reason}.${ov.models.deep_state.enabled ? "" : html` Fix: ${ov.models.deep_state.fix}.`}</p>` : null}
          <div class="setting"><div class="seg" role="radiogroup" aria-label="Deep tier">${[["auto", "When a model is available"], [true, "Always"], [false, "Never"]].map(([k, l]) => {
            const cur = val("deep.enabled", "auto"); const on = cur === k || (k === true && cur === "on") || (k === false && cur === "off");
            return html`<button role="radio" aria-checked=${on} aria-pressed=${on} disabled=${off("deep.enabled")} onClick=${() => set("deep.enabled", k)}>${l}</button>`; })}</div>
            ${locked.has("deep.enabled") ? html`<div class="field">${hint("deep.enabled")}</div>` : null}${keyline("deep.enabled")}</div>
          ${number("deep.budget_tokens", "Token budget per full sync", "Model work stops for the sync once this many tokens are spent; what is left fills in on later syncs.", 10000)}
          ${number("deep.wall_seconds", "Time limit per full sync (seconds)", "The deep tier stops after this long, so a slow model cannot hold a sync open.", 30)}
        </div>
      </section>
      <section class="setsec">
        <div class="seth"><h2>Memory</h2><p class="sub">The decisions, conventions and gotchas Cairn keeps for every agent.</p></div>
        <div class="setbody">
          ${toggle("memory.seed", "Learn from the repository on sync", "Reads commits and documents for decisions, clarifications, conventions and gotchas worth remembering.")}
          ${toggle("memory.rerank", "Rank recalled memories with a model", "Better ordering of what is recalled, at the cost of a model call per recall. Off uses the deterministic ranking.")}
          ${number("memory.wall_seconds", "Time limit for memory seeding (seconds)", "Seeding stops after this long and carries on at the next sync.", 30)}
        </div>
      </section>
` : null}
      ${tab === "context" ? html`
      <section class="setsec">
        <div class="seth"><h2>Context for agents</h2><p class="sub">What Cairn hands an agent before it edits: dependents, history, decisions and past work, cut to a size.</p></div>
        <div class="setbody">
          <div class="setting"><span class="lbl">Size of a context answer</span>
            <div class="budget"><input type="range" min="600" max="4000" step="100" value=${val("context.budget", 1800)} disabled=${off("context.budget")} onInput=${e => set("context.budget", +e.target.value)} aria-label="Context budget in tokens"/>
              <b class="num">${fmt(val("context.budget", 1800))} tokens</b></div>
            <p class="sub">About ${fmt(val("context.budget", 1800) * 4 / 6)} words of cited evidence per answer. Lower-ranked sections are dropped first when it is full.</p>
            ${locked.has("context.budget") ? html`<div class="field">${hint("context.budget")}</div>` : null}${keyline("context.budget")}</div>
          ${toggle("context.ambient", "Add a short note to every code prompt", "Claude Code only: a few lines of what Cairn knows about the files you mention, under 500 tokens. Off means agents ask Cairn only when they choose to.")}
        </div>
      </section>
      <section class="setsec">
        <div class="seth"><h2>Limits and budgets</h2><p class="sub">Every limit Cairn applies: what it caps, the value in effect now, how much has been used and what happens at the limit. Change them in the sections that own them.</p></div>
        <div class="setbody limits">
          ${(ov?.budgets || []).map(b => html`<div class="limit"><h3>${b.name}</h3><p>${b.meaning}</p>
            <dl class="kv"><dt>In effect</dt><dd>${fmt(b.value)} ${b.unit}</dd>
              ${b.used != null ? html`<dt>Used</dt><dd>${fmt(b.used)} ${b.used_note ? `(${b.used_note})` : ""}</dd>` : null}
              <dt>At the limit</dt><dd>${b.when_exceeded}</dd></dl></div>`)}
          ${ov?.budgets?.length ? null : html`<p class="sub">Limits appear here once the project has been loaded.</p>`}
        </div>
      </section>
` : null}
      ${tab === "team" ? html`
      <section class="setsec">
        <div class="seth"><h2>Repository</h2><p class="sub">Where Cairn reads this project from.</p></div>
        <div class="setbody"><dl class="kv">
          <dt>Source</dt><dd class="path">${project.root || project.git_url || ov?.root || "—"}</dd>
          ${project.branch ? html`<dt>Branch</dt><dd>${project.branch}</dd>` : null}
          <dt>Last sync</dt><dd>${project.last_sync || ov?.last_sync ? stamp(ov?.last_sync || project.last_sync) : "Never"}</dd>
        </dl>${project.team_id && can.admin ? html`<a class="btn sm" style="margin-top:12px" href=${link.team(project.team_id, "projects")}>Manage in team projects</a>` : null}</div>
      </section>
      <section class="setsec">
        <div class="seth"><h2>Team connection</h2><p class="sub">Sharing memory with a team server. These are set by the server's operator or by <code>cairn agents connect</code>, never from this page, because they decide where your data and token go.</p></div>
        <div class="setbody"><dl class="kv">
          <dt>Server</dt><dd><code>team.server</code> — the team server's URL</dd>
          <dt>Project</dt><dd><code>team.project</code> — this project's id on that server</dd>
          <dt>Token</dt><dd><code>team.token_env</code> — the environment variable holding your API token (default <code>CAIRN_TOKEN</code>)</dd>
        </dl></div>
      </section>
` : null}
    </div>`}</${Load}>
    ${dirty ? html`<div class=${"savebar" + (error ? " bad" : "")} role="region" aria-label="Unsaved changes">${error ? html`<p class="err" role="alert">Not saved. ${error.text}</p>` : null}<span>${plural(Object.keys(draft).length, "unsaved change")}</span>
      <button class="btn ghost sm" onClick=${() => setDraft({})}>Discard</button><button class="btn primary sm" disabled=${busy} onClick=${save}>${busy ? "Saving…" : "Save changes"}</button></div>` : null}
  </div>`;
}
function get(o, path) { return String(path).split(".").reduce((a, k) => a == null ? a : a[k], o); }
