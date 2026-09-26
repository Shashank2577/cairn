// The spec workflow as a surveyed route: constitution → specify → clarify → plan → tasks →
// analyze → implement. Finished stages are solid; the line draws in up to the next stage.
import { useEffect, useState } from "preact/hooks";
import { html } from "./ui.js";

export const STAGES = [
  ["constitution", "Constitution", "Principles every feature follows"],
  ["specify", "Specify", "Stories, requirements, success criteria"],
  ["clarify", "Clarify", "Questions answered back into the spec"],
  ["plan", "Plan", "Design, research, data model, contracts"],
  ["tasks", "Tasks", "Ordered work, grouped by story"],
  ["analyze", "Analyze", "Gaps and conflicts across the documents"],
  ["implement", "Implement", "Tasks ticked as the code lands"],
];

export function stageState(stages, fid) {
  const done = new Set((stages || []).filter(s => (s.done_for || []).includes(fid)).map(s => s.id));
  const next = STAGES.findIndex(([id]) => !done.has(id));
  return { done, next: next < 0 ? STAGES.length : next };
}

export function Stepper({ stages, fid, compact, commands }) {
  const { done, next } = stageState(stages, fid);
  const [drawn, setDrawn] = useState(0);
  useEffect(() => { const t = requestAnimationFrame(() => setDrawn(next)); return () => cancelAnimationFrame(t); }, [next]);
  const frac = Math.min(1, drawn / (STAGES.length - 1));
  const cmd = id => (commands || []).find(c => c.name.endsWith("." + id) || c.name.endsWith("-" + id))?.name;
  return html`<ol class=${"stepper" + (compact ? " compact" : "")} aria-label="Workflow stages" style=${{ "--fillf": frac }}>
    ${STAGES.map(([id, label, what], i) => {
      const st = done.has(id) ? "done" : i === next ? "next" : "todo";
      return html`<li class=${st} key=${id} style=${{ "--i": i }} aria-current=${i === next ? "step" : undefined}
        title=${`${label}: ${st === "done" ? "done" : st === "next" ? "next step" : "not started"}${cmd(id) ? ` (${cmd(id)})` : ""}`}>
        <span class="dot" aria-hidden="true"></span>
        <span class="lb">${label}</span>
        ${compact ? null : html`<span class="wh">${what}</span>`}
        ${!compact && i === next && cmd(id) ? html`<code class="nx">${cmd(id)}</code>` : null}
        <span class="sr-only">${st === "done" ? "done" : st === "next" ? "next" : "not started"}</span>
      </li>`;
    })}
  </ol>`;
}
