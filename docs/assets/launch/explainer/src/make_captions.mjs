// Write the captions in captions.js as SRT and VTT.   node make_captions.mjs ..
import { readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const out = process.argv[2] || join(here, "..");
const ctx = { window: {} };
vm.runInNewContext(readFileSync(join(here, "captions.js"), "utf8"), ctx);
const caps = ctx.window.CAPTIONS;

const stamp = (s, sep) => {
  const ms = Math.round(s * 1000), h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, sec = Math.floor(ms / 1000) % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}${sep}${String(ms % 1000).padStart(3, "0")}`;
};
writeFileSync(join(out, "cairn-explainer.srt"), caps.map(([a, b, t], i) => `${i + 1}\n${stamp(a, ",")} --> ${stamp(b, ",")}\n${t}\n`).join("\n"));
writeFileSync(join(out, "cairn-explainer.vtt"), "WEBVTT\n\n" + caps.map(([a, b, t]) => `${stamp(a, ".")} --> ${stamp(b, ".")}\n${t}\n`).join("\n"));
console.log(`${caps.length} captions written to ${out}`);
