#!/usr/bin/env node
// UI smoke check: visits every page of the Cairn UI in headless Chrome, at desktop and phone width, and fails
// on console errors, failed requests, horizontal overflow, error states and pages that never finish loading.
//
//   node tests/ui/smoke.mjs --base http://127.0.0.1:4747     against a running server (cairn ui / cairn serve)
//   node tests/ui/smoke.mjs --mock                           against the UI's sample data; no Cairn server needed
//
// Options: --email/--password (team mode sign-in), --widths 1440,390, --project <id>, --chrome <path>
// (or CHROME_PATH), --shots <dir> (a screenshot per page), --verbose. Needs Node 22+ (global WebSocket and fetch)
// and Chrome or Chromium; without them it says why and exits 0. Exits 1 when any page has a problem.
import { spawn, execFileSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const args = parseArgs(process.argv.slice(2));
const UI_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/cairn/ui");
const WIDTHS = String(args.widths || "1440,390").split(",").map(Number).filter(Boolean);
const SETTLE_MS = 700, PAGE_TIMEOUT_MS = 20000;
const sleep = ms => new Promise(r => setTimeout(r, ms));

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const [k, v] = a.slice(2).split("=");
    if (v !== undefined) out[k] = v;
    else if (argv[i + 1] && !argv[i + 1].startsWith("--")) out[k] = argv[++i];
    else out[k] = true;
  }
  return out;
}

function skip(why) {
  console.log(`SKIP: ${why}`);
  process.exit(0);
}

function findChrome() {
  const given = args.chrome || process.env.CHROME_PATH;
  if (given) return fs.existsSync(given) ? given : null;
  const fixed = {
    darwin: ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium",
      "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"],
    win32: [`${process.env.PROGRAMFILES}\\Google\\Chrome\\Application\\chrome.exe`, `${process.env["PROGRAMFILES(X86)"]}\\Google\\Chrome\\Application\\chrome.exe`,
      `${process.env.LOCALAPPDATA}\\Google\\Chrome\\Application\\chrome.exe`],
  }[process.platform] || [];
  for (const p of fixed) if (p && fs.existsSync(p)) return p;
  for (const name of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"]) {
    try { const p = execFileSync(process.platform === "win32" ? "where" : "which", [name], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).split("\n")[0].trim(); if (p) return p; }
    catch (e) { /* not on PATH */ }
  }
  return null;
}

/** Serve src/cairn/ui the way the Cairn server does (/ is the page, /ui/* its files), for --mock. */
function serveUi() {
  const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
    ".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png" };
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    const rel = url.pathname === "/" ? "index.html" : url.pathname.startsWith("/ui/") ? url.pathname.slice(4) : null;
    const file = rel && path.resolve(UI_DIR, decodeURIComponent(rel));
    if (!file || !file.startsWith(UI_DIR) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end("not found"); return; }
    res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise(r => server.listen(0, "127.0.0.1", () => r(server)));
}

/** Launch Chrome on a free debugging port and return its browser WebSocket URL. */
function launchChrome(bin) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "cairn-ui-smoke-"));
  const proc = spawn(bin, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check",
    "--disable-gpu", "--disable-extensions", "--hide-scrollbars", "--mute-audio", "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  return new Promise((resolve, reject) => {
    let buf = "";
    const t = setTimeout(() => reject(new Error("Chrome didn't open a debugging port within 20 seconds.")), 20000);
    proc.stderr.on("data", d => { buf += d; const m = buf.match(/DevTools listening on (ws:\/\/\S+)/); if (m) { clearTimeout(t); resolve({ proc, ws: m[1], profile }); } });
    proc.on("exit", code => { clearTimeout(t); reject(new Error(`Chrome exited (${code}) before it was ready.`)); });
  });
}

