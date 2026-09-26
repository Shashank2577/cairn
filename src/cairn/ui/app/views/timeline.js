// Timeline: what happened (commits, agent work, spec changes, drift, memory) and what was true when.
// Facts are drawn as validity bars on the same axis as events; the as-of rule shows what was true
// at any moment.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, Empty, Load, Tag, Skeleton, Modal } from "../components/ui.js";
import { useApp, useFetch, useMedia } from "../state.js";
import { api } from "../api.js";
import { link, replace, citeHref } from "../router.js";
import { ago, fmt, plural, rich, stamp, day, clock, date, secs } from "../lib/format.js";

const KINDS = [["commit", "Commits", "var(--ink-2)"], ["session", "Agent work", "var(--agent)"], ["spec", "Specs", "var(--spec)"], ["drift", "Drift", "var(--risk)"], ["memory", "Memory", "var(--memory)"],
  ["fact", "Facts learned", "var(--code)"], ["fact_end", "Facts retired", "var(--risk)"]];
// Fact events repeat what the fact bars already show, so they start hidden.
const QUIET = ["fact", "fact_end"];
const MAXROWS = 36;
const KCOLOR = Object.fromEntries(KINDS.map(k => [k[0], k[2]]));
const KLABEL = Object.fromEntries(KINDS.map(k => [k[0], k[1]]));

export function Timeline() {
  const { pid, route } = useApp();
  const ev = useFetch(() => api.p(pid).timeline({ limit: 400 }), [pid]);
  const facts = useFetch(() => api.p(pid).facts({ limit: 500 }), [pid]);
  const ents = useFetch(() => api.p(pid).entities({ limit: 200 }).then(es => es.map(e => ({ ...e, id: e.id ?? e.uuid }))), [pid]);
  const comms = useFetch(() => api.p(pid).factCommunities(), [pid]);
  const eps = useFetch(() => api.p(pid).episodes(30), [pid]);
  const setQ = patch => { const n = new URLSearchParams(route.q); for (const [k, v] of Object.entries(patch)) v != null && v !== "" ? n.set(k, v) : n.delete(k); const s = n.toString(); replace(link.project(pid, "timeline") + (s ? "?" + s : "")); };
  const entity = route.q.get("entity");

  return html`<div class="page view-in timeline">
    <div class="page-h"><div><h1>Timeline</h1>
      <p class="lede">What happened in this project, and what was true when. Events come from git, agent sessions, specs, drift checks and memory. Facts are extracted from those events by a model and carry the window in which they held.</p></div></div>
    ${ev.error ? html`<${Load} res=${ev}>${() => null}</${Load}>` : !ev.data || !facts.data ? html`<${Skeleton} rows="10" block/>`
      : html`<${Body} events=${normEvents(ev.data)} facts=${normFacts(facts.data)} entities=${ents.data || []} setQ=${setQ}/>`}
    <div class="tl-lower">
      <section class="sec"><div class="sec-h"><div><h2>Groups of facts</h2><p class="sub">Entities that tend to change together.</p></div></div>
        <${Load} res=${comms} rows="3">${cs => cs.length ? html`<div class="fcomms">${cs.map(c => html`<div class="panel"><h3>${c.name}</h3><p class="sub">${c.summary}</p>
          <div class="chips">${(c.members || c.entities || []).map(m => { const e = (ents.data || []).find(x => x.name === m); return e ? html`<button class="chip" onClick=${() => setQ({ entity: e.id })}>${m}</button>` : html`<span class="chip">${m}</span>`; })}</div></div>`)}</div>`
          : html`<p class="sub">No groups yet.</p>`}</${Load}></section>
      <section class="sec"><div class="sec-h"><div><h2>Sources</h2><p class="sub">The episodes facts were extracted from.</p></div></div>
        <${Load} res=${eps} rows="4">${es => es.length ? html`<ol class="episodes">${es.map(e => html`<li key=${e.id || e.uuid}><span class="tag outline">${e.source || "episode"}</span><div><b>${e.name}</b><p class="sub">${rich(e.content_preview || e.content || e.source_description || "", 160)}</p></div><time class="sub nowrap">${ago(e.ts ?? e.valid_at ?? e.created_at)}</time></li>`)}</ol>`
          : html`<p class="sub">No episodes yet.</p>`}</${Load}></section>
    </div>
    ${entity ? html`<${EntityPanel} id=${entity} entities=${ents.data || []} onClose=${() => setQ({ entity: null })} onEntity=${id => id && setQ({ entity: id })}/>` : null}
  </div>`;
}

