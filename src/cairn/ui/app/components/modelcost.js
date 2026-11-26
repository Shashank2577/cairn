// What Cairn's own model calls cost: tokens measured (provider usage reports), dollars estimated (list price), and the
// before/after of the cheaper session-observation defaults. Every figure says which of the two it is.
import { html, Tag } from "./ui.js";
import { fmt, fmtK } from "../lib/format.js";

export const usd = v => v == null ? "n/a" : v >= 0.1 ? `$${v.toFixed(2)}` : v >= 0.001 ? `$${v.toFixed(3)}` : v >= 0.00001 ? `$${v.toFixed(5)}` : "<$0.00001";
const pct = (a, b) => a > 0 && b != null ? Math.round((1 - b / a) * 100) : null;

export function Measured() { return html`<span class="cm-tag m" title="The provider's own usage report, summed">measured</span>`; }
export function Estimated({ why }) { return html`<span class="cm-tag e" title=${why || "Tokens × a list price (or the price set in [models.prices]); not a bill"}>estimated</span>`; }

// One before→after pair as two bars on a shared scale, so the drop is visible, not just stated.
function Pair({ title, before, after, fmtv, beforeLabel, afterLabel, kind }) {
  const max = Math.max(before.v, after.v, 1e-12), drop = pct(before.v, after.v);
  return html`<div class="cm-pair">
    <div class="cm-pt"><b>${title}</b> ${kind === "m" ? html`<${Measured}/>` : html`<${Estimated}/>`}${drop != null && drop > 0 ? html`<span class="cm-drop num">−${drop}%</span>` : null}</div>
    <div class="cm-bar"><span class="lb">${beforeLabel}</span><i class="b" style=${{ width: `${Math.max(1, before.v / max * 100)}%` }}></i><span class="v num">${fmtv(before.v)}</span></div>
    <div class="cm-bar"><span class="lb">${afterLabel}</span><i class="a" style=${{ width: `${Math.max(1, after.v / max * 100)}%` }}></i><span class="v num">${fmtv(after.v)}</span></div>
  </div>`;
}

