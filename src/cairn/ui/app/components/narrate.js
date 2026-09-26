// Streamed explanations: a model writes a plain-language answer on top of Cairn's cited evidence
// (POST /narrate). The text appears as it arrives, citations become links, and the reader can stop it.
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, Skeleton } from "./ui.js";
import { Markdown } from "../lib/md.js";
import { narrate } from "../api.js";
import { useApp } from "../state.js";
import { link, citeHref } from "../router.js";
import { base, clip } from "../lib/format.js";

/** Say what went wrong in plain words; the raw message stays available on hover. */
export function plainError(raw) {
  const m = String(raw || "");
  const reset = m.match(/resets?\s+(?:at\s+)?([0-9]{1,2}(?::[0-9]{2})?\s*[ap]m)/i);
  if (/session limit|usage limit|quota|rate.?limit|429/i.test(m)) return `Your Claude plan's session limit is reached.${reset ? ` It resets at ${reset[1]}.` : " Try again when it resets."}`;
  if (/api.?key|unauthori[sz]ed|authentication|401|403|permission/i.test(m)) return "The model provider refused the request. Check the key or sign-in in Settings.";
  if (/timed? ?out|timeout/i.test(m)) return "The model took too long to answer. Try again.";
  if (/connect|network|unreachable|ECONN/i.test(m)) return "Cairn couldn't reach the model provider.";
  return "The model call failed.";
}

export function useNarration(pid) {
  const [st, setSt] = useState({ status: "idle" });
  const ctl = useRef(null), text = useRef(""), raf = useRef(0);
  const flush = () => { cancelAnimationFrame(raf.current); raf.current = requestAnimationFrame(() => setSt(s => ({ ...s, text: text.current }))); };
  const stop = useCallback(() => { ctl.current?.abort(); }, []);
  const reset = useCallback(() => { ctl.current?.abort(); ctl.current = null; text.current = ""; setSt({ status: "idle" }); }, []);
  const start = useCallback(async (kind, subject, budget) => {
    ctl.current?.abort();
    const c = new AbortController(); ctl.current = c;
    text.current = "";
    setSt({ status: "waiting", kind, subject, text: "" });
    let ended = false;
    try {
      await narrate(pid, { kind, text: subject, ...(budget ? { budget } : {}) }, {
        signal: c.signal,
        onEvent: ev => {
          if (c.signal.aborted) return;
          if (ev.type === "start") setSt(s => ({ ...s, status: "streaming", model: ev.model, tier: ev.tier, pack: ev.pack || null, labels: ev.labels || null }));
          else if (ev.type === "text") { text.current += ev.text || ""; setSt(s => s.status === "waiting" ? { ...s, status: "streaming" } : s); flush(); }
          else if (ev.type === "done") { ended = true; cancelAnimationFrame(raf.current); setSt(s => ({ ...s, status: "done", text: text.current })); }
          else if (ev.type === "error") { ended = true; cancelAnimationFrame(raf.current); setSt(s => ({ ...s, status: "error", text: text.current, error: { plain: plainError(ev.message), raw: ev.message } })); }
        },
      });
      if (!ended && !c.signal.aborted) setSt(s => ({ ...s, status: "done", text: text.current, cut: true }));
    } catch (e) {
      cancelAnimationFrame(raf.current);
      if (e.name === "AbortError" || c.signal.aborted) setSt(s => ({ ...s, status: "stopped", text: text.current }));
      else if (e.status === 409) setSt({ status: "unavailable", detail: e.message });
      else setSt(s => ({ ...s, status: "error", text: text.current, error: { plain: e.status === 422 ? "Cairn couldn't explain that. Try a file path, a symbol or a fuller question." : e.status === 0 ? "Can't reach the Cairn server." : plainError(e.message), raw: e.message } }));
    } finally { if (ctl.current === c) ctl.current = null; }
  }, [pid]);
  useEffect(() => () => { ctl.current?.abort(); cancelAnimationFrame(raf.current); }, []);
  const busy = st.status === "waiting" || st.status === "streaming";
  return { ...st, busy, start, stop, reset };
}

const NEEDS_MODEL = "Explaining needs a model. Set one up in Settings.";

/** Whether a model can be used; false only when the server has said so. */
export function useModelReady(n) {
  const { ov } = useApp();
  return !(n?.status === "unavailable" || ov?.models?.available === false);
}

/** The action that starts an explanation. Stays focusable when unavailable, so its reason can be read. */
export function ExplainButton({ n, kind, subject, label = "Explain", budget, cls = "btn sm" }) {
  const { pid } = useApp();
  const ready = useModelReady(n);
  const why = n.status === "unavailable" && n.detail ? n.detail : NEEDS_MODEL;
  if (!ready) return html`<span class="explain-off">
    <button type="button" class=${cls} aria-disabled="true" title=${why} aria-describedby=${"why-" + kind}><${Icon} name="bolt" size="14"/>${label}</button>
    <span class="sub" id=${"why-" + kind}>Needs a model. <a href=${link.project(pid, "settings")}>Set one up</a></span></span>`;
  return html`<button type="button" class=${cls} disabled=${n.busy} onClick=${() => n.start(kind, subject, budget)}
    title="A model explains this in plain language, citing the evidence Cairn found"><${Icon} name="bolt" size="14"/>${n.busy ? "Explaining…" : n.status === "idle" ? label : `${label} again`}</button>`;
}