// Timestamps may arrive as epoch seconds or ISO strings; the chart works in seconds.
const normEvents = es => es.map(e => ({ ...e, ts: secs(e.ts) })).filter(e => Number.isFinite(e.ts)).sort((a, b) => b.ts - a.ts);
const normFacts = fs => fs.map(f => ({ ...f, valid_at: secs(f.valid_at), invalid_at: f.invalid_at ? secs(f.invalid_at) : null })).filter(f => Number.isFinite(f.valid_at));

function Body({ events, facts, entities, setQ }) {
  const { pid, route, ov } = useApp();
  const now = Date.now() / 1000;
  const t0 = Math.min(...events.map(e => e.ts), ...facts.map(f => f.valid_at), now - 3600);
  const t1 = now;
  const atQ = Number(route.q.get("at"));
  const at = atQ && atQ >= t0 && atQ <= t1 ? atQ : t1;
  const valid = f => f.valid_at <= at && (!f.invalid_at || f.invalid_at > at);
  const [off, setOff] = useState(() => new Set(QUIET));
  const [ffilter, setFfilter] = useState("");
  const [allFacts, setAllFacts] = useState(false);
  const shownFacts = useMemo(() => {
    const s = ffilter.trim().toLowerCase();
    return s ? facts.filter(f => (f.fact + " " + f.source_entity + " " + f.target_entity).toLowerCase().includes(s)) : facts;
  }, [facts, ffilter]);
  const byEntity = useMemo(() => {
    const m = new Map(); for (const f of shownFacts.filter(valid)) { const k = f.source_entity; if (!m.has(k)) m.set(k, []); m.get(k).push(f); }
    return [...m].sort((a, b) => b[1].length - a[1].length);
  }, [shownFacts, at]);
  const focus = route.q.get("focus");
  const trueNow = facts.filter(valid);
  const shown = events.filter(e => !off.has(e.kind) && e.ts <= at + 1);
  const entId = name => entities.find(e => e.name === name)?.id;
  useEffect(() => { if (focus) setTimeout(() => document.getElementById("ev-" + focus)?.scrollIntoView({ block: "center" }), 100); }, [focus]);

  if (!events.length && !facts.length) return html`<${Empty} title="Nothing on the timeline yet"><p>Run a sync to read git history. With a model configured, the full sync also extracts facts and the windows in which they held.</p></${Empty}>`;
  return html`<div>
    ${facts.length > 12 ? html`<div class="row factbar"><input class="input" type="search" placeholder=${`Filter ${fmt(facts.length)} facts by words or entity`} value=${ffilter} onInput=${e => setFfilter(e.target.value)} aria-label="Filter facts"/>
      <span class="sub">${ffilter ? `${plural(shownFacts.length, "fact")} match` : ""}</span></div>` : null}
    <${Axis} events=${events.filter(e => !off.has(e.kind) || !QUIET.includes(e.kind))} facts=${shownFacts} t0=${t0} t1=${t1} at=${at} setAt=${v => setQ({ at: v >= t1 - 30 ? null : Math.round(v) })} off=${off}
      limit=${allFacts || ffilter ? Infinity : MAXROWS} onMore=${() => setAllFacts(true)} onFilter=${name => setFfilter(name)} onEntity=${name => { const id = entId(name); if (id) setQ({ entity: id }); }}/>
    ${!facts.length ? html`<p class="sub" style="margin-top:10px">${ov?.models?.available ? "No facts extracted yet. Run a full sync with models." : "Facts need a model. Set one up in Project settings, then run a full sync."}</p>` : null}
    <div class="tl-cols">
      <section class="sec" aria-labelledby="h-true">
        <div class="sec-h"><div><h2 id="h-true">${at >= t1 - 30 ? "True now" : `True on ${day(at)}, ${clock(at)}`}</h2>
          <p class="sub">${plural(trueNow.length, "fact")} held at this moment${facts.length - trueNow.length ? `; ${facts.length - trueNow.length} others had not started or had already stopped` : ""}.</p></div></div>
        ${byEntity.length ? html`<div class="truelist">${byEntity.slice(0, allFacts ? Infinity : 10).map(([name, fs]) => html`<div class="tgroup">
          <h3><button class="linkish" onClick=${() => { const id = entId(name); if (id) setQ({ entity: id }); }}>${name}</button></h3>
          <ul>${fs.slice(0, allFacts ? Infinity : 6).map(f => html`<li><span>${rich(f.fact)}</span><span class="sub nowrap">since ${date(f.valid_at)}${f.invalid_at ? `, until ${date(f.invalid_at)}` : ""}</span></li>`)}
            ${!allFacts && fs.length > 6 ? html`<li class="sub">and ${fs.length - 6} more</li>` : null}</ul></div>`)}</div>
          ${!allFacts && byEntity.length > 10 ? html`<button class="btn sm" style="margin-top:12px" onClick=${() => setAllFacts(true)}>Show all ${plural(byEntity.length, "entity", "entities")}</button>` : null}`
          : html`<p class="sub">No facts held at this moment.</p>`}
      </section>
      <section class="sec" aria-labelledby="h-ev">
        <div class="sec-h"><div><h2 id="h-ev">${at >= t1 - 30 ? "What happened" : "What happened up to then"}</h2><p class="sub">Newest first.</p></div></div>
        <div class="chips" style="margin-bottom:12px">${KINDS.filter(([k]) => events.some(e => e.kind === k)).map(([k, l, c]) => html`<button class="chip vis" aria-pressed=${!off.has(k)} onClick=${() => { const s = new Set(off); s.has(k) ? s.delete(k) : s.add(k); setOff(s); }}>
          <i style=${{ background: c }}></i>${l}<span class="n">${events.filter(e => e.kind === k).length}</span></button>`)}</div>
        <${EventList} events=${shown} focus=${focus}/>
      </section>
    </div>
  </div>`;
}