/** One browser connection; each page is a flattened CDP session on it. */
async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = () => j(new Error("Couldn't connect to Chrome.")); });
  let id = 0;
  const pending = new Map(), listeners = new Map();
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result); }
    else if (m.method && m.sessionId && listeners.has(m.sessionId)) listeners.get(m.sessionId)(m);
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const i = ++id; pending.set(i, { resolve, reject });
    ws.send(JSON.stringify({ id: i, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  return { ws, send, listeners };
}

async function openPage(B, width) {
  const mobile = width < 700;
  const { targetId } = await B.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await B.send("Target.attachToTarget", { targetId, flatten: true });
  const send = (m, p) => B.send(m, p, sessionId);
  const page = { errors: [], failed: [], inflight: new Map(), lastActivity: Date.now(), send, targetId };
  B.listeners.set(sessionId, m => {
    const p = m.params;
    switch (m.method) {
      case "Runtime.exceptionThrown": page.errors.push("uncaught " + (p.exceptionDetails.exception?.description || p.exceptionDetails.text).split("\n")[0]); break;
      case "Runtime.consoleAPICalled": if (p.type === "error" || p.type === "assert") page.errors.push(`console.${p.type}: ` + p.args.map(a => a.value ?? a.description ?? "").join(" ").slice(0, 300)); break;
      case "Log.entryAdded": if (p.entry.level === "error" && p.entry.source !== "network") page.errors.push(`${p.entry.source}: ${p.entry.text}`.slice(0, 300)); break;
      case "Network.requestWillBeSent":
        // Documents (the page itself, engine views in their sandboxed frame) finish in other sessions; the
        // skeleton check covers them. Live streams never finish.
        if (p.type !== "EventSource" && p.type !== "Document" && !p.request.url.startsWith("data:")) { page.inflight.set(p.requestId, { url: p.request.url, method: p.request.method }); page.lastActivity = Date.now(); }
        break;
      case "Network.responseReceived":
        if (p.response.status >= 400 && p.type !== "EventSource") page.failed.push(`${p.response.status} ${page.inflight.get(p.requestId)?.method || "GET"} ${p.response.url}`);
        break;
      case "Network.loadingFinished": page.inflight.delete(p.requestId); page.lastActivity = Date.now(); break;
      case "Network.loadingFailed": {
        const r = page.inflight.get(p.requestId); page.inflight.delete(p.requestId); page.lastActivity = Date.now();
        if (r && !p.canceled && p.errorText !== "net::ERR_ABORTED" && p.type !== "EventSource") page.failed.push(`${p.errorText} ${r.method} ${r.url}`);
        break;
      }
    }
  });
  for (const d of ["Runtime", "Log", "Network", "Page"]) await send(`${d}.enable`);
  await send("Emulation.setDeviceMetricsOverride", { width, height: mobile ? 844 : 900, deviceScaleFactor: 1, mobile });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  page.eval = async expr => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  page.drain = () => { const out = { errors: page.errors.splice(0), failed: page.failed.splice(0) }; return out; };
  return page;
}

/** Wait until the network is quiet and no skeleton or "laying out" placeholder is left on the page. */
async function settle(page) {
  const start = Date.now();
  await sleep(250);
  while (Date.now() - start < PAGE_TIMEOUT_MS) {
    const quiet = page.inflight.size === 0 && Date.now() - page.lastActivity > SETTLE_MS;
    const busy = await page.eval(`!!document.querySelector('#app .boot, main [aria-busy="true"], main .gloading')`).catch(() => true);
    if (quiet && !busy) return true;
    await sleep(150);
  }
  return false;
}

// What each page must not show: a broken layout, an error state, or a view left loading.
const CHECK = `(() => {
  const d = document.documentElement, m = document.getElementById("main");
  const out = { docOverflow: d.scrollWidth - d.clientWidth, mainOverflow: m ? m.scrollWidth - m.clientWidth : 0, offenders: [], states: [], h1: document.querySelector("main h1")?.textContent || "" };
  if (m && out.mainOverflow > 1) {
    const edge = m.getBoundingClientRect().right;
    for (const e of m.querySelectorAll("*")) {
      const r = e.getBoundingClientRect();
      if (!r.width || r.right <= edge + 1) continue;
      let p = e.parentElement, clipped = false;
      while (p && p !== m) { if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) { clipped = true; break; } p = p.parentElement; }
      if (!clipped) out.offenders.push(e.tagName.toLowerCase() + (typeof e.className === "string" && e.className ? "." + e.className.trim().split(/\\s+/).join(".") : "") + " (" + Math.round(r.width) + "px)");
      if (out.offenders.length >= 3) break;
    }
  }
  if (document.getElementById("signin-h")) out.states.push("the sign-in page instead of the view");
  for (const h of document.querySelectorAll(".empty h3")) if (/^(Couldn't load|You can't open|This page doesn't exist|No such team|Can't reach)/.test(h.textContent)) out.states.push(h.textContent.trim());
  for (const e of document.querySelectorAll("main p.err, main .gloading .err")) if (e.textContent.trim()) out.states.push("error: " + e.textContent.trim().slice(0, 160));
  if (document.querySelector(".mockbadge") && /Partly/.test(document.querySelector(".mockbadge").textContent)) out.states.push("parts of the page fell back to sample data: the server is missing routes");
  return out;
})()`;

/** Routes to visit, discovered through the page's own data layer (the same calls the views make). */
async function discover(page, pid) {
  return page.eval(`(async () => {
    const { api } = await import("/ui/app/api.js");
    const s = await api.session();
    const projects = s.projects || [];
    const p = projects.find(x => x.id === ${JSON.stringify(pid || "")}) || projects[0];
    if (!p) return { error: "The server has no projects to visit." };
    const P = api.p(p.id), safe = f => f().catch(() => null);
    const [ov, specs, sess] = await Promise.all([safe(() => P.overview()), safe(() => P.specs()), safe(() => P.sessionList({ limit: 1 }))]);
    const hub = ov?.hubs?.[0];
    return { pid: p.id, tid: p.team_id || s.teams?.[0]?.id || null, mode: s.mode,
      teamRole: (s.teams || []).find(t => t.id === p.team_id)?.role || null,
      target: hub ? (hub.file && hub.file.split("/").pop() === hub.label ? hub.file : "symbol:" + hub.id) : null,
      feature: specs?.features?.[0]?.id || null, session: (sess?.items || sess || [])[0]?.id || null };
  })()`);
}

function routesFor(d) {
  const e = encodeURIComponent, P = v => `#/p/${e(d.pid)}/${v}`;
  const r = [P("overview"), P("impact"), P("map"), P("map/architecture"), P("map/views"), P("map/wiki"), P("map/report"),
    P("specs"), P("sessions"), P("timeline"), P("memory"), P("settings"), "#/account"];
  if (d.target) r.splice(2, 0, P(`impact/${e(d.target)}`));
  if (d.feature) r.splice(r.indexOf(P("specs")) + 1, 0, P(`specs/${e(d.feature)}/tasks`), P(`specs/${e(d.feature)}/docs`));
  if (d.session) r.splice(r.indexOf(P("sessions")) + 1, 0, P(`sessions/${e(d.session)}`));
  if (d.tid) {
    r.push(`#/team/${e(d.tid)}/projects`, `#/team/${e(d.tid)}/tokens`);
    if (["owner", "admin"].includes(d.teamRole) || d.mode === "local") r.push(`#/team/${e(d.tid)}/audit`);
    if (d.mode === "team") r.push(`#/team/${e(d.tid)}/members`);
  }
  return r;
}

async function signIn(page) {
  if (!args.email || !args.password) return "The server needs a sign-in: pass --email and --password.";
  const r = await page.eval(`fetch("/api/auth/login", { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
    body: JSON.stringify(${JSON.stringify({ email: args.email, password: args.password })}) }).then(async r => { await r.text(); return r.status; })`);
  return r === 200 ? null : `Signing in as ${args.email} failed (${r}).`;
}

async function main() {
  if (typeof WebSocket === "undefined" || typeof fetch === "undefined") skip(`needs Node 22 or newer (this is ${process.version}).`);
  if (!args.base && !args.mock) { console.error("Usage: node tests/ui/smoke.mjs --base http://127.0.0.1:4747   or   --mock"); process.exit(2); }
  const bin = findChrome();
  if (!bin) skip("Chrome or Chromium not found. Install it or set CHROME_PATH.");
  let staticServer = null, base = args.base ? String(args.base).replace(/\/+$/, "") : null;
  if (args.mock) { staticServer = await serveUi(); base = `http://127.0.0.1:${staticServer.address().port}`; }
  const entry = base + (args.mock ? "/?mock=1" : "/");
  if (!args.mock) {
    try { const h = await fetch(base + "/api/health"); if (!h.ok) throw new Error(`answered ${h.status}`); }
    catch (e) { console.error(`FAIL: no Cairn server at ${base} (${e.message}). Start one with \`cairn ui\`, or run with --mock.`); process.exit(1); }
  }
  if (args.shots) fs.mkdirSync(args.shots, { recursive: true });
  const chrome = await launchChrome(bin);
  const B = await connect(chrome.ws);
  const problems = [];
  let visited = 0;
  try {
    for (const width of WIDTHS) {
      const page = await openPage(B, width);
      await page.send("Page.navigate", { url: entry + "#/" });
      await settle(page);
      if (!args.mock && await page.eval(`fetch("/api/session", { credentials: "same-origin" }).then(async r => { await r.text(); return r.status; })`) === 401) {
        const why = await signIn(page);
        if (why) { console.error("FAIL: " + why); process.exitCode = 1; return; }
        await page.send("Page.reload", { ignoreCache: true }); await settle(page);
      }
      const d = await discover(page, args.project);
      if (d.error) { console.error("FAIL: " + d.error); process.exitCode = 1; return; }
      if (width === WIDTHS[0]) console.log(`Cairn UI smoke: ${args.mock ? "sample data" : base} (${d.mode} mode), project ${d.pid}, widths ${WIDTHS.join(" and ")}px, Chrome ${path.basename(bin)}`);
      page.drain(); page.inflight.clear();  // the start page, sign-in and discovery are not under test
      for (const route of routesFor(d)) {
        await page.eval(`location.hash = ${JSON.stringify(route)}`);
        const settled = await settle(page);
        const c = await page.eval(CHECK);
        const { errors, failed } = page.drain();
        const issues = [];
        if (!settled) issues.push(`still loading after ${PAGE_TIMEOUT_MS / 1000}s${page.inflight.size ? ": waiting on " + [...page.inflight.values()].map(r => r.url.replace(base, "")).join(", ") : ""}`);
        errors.forEach(e => issues.push("console " + e));
        failed.forEach(f => issues.push("request " + f.replace(base, "")));
        if (c.docOverflow > 1) issues.push(`page scrolls sideways by ${c.docOverflow}px`);
        if (c.mainOverflow > 1) issues.push(`content is ${c.mainOverflow}px wider than the page${c.offenders.length ? ": " + c.offenders.join(", ") : ""}`);
        c.states.forEach(s => issues.push("shows " + s));
        visited++;
        if (args.shots) {
          const shot = await page.send("Page.captureScreenshot", { format: "png" });
          fs.writeFileSync(path.join(args.shots, `${width}-${route.replace(/[^a-z0-9]+/gi, "_").replace(/^_+|_+$/g, "")}.png`), Buffer.from(shot.data, "base64"));
        }
        const label = `${String(width).padStart(4)}px  ${decodeURIComponent(route)}`;
        if (issues.length) { problems.push({ label, issues }); console.log(`FAIL ${label}`); issues.forEach(i => console.log(`       - ${i}`)); }
        else if (args.verbose) console.log(`ok   ${label}  ${c.h1 ? `(${c.h1.trim().slice(0, 50)})` : ""}`);
      }
      await B.send("Target.closeTarget", { targetId: page.targetId });
    }
  } finally {
    await Promise.race([B.send("Browser.close").catch(() => {}), sleep(2000)]);
    B.ws.close();
    chrome.proc.kill();
    setTimeout(() => fs.rmSync(chrome.profile, { recursive: true, force: true }), 500);
    staticServer?.close();
  }
  const n = problems.reduce((a, p) => a + p.issues.length, 0);
  console.log(problems.length ? `\n${problems.length} of ${visited} page visits failed (${n} problems).` : `\nAll ${visited} page visits passed.`);
  process.exitCode = problems.length ? 1 : 0;
}

main().catch(e => { console.error("FAIL: " + (e.stack || e.message)); process.exit(1); });
