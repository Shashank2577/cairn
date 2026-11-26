// Render index.html to an MP4: headless Chrome draws each frame (seek(t), then a screenshot over the
// DevTools protocol), and ffmpeg only joins the frames and the music into the file.
//
//   node render.mjs --out ../cairn-explainer.mp4 --audio music.wav
//   node render.mjs --stills 3,37,58 --dir /tmp/stills          (PNG frames for checking)
//   node render.mjs --from 20 --to 30 --out part.mp4             (a section)
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, all) => (v.startsWith("--") ? [...a, [v.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : true]] : a), []));
const FPS = Number(args.fps || 30);
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9300 + Math.floor(Math.random() * 500);

const profile = mkdtempSync(join(tmpdir(), "cairn-explainer-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  "--hide-scrollbars", "--force-device-scale-factor=1", "--window-size=1920,1080", "--allow-file-access-from-files",
  "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "about:blank"], { stdio: "ignore" });

async function target() {
  for (let i = 0; i < 100; i++) {
    try { const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); const p = l.find(x => x.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch {}
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error("Chrome did not start");
}
const ws = new WebSocket(await target());
await new Promise(r => ws.addEventListener("open", r, { once: true }));
let seq = 0; const waiting = new Map(); const events = [];
ws.addEventListener("message", m => { const d = JSON.parse(m.data); if (d.id && waiting.has(d.id)) { const { ok, no } = waiting.get(d.id); waiting.delete(d.id); d.error ? no(new Error(d.error.message)) : ok(d.result); } else events.push(d); });
const cdp = (method, params = {}) => new Promise((ok, no) => { const id = ++seq; waiting.set(id, { ok, no }); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async expr => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result.value; };

await cdp("Page.enable");
await cdp("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
await cdp("Page.navigate", { url: pathToFileURL(join(here, "index.html")).href + "#t=0" });
for (let i = 0; i < 200 && !(await evaluate("document.readyState === 'complete' && !!window.seek").catch(() => false)); i++) await new Promise(r => setTimeout(r, 50));
await evaluate("window.__ready.then(() => true)");
const DURATION = await evaluate("DURATION");

async function frame(t, format = "jpeg") {
  await evaluate(`seek(${t}); new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))`);
  const r = await cdp("Page.captureScreenshot", { format, quality: format === "jpeg" ? 94 : undefined, captureBeyondViewport: false });
  return Buffer.from(r.data, "base64");
}

try {
  if (args.stills) {
    const dir = resolve(args.dir || "stills"); mkdirSync(dir, { recursive: true });
    for (const s of String(args.stills).split(",")) { writeFileSync(join(dir, `t${String(s).padStart(6, "0")}.png`), await frame(Number(s), "png")); }
    console.log("stills in", dir);
  } else {
    const from = Number(args.from || 0), to = Number(args.to || DURATION);
    const out = resolve(args.out || "out.mp4");
    const ff = ["-y", "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-"];
    if (args.audio) ff.push("-ss", String(from), "-t", String(to - from), "-i", resolve(args.audio));
    ff.push("-c:v", "libx264", "-preset", "slow", "-crf", String(args.crf || 20), "-maxrate", "2400k", "-bufsize", "4800k",
      "-pix_fmt", "yuv420p", "-r", String(FPS), "-movflags", "+faststart");
    if (args.audio) ff.push("-c:a", "aac", "-b:a", "160k", "-shortest");
    ff.push(out);
    const enc = spawn("ffmpeg", ff, { stdio: ["pipe", "inherit", "inherit"] });
    const n = Math.round((to - from) * FPS); const t0 = Date.now();
    for (let i = 0; i < n; i++) {
      const buf = await frame(from + i / FPS);
      if (!enc.stdin.write(buf)) await new Promise(r => enc.stdin.once("drain", r));
      if (i % 150 === 0) console.log(`frame ${i}/${n}  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
    }
    enc.stdin.end();
    await new Promise((ok, no) => enc.on("close", c => (c === 0 ? ok() : no(new Error("ffmpeg exited " + c)))));
    console.log("wrote", out);
  }
} finally {
  ws.close(); chrome.kill(); setTimeout(() => rmSync(profile, { recursive: true, force: true }), 500);
}