function EventList({ events, focus }) {
  const { pid } = useApp();
  const [n, setN] = useState(40);
  let last = "";
  if (!events.length) return html`<p class="sub">Nothing to show with these filters.</p>`;
  const list = html`<ol class="events">${events.slice(0, Math.max(n, focus ? events.findIndex(e => e.id === focus) + 5 : 0)).map(e => {
    const d = day(e.ts), head = d !== last ? (last = d, html`<li class="day">${d}</li>`) : null;
    const ref = (e.refs || [])[0];
    const href = e.kind === "commit" ? null : ref ? citeHref(pid, ref) : null;
    const title = html`${rich(e.title, 160)}${e.kind === "commit" && e.meta?.sha ? html` <code class="sha">${e.meta.sha}</code>` : null}`;
    return html`${head}<li class=${"ev" + (focus === e.id ? " focus" : "")} id=${"ev-" + e.id}>
      <time class="num">${clock(e.ts)}</time><i style=${{ background: KCOLOR[e.kind] || "var(--muted)" }} title=${KLABEL[e.kind] || e.kind}></i>
      <div>${href ? html`<a href=${href}>${title}</a>` : title}
        ${e.kind === "commit" && (e.meta?.files || []).length ? html`<div class="sub small">${plural(e.meta.files.length, "file")}${e.actor ? `, ${e.actor}` : ""}</div>` : e.actor && e.kind !== "memory" ? html`<div class="sub small">${e.actor}</div>` : null}
        ${(e.meta?.risk || []).length ? html`<${Tag} tone="risk">${e.meta.risk.join(", ")}</${Tag}>` : null}</div></li>`;
  })}</ol>`;
  return html`${list}${events.length > n ? html`<button class="btn sm" style="margin-top:10px" onClick=${() => setN(n + 60)}>Show ${Math.min(60, events.length - n)} older events</button> <span class="sub">${fmt(events.length - n)} more</span>` : null}`;
}