const CITE_KINDS = "file|symbol|memory|req|requirement|task|story|spec|fact|entity|obs|observation|session|commit|drift|rationale|wiki";
const CITE = new RegExp(`\\[(${CITE_KINDS}):([^\\]\\s]+)\\]`, "g");
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function citeLabel(kind, val, labels) {
  // The server names every citation in the evidence; the guesses below cover anything else.
  const named = labels?.[`${kind}:${val}`];
  if (named && kind === "file" && named === val) return base(val) || val;  // the full path is in the chip's title
  // Memory labels can start with their kind in brackets ("[decision] …", or "decision] …" once trimmed): drop it.
  if (named) return clip(String(named).replace(/^\[?[a-z_-]+\]\s*/i, "").replace(/\s+/g, " "), 42);
  if (kind === "file") return base(val) || val;
  if (/^(req|requirement|task|story)$/.test(kind)) return val.split("/").pop();
  if (kind === "symbol") return val.split("_").filter(Boolean).slice(-2).join(".") || "code";
  if (kind === "commit") return val.slice(0, 7);
  if (kind === "obs" || kind === "observation" || kind === "session") return "session";
  return kind;
}
const TONE = { file: "code", symbol: "code", rationale: "code", wiki: "code", req: "spec", requirement: "spec", task: "spec", story: "spec", spec: "spec", drift: "risk",
  memory: "memory", fact: "ink", entity: "ink", commit: "ink", obs: "agent", observation: "agent", session: "agent" };
/** Turn [kind:value] citations into small links, leaving code spans alone. */
export function linkCitations(md, pid, labels) {
  return String(md || "").split(/(`[^`]*`)/g).map(part => part.startsWith("`") ? part : part.replace(CITE, (m, kind, val) => {
    const href = citeHref(pid, `${kind}:${val}`);
    const named = labels?.[`${kind}:${val}`];
    return href ? `<a class="cite ${TONE[kind] || ""}" href="${esc(href)}" title="${esc(named ? `${named} (${m.slice(1, -1)})` : m.slice(1, -1))}">${esc(citeLabel(kind, val, labels))}</a>` : m;
  })).join("");
}

/** The streamed answer. Opens at a fixed minimum height so nothing jumps; long answers scroll inside it. */
export function NarrationPanel({ n, title, onClose, packLabel = "Evidence the answer used" }) {
  const { pid } = useApp();
  const body = useRef(null), stick = useRef(true);
  // While streaming, hold back a citation whose closing bracket hasn't arrived yet.
  const src = useMemo(() => {
    const t = n.status === "streaming" ? String(n.text || "").replace(/\[[a-z]*(?::[^\]\s]*)?$/, "") : n.text;
    return linkCitations(t, pid, n.labels) + (n.status === "streaming" ? '<span class="caret" aria-hidden="true"></span>' : "");
  }, [n.text, n.status, n.labels, pid]);
  useEffect(() => { const el = body.current; if (el && stick.current && n.busy) el.scrollTop = el.scrollHeight; }, [src]);
  if (n.status === "idle" || n.status === "unavailable") return null;
  const who = n.model ? `${n.model}${n.tier ? ` (${n.tier} tier)` : ""}` : "";
  return html`<section class=${"narr-panel" + (n.busy ? " live" : "")} aria-label=${title || "Explanation"} aria-busy=${n.busy}>
    <header class="narr-h">
      <span class="narr-t"><${Icon} name="bolt" size="15"/>${title || "Explanation"}</span>
      <span class="sub narr-who" aria-live="polite">${n.status === "waiting" ? "Gathering the evidence…" : n.busy ? `Writing${who ? ` with ${who}` : ""}…` : n.status === "stopped" ? "Stopped." : n.status === "error" ? "" : who ? `Written by ${who}` : ""}</span>
      <span class="spacer"></span>
      ${n.busy ? html`<button type="button" class="btn xs" onClick=${n.stop}><${Icon} name="pause" size="13"/>Stop</button>`
        : html`<button type="button" class="icon-btn sm" onClick=${onClose || n.reset} aria-label="Close the explanation"><${Icon} name="x" size="15"/></button>`}
    </header>
    <div class="narr-body" ref=${body} onScroll=${e => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24; }}>
      ${!n.text && n.status === "waiting" ? html`<${Skeleton} rows="4"/>` : n.text ? html`<${Markdown} src=${src} class="prose narr-text"/>` : null}
      ${n.status === "error" ? html`<p class="narr-err" title=${n.error?.raw || ""}><${Icon} name="info" size="15"/><span>${n.error?.plain}${n.error?.raw ? html`<span class="sr-only"> (${n.error.raw})</span>` : null}</span></p>` : null}
      ${n.status === "stopped" && !n.text ? html`<p class="sub">Stopped before any text arrived.</p>` : null}
      ${n.cut ? html`<p class="sub">The answer ended early.</p>` : null}
    </div>
    ${n.pack ? html`<details class="narr-pack"><summary>${packLabel}</summary><${Markdown} src=${linkCitations(n.pack, pid, n.labels)} class="prose narr-packmd"/></details>` : null}
  </section>`;
}
