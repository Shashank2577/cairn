// The savings scale bar: tokens sent against the source they summarise, drawn like a map's scale.
import { useEffect, useState } from "preact/hooks";
import { html } from "./ui.js";
import { fmt, fmtK } from "../lib/format.js";
import { useCountUp } from "../state.js";

export function ScaleBar({ sent, source, files, sentLabel = "Sent to the agent", sourceLabel = "Files behind the answers", unit = "tokens", compact }) {
  const [on, setOn] = useState(false);
  useEffect(() => { const t = requestAnimationFrame(() => setOn(true)); return () => cancelAnimationFrame(t); }, []);
  const max = Math.max(source, sent, 1);
  const step = [100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 1e5, 2e5, 5e5, 1e6, 2e6, 5e6, 1e7].find(s => max / s <= 5) || 2e7;
  const srcPct = source / max * 100;
  const segPct = step / Math.max(1, source) * 100;
  const ticks = []; for (let v = 0; v <= max + 1; v += step) ticks.push(v);
  const s = useCountUp(sent), so = useCountUp(source);
  return html`<div class=${"scale" + (compact ? " compact" : "")}>
    <div class="srow"><span class="lbl">${sentLabel}</span><span class="val num">${fmt(s)} <small>${unit}</small></span></div>
    <div class="track"><div class="bar-sent" style=${{ width: on ? `${Math.max(0.4, sent / max * 100)}%` : "0%" }}></div></div>
    <div class="srow"><span class="lbl">${sourceLabel}</span><span class="val num">${fmt(so)} <small>${unit}${files != null ? ` in ${fmt(files)} file${files === 1 ? "" : "s"}` : ""}</small></span></div>
    <div class="track"><div class="bar-src" style=${{ width: on ? `${srcPct}%` : "0%", "--seg": `${segPct}%` }}></div></div>
    <div class="ticks num" aria-hidden="true">${ticks.map((v, i) => html`<span key=${i} style=${{ left: `${v / max * 100}%` }}>${v ? fmtK(v) : "0"}</span>`)}</div>
  </div>`;
}

export function Ratio({ sent, source, who = "The agent" }) {
  if (!(source > sent && sent > 0)) return null;
  const r = source / sent;
  return html`<p class="ratio">${who} read <b>${r.toFixed(r >= 10 ? 0 : 1)}× fewer tokens</b> than opening those files would take: about ${fmt(source - sent)} tokens kept out of context.</p>`;
}
