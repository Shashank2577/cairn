// Seeded topographic contours. Every project gets its own "survey sheet": the same id always
// draws the same hills. Marching squares over a sum of gaussian bumps.

function hash(s) {
  let h = 2166136261;
  for (const c of String(s)) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return h >>> 0;
}
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const cache = new Map();

/** Returns [{d, index}] — one SVG path per contour level; `index` marks every fifth (bolder) line. */
export function contours(seed, w, h, { levels = 12, cell = 10, bumps = 6 } = {}) {
  const key = [seed, w, h, levels, cell, bumps].join("|");
  if (cache.has(key)) return cache.get(key);
  const r = rng(hash(seed));
  const S = Math.max(w, h), m = Math.min(w, h);
  const count = Math.max(bumps, Math.round(3 * w / h) + 3);
  const B = Array.from({ length: count }, (_, i) => ({
    x: r() * w, y: (r() * 1.2 - 0.1) * h,
    s: (0.16 + r() * 0.4) * m,
    a: (0.55 + r() * 0.9) * (i > 1 && r() < 0.3 ? -0.6 : 1),
  }));
  const p1 = r() * 6.28, p2 = r() * 6.28;
  const nx = Math.ceil(w / cell) + 1, ny = Math.ceil(h / cell) + 1;
  const f = new Float32Array(nx * ny);
  let min = Infinity, max = -Infinity;
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    const x = i * cell, y = j * cell;
    let v = 0.05 * Math.sin(x / S * 9 + p1) * Math.cos(y / S * 7 + p2);
    for (const b of B) { const dx = x - b.x, dy = y - b.y; v += b.a * Math.exp(-(dx * dx + dy * dy) / (2 * b.s * b.s)); }
    f[j * nx + i] = v; if (v < min) min = v; if (v > max) max = v;
  }
  const out = [];
  for (let L = 1; L <= levels; L++) {
    const t = min + (max - min) * (L / (levels + 1));
    let d = "";
    const P = (x, y) => `${x.toFixed(1)} ${y.toFixed(1)}`;
    for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
      const tl = f[j * nx + i], tr = f[j * nx + i + 1], br = f[(j + 1) * nx + i + 1], bl = f[(j + 1) * nx + i];
      const c = (tl > t ? 8 : 0) | (tr > t ? 4 : 0) | (br > t ? 2 : 0) | (bl > t ? 1 : 0);
      if (c === 0 || c === 15) continue;
      const x = i * cell, y = j * cell;
      const top = () => P(x + cell * (t - tl) / (tr - tl), y);
      const right = () => P(x + cell, y + cell * (t - tr) / (br - tr));
      const bottom = () => P(x + cell * (t - bl) / (br - bl), y + cell);
      const left = () => P(x, y + cell * (t - tl) / (bl - tl));
      const seg = (a, b) => { d += `M${a()}L${b()}`; };
      switch (c) {
        case 1: case 14: seg(left, bottom); break;
        case 2: case 13: seg(bottom, right); break;
        case 3: case 12: seg(left, right); break;
        case 4: case 11: seg(top, right); break;
        case 5: seg(left, top); seg(bottom, right); break;
        case 6: case 9: seg(top, bottom); break;
        case 7: case 8: seg(left, top); break;
        case 10: seg(top, right); seg(left, bottom); break;
      }
    }
    if (d) out.push({ d, index: L % 5 === 0 });
  }
  cache.set(key, out);
  return out;
}
