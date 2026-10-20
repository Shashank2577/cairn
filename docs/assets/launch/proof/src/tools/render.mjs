// Render proof.html frame by frame in headless Chrome and pipe the frames to ffmpeg.
//   node tools/render.mjs stills <outdir> 3 12.5 30    -> PNGs at those times
//   node tools/render.mjs video <out.mp4>              -> full MP4 with assets/music.m4a, plus captions .srt/.vtt beside it
// PUPPETEER_CORE points at a puppeteer-core install; CHROME at a Chrome binary.
import { createRequire } from "module";
import { spawn } from "child_process";
import { writeFileSync } from "fs";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(process.env.PUPPETEER_CORE || path.join(here, "node_modules") + "/");
const puppeteer = require("puppeteer-core");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const FPS = 30;
const [mode, out, ...times] = process.argv.slice(2);

const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new",
  args: ["--force-color-profile=srgb", "--hide-scrollbars", "--allow-file-access-from-files", "--font-render-hinting=none"] });
const page = await browser.newPage();
await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(path.join(here, "..", "proof.html")).href, { waitUntil: "load" });
await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map(i => i.decode())); });
const meta = await page.evaluate(() => ({ DURATION: window.PROOF.DURATION, CAPTIONS: window.PROOF.CAPTIONS }));

async function frame(t) {
  await page.evaluate(t => window.render(t), t);
  return page.screenshot({ type: "png", optimizeForSpeed: true });
}

if (mode === "stills") {
  for (const t of times) writeFileSync(path.join(out, `t${String(t).padStart(5, "0")}.png`), await frame(parseFloat(t)));
} else {
  const total = Math.round(meta.DURATION * FPS);
  const ff = spawn("ffmpeg", ["-y", "-v", "error", "-f", "image2pipe", "-framerate", String(FPS), "-i", "-",
    "-i", path.join(here, "..", "assets", "music.m4a"),
    "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-tune", "stillimage", "-movflags", "+faststart",
    "-c:a", "aac", "-b:a", "160k", "-shortest", out], { stdio: ["pipe", "inherit", "inherit"] });
  for (let i = 0; i < total; i++) {
    const buf = await frame(i / FPS);
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once("drain", r));
    if (i % 150 === 0) console.log(`frame ${i}/${total}`);
  }
  ff.stdin.end();
  await new Promise(r => ff.on("close", r));
  // captions: the same CAPTIONS array the frames draw
  const stamp = (s, sep) => { const ms = Math.round(s * 1000); const h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60,
    sec = Math.floor(ms / 1000) % 60, r = ms % 1000; return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}${sep}${String(r).padStart(3, "0")}`; };
  const plain = s => s.replace(/<[^>]+>/g, "");
  const base = out.replace(/\.mp4$/, "");
  writeFileSync(base + ".srt", meta.CAPTIONS.map((c, i) => `${i + 1}\n${stamp(c[0], ",")} --> ${stamp(c[1], ",")}\n${plain(c[2])}\n`).join("\n"));
  writeFileSync(base + ".vtt", "WEBVTT\n\n" + meta.CAPTIONS.map(c => `${stamp(c[0], ".")} --> ${stamp(c[1], ".")}\n${plain(c[2])}\n`).join("\n"));
}
await browser.close();
