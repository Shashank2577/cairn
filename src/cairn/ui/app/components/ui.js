// Shared UI primitives.
import { h, Fragment } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { contours } from "../lib/contour.js";
import { copyText, initials, avatarColor, ROLE_LABEL } from "../lib/format.js";
import { useApp } from "../state.js";

export const html = htm.bind(h);

const P = {
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.5 15.5 20 20",
  chev: "M6 9l6 6 6-6",
  chevr: "M9 6l6 6-6 6",
  sync: "M4 12a8 8 0 0 1 13.7-5.6L20 8.5M20 4v4.5h-4.5M20 12a8 8 0 0 1-13.7 5.6L4 15.5M4 20v-4.5h4.5",
  theme: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 3v18",
  menu: "M4 7h16M4 12h16M4 17h16",
  x: "M6 6l12 12M18 6 6 18",
  copy: "M9 9h10v11H9zM5 15V4h10",
  check: "M5 12.5 10 17l9-10",
  plus: "M12 5v14M5 12h14",
  trash: "M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13",
  edit: "M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4",
  history: "M4 12a8 8 0 1 0 2.3-5.6L4 8.7M4 4v4.7h4.7M12 8v4.5l3 2",
  ext: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  filter: "M4 5h16l-6 8v6l-4-2v-4z",
  lock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20a7.5 7.5 0 0 1 15 0",
  out: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 13a7.6 7.6 0 0 0 0-2l2-1.5-2-3.4-2.3 1a7.5 7.5 0 0 0-1.8-1L15 3.5h-4l-.4 2.6a7.5 7.5 0 0 0-1.7 1l-2.4-1-2 3.4L6.6 11a7.6 7.6 0 0 0 0 2l-2 1.5 2 3.4 2.4-1a7.5 7.5 0 0 0 1.7 1l.4 2.6h4l.3-2.6a7.5 7.5 0 0 0 1.8-1l2.3 1 2-3.4z",
  key: "M14.5 9.5a4 4 0 1 1-1.2-2.8M13.3 6.7 20 13.5V17h-3v-2h-2v-2h-2",
  users: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 6.5M18.5 14a6.5 6.5 0 0 1 3 6",
  folder: "M3 6.5h6l2 2h10V19H3z",
  list: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
  target: "M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
  play: "M8 5v14l11-7z",
  pause: "M8 5v14M16 5v14",
  expand: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5M9 12h6",
  route: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM6 15V9a4 4 0 0 1 4-4h6M18 9v6a4 4 0 0 1-4 4H8",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7.5v.5",
  bolt: "M13 3 5 13h6l-1 8 8-10h-6z",
  doc: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6",
  back: "M15 6l-6 6 6 6",
  more: "M5 12h.01M12 12h.01M19 12h.01",
};
export function Icon({ name, size = 18, class: cls = "", title }) {
  return html`<svg class=${"ic " + cls} width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden=${title ? undefined : "true"} role=${title ? "img" : undefined}>${title ? html`<title>${title}</title>` : null}<path d=${P[name] || P.info} /></svg>`;
}

/** The cairn: three stacked stones. */
export function Logo({ size = 26 }) {
  return html`<svg width=${size} height=${size} viewBox="0 0 32 32" aria-hidden="true"><ellipse cx="16" cy="26" rx="12" ry="4.5" fill="var(--code)"/><ellipse cx="16" cy="17.5" rx="8.5" ry="4" fill="var(--code)" opacity=".78"/><ellipse cx="16" cy="10" rx="5" ry="3" fill="var(--code)" opacity=".56"/></svg>`;
}

/** A project's survey sheet: its own contour lines, seeded by its id. */
export function SheetMark({ seed, size = 28, color = "var(--code)" }) {
  const lines = contours(seed, 60, 60, { levels: 6, cell: 5, bumps: 3 });
  return html`<svg class="sheetmark" width=${size} height=${size} viewBox="0 0 60 60" aria-hidden="true">
    ${lines.map((l, i) => html`<path d=${l.d} fill="none" stroke=${color} stroke-width=${l.index ? 2.6 : 1.7} stroke-opacity=${0.35 + i * 0.1} stroke-linecap="round"/>`)}</svg>`;
}