/** The time axis: event lanes, fact bars grouped by entity, and a draggable as-of rule. */
function Axis({ events, facts, t0, t1, at, setAt, off, onEntity, limit = Infinity, onMore, onFilter }) {
  const host = useRef(null);
  const [w, setW] = useState(900);
  const narrow = useMedia("(max-width: 720px)");
  useEffect(() => { const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width))); ro.observe(host.current); return () => ro.disconnect(); }, []);
  const LBL = narrow ? 104 : 176, R = 18;
  const width = Math.max(narrow ? 620 : 640, w);
  const span = Math.max(60, t1 - t0);
  const x = t => LBL + (t - t0) / span * (width - LBL - R);
  const tFromX = px => t0 + Math.max(0, Math.min(1, (px - LBL) / (width - LBL - R))) * span;
  const lanes = KINDS.filter(([k]) => events.some(e => e.kind === k) && !(QUIET.includes(k) && off.has(k)));
  const LH = 22, FH = 24, top = 30;
  // Entities with the most facts first; past the row limit the rest wait behind "show all".
  const { groups, hiddenFacts } = useMemo(() => {
    const m = new Map(); [...facts].sort((a, b) => a.valid_at - b.valid_at).forEach(f => { if (!m.has(f.source_entity)) m.set(f.source_entity, []); m.get(f.source_entity).push(f); });
    const all = [...m].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
    const per = limit === Infinity ? Infinity : all.length > 1 ? 4 : limit;
    const out = []; let n = 0;
    for (const [name, fs] of all) { if (n >= limit) break; const take = fs.slice(0, Math.max(1, Math.min(per, limit - n))); out.push([name, take, fs.length - take.length]); n += take.length; }
    return { groups: out, hiddenFacts: facts.length - n };
  }, [facts, limit]);
  const factsTop = top + lanes.length * LH + 18;
  let y = factsTop;
  const rows = [];
  for (const [name, fs, more] of groups) { rows.push({ head: name, y: y + 3 }); for (const f of fs) { rows.push({ f, y }); y += FH; } if (more) { rows.push({ more, name, y }); y += 18; } y += 10; }
  const height = y + 8;
  const ticks = useMemo(() => {
    const steps = [300, 900, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 7 * 86400, 30 * 86400, 91 * 86400, 365 * 86400];
    const st = steps.find(s => span / s <= (width - LBL) / 110) || 365 * 86400;
    const out = []; for (let t = Math.ceil(t0 / st) * st; t <= t1; t += st) out.push(t);
    return { st, out };
  }, [t0, t1, width]);
  const tickLabel = t => ticks.st < 86400 ? (new Date(t * 1000).getHours() === 0 && new Date(t * 1000).getMinutes() === 0 ? day(t) : clock(t)) : ticks.st < 30 * 86400 ? day(t) : date(t);
  const drag = useRef(false);
  const onPtr = e => {
    const svg = e.currentTarget.ownerSVGElement || e.currentTarget;
    const r = svg.getBoundingClientRect();
    setAt(tFromX((e.clientX - r.left) * (width / r.width)));
  };
  const valid = f => f.valid_at <= at && (!f.invalid_at || f.invalid_at > at);
  return html`<div class="axis-wrap">
    <div class="asof">
      <label for="asof" class="asof-l">As of</label>
      <input id="asof" type="range" min=${Math.floor(t0)} max=${Math.ceil(t1)} step="60" value=${Math.round(at)} onInput=${e => setAt(+e.target.value)}
        aria-valuetext=${at >= t1 - 30 ? "Now" : stamp(at)}/>
      <b class="asof-v num">${at >= t1 - 30 ? "Now" : stamp(at)}</b>
      ${at < t1 - 30 ? html`<button class="btn xs" onClick=${() => setAt(t1)}>Back to now</button>` : null}
    </div>
    <div class="axis-scroll" ref=${host}>
      <svg width=${width} height=${height} viewBox=${`0 0 ${width} ${height}`} class="axis" role="img" aria-label="Events and facts over time">
        <rect x=${LBL} y="0" width=${width - LBL - R} height=${height} class="hit" onPointerDown=${e => { drag.current = true; e.currentTarget.setPointerCapture(e.pointerId); onPtr(e); }}
          onPointerMove=${e => drag.current && onPtr(e)} onPointerUp=${() => { drag.current = false; }}/>
        ${ticks.out.map(t => html`<g key=${t}><line class="tick" x1=${x(t)} x2=${x(t)} y1="18" y2=${height}/><text class="ticklbl" x=${x(t) + 4} y="13">${tickLabel(t)}</text></g>`)}
        ${lanes.map(([k, l, c], i) => html`<g key=${k} class=${off.has(k) ? "lane off" : "lane"}>
          <text class="lanelbl" x="0" y=${top + i * LH + 14}>${l}</text>
          <line class="laneline" x1=${LBL} x2=${width - R} y1=${top + i * LH + 10} y2=${top + i * LH + 10}/>
          ${events.filter(e => e.kind === k).map(e => html`<circle key=${e.id} cx=${x(e.ts)} cy=${top + i * LH + 10} r=${k === "commit" ? 4.5 : 4} style=${{ fill: c, opacity: e.ts <= at ? 1 : .25 }}><title>${stamp(e.ts)}: ${e.title}</title></circle>`)}
        </g>`)}
        ${facts.length ? html`<text class="lanelbl strong" x="0" y=${factsTop - 6}>Facts</text>` : null}
        ${rows.map((r, i) => r.more ? html`<text key=${"m" + r.name} class="factmore" x=${LBL + 4} y=${r.y + 12} onClick=${() => onFilter?.(r.name)}>+ ${r.more} more about ${r.name.length > 40 ? r.name.slice(0, 39) + "…" : r.name}</text>`
          : r.head ? html`<text key=${"h" + r.head} class="enthead" x="0" y=${r.y + 14} onClick=${() => onEntity(r.head)}>${r.head.length > (narrow ? 13 : 24) ? r.head.slice(0, narrow ? 12 : 23) + "…" : r.head}</text>`
          : html`<g key=${r.f.id} class=${"fbar" + (valid(r.f) ? " on" : "") + (r.f.invalid_at ? " ended" : "")} style=${{ "--i": i }}>
              <title>${r.f.fact}. Valid from ${stamp(r.f.valid_at)}${r.f.invalid_at ? ` until ${stamp(r.f.invalid_at)}` : ", still true"}.</title>
              <rect class="bar" x=${x(r.f.valid_at)} y=${r.y + 3} width=${Math.max(3, x(r.f.invalid_at || t1) - x(r.f.valid_at))} height="16" rx="3" style=${{ transformOrigin: `${x(r.f.valid_at)}px 0` }}/>
              ${r.f.invalid_at ? html`<line class="cap" x1=${x(r.f.invalid_at)} x2=${x(r.f.invalid_at)} y1=${r.y + 1} y2=${r.y + 21}/>` : null}
              ${(() => { const p = labelPos(x, r.f, t1, width, R, LBL); return html`<text class=${"flbl " + p.cls} x=${p.x} y=${r.y + 15} text-anchor=${p.anchor}>${p.text}</text>`; })()}
            </g>`)}
        <g class="rule" style=${{ transform: `translateX(${x(at)}px)` }}><line x1="0" x2="0" y1="18" y2=${height}/><path d="M-6,18 L6,18 L0,26 Z"/></g>
      </svg>
    </div>
    <div class="keyline" style="margin-top:8px">${lanes.map(([k, l, c]) => html`<span><i class="dot" style=${{ background: c }}></i>${l}</span>`)}
      <span><i style="background:var(--ink-2);border-color:var(--ink-2);height:9px"></i>Fact, while it held</span><span><i class="line" style="border-color:var(--risk);transform:rotate(90deg);width:10px"></i>Stopped being true</span>
      <span>Drag across the chart or use the slider to move the as-of line</span></div>
    ${hiddenFacts > 0 ? html`<div style="margin-top:10px"><button class="btn sm" onClick=${onMore}>Show all ${fmt(facts.length)} facts</button> <span class="sub">The chart shows up to four facts per entity; ${plural(hiddenFacts, "more fact")} are hidden. Filter above to focus on one entity.</span></div>` : null}
  </div>`;
}
/** Put a fact's label after its bar, inside it, or before it — wherever it fits. */
function labelPos(x, f, t1, width, R, LBL) {
  const start = x(f.valid_at), end = x(f.invalid_at || t1), tw = f.fact.length * 6.3;
  if (width - R - end >= tw + 10) return { x: end + 7, anchor: "start", cls: "", text: f.fact };
  if (end - start >= tw + 14) return { x: start + 7, anchor: "start", cls: "inside", text: f.fact };
  if (start - LBL >= tw + 10) return { x: start - 7, anchor: "end", cls: "", text: f.fact };
  const room = Math.max(end - start - 14, width - R - end - 10, start - LBL - 10);
  const n = Math.max(8, Math.floor(room / 6.3));
  const text = f.fact.length > n ? f.fact.slice(0, n - 1) + "…" : f.fact;
  if (room === end - start - 14) return { x: start + 7, anchor: "start", cls: "inside", text };
  if (room === width - R - end - 10) return { x: end + 7, anchor: "start", cls: "", text };
  return { x: start - 7, anchor: "end", cls: "", text };
}

