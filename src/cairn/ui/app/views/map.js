// Map: the code and document knowledge graph. Explore it interactively (communities, search, focus,
// path finder, questions), read it as architecture layers, open the engine's own views, its wiki and
// its report.
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { html, Icon, Empty, Load, Tag, Skeleton, Tabs } from "../components/ui.js";
import { ArchMap } from "../components/archmap.js";
import { CommunityMap, buildRegions, loadPack } from "../components/communitymap.js";
import { RepoMap, LinkCard } from "../components/repomap.js";
import { Markdown } from "../lib/md.js";
import { useApp, useFetch, reducedMotion, useMedia } from "../state.js";
import { api } from "../api.js";
import { link, replace, go } from "../router.js";
import { fmt, plural, ago, base, failText } from "../lib/format.js";
import { loadCytoscape, themeColors, onThemeChange } from "../lib/cyto.js";

const TABS = [["explore", "Explore"], ["architecture", "Architecture"], ["views", "Engine views"], ["wiki", "Wiki"], ["report", "Report"]];

export function MapView() {
  const { pid, route, ov } = useApp();
  const tab = TABS.some(t => t[0] === route.rest[0]) ? route.rest[0] : "explore";
  const L = ov?.layers?.map;
  return html`<div class=${"page view-in map-page" + (tab === "explore" ? " wide" : "")}>
    <div class="page-h"><div><h1>Map</h1>
      <p class="lede">The code and documents of ${ov?.project || "this project"} as one graph${L?.nodes ? `: ${fmt(L.nodes)} nodes and ${fmt(L.edges)} links in ${fmt(L.areas)} communities` : ""}. Communities are groups of code and docs that link to each other more than to anything else.</p></div></div>
    <${Tabs} label="Map views" current=${tab} keyColor="var(--code)" items=${TABS.map(([id, label]) => ({ id, label, href: link.project(pid, "map", id === "explore" ? [] : [id]) }))}/>
    ${!L?.nodes && ov ? html`<${Empty} title="The map is empty"><p>Cairn hasn't parsed this repository yet. Run a sync and the graph of its code and documents appears here.</p></${Empty}>`
      : tab === "explore" ? html`<${Explore}/>` : tab === "architecture" ? html`<${Architecture}/>` : tab === "views" ? html`<${EngineViews}/>` : tab === "wiki" ? html`<${Wiki}/>` : html`<${Report}/>`}
  </div>`;
}

/* ------------------------------------------------------------------ explore */
const KIND_LABEL = { code: "Code", file: "Files", class: "Classes", function: "Functions", method: "Methods", module: "Modules", document: "Documents", section: "Doc sections", concept: "Concepts", rationale: "Rationale comments" };
/** Node shape from what the node is: files as tiles, functions as dots, documents as tags. */
function shapeOf(n) {
  const k = n.kind, lbl = String(n.label || ""), b = base(n.file || "");
  if (k === "rationale") return "diamond";
  if (k === "concept") return "hexagon";
  if (k === "section" || (k === "document" && lbl !== b)) return "round-tag";
  if (k === "file" || k === "document" || (lbl && lbl === b)) return "round-rectangle";
  return "ellipse";
}
/** The graph engine answers questions as plain text; pull out the headline and the nodes it lists. */
function readAnswer(res, nodes) {
  const text = String(res?.text || "");
  let ids = res?.nodes || [];
  if (!ids.length) {
    const byKey = new Map(nodes.map(n => [n.label + "|" + n.file, n.id]));
    ids = [...text.matchAll(/^NODE (.+?) \[src=(\S+)/gm)].map(m => byKey.get(m[1] + "|" + m[2])).filter(Boolean);
  }
  const found = text.match(/\|\s*(\d+) nodes found/), start = text.match(/Start: \[(.*?)\]/), shown = text.match(/showing (\d+) of (\d+) nodes/);
  const headline = res?.summary ? res.summary : found ? `Found ${found[1]} related nodes${start ? ` starting from ${start[1].replace(/'/g, "")}` : ""}${shown ? `; the answer lists the ${shown[1]} most relevant` : ""}.`
    : text.split("\n").find(l => l.trim()) || "No answer.";
  return { headline, ids, raw: text.length > headline.length + 40 ? text : "" };
}
const REL = {
  calls: ["Calls", "Called by"], indirect_call: ["Calls indirectly", "Called indirectly by"], imports: ["Imports", "Imported by"], imports_from: ["Imports from", "Imported by"],
  references: ["Refers to", "Referred to by"], contains: ["Contains", "Inside"], method: ["Methods", "Method of"], uses: ["Uses", "Used by"],
  inherits: ["Inherits from", "Inherited by"], defines: ["Defines", "Defined by"],
};

function cyStyle(V) {
  return [
    { selector: "node", style: { "background-color": "data(color)", width: "data(size)", height: "data(size)", label: "data(short)", "font-family": "Overpass, system-ui, sans-serif",
      "font-size": 11, "font-weight": "bold", color: V.ink, "text-outline-color": V.paper, "text-outline-width": 3, "text-valign": "bottom", "text-margin-y": 3,
      "text-max-width": 170, "text-wrap": "ellipsis", "border-width": 0, "overlay-opacity": 0 } },
    { selector: "node[shape]", style: { shape: "data(shape)" } },
    { selector: "edge", style: { width: 0.8, "line-color": V.rule, opacity: 0.65, "curve-style": "haystack", "haystack-radius": 0 } },
    { selector: ".faded", style: { opacity: 0.1, "text-opacity": 0 } },
    { selector: "edge.hl", style: { width: 1.8, opacity: 0.95, "line-color": V.ink2 } },
    { selector: "node.hl", style: { "border-width": 1.5, "border-color": V.ink, "z-index": 14 } },
    { selector: "node.match", style: { "border-width": 3, "border-color": V.agent, "z-index": 15 } },
    { selector: "node.sel", style: { "border-width": 4, "border-color": V.agent, "z-index": 20 } },
    { selector: "edge.path", style: { width: 4, opacity: 1, "line-color": V.code, "z-index": 30 } },
    { selector: "node.path", style: { "border-width": 3.5, "border-color": V.code, "z-index": 30 } },
    { selector: ".hidden", style: { display: "none" } },
  ];
}

const PALETTE = Array.from({ length: 12 }, (_, i) => `var(--c${i})`);
function levelOf(scope) {
  if (scope === "repos") return "repos";
  if (!scope || scope === "areas" || scope === "communities") return "areas";
  if (scope.startsWith("community:")) return "community";
  if (scope.startsWith("around:")) return "around";
  if (scope.startsWith("path:")) return "path";
  if (scope.startsWith("file:")) return "file";
  return "all";
}
function Explore() {
  const { pid, route, session, project } = useApp();
  // With two or more repositories there is a level above this project's areas: all repositories.
  const multi = (session?.projects?.length || 0) >= 2;
  const scope = route.q.get("scope") || "areas";
  const level = levelOf(scope) === "repos" && !multi ? "areas" : levelOf(scope);
  const selId = route.q.get("node") || null;
  const areaQ = route.q.get("area") || null;
  const setQ = patch => {
    const q = new URLSearchParams(route.q);
    for (const [k, v] of Object.entries(patch)) v ? q.set(k, v) : q.delete(k);
    const s = q.toString();
    replace(link.project(pid, "map") + (s ? "?" + s : ""));
  };
  const summary = useFetch(() => api.p(pid).graphSummary(), [pid]);
  const cdata = useFetch(() => api.p(pid).graphData("communities", 60), [pid]);
  const [d3, setD3] = useState(window.d3?.pack ? window.d3 : null);
  useEffect(() => { if (!d3) loadPack().then(setD3, () => {}); }, []);
  const [, setThemeTick] = useState(0);
  useEffect(() => onThemeChange(() => setThemeTick(t => t + 1)), []);
  const narrow = useMedia("(max-width: 900px)");

  // Areas and the name of every community, shared by every level (breadcrumbs, colours, answers).
  const R = useMemo(() => {
    if (!cdata.data?.nodes?.length) return null;
    const comms = cdata.data.nodes.map(n => {
      const cid = String(n.community ?? String(n.id).replace(/^community:/, ""));
      return { id: String(n.id).startsWith("community:") ? String(n.id) : "community:" + cid, cid, label: n.label, size: n.size || 1, top: n.top || [], degree: n.degree,
        area: n.area ?? "", files: n.files ?? null };
    });
    const { regions, common } = buildRegions(comms, PALETTE);
    const byCid = new Map();
    regions.forEach((r, ri) => r.communities.forEach(c => byCid.set(c.cid, { ...c, regionLabel: r.label, regionKey: r.key, colorIndex: ri % 12 })));
    const links = (cdata.data.links || []).map(l => ({ ...l, source: String(l.source).startsWith("community:") ? String(l.source) : "community:" + l.source, target: String(l.target).startsWith("community:") ? String(l.target) : "community:" + l.target }));
    return { regions, common, byCid, links };
  }, [cdata.data]);
  const focusRegion = areaQ && R?.regions.some(r => r.key === areaQ) ? areaQ : null;

  const [find, setFind] = useState("");
  const [ask, setAsk] = useState({ q: "", res: null, busy: false });
  const [path, setPath] = useState({ a: null, b: null, res: null, busy: false });
  const [hoverC, setHoverC] = useState(null);
  const weights = useMemo(() => (R?.links || []).map(l => l.weight || 1).sort((a, b) => b - a), [R]);
  const [thr, setThr] = useState(null);
  const threshold = thr ?? (weights.length ? weights[Math.min(35, weights.length - 1)] : 1);

  // Search: communities whose name, area or members match light up; server hits can be opened directly.
  const [hits, setHits] = useState([]);
  const tHits = useRef(0);
  useEffect(() => {
    clearTimeout(tHits.current);
    const q = find.trim();
    if (q.length < 2) { setHits([]); return; }
    tHits.current = setTimeout(() => api.p(pid).search(q, "symbol,file", 8).then(setHits, () => setHits([])), 180);
  }, [find, pid]);
  const findComms = useMemo(() => {
    const q = find.trim().toLowerCase();
    if (q.length < 2 || !R) return null;
    return new Set([...R.byCid.values()].filter(c => `${c.display} ${c.sub} ${c.area} ${c.label} ${(c.top || []).join(" ")}`.toLowerCase().includes(q)).map(c => c.id));
  }, [find, R]);

  const drillToNode = async id => {
    let cid = null;
    try { cid = (await api.p(pid).node(id)).node?.community; } catch (e) { /* fall back to its neighbourhood */ }
    setQ({ scope: cid != null ? `community:${cid}` : `around:${id}`, node: id, area: null });
  };
  const drillToFile = async p => {
    try {
      const d = await api.p(pid).graphData("file:" + p, 120);
      const own = d.nodes.filter(n => n.file === p);
      const fnode = own.find(n => n.label === base(p) || n.label === p) || own.sort((a, b) => b.degree - a.degree)[0];
      if (fnode) setQ({ scope: `community:${fnode.community}`, node: fnode.id, area: null });
    } catch (e) { setQ({ scope: "file:" + p, node: null }); }
  };
  const openHit = h => {
    setFind(""); setHits([]);
    const raw = String(h.id);
    if (raw.startsWith("file:")) return drillToFile(raw.slice(5));
    const id = raw.replace(/^symbol:/, "");
    if (h.community != null) setQ({ scope: `community:${h.community}`, node: id, area: null }); else drillToNode(id);
  };

  // Answers: highlight the communities they fall in, and let the reader open one.
  const runAsk = async e => {
    e.preventDefault();
    if (!ask.q.trim()) return;
    setAsk(a => ({ ...a, busy: true }));
    try {
      const r = await api.p(pid).query(ask.q.trim());
      const res = readAnswer(r, []);
      res.nodeComms = r.communities ? Object.fromEntries((r.nodes || []).map((id, i) => [id, String(r.communities[i])])) : r.node_communities || null;
      setAsk(a => ({ ...a, res, busy: false })); setPath(p => ({ ...p, res: null }));
    } catch (err) { setAsk(a => ({ ...a, res: { headline: err.message, ids: [], raw: "" }, busy: false })); }
  };
  const askComms = useMemo(() => {
    if (!ask.res?.ids?.length) return null;
    const m = new Map();
    for (const id of ask.res.ids) { const c = ask.res.nodeComms?.[id]; if (c != null) m.set(String(c), (m.get(String(c)) || 0) + 1); }
    return m.size ? m : null;
  }, [ask.res]);

  const findPath = async () => {
    if (!path.a || !path.b) return;
    setPath(p => ({ ...p, busy: true }));
    try { const r = await api.p(pid).path(path.a.id, path.b.id); setPath(p => ({ ...p, res: { ...r, text: pathText(r) }, busy: false })); if ((r.path || []).length > 1) setQ({ scope: `path:${path.a.id}~${path.b.id}`, node: null, area: null }); }
    catch (e) { setPath(p => ({ ...p, res: { path: [], text: e.message }, busy: false })); }
  };

  const nameOf = c => R?.byCid.get(String(c))?.display || summary.data?.communities?.find(x => String(x.id) === String(c))?.name || `community ${c}`;
  const cid = level === "community" ? scope.slice(10) : null;
  const cm = cid != null ? R?.byCid.get(cid) : null;
  const regionOf = key => R?.regions.find(r => r.key === key);
  const [aroundLabel, setAroundLabel] = useState("");
  const crumbs = [];
  if (multi) crumbs.push({ label: "All repositories", q: { scope: "repos", node: null, area: null }, current: level === "repos" });
  if (level !== "repos") crumbs.push({ label: multi ? project?.name || "This repository" : "All areas", q: { scope: null, node: null, area: null }, current: level === "areas" && !focusRegion });
  if (level === "areas" && focusRegion && regionOf(focusRegion)) crumbs.push({ label: regionOf(focusRegion).label, current: true });
  if (level === "community") { if (cm) crumbs.push({ label: cm.regionLabel, q: { scope: null, node: null, area: cm.regionKey } }); crumbs.push({ label: cm?.display || nameOf(cid), current: true }); }
  if (level === "around") crumbs.push({ label: `Around ${aroundLabel || "a node"}`, current: true });
  if (level === "file") crumbs.push({ label: scope.slice(5), current: true });
  if (level === "path") crumbs.push({ label: "Path", current: true });
  if (level === "all") crumbs.push({ label: "All nodes", current: true });

  const crumbNav = html`<nav class="crumbs2" aria-label="Where you are in the map">${crumbs.map((c, i) => html`${i ? html`<span class="sep" aria-hidden="true">›</span>` : null}${c.current ? html`<b aria-current="location">${c.label}</b>` : html`<button class="linkish" onClick=${() => setQ(c.q || {})}>${c.label}</button>`}`)}</nav>`;
  const highlight = level === "areas" ? (askComms ? new Set([...askComms.keys()].map(c => "community:" + c)) : findComms) : null;
  const total = summary.data?.nodes;
  const covered = R ? R.regions.reduce((a, r) => a + r.size, 0) : 0;
  const hoverComm = hoverC ? [...(R?.byCid.values() || [])].find(c => c.id === hoverC) : null;

  return html`<div class="explore">
    <div class="gtools">
      <div class="gsearch">
        <${Icon} name="search" size="15"/>
        <input class="input" placeholder=${level === "areas" ? "Find code, a file or an area" : "Highlight nodes by name, or find anything"} value=${find} aria-label="Find in the map"
          onInput=${e => { setFind(e.target.value); setAsk(a => ({ ...a, res: null })); }}
          onKeyDown=${e => { if (e.key === "Enter" && hits[0]) openHit(hits[0]); if (e.key === "Escape") setFind(""); }}/>
        ${hits.length ? html`<div class="pop menu ghits" role="listbox">${hits.map(h => html`<button type="button" role="option" onMouseDown=${e => { e.preventDefault(); openHit(h); }}>
          <span class="clip" style="flex:1">${h.title}</span><span class="sub path clip" style="max-width:45%">${h.kind === "file" ? "file" : h.path}</span></button>`)}</div>` : null}
      </div>
      <form class="gask" onSubmit=${runAsk}>
        <input class="input" placeholder="Ask the graph, e.g. how does sync reach the read model" value=${ask.q} aria-label="Ask the graph" onInput=${e => setAsk(a => ({ ...a, q: e.target.value }))}/>
        <button class="btn sm" type="submit" disabled=${ask.busy || !ask.q.trim()}>${ask.busy ? "Asking…" : "Ask"}</button>
      </form>
      <span class="seg gview" role="radiogroup" aria-label="Map level">
        ${multi ? html`<button role="radio" aria-checked=${level === "repos"} aria-pressed=${level === "repos"} onClick=${() => setQ({ scope: "repos", node: null, area: null })}>Repositories</button>` : null}
        <button role="radio" aria-checked=${level !== "all" && level !== "repos"} aria-pressed=${level !== "all" && level !== "repos"} onClick=${() => setQ({ scope: null, node: null })}>Areas</button>
        <button role="radio" aria-checked=${level === "all"} aria-pressed=${level === "all"} onClick=${() => setQ({ scope: "all", node: null, area: null })}>All nodes</button>
      </span>
    </div>
    ${ask.res || path.res ? html`<div class="gnotes">
      ${ask.res ? html`<div class="gans"><${Tag} tone="agent">answer</${Tag}><span>${ask.res.headline}
          ${level === "areas" && askComms?.size ? html`<span class="achips">${[...askComms].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([c, n]) => html`<button class="chip" onClick=${() => setQ({ scope: `community:${c}`, node: null, area: null })}>${nameOf(c)}<span class="n">${n}</span></button>`)}</span>` : null}
          ${ask.res.raw ? html`<details class="rawans"><summary>Full answer</summary><pre class="pack">${ask.res.raw}</pre></details>` : null}</span>
        <button class="btn xs ghost" onClick=${() => setAsk(a => ({ ...a, res: null }))}>Clear</button></div>` : null}
      ${path.res && level !== "path" ? html`<div class="gans"><${Tag} tone="code">path</${Tag}><span>${path.res.text}</span><button class="btn xs ghost" onClick=${() => setPath(p => ({ ...p, res: null }))}>Clear</button></div>` : null}
    </div>` : null}
    <div class=${"gbody" + (level === "areas" || level === "repos" ? " areas" : "")}>
      ${level === "repos" ? html`<${RepoView} d3=${d3} crumbs=${crumbNav} narrow=${narrow}/>`
      : level === "areas" ? html`
        <div class="gstage cstage">
          ${crumbNav}
          ${!R || !d3 ? html`<div class="gloading" style="position:static;min-height:420px"><${Skeleton} rows="3"/><span class="sub">${cdata.error ? cdata.error.message : "Drawing the map…"}</span></div>`
            : html`<${CommunityMap} d3=${d3} regions=${R.regions} links=${R.links} focusRegion=${focusRegion} onRegion=${k => setQ({ area: k })} hover=${hoverC} onHover=${setHoverC}
                onOpen=${c => setQ({ scope: `community:${c.cid}`, node: null, area: null })} highlight=${highlight} threshold=${threshold} height=${narrow ? 460 : undefined}/>`}
          ${R ? html`<div class="gstatus2 sub">
            <span>The ${R.byCid.size} largest of ${fmt(summary.data?.communities?.length || R.byCid.size)} communities, ${fmt(covered)} of ${fmt(total || covered)} nodes. Circle size: nodes in the community. Rings: the folder they live in.</span>
            ${weights.length ? html`<label class="thr">Lines: dependencies with at least <b class="num">${fmt(threshold)}</b> links
              <input type="range" min=${weights[weights.length - 1]} max=${weights[0]} value=${threshold} onInput=${e => setThr(+e.target.value)} aria-label="Hide weaker dependencies"/></label>` : null}
          </div>` : null}
        </div>
        <aside class="ginspect">
          ${hoverComm ? html`<${CommunityCard} c=${hoverComm} R=${R} onOpen=${() => setQ({ scope: `community:${hoverComm.cid}`, node: null, area: null })}/>` : html`<${AreasGuide} R=${R} total=${total} focusRegion=${focusRegion} onRegion=${k => setQ({ area: k })}/>`}
          <${PathFinder} path=${path} setPath=${setPath} onFind=${findPath}/>
        </aside>`
      : html`<${MemberGraph} key=${scope} scope=${scope} level=${level} selId=${selId} setQ=${setQ} R=${R} find=${find} ask=${ask} path=${path} setPath=${setPath} onFind=${findPath}
          onAround=${setAroundLabel} narrow=${narrow} drillToNode=${drillToNode} crumbs=${crumbNav}/>`}
    </div>
  </div>`;
}

const LANG = { ".py": "Python", ".md": "Markdown", ".js": "JavaScript", ".mjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust",
  ".java": "Java", ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".cpp": "C++", ".c": "C", ".swift": "Swift", ".sh": "Shell", ".html": "HTML", ".css": "CSS", ".sql": "SQL" };
const langsOf = r => Object.entries(r.languages || {}).filter(([e]) => LANG[e]).sort((a, b) => b[1] - a[1]).map(([e]) => LANG[e]).filter((l, i, a) => a.indexOf(l) === i).slice(0, 3);

/** All repositories the caller can see, and the packages they share. */
function RepoView({ d3, crumbs, narrow }) {
  const { pid, session } = useApp();
  const teams = session?.teams || [];
  const [team, setTeam] = useState("");
  const res = useFetch(() => api.reposMap(team || undefined), [team]);
  const [hover, setHover] = useState(null), [pinned, setPinned] = useState(null), [hoverRepo, setHoverRepo] = useState(null);
  const colors = Array.from({ length: 12 }, (_, i) => `var(--c${i})`);
  const data = res.data;
  const openRepo = r => go(link.project(r.id, "map"));
  const openComm = (r, c) => go(link.project(r.id, "map", [], { scope: `community:${c.cid}` }));
  const openEvidence = (l, e) => go(e.node ? link.project(l.source, "map", [], { scope: `around:${e.node}`, node: e.node }) : link.impact(l.source, e.file));
  const shownLink = hover || pinned;
  return html`
    <div class="gstage cstage">
      ${crumbs}
      ${res.error ? html`<div class="gloading" style="position:static;min-height:320px"><p class="err">${res.error.message}</p></div>`
        : !data || !d3 ? html`<div class="gloading" style="position:static;min-height:420px"><${Skeleton} rows="3"/><span class="sub">Drawing the repositories…</span></div>`
        : !data.nodes.length ? html`<div style="padding:60px 24px 24px"><${Empty} title="No repository has a map yet"><p>Run a sync in a project to build its map; it appears here once it has one.</p></${Empty}></div>`
        : html`<${RepoMap} d3=${d3} repos=${data.nodes} links=${data.links} current=${pid} colors=${colors} height=${narrow ? 420 : undefined}
            active=${hover} pinned=${pinned} onLink=${setHover} onPin=${l => { setPinned(p => p && p.source === l.source && p.target === l.target && p.kind === l.kind ? null : l); if (narrow) setTimeout(() => document.querySelector(".lcard")?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 60); }}
            hoverRepo=${hoverRepo} onHoverRepo=${setHoverRepo} onOpen=${openRepo} onOpenCommunity=${openComm}/>`}
      ${data ? html`<div class="gstatus2 sub">
        <span>${plural(data.nodes.length, "repository", "repositories")} with a map${data.links.length ? `, ${plural(data.links.filter(l => l.kind === "imports").length, "import link")}${data.links.some(l => l.kind === "depends") ? ` and ${plural(data.links.filter(l => l.kind === "depends").length, "manifest-only link")}` : ""}` : ", and no packages shared between them"}.
          ${data.nodes.length < 2 ? " Only one repository you can see has a map so far." : ""}</span>
        ${teams.length > 1 ? html`<span class="seg" role="radiogroup" aria-label="Team">${[["", "All teams"], ...teams.map(t => [t.id, t.name])].map(([id, name]) => html`<button role="radio" aria-checked=${team === id} aria-pressed=${team === id} onClick=${() => { setTeam(id); setPinned(null); }}>${name}</button>`)}</span>` : null}
      </div>` : null}
    </div>
    <aside class="ginspect">
      ${shownLink && data ? html`<${LinkCard} l=${shownLink} repos=${data.nodes} pinned=${!!pinned && !hover} onEvidence=${openEvidence} onClose=${() => setPinned(null)}/>`
        : html`<div class="panel guide">
          <h3>How to read this map</h3>
          <p class="sub">Each large circle is a repository, sized by the nodes in its map; the small circles inside are its largest communities. An arrow runs from a repository to the one whose package it imports, thicker when more files import it. A dashed arrow means a manifest lists the package but no file imports it. Hover an arrow to see where; click a repository to open its map.</p>
          ${data ? html`<h4>Repositories</h4><ul class="repolist">${data.nodes.map((r, i) => html`<li class=${hoverRepo === r.id ? "on" : ""}><button type="button" onClick=${() => openRepo(r)} onMouseEnter=${() => setHoverRepo(r.id)} onMouseLeave=${() => setHoverRepo(null)}>
            <span class="rl-h"><span class="sw" style=${{ background: colors[i % colors.length] }}></span><b>${r.name}</b>${r.id === pid ? html`<span class="tag outline">here</span>` : null}<span class="spacer"></span><span class="sub num">${fmt(r.nodes)} nodes</span></span>
            ${r.description ? html`<span class="rl-d">${r.description}</span>` : null}
            <span class="rl-m">${(r.packages || []).map(p => html`<code class="pkg" title=${p.ecosystem}>${p.name}</code>`)}<span class="sub">${[plural(r.files || 0, "file"), ...langsOf(r)].join(", ")}</span></span>
          </button></li>`)}</ul>` : html`<${Skeleton} rows="4"/>`}
        </div>`}
    </aside>`;
}

function AreasGuide({ R, total, focusRegion, onRegion }) {
  if (!R) return html`<div class="panel"><${Skeleton} rows="5"/></div>`;
  const max = Math.max(...R.regions.map(r => r.size));
  return html`<div class="panel guide">
    <h3>How to read this map</h3>
    <p class="sub">Each circle is a community: code and docs that link to each other more than to anything else. Circles sit inside the folder they mostly live in${R.common ? html`; most areas are under <code>${R.common}/</code>` : ""}. Lines are dependencies between communities. Click an area to zoom in, a circle to open it.</p>
    <h4>Areas, largest first</h4>
    <ul class="arealist">${R.regions.map(r => html`<li><button class=${focusRegion === r.key ? "on" : ""} onClick=${() => onRegion(focusRegion === r.key ? null : r.key)}>
      <span class="sw" style=${{ background: r.color }}></span><span class="clip">${r.label}</span><span class="n num">${fmt(r.size)}</span>
      <span class="bar"><b style=${{ width: `${r.size / max * 100}%`, background: r.color }}></b></span></button></li>`)}</ul>
  </div>`;
}

function CommunityCard({ c, R, onOpen }) {
  const deps = R.links.filter(l => l.source === c.id || l.target === c.id).sort((a, b) => b.weight - a.weight).slice(0, 5)
    .map(l => { const other = l.source === c.id ? l.target : l.source; return { other: [...R.byCid.values()].find(x => x.id === other), w: l.weight, out: l.source === c.id }; }).filter(d => d.other);
  return html`<div class="panel ccard">
    <div class="row tight"><span class="sw" style=${{ background: PALETTE[c.colorIndex] || "var(--c-other)" }}></span><span class="sub">${c.regionLabel}</span></div>
    <h3 class="insp-t">${c.display}</h3>
    <p class="sub" style="margin:2px 0 8px">${plural(c.size, "node")}${c.area ? html` in <span class="path">${c.area}/</span>` : ""}</p>
    ${c.top?.length ? html`<h4>Most connected members</h4><ul class="plain">${c.top.slice(0, 5).map(t => html`<li><code>${t}</code></li>`)}</ul>` : null}
    ${c.files?.length ? html`<h4>Files</h4><ul class="plain">${c.files.map(([f, n]) => html`<li><span class="path">${f}</span> <span class="sub num">${n}</span></li>`)}</ul>` : null}
    ${deps.length ? html`<h4>Strongest dependencies</h4><ul class="plain">${deps.map(d => html`<li>${d.out ? "uses" : "used by"} <b>${d.other.display}</b> <span class="sub">${d.other.regionLabel}, ${plural(d.w, "link")}</span></li>`)}</ul>` : null}
    <button class="btn sm primary" style="margin-top:10px" onClick=${onOpen}>Open this community</button>
  </div>`;
}

/** A drilled-in view: one community, a node's neighbourhood, a path, or every node. Labels stay sparse. */
function MemberGraph({ scope, level, selId, setQ, R, find, ask, path, setPath, onFind, onAround, narrow, drillToNode, crumbs }) {
  const { pid } = useApp();
  const [a, b] = level === "path" ? scope.slice(5).split("~") : [];
  const res = useFetch(() => level === "path" ? api.p(pid).path(a, b).then(r => ({ nodes: r.nodes || [], links: r.links || [], path: r.path || [], text: pathText(r) }))
    : api.p(pid).graphData(scope, level === "all" ? 600 : 500), [pid, scope]);
  const [hideKind, setHideKind] = useState(() => new Set(["rationale"]));
  const [hover, setHover] = useState(null);
  const [ready, setReady] = useState(false);
  const [loadErr, setLoadErr] = useState(null);
  const cyRef = useRef(null), el = useRef(null);
  const data = res.data;

  useEffect(() => { if (level === "around" && data) onAround(data.nodes.find(n => n.id === scope.slice(7))?.label || ""); }, [data]);
  // Colour by file inside one community; by area everywhere else.
  const ROLES = [["function", "Functions"], ["method", "Methods"], ["class", "Classes and types"], ["file", "Files and modules"], ["document", "Documents"], ["other", "Other"]];
  const roleOf = n => n.kind === "document" || n.kind === "section" ? "document" : String(n.label).startsWith(".") && String(n.label).endsWith("()") ? "method"
    : String(n.label).endsWith("()") ? "function" : n.label === base(n.file) || /\.[a-z]{1,4}$/.test(n.label) ? "file" : /^[A-Z]/.test(n.label) ? "class" : "other";
  const byRole = useMemo(() => {
    if (!data || level !== "community") return false;
    const counts = new Map(); data.nodes.forEach(n => counts.set(n.file, (counts.get(n.file) || 0) + 1));
    return Math.max(0, ...counts.values()) / (data.nodes.length || 1) > 0.7;
  }, [data, level]);
  const colorFor = useMemo(() => {
    if (!data) return () => "";
    if (byRole) { const idx = Object.fromEntries(ROLES.map(([k], i) => [k, [1, 3, 4, 0, 5, 11][i]])); return (V, n) => V.c[idx[roleOf(n)]] || V.other; }
    if (level === "community") {
      const counts = new Map(); data.nodes.forEach(n => counts.set(n.file, (counts.get(n.file) || 0) + 1));
      const rank = new Map([...counts].sort((x, y) => y[1] - x[1]).map(([f], i) => [f, i]));
      return (V, n) => { const r = rank.get(n.file); return r != null && r < 12 ? V.c[r] : V.other; };
    }
    return (V, n) => { const m = R?.byCid.get(String(n.community)); return m ? V.c[m.colorIndex] : V.other; };
  }, [data, R, level, byRole]);
  const legend = useMemo(() => {
    if (!data) return [];
    if (byRole) {
      const counts = new Map(); data.nodes.forEach(n => counts.set(roleOf(n), (counts.get(roleOf(n)) || 0) + 1));
      return ROLES.filter(([k]) => counts.get(k)).map(([k, l], i) => ({ label: l, n: counts.get(k), color: PALETTE[[1, 3, 4, 0, 5, 11][ROLES.findIndex(r => r[0] === k)]] }));
    }
    if (level === "community") {
      const counts = new Map(); data.nodes.forEach(n => counts.set(n.file, (counts.get(n.file) || 0) + 1));
      return [...counts].sort((x, y) => y[1] - x[1]).slice(0, 12).map(([f, n], i) => ({ label: base(f) || "(no file)", title: f, n, color: PALETTE[i], find: f }));
    }
    const counts = new Map(); data.nodes.forEach(n => { const m = R?.byCid.get(String(n.community)); const k = m ? m.regionLabel : "other"; counts.set(k, (counts.get(k) || 0) + 1); });
    return [...counts].sort((x, y) => y[1] - x[1]).slice(0, 12).map(([k, n]) => { const r = R?.regions.find(x => x.label === k); return { label: k, n, color: r ? r.color : "var(--c-other)" }; });
  }, [data, R, level, byRole]);
  const prio = useRef(new Set());
  const relabel = useRef(() => {});

  useEffect(() => {
    if (!data || !el.current) return;
    let cy = null, dead = false;
    setReady(false);
    loadCytoscape().then(() => {
      if (dead) return;
      const V = themeColors();
      const maxDeg = Math.max(1, ...data.nodes.map(n => n.degree || 0));
      const elements = [
        ...data.nodes.map(n => ({ data: { id: n.id, label: n.label, short: "", kind: n.kind, file: n.file, community: n.community, degree: n.degree,
          color: colorFor(V, n), shape: shapeOf(n), size: 9 + 27 * Math.sqrt((n.degree || 0) / maxDeg) } })),
        ...data.links.filter(l => data.nodes.some(n => n.id === l.source) && data.nodes.some(n => n.id === l.target)).map((l, i) => ({ data: { id: "e" + i, source: l.source, target: l.target, rel: l.rel } })),
      ];
      cy = window.cytoscape({ container: el.current, elements, style: cyStyle(V), minZoom: 0.05, maxZoom: 4, boxSelectionEnabled: false, pixelRatio: "auto",
        hideEdgesOnViewport: data.nodes.length > 450 });
      cyRef.current = cy;
      cy.on("tap", "node", e => setQ({ node: e.target.id() }));
      cy.on("tap", e => { if (e.target === cy) setQ({ node: null }); });
      cy.on("dbltap", "node", e => setQ({ scope: "around:" + e.target.id(), node: e.target.id() }));
      let raf = 0, hoverSet = new Set(), hoverId = null, fitZoom = 0;
      // About 30 labels at the fitted zoom; more as the reader zooms in and nodes spread apart.
      relabel.current = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => {
        if (cy.destroyed()) return;
        const ratio = fitZoom ? Math.max(1, cy.zoom() / fitZoom) : 1;
        const force = new Set(cy.nodes(".sel, .path").map(n => n.id())); if (hoverId) force.add(hoverId);
        placeLabels(cy, new Set([...prio.current, ...hoverSet]), level === "path" ? 999 : Math.min(160, Math.round(30 * ratio * ratio)), force);
      }); };
      cy.on("mouseover", "node", e => { setHover(e.target.data()); hoverId = e.target.id(); hoverSet = new Set(e.target.closedNeighborhood().nodes().map(n => n.id())); relabel.current(); });
      cy.on("mouseout", "node", () => { setHover(null); hoverId = null; hoverSet = new Set(); relabel.current(); });
      cy.on("zoom pan", () => relabel.current());
      applyFilters(cy, new Set(), hideKind);
      const lay = level === "path" ? cy.elements().layout({ name: "breadthfirst", directed: false, spacingFactor: 1.4, animate: !reducedMotion(), fit: true, padding: 40 })
        : arrangeLayout(cy, data.nodes.length);
      lay.on("layoutstop", () => {
        if (level === "path" && cy.zoom() > 1.1) { cy.zoom(1.1); cy.center(); }
        fitZoom = cy.zoom(); relabel.current();
      });
      lay.run();
      relabel.current();
      setReady(true);
    }, e => setLoadErr(e));
    const off = onThemeChange(() => {
      if (!cy) return;
      const V = themeColors();
      cy.batch(() => cy.nodes().forEach(n => n.data("color", colorFor(V, { file: n.data("file"), community: n.data("community") }))));
      cy.style(cyStyle(V));
    });
    return () => { dead = true; off(); cy && cy.destroy(); cyRef.current = null; };
  }, [data]);
  useEffect(() => { const cy = cyRef.current; if (cy && ready) applyFilters(cy, new Set(), hideKind); }, [hideKind, ready]);

  const matches = useMemo(() => {
    const s = find.trim().toLowerCase();
    if (s.length < 2 || !data) return null;
    return data.nodes.filter(n => n.label.toLowerCase().includes(s) || (n.file || "").toLowerCase().includes(s)).sort((x, y) => y.degree - x.degree);
  }, [find, data]);
  const pathIds = level === "path" ? data?.path : (path.res?.path?.length ? path.res.path : null);
  useEffect(() => {
    const cy = cyRef.current; if (!cy || !ready) return;
    const timers = [];
    const askIds = ask.res?.ids?.length ? ask.res.ids : null;
    cy.batch(() => {
      cy.elements().removeClass("faded hl sel match path");
      if (pathIds?.length) {
        const nodes = cy.collection(pathIds.map(id => cy.getElementById(id)).filter(n => n.nonempty()));
        cy.elements().not(nodes).not(nodes.edgesWith(nodes)).addClass("faded");
        nodes.addClass("path");
        pathIds.slice(1).forEach((id, i) => {
          const e = cy.getElementById(pathIds[i]).edgesWith(cy.getElementById(id));
          if (reducedMotion()) e.addClass("path"); else timers.push(setTimeout(() => e.addClass("path"), 160 * (i + 1)));
        });
      } else if (askIds || matches) {
        const ids = new Set(askIds || matches.map(n => n.id));
        const nodes = cy.nodes().filter(n => ids.has(n.id()));
        if (nodes.nonempty()) { cy.elements().not(nodes).not(nodes.edgesWith(nodes)).addClass("faded"); nodes.addClass("match"); nodes.edgesWith(nodes).addClass("hl"); }
      }
      if (selId) {
        const n = cy.getElementById(selId);
        if (n.nonempty()) {
          if (!pathIds && !askIds && !matches) { const nb = n.closedNeighborhood(); cy.elements().not(nb).addClass("faded"); nb.addClass("hl"); }
          n.removeClass("faded").addClass("sel");
        }
      }
    });
    prio.current = new Set(cy.nodes(".sel, .hl, .match, .path").map(n => n.id()));
    relabel.current();
    return () => timers.forEach(clearTimeout);
  }, [selId, pathIds, ask.res, matches, ready]);
  useEffect(() => {
    const cy = cyRef.current; if (!cy || !ready || !selId) return;
    const n = cy.getElementById(selId);
    if (n.nonempty()) setTimeout(() => cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 1.1) }, { duration: reducedMotion() ? 0 : 420, easing: "ease-out-cubic" }), 700);
  }, [selId, ready]);

  const detail = useFetch(() => selId ? api.p(pid).node(selId) : Promise.resolve(null), [pid, selId]);
  const kinds = useMemo(() => { const c = {}; (data?.nodes || []).forEach(n => { c[n.kind] = (c[n.kind] || 0) + 1; }); return Object.entries(c).sort((x, y) => y[1] - x[1]); }, [data]);
  const V = themeColors();
  const side = html`<div class="gfilters">
    <div class="gf-h"><h3>${byRole ? "What the nodes are" : level === "community" ? "Files" : "Areas"}</h3></div>
    <ul class="legendlist">${legend.map(l => html`<li title=${l.title || l.label}><span class="sw" style=${{ background: l.color }}></span><span class="clip">${l.label}</span><span class="n num">${l.n}</span></li>`)}</ul>
    ${kinds.length > 1 ? html`<div class="gf-h" style="margin-top:14px"><h3>Kinds</h3></div>
      <div class="chips">${kinds.map(([k, n]) => html`<button class="chip vis" aria-pressed=${!hideKind.has(k)} onClick=${() => { const s = new Set(hideKind); s.has(k) ? s.delete(k) : s.add(k); setHideKind(s); }}>${KIND_LABEL[k] || k}<span class="n">${n}</span></button>`)}</div>` : null}
  </div>`;
  return html`
    ${narrow ? html`<details class="gfold"><summary>${level === "community" ? "Files" : "Areas"} and kinds</summary>${side}</details>` : html`<aside class="gside">${side}</aside>`}
    <div class="gstage">
      ${crumbs}
      <div class="gbar">
        <button class="btn xs" onClick=${() => { const cy = cyRef.current; if (!cy) return; const l = arrangeLayout(cy, data?.nodes.length || 0); l.on("layoutstop", () => relabel.current()); l.run(); }} title="Run the layout again">Re-arrange</button>
        <button class="btn xs" onClick=${() => cyRef.current?.animate({ fit: { padding: 30 } }, { duration: 300 })}><${Icon} name="fit" size="14"/>Fit</button>
      </div>
      <div class="cy" ref=${el} role="img" aria-label=${`Graph with ${data ? data.nodes.length : 0} nodes. Use the search box or the inspector to move through it with a keyboard.`}></div>
      ${!ready && !loadErr && !res.error ? html`<div class="gloading"><${Skeleton} rows="3"/><span class="sub">${data ? "Laying out…" : "Loading…"}</span></div>` : null}
      ${loadErr || res.error ? html`<div class="gloading"><p class="err">${(loadErr || res.error).message}</p></div>` : null}
      ${hover ? html`<div class="hoverread"><b>${hover.label}</b><span class="path">${hover.file}</span><span class="sub">${plural(hover.degree || 0, "link")}</span></div>` : null}
      <div class="gstatus sub">${data ? level === "path" ? data.text : html`${fmt(data.nodes.length)}${data.truncated ? ` of ${fmt(data.total || data.nodes.length)}` : ""} nodes${data.truncated ? ", most connected first" : ""}. Labels are placed where they fit, most connected first; zoom in for more, hover a node to name its neighbours. Double-click a node to see only its neighbourhood.` : ""}</div>
    </div>
    <aside class="ginspect">
      ${selId ? html`<${Inspector} res=${detail} pid=${pid} commName=${c => R?.byCid.get(String(c))?.display} onPick=${id => !id ? setQ({ node: null }) : data?.nodes.some(n => n.id === id) ? setQ({ node: id }) : drillToNode(id)} setPath=${setPath} onFocus=${id => setQ({ scope: "around:" + id, node: id })}/>`
        : html`<div class="panel ghint"><h3>${level === "community" ? "Inside this community" : level === "path" ? "The path" : level === "around" ? "A neighbourhood" : "Every node"}</h3>
            <p class="sub">${level === "path" ? "Each step is one link in the code. Click a node for details." : "Click a node to see what it is, what connects to it and why it exists. Double-click to focus on its neighbourhood."}</p>
            ${matches?.length ? html`<ul class="nblist">${matches.slice(0, 8).map(n => html`<li><button onClick=${() => setQ({ node: n.id })}><span class="sw" style=${{ background: colorFor(V, n) }}></span><span class="clip">${n.label}</span><span class="sub path clip">${base(n.file)}</span></button></li>`)}</ul>` : null}</div>`}
      <${PathFinder} path=${path} setPath=${setPath} onFind=${onFind}/>
    </aside>`;
}

/**
 * Label placement: labels keep one on-screen size whatever the zoom, and are placed greedily by priority
 * (selection, hover neighbourhood, matches and path first, then the most connected), skipping any label
 * that would collide with one already placed. Zooming in spreads nodes apart, so more labels fit.
 */
const LABEL_PX = 11.5, CHAR_PX = 6.6, LABEL_H = 15;
function placeLabels(cy, prio, cap = 140, force = new Set()) {
  const z = cy.zoom(), ext = cy.extent();
  cy.style().selector("node").style({ "font-size": LABEL_PX / z, "text-outline-width": 3 / z, "text-margin-y": 3 / z, "text-max-width": 170 / z }).update();
  const nodes = cy.nodes().filter(n => !n.hasClass("hidden") && !n.hasClass("faded"));
  const rank = n => force.has(n.id()) ? 2 : prio.has(n.id()) ? 1 : 0;
  const order = nodes.sort((a, b) => (rank(b) - rank(a)) || ((b.data("degree") || 0) - (a.data("degree") || 0)));
  const cell = 90, grid = new Map(), boxes = [];
  const key = (x, y) => `${Math.floor(x / cell)},${Math.floor(y / cell)}`;
  const hits = b => { for (let gx = Math.floor(b[0] / cell); gx <= Math.floor((b[0] + b[2]) / cell); gx++) for (let gy = Math.floor(b[1] / cell); gy <= Math.floor((b[1] + b[3]) / cell); gy++) for (const o of grid.get(`${gx},${gy}`) || []) if (b[0] < o[0] + o[2] && o[0] < b[0] + b[2] && b[1] < o[1] + o[3] && o[1] < b[1] + b[3]) return true; return false; };
  const add = b => { boxes.push(b); for (let gx = Math.floor(b[0] / cell); gx <= Math.floor((b[0] + b[2]) / cell); gx++) for (let gy = Math.floor(b[1] / cell); gy <= Math.floor((b[1] + b[3]) / cell); gy++) { const k = `${gx},${gy}`; if (!grid.has(k)) grid.set(k, []); grid.get(k).push(b); } };
  cy.batch(() => {
    cy.nodes().forEach(n => { if (n.data("short")) n.data("short", ""); });
    for (const n of order) {
      const p = n.renderedPosition(), r = n.renderedWidth() / 2, text = n.data("label") || "";
      const pos = n.position();
      if (pos.x < ext.x1 || pos.x > ext.x2 || pos.y < ext.y1 || pos.y > ext.y2) continue;
      const w = Math.min(170, text.length * CHAR_PX) + 4;
      const b = [p.x - w / 2 - 8, p.y + r - 2, w + 16, LABEL_H + 10];
      if (!force.has(n.id()) && (hits(b) || (!prio.has(n.id()) && boxes.length >= cap))) continue;
      add(b); n.data("short", text);
    }
  });
}
function pathText(r) {
  const ids = r.path || [];
  if (r.text && (ids.length < 2) === /^no\b/i.test(r.text)) return r.text;
  if (ids.length < 2) return r.text || "No path connects these two nodes.";
  const lbl = id => (r.nodes || []).find(n => n.id === id)?.label || id;
  const steps = ids.slice(1).map((id, i) => {
    const a = ids[i], l = (r.links || []).find(x => (x.source === a && x.target === id) || (x.source === id && x.target === a));
    const rel = l ? l.rel.replace(/_/g, " ") : "links to";
    return l && l.source === id ? `${lbl(id)} ${rel} ${lbl(a)}` : `${lbl(a)} ${rel} ${lbl(id)}`;
  });
  return `${plural(ids.length - 1, "step")}: ${steps.join("; ")}.`;
}
function applyFilters(cy, hideComm, hideKind) {
  cy.batch(() => {
    cy.nodes().forEach(n => { const hide = hideComm.has(n.data("community")) || hideKind.has(n.data("kind")); n.toggleClass("hidden", hide); });
    cy.edges().forEach(e => e.toggleClass("hidden", e.source().hasClass("hidden") || e.target().hasClass("hidden")));
  });
}
function arrangeLayout(cy, n) {
  const eles = cy.elements().not(".hidden");
  const big = n > 350;
  return eles.layout({ name: "fcose", quality: big ? "default" : "proof", randomize: true, animate: !reducedMotion(), animationDuration: 900,
    animationEasing: "ease-out", fit: true, padding: 30, nodeRepulsion: () => big ? 6000 : 14000, idealEdgeLength: () => big ? 50 : 85, edgeElasticity: () => 0.35, gravity: 0.25,
    numIter: 2500, packComponents: true, nodeSeparation: big ? 60 : 110, tile: true });
}

function PathFinder({ path, setPath, onFind }) {
  return html`<div class="panel pathf">
    <h3><${Icon} name="route" size="16"/> Path between two things</h3>
    <${NodePick} label="From" value=${path.a} onPick=${a => setPath(p => ({ ...p, a, res: null }))}/>
    <${NodePick} label="To" value=${path.b} onPick=${b => setPath(p => ({ ...p, b, res: null }))}/>
    <div class="row tight" style="margin-top:8px">
      <button class="btn sm primary" disabled=${!path.a || !path.b || path.busy} onClick=${onFind}>${path.busy ? "Finding…" : "Find path"}</button>
      ${path.a || path.b ? html`<button class="btn sm ghost" onClick=${() => setPath({ a: null, b: null, res: null, busy: false })}>Clear</button>` : null}
    </div>
    ${path.res && !path.res.path?.length ? html`<p class="sub path-none" role="status">${path.res.text}</p>` : null}
  </div>`;
}
/** Pick any symbol or file in the project (server search), not only what is on screen. */
function NodePick({ label, value, onPick }) {
  const { pid } = useApp();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState([]);
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const t = useRef(0);
  useEffect(() => { setQ(value ? value.label : ""); }, [value?.id]);
  const search = v => {
    clearTimeout(t.current);
    if (v.trim().length < 2) { setHits([]); return; }
    t.current = setTimeout(() => api.p(pid).search(v.trim(), "symbol,file", 8).then(setHits, () => setHits([])), 160);
  };
  const choose = async h => {
    setOpen(false);
    const raw = String(h.id);
    if (raw.startsWith("file:")) {
      try { const d = await api.p(pid).graphData(raw, 60); const own = d.nodes.filter(n => n.file === raw.slice(5)); const f = own.find(n => n.label === base(raw.slice(5))) || own[0]; if (f) onPick({ id: f.id, label: f.label }); }
      catch (e) { /* no graph node for this file */ }
    } else onPick({ id: raw.replace(/^symbol:/, ""), label: h.title });
  };
  return html`<label class="field npick"><span>${label}</span>
    <div style="position:relative">
      <input class="input" value=${q} placeholder="A function, class or file" autocomplete="off" role="combobox" aria-expanded=${open && hits.length > 0}
        onInput=${e => { setQ(e.target.value); setOpen(true); setSel(0); search(e.target.value); }} onBlur=${() => setTimeout(() => setOpen(false), 150)}
        onKeyDown=${e => { if (e.key === "ArrowDown") { e.preventDefault(); setSel(Math.min(sel + 1, hits.length - 1)); } else if (e.key === "ArrowUp") { e.preventDefault(); setSel(Math.max(0, sel - 1)); } else if (e.key === "Enter" && hits[sel]) { e.preventDefault(); choose(hits[sel]); } }}/>
      ${open && hits.length ? html`<div class="pop menu" style="top:calc(100% + 4px);left:0;right:0" role="listbox">${hits.map((h, i) => html`<button type="button" role="option" aria-selected=${i === sel} onMouseDown=${e => { e.preventDefault(); choose(h); }}>
        <span class="clip" style="flex:1">${h.title}</span><span class="sub clip path" style="max-width:45%">${h.kind === "file" ? "file" : base(h.path)}</span></button>`)}</div>` : null}
    </div></label>`;
}

/** One readable sentence about a node; the engine's own text is a terminal dump, so build it from the neighbours. */
function explain(d, cname) {
  const n = d.node, engineName = n.community_name;
  // Communities go by the name the map shows everywhere else (areas, breadcrumbs), not the engine's label.
  const t = String(d.explain_text || "").trim().split(`“${engineName}”`).join(`“${cname || engineName}”`).split(`"${engineName}"`).join(`“${cname || engineName}”`);
  // Use the server's sentence only when it reads as prose; engine output (listings, "Ambiguous…", "Retry with…") never shows raw.
  const raw = /^(Node:|Ambiguous|No (node|match)|Error|Usage)/i.test(t) || /path::symbol|Retry with|\n\s{2,}|\[(EXTRACTED|INFERRED)\]/.test(t);
  if (t && !raw && t.length < 700) return t;
  const nb = d.neighbours || [];
  const cnt = (dir, rels) => nb.filter(x => x.direction === dir && rels.includes(x.rel)).length;
  const callers = cnt("in", ["calls", "indirect_call"]), importers = cnt("in", ["imports", "imports_from"]), users = cnt("in", ["uses", "inherits"]), refs = cnt("in", ["references"]);
  const calls = cnt("out", ["calls", "indirect_call"]), methods = cnt("out", ["method", "contains", "defines"]);
  const loc = n.loc || n.location;
  const parts = [`${n.label} is ${n.kind === "document" ? "a document" : n.kind === "concept" ? "a concept" : n.kind === "module" && !n.file ? "an imported module" : "code"}${n.file ? ` in ${n.file}${loc ? `, line ${String(loc).replace(/^L/, "")}` : ""}` : ""}${cname || engineName ? `, in the “${cname || engineName}” community` : ""}.`];
  const ins = [callers && plural(callers, "caller"), importers && plural(importers, "importer"), users && `${users} ${users === 1 ? "thing uses" : "things use"} it`, refs && plural(refs, "document reference")].filter(Boolean);
  if (ins.length) parts.push(`It has ${ins.join(", ")}.`);
  if (methods) parts.push(`It contains ${plural(methods, "member")}.`);
  if (calls) parts.push(`It calls ${plural(calls, "other function")}.`);
  if (!nb.length) parts.push("Nothing else in the graph connects to it.");
  return parts.join(" ");
}
function Inspector({ res, pid, onPick, setPath, onFocus, commName }) {
  return html`<div class="panel inspector"><${Load} res=${res} rows="6">${d => {
    if (!d) return null;
    const n = d.node;
    const cname = (n.community != null && commName?.(n.community)) || n.community_name;
    const groups = new Map();
    for (const x of d.neighbours) { const k = (REL[x.rel] || [x.rel, x.rel + " (in)"])[x.direction === "out" ? 0 : 1]; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(x); }
    const target = n.kind === "file" || (n.file && base(n.file) === n.label) ? n.file : "symbol:" + n.id;
    return html`<div>
      <div class="row" style="justify-content:space-between;align-items:flex-start;gap:8px"><h3 class="insp-t">${n.label}</h3><button class="icon-btn sm" aria-label="Close" onClick=${() => onPick(null)}><${Icon} name="x" size="15"/></button></div>
      <div class="row tight" style="margin:4px 0 8px"><${Tag} tone="code">${n.kind}</${Tag}>${cname ? html`<${Tag}>${cname}</${Tag}>` : null}<span class="sub">${plural(d.neighbours.length, "link")}</span></div>
      ${n.file ? html`<a class="path small" href=${link.impact(pid, n.file)}>${n.file}${n.loc || n.location ? ":" + String(n.loc || n.location).replace(/^L/, "") : ""}</a>` : null}
      <p class="explain">${explain(d, cname)}</p>
      ${d.rationale?.length ? html`<blockquote class="why">${d.rationale.slice(0, 2).map(r => html`<p>“${typeof r === "string" ? r : r.text}”</p>`)}</blockquote>` : null}
      <div class="row tight" style="margin:10px 0 4px">
        <a class="btn sm primary" href=${link.impact(pid, target)}>Impact</a>
        <button class="btn sm" onClick=${() => onFocus(n.id)} title="Show only this node and its neighbourhood">Focus</button>
        <button class="btn sm" onClick=${() => setPath(p => ({ ...p, a: n, res: null }))}>Path from</button>
        <button class="btn sm" onClick=${() => setPath(p => ({ ...p, b: n, res: null }))}>Path to</button>
      </div>
      ${[...groups].map(([k, xs]) => html`<div class="nbgroup"><h4>${k} <span class="sub num">${xs.length}</span></h4>
        <ul class="nblist">${xs.slice(0, 8).map(x => html`<li><button onClick=${() => onPick(x.id)} title=${x.file}><span class="clip">${x.label}</span><span class="sub path clip">${base(x.file)}</span>${x.provenance && x.provenance !== "EXTRACTED" ? html`<${Tag}>inferred</${Tag}>` : null}</button></li>`)}
          ${xs.length > 8 ? html`<li class="sub" style="padding:3px 8px">and ${xs.length - 8} more</li>` : null}</ul></div>`)}
    </div>`;
  }}</${Load}></div>`;
}

/* ------------------------------------------------------------------ other tabs */
function Architecture() {
  const { pid } = useApp();
  const res = useFetch(() => api.p(pid).architecture(), [pid]);
  return html`<${Load} res=${res} rows="6" block>${g => {
    const unit = g.level === "folder" ? "folder" : "file";
    const small = g.nodes.filter(n => (n.cycle || []).length && (n.cycle || []).length <= 8);
    const big = Math.max(0, ...g.nodes.map(n => (n.cycle || []).length));
    const layers = new Set(g.nodes.map(n => n.layer)).size;
    return html`<div>
      <p class="sub" style="margin:-6px 0 14px;max-width:84ch">Every ${unit} sits above the code it uses, in ${plural(layers, "layer")}: entry points at the top, foundations at the bottom.
        ${small.length ? ` ${plural(small.length, unit)} ${small.length === 1 ? "is" : "are"} in a small import cycle (dashed red).` : ""}
        ${big > 8 ? ` ${big} ${unit}s import each other in one large cycle, so most of the code moves together.` : !small.length ? " There are no import cycles." : ""}
 Hover a ${unit} to trace its links; click to ${g.level === "folder" ? "explore it in the graph" : "see what breaks if it changes"}.</p>
      <${ArchMap} g=${g} pid=${pid}/></div>`;
  }}</${Load}>`;
}

function EngineViews() {
  const { pid, route } = useApp();
  const res = useFetch(() => api.p(pid).views(), [pid]);
  const cur = route.q.get("view");
  return html`<${Load} res=${res} rows="3">${views => {
    if (!views.length) return html`<${Empty} title="No engine views yet"><p>The graph engine writes interactive views (full graph, call flow, file tree) on each sync. Run a sync to create them.</p></${Empty}>`;
    const v = views.find(x => x.kind === cur) || views[0];
    const src = api.p(pid).viewUrl(v.kind);
    return html`<div>
      <div class="row" style="margin:-6px 0 12px;justify-content:space-between">
        <div class="seg" role="tablist" aria-label="Engine views">${views.map(x => html`<a role="tab" aria-selected=${x.kind === v.kind} aria-current=${x.kind === v.kind} href=${link.project(pid, "map", ["views"], { view: x.kind })}>${x.title}</a>`)}</div>
        <a class="btn sm" href=${src} target="_blank" rel="noopener"><${Icon} name="ext" size="15"/>Open in a new tab</a>
      </div>
      <p class="sub" style="margin:0 0 10px">Drawn by the graph engine itself, isolated from this page. If it stays blank, open it in a new tab.</p>
      <iframe class="engine-frame" title=${v.title} src=${src} sandbox="allow-scripts allow-popups" referrerpolicy="no-referrer" loading="lazy"></iframe>
    </div>`;
  }}</${Load}>`;
}

function Wiki() {
  const { pid, route, can } = useApp();
  const slug = route.rest[1];
  const list = useFetch(() => api.p(pid).wiki(), [pid]);
  const page = useFetch(() => slug ? api.p(pid).wikiPage(slug) : Promise.resolve(null), [pid, slug]);
  const [writing, setWriting] = useState({ busy: false, error: null });
  useEffect(() => { if (!slug && list.data?.length) replace(link.project(pid, "map", ["wiki", list.data[0].slug])); }, [slug, list.data]);
  const write = async () => {
    setWriting({ busy: true, error: null });
    try {
      const r = await api.p(pid).writeWiki();
      if (!r.ok) return setWriting({ busy: false, error: failText(String(r.log || "").trim().split("\n").pop()) || "The wiki couldn't be written." });
      const pages = await api.p(pid).wiki();
      setWriting({ busy: false, error: pages.length ? null : "The wiki was written, but no pages came back for this project." });
      if (pages.length) list.setData(pages);
    } catch (e) { setWriting({ busy: false, error: e.message }); }
  };
  return html`<${Load} res=${list} rows="6">${pages => {
    if (!pages.length) return html`<${Empty} title="No wiki pages yet" actions=${can.sync ? html`<button class="btn primary" disabled=${writing.busy} onClick=${write}>${writing.busy ? "Writing the wiki…" : "Write the wiki now"}</button>` : null}>
      <p>The wiki has one page per community of the graph. Each sync writes it when the map changes.</p>
      ${writing.error ? html`<p class="err" role="alert">${writing.error}</p>` : null}</${Empty}>`;
    const cur = slug || pages[0].slug;
    return html`<div class="docs">
      <nav class="doclist" aria-label="Wiki pages"><ol>${pages.map(p => html`<li><a href=${link.project(pid, "map", ["wiki", p.slug])} aria-current=${p.slug === cur ? "page" : undefined}>${p.title}</a></li>`)}</ol></nav>
      <article class="docbody">${page.data ? html`<${Markdown} src=${page.data.markdown}/>` : page.error ? html`<p class="err">${page.error.message}</p>` : html`<${Skeleton} rows="8"/>`}</article>
    </div>`;
  }}</${Load}>`;
}

function Report() {
  const { pid } = useApp();
  const res = useFetch(() => api.p(pid).report(), [pid]);
  return html`<${Load} res=${res} rows="10">${r => r.markdown ? html`<article class="report"><${Markdown} src=${r.markdown}/></article>`
    : html`<${Empty} title="No report yet"><p>The graph report summarises communities, hubs and surprising connections. It is written on sync.</p></${Empty}>`}</${Load}>`;
}