/** Faint contour field used behind page headers and the sign-in page. */
export function ContourField({ seed, w = 1400, h = 320, class: cls = "", levels = 14, bumps = 7, color = "var(--contour-strong)", accent }) {
  const lines = contours(seed, w, h, { levels, cell: 12, bumps });
  return html`<svg class=${cls} viewBox=${`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid slice" aria-hidden="true">
    ${lines.map(l => html`<path d=${l.d} fill="none" stroke=${l.index && accent ? accent : color} stroke-width=${l.index ? 1.6 : 1} stroke-linecap="round" vector-effect="non-scaling-stroke"/>`)}</svg>`;
}

export function Avatar({ name, size = "" }) {
  return html`<span class=${"avatar " + size} style=${{ background: avatarColor(name) }} aria-hidden="true">${initials(name)}</span>`;
}
export const RoleBadge = ({ role }) => html`<span class="tag outline">${ROLE_LABEL[role] || role}</span>`;

export function Skeleton({ rows = 4, block = false }) {
  return html`<div class=${"skel" + (block ? " block" : "")} aria-busy="true" aria-label="Loading">${Array.from({ length: rows }, (_, i) => html`<i key=${i}></i>`)}</div>`;
}

export function Empty({ title, children, actions, tone = "code" }) {
  return html`<div class="empty">
    <svg class="mark" width="40" height="40" viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="17" fill="none" stroke=${`var(--${tone})`} stroke-opacity=".35" stroke-width="1.5"/><circle cx="20" cy="20" r="11" fill="none" stroke=${`var(--${tone})`} stroke-opacity=".55" stroke-width="1.5"/><circle cx="20" cy="20" r="4.5" fill=${`var(--${tone})`} fill-opacity=".8"/></svg>
    <h3>${title}</h3>
    <div>${children}</div>
    ${actions ? html`<div class="actions">${actions}</div>` : null}
  </div>`;
}

/** Error state; 403 becomes a "no access" message, everything else says what failed and offers a retry. */
export function Failure({ error, retry, what = "this page" }) {
  if (error?.status === 403) return html`<${Empty} title="You don't have access to this" tone="risk">
    <p>${error.message || "Your role in this team doesn't include this."} Ask a team admin to change your role, or switch to a project you can open.</p></${Empty}>`;
  if (error?.status === 404) return html`<${Empty} title="Nothing here" tone="risk"><p>${error.message}</p></${Empty}>`;
  return html`<${Empty} title=${`Couldn't load ${what}`} tone="risk" actions=${retry ? html`<button class="btn sm" onClick=${retry}>Try again</button>` : null}>
    <p>${error?.message || "Something went wrong."} ${error?.status === 0 ? "Check that the Cairn server is still running." : ""}</p></${Empty}>`;
}

/** Render loading / error / data states for a useFetch result. */
export function Load({ res, children, rows = 5, what, block }) {
  if (res.error) return html`<${Failure} error=${res.error} retry=${res.reload} what=${what}/>`;
  if (res.data === undefined) return html`<${Skeleton} rows=${rows} block=${block}/>`;
  return children(res.data);
}

export function CopyButton({ text, label = "Copy", small = true, iconOnly = false }) {
  const [done, setDone] = useState(false);
  const click = async () => { const ok = await copyText(text); setDone(ok ? "Copied" : "Press Ctrl+C"); setTimeout(() => setDone(false), 1600); };
  if (iconOnly) return html`<button type="button" class="icon-btn sm" onClick=${click} aria-label=${done || label} title=${done || label}><${Icon} name=${done ? "check" : "copy"} size="16"/></button>`;
  return html`<button type="button" class=${"btn" + (small ? " sm" : "")} onClick=${click} aria-live="polite"><${Icon} name=${done ? "check" : "copy"} size="15"/>${done || label}</button>`;
}
export const Cmd = ({ children }) => html`<div class="cmd"><code>${children}</code><${CopyButton} text=${children} iconOnly label="Copy command"/></div>`;

export function Tag({ tone, children, title }) { return html`<span class=${"tag " + (tone || "")} title=${title}>${children}</span>`; }

export function Meter({ value, max, tone = "", label }) {
  const pct = max ? Math.max(0, Math.min(100, value / max * 100)) : 0;
  return html`<span class=${"meter " + tone} role="meter" aria-valuemin="0" aria-valuemax=${max} aria-valuenow=${value} aria-label=${label}><b style=${{ width: pct + "%" }}></b></span>`;
}

export function Modal({ title, onClose, children, actions, wide }) {
  const ref = useRef(null);
  useEffect(() => {
    const prev = document.activeElement;
    const el = ref.current;
    const first = el?.querySelector("input,select,textarea,button:not(.modal-x)");
    (first || el)?.focus();
    const onKey = e => {
      if (e.key === "Escape") { e.preventDefault(); onClose?.(); }
      if (e.key === "Tab" && el) {
        const f = [...el.querySelectorAll("a[href],button:not([disabled]),input,select,textarea,[tabindex]:not([tabindex='-1'])")];
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, []);
  return html`<div class="scrim" onClick=${e => { if (e.target === e.currentTarget) onClose?.(); }}>
    <div class="modal" role="dialog" aria-modal="true" aria-label=${title} ref=${ref} tabindex="-1" style=${wide ? { width: "min(720px, 100%)" } : null}>
      <h2>${title}</h2>
      ${children}
      ${actions ? html`<div class="actions">${actions}</div>` : null}
    </div></div>`;
}

/** A button that asks "are you sure" in place instead of a browser dialog. */
export function ConfirmButton({ label, confirm = "Confirm", onConfirm, class: cls = "btn sm danger", disabled, icon, ariaLabel }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!armed) return; const t = setTimeout(() => setArmed(false), 4000); return () => clearTimeout(t); }, [armed]);
  if (!armed) return html`<button type="button" class=${cls} disabled=${disabled} onClick=${() => setArmed(true)} aria-label=${ariaLabel || label} title=${ariaLabel || undefined}>${icon ? html`<${Icon} name=${icon} size="15"/>` : null}${label}</button>`;
  return html`<button type="button" class=${cls + " solid"} disabled=${busy} onClick=${async () => { setBusy(true); try { await onConfirm(); } finally { setBusy(false); setArmed(false); } }}>${confirm}</button>`;
}

export function Toasts() {
  const { toasts, dismiss } = useApp();
  return html`<div class="toasts" role="status" aria-live="polite">${toasts.map(t => html`
    <div class="toast" key=${t.id} style=${{ "--toast-key": `var(--${t.tone || "ink"})` }}>
      <div><b>${t.title}</b>${t.body ? html`<p>${t.body}</p>` : null}${t.diff ? html`<div class="diff">${t.diff.old ? html`<del>${t.diff.old}</del>` : null}${t.diff.new ? html`<ins>${t.diff.new}</ins>` : null}</div>` : null}</div>
      <button class="icon-btn sm" onClick=${() => dismiss(t.id)} aria-label="Dismiss"><${Icon} name="x" size="15"/></button>
    </div>`)}</div>`;
}

export function Tabs({ items, current, keyColor, label }) {
  return html`<nav class="tabs" aria-label=${label} style=${keyColor ? { "--tab-key": keyColor } : null}>${items.map(it => html`
    <a href=${it.href} aria-current=${it.id === current ? "page" : undefined}>${it.label}${it.n != null ? html`<span class="n num">${it.n}</span>` : null}</a>`)}</nav>`;
}

export const Frag = Fragment;