function EntityPanel({ id, entities, onClose, onEntity }) {
  const { pid } = useApp();
  const res = useFetch(() => api.p(pid).entity(id), [pid, id]);
  return html`<${Modal} title=${res.data?.entity?.name || res.data?.name || "Entity"} onClose=${onClose} wide actions=${html`<button class="btn" onClick=${onClose}>Close</button>`}>
    <${Load} res=${res} rows="6">${raw => { const d = { entity: raw.entity || raw, facts: raw.facts || [], episodes: raw.episodes || [] }; return html`<div class="entity">
      <p class="ink2" style="margin:0 0 8px">${d.entity.summary}</p>
      <div class="chips" style="margin-bottom:14px">${(d.entity.labels || []).map(l => html`<span class="tag outline">${l}</span>`)}</div>
      <h3>Facts <span class="sub num" style="font-weight:500">${d.facts.length}</span></h3>
      <ol class="efacts">${normFacts(d.facts).sort((a, b) => a.valid_at - b.valid_at).map(f => html`<li class=${f.invalid_at ? "ended" : ""}>
        <span class="win num">${date(f.valid_at)} → ${f.invalid_at ? date(f.invalid_at) : "now"}</span>
        <span>${rich(f.fact)}${f.invalid_at ? html` <${Tag} tone="risk">no longer true</${Tag}>` : null}</span>
        ${(() => { const out = f.source_entity === d.entity.name; const other = out ? f.target_entity : f.source_entity;
          const oid = entities.find(e => e.name === other)?.id || (out ? f.target_id : f.source_id);
          return html`<span class="sub small">${out ? "with" : "from"} ${oid ? html`<button class="linkish" onClick=${() => onEntity(oid)}>${other}</button>` : other}</span>`; })()}</li>`)}</ol>
      ${(d.episodes || []).length ? html`<h3 style="margin-top:16px">Where these facts came from</h3><ul class="list">${d.episodes.map(e => html`<li><span class="tag outline">${e.source || "episode"}</span> ${e.name} <span class="sub">${ago(e.ts ?? e.valid_at ?? e.created_at)}</span></li>`)}</ul>` : null}
    </div>`; }}</${Load}></${Modal}>`;
}