export function ModelCost({ r, full }) {
  const c = r.cost, obs = c.observer, study = c.study, t = c.totals;
  if (!t.calls) return html`<p class="sub">No model calls yet. Once an agent session is recorded, the cost of writing its observations appears here, with tokens measured and dollars estimated.</p>`;
  const live = obs.before && obs.after && obs.before.tokens_per_event != null && obs.after.tokens_per_event != null;
  const a = obs.after, b = obs.before;
  const tokPerEvent = v => `${fmt(v)} tokens`;
  const pins = r.pins || [];
  return html`<div class="cm">
    ${pins.length ? html`<p class="cm-pin"><${Tag} tone="risk">older default</${Tag}> ${pins.map(p => `${p.tier} is pinned to ${p.pinned}; the default is now ${p.default}`).join(". ")}. Run <code>cairn models --upgrade</code>, or change it in Settings.</p>` : null}
    <p class="cm-key"><${Measured}/> token counts from the provider. <${Estimated}/> dollars = tokens × list price, per model. Thinking tokens are part of output.</p>
    ${live ? html`<div class="cm-pairs">
        <${Pair} kind="m" title="Tokens per tool event" before=${{ v: b.tokens_per_event }} after=${{ v: a.tokens_per_event }} fmtv=${tokPerEvent}
          beforeLabel=${`Before · cache writes on (${fmt(b.calls)} calls)`} afterLabel=${`After · cache writes off (${fmt(a.calls)} calls)`}/>
        <${Pair} kind="e" title="Dollars per tool event" before=${{ v: b.usd_per_event }} after=${{ v: a.usd_per_event }} fmtv=${usd}
          beforeLabel=${b.models.join(", ")} afterLabel=${a.models.join(", ")}/>
      </div>
      <p class="sub">${a.tokens_per_event >= b.tokens_per_event && a.usd_per_event < b.usd_per_event ? "Token counts did not fall: the same text is still read. The saving is price, since those tokens are no longer written to the prompt cache at 2× the input rate, and the cheaper model costs less per token. " : ""}Compared from this project's own ledger: calls that wrote prompt-cache entries (the old behaviour) against calls that did not, divided by the tool events the session capture recorded in the same periods.</p>`
      : html`<div class="cm-pairs"><${Pair} kind="e" title="Dollars per tool event: the study baseline" before=${{ v: study.before.usd_per_event }} after=${{ v: study.after_haiku_5_5.usd_per_event }} fmtv=${usd}
          beforeLabel=${study.before.label} afterLabel=${study.after_haiku_5_5.label}/>
        <${Pair} kind="m" title="Tokens per tool event: the study baseline" before=${{ v: study.before.tokens_per_event }} after=${{ v: study.after_haiku_4_5.tokens_per_event }} fmtv=${tokPerEvent}
          beforeLabel=${study.before.label} afterLabel=${study.after_haiku_4_5.label}/></div>
        <p class="sub">${obs.before ? "Old and new calls overlap in time, so a per-event split would be a guess." : "This project's ledger has no calls from before the change, so this shows what the study measured by replaying the same events"} (${study.source}). ${a ? `This project so far: ${fmt(a.tokens_per_call)} tokens per observer call${a.tokens_per_event != null ? `, ${fmt(a.tokens_per_event)} per tool event` : ""}.` : ""}</p>`}
    <dl class="cm-stats">
      ${a ? html`<div><dt>Tokens per observation <${Measured}/></dt><dd class="num">${a.tokens_per_observation != null ? fmt(a.tokens_per_observation) : "n/a"}</dd></div>
        <div><dt>Tokens per tool event <${Measured}/></dt><dd class="num">${a.tokens_per_event != null ? fmt(a.tokens_per_event) : "n/a"}</dd></div>
        <div><dt>Est. dollars per observation <${Estimated}/></dt><dd class="num">${usd(a.usd_per_observation)}</dd></div>` : null}
      <div><dt>Cache write vs read <${Measured}/></dt><dd class="num">${fmtK(t.cache_write)} <small>written</small> · ${fmtK(t.cache_read)} <small>read</small></dd>
        <dd class="sub">${t.cache_write ? (t.cache_write_1h ? `${fmtK(t.cache_write_1h)} of the writes were 1-hour entries (2× input price). ` : "The write TTL was not recorded before this build; the Claude Code CLI measured 1-hour entries (2× input price), which the dollar estimate assumes. ") : ""}${t.cache_write > 0 && t.cache_read < t.cache_write * 0.05 ? "Almost none was ever read, which is why caching is now off for these calls." : ""}</dd></div>
      <div><dt>Thinking tokens <${Measured}/></dt><dd class="num">${fmtK(t.thinking)}</dd><dd class="sub">Inside output tokens; billed as output. Off for bulk tasks, though Haiku 5.5 keeps a short step.</dd></div>
      <div><dt>All model calls <${Measured}/> · <${Estimated}/></dt><dd class="num">${fmt(t.calls)} calls · ${usd(t.usd)}</dd><dd class="sub">${fmtK(t.tokens)} tokens in this ledger.</dd></div>
    </dl>
    ${full ? html`<div class="table-wrap"><table class="table cm-table"><thead><tr><th>Tier</th><th>Model</th><th class="n">Calls</th><th class="n">Input</th><th class="n">Output</th><th class="n" title="Inside output">Thinking</th>
        <th class="n">Cache read</th><th class="n">Cache write</th><th class="n">Price in/out per M</th><th class="n">Est. dollars</th></tr></thead><tbody>
      ${c.tiers.map(x => html`<tr><td>${x.tier}</td><td><code>${x.model}</code></td><td class="n num">${fmt(x.calls)}</td><td class="n num">${fmt(x.input)}</td><td class="n num">${fmt(x.output)}</td>
        <td class="n num">${fmt(x.thinking)}</td><td class="n num">${fmt(x.cache_read)}</td><td class="n num">${fmt(x.cache_write)}${x.cache_write_1h ? html` <small>(${fmtK(x.cache_write_1h)} 1h)</small>` : null}</td>
        <td class="n num">${x.price ? `$${x.price[0]} / $${x.price[1]}` : "no price"}</td><td class="n num">${usd(x.usd)}</td></tr>`)}</tbody></table></div>
      <p class="sub">Dollar figures are estimates from list prices. Add <code>[models.prices]</code> in <code>.cairn/config.toml</code> (for example <code>"my-model" = [1.0, 5.0]</code>, dollars per million input and output tokens) to price another model. Subscription users pay in plan usage rather than dollars; the token counts and the before/after ratios apply either way.</p>` : null}
  </div>`;
}
