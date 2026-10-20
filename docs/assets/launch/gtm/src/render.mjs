// Renders index.html frame by frame through headless Chrome (raw DevTools protocol, no libraries)
// and pipes JPEG frames into ffmpeg together with the synthesized music bed.
//
//   node render.mjs                         full render -> ../cairn-gtm.mp4, ../cairn-gtm.srt, ../cairn-gtm.vtt
//   node render.mjs --stills 3,20,35 DIR    write PNG stills at those seconds into DIR (for checking)
//
// Needs: Google Chrome, ffmpeg, and music.wav from music.py next to this file.
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, writeFile, mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../../../../.."); // repository root, so the page can load the UI fonts and screenshots
const OUT = path.resolve(HERE, "..");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const FPS = 30;
const PORT = 9341;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const TYPES = { ".html": "text/html", ".png": "image/png", ".woff2": "font/woff2", ".js": "text/javascript", ".css": "text/css" };
function serve() {
  const srv = createServer(async (req, res) => {
    const p = path.normalize(path.join(ROOT, decodeURIComponent(new URL(req.url, "http://x").pathname)));
    if (!p.startsWith(ROOT)) { res.writeHead(403).end(); return; }
    try { const body = await readFile(p); res.writeHead(200, { "content-type": TYPES[path.extname(p)] || "application/octet-stream" }).end(body); }
    catch { res.writeHead(404).end(); }
  });
  return new Promise((r) => srv.listen(0, "127.0.0.1", () => r(srv)));
}

async function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0; const pending = new Map();
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { const { r, j } = pending.get(d.id); pending.delete(d.id); d.error ? j(new Error(JSON.stringify(d.error))) : r(d.result); } };
  const send = (method, params = {}) => new Promise((r, j) => { const i = ++id; pending.set(i, { r, j }); ws.send(JSON.stringify({ id: i, method, params })); });
  return { send, close: () => ws.close() };
}

async function evaluate(c, expr) {
  const r = await c.send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}

const fmt = (s, sep) => { const ms = Math.round(s * 1000); const h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, ss = Math.floor(ms / 1000) % 60, x = ms % 1000;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}${sep}${String(x).padStart(3, "0")}`; };

async function main() {
  const args = process.argv.slice(2);
  const stills = args[0] === "--stills" ? args[1].split(",").map(Number) : null;
  const srv = await serve();
  const url = `http://127.0.0.1:${srv.address().port}/docs/assets/launch/gtm/src/index.html`;
  const profile = await mkdtemp(path.join(tmpdir(), "cairn-gtm-chrome-"));
  const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "--window-size=1920,1080",
    "--hide-scrollbars", "--force-device-scale-factor=1", "--no-first-run", "--no-default-browser-check", "--mute-audio", "about:blank"], { stdio: "ignore" });
  let target;
  for (let i = 0; i < 100 && !target; i++) {
    await sleep(150);
    try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"); } catch {}
  }
  if (!target) throw new Error("Chrome did not start");
  const c = await cdp(target.webSocketDebuggerUrl);
  try {
    await c.send("Page.enable");
    await c.send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
    await c.send("Page.navigate", { url });
    for (let i = 0; i < 100; i++) { await sleep(100); if (await evaluate(c, "document.readyState === 'complete' && typeof renderAt === 'function'").catch(() => false)) break; }
    await evaluate(c, "document.fonts.ready.then(() => Promise.all([...document.images].map(i => i.decode()))).then(() => true)");
    const fontsOk = await evaluate(c, "document.fonts.check('700 40px Overpass') && document.fonts.check('400 30px \"Overpass Mono\"')");
    if (!fontsOk) throw new Error("brand fonts did not load");
    const duration = await evaluate(c, "window.DURATION");
    const captions = await evaluate(c, "window.CAPTIONS");
    const frame = async (t, format) => {
      await evaluate(c, `renderAt(${t}); new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))`);
      const shot = await c.send("Page.captureScreenshot", format === "png" ? { format: "png" } : { format: "jpeg", quality: 94 });
      return Buffer.from(shot.data, "base64");
    };

    if (stills) {
      const dir = path.resolve(args[2] || ".");
      await mkdir(dir, { recursive: true });
      for (const s of stills) await writeFile(path.join(dir, `still-${String(Math.round(s * 1000)).padStart(6, "0")}.png`), await frame(s, "png"));
      console.log(`wrote ${stills.length} stills to ${dir}`);
      return;
    }

    // captions -> SRT and VTT, from the same list the page draws
    const srt = captions.map(([a, b, x], i) => `${i + 1}\n${fmt(a, ",")} --> ${fmt(b, ",")}\n${x}\n`).join("\n");
    const vtt = "WEBVTT\n\n" + captions.map(([a, b, x]) => `${fmt(a, ".")} --> ${fmt(b, ".")}\n${x}\n`).join("\n");
    await writeFile(path.join(OUT, "cairn-gtm.srt"), srt);
    await writeFile(path.join(OUT, "cairn-gtm.vtt"), vtt);

    const n = Math.round(duration * FPS);
    const ff = spawn("ffmpeg", ["-y", "-v", "error", "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", String(FPS), "-i", "-",
      "-i", path.join(HERE, "music.wav"), "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "slow", "-crf", "21", "-vf", "scale=in_range=pc:out_range=tv,format=yuv420p", "-color_range", "tv",
      "-tune", "animation", "-c:a", "aac", "-b:a", "160k", "-t", String(duration), "-movflags", "+faststart", path.join(OUT, "cairn-gtm.mp4")], { stdio: ["pipe", "inherit", "inherit"] });
    const done = new Promise((r, j) => ff.on("close", (code) => (code === 0 ? r() : j(new Error("ffmpeg exited " + code)))));
    for (let i = 0; i < n; i++) {
      const buf = await frame(i / FPS, "jpeg");
      if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once("drain", r));
      if (i % 150 === 0) console.log(`frame ${i}/${n}`);
    }
    ff.stdin.end();
    await done;
    console.log("wrote", path.join(OUT, "cairn-gtm.mp4"));
  } finally {
    c.close(); chrome.kill(); srv.close();
    await sleep(300); await rm(profile, { recursive: true, force: true }).catch(() => {});
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
