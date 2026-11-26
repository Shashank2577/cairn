/* Contour-map background: value noise traced with marching squares into survey-style contour lines.
   Usage: <canvas class="topo" data-topo data-seed="3" data-drift="1"></canvas> */
(function () {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  function hash(x, y, s) {
    let h = (x * 374761393 + y * 668265263 + s * 2246822519) | 0;
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
  }
  const fade = (t) => t * t * (3 - 2 * t);
  function noise(x, y, s) {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
    const a = hash(xi, yi, s), b = hash(xi + 1, yi, s), c = hash(xi, yi + 1, s), d = hash(xi + 1, yi + 1, s);
    const u = fade(xf), v = fade(yf);
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
  }
  const fbm = (x, y, s) => noise(x, y, s) * 0.6 + noise(x * 2.1, y * 2.1, s + 7) * 0.28 + noise(x * 4.3, y * 4.3, s + 13) * 0.12;

  function draw(cv, t) {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    }
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const css = getComputedStyle(cv);
    const minor = css.getPropertyValue("--contour").trim() || "#D9DCCF";
    const major = css.getPropertyValue("--contour-index").trim() || "#C2C6B6";
    const seed = +(cv.dataset.seed || 1);
    const cell = 12, scale = 1 / 260;
    const cols = Math.ceil(w / cell) + 1, rows = Math.ceil(h / cell) + 1;
    const f = new Float32Array(cols * rows);
    for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++)
      f[j * cols + i] = fbm(i * cell * scale + t * 0.6, j * cell * scale + t, seed);

    const levels = 22;
    for (let k = 1; k < levels; k++) {
      const lv = 0.18 + (k / levels) * 0.68;
      const index = k % 5 === 0;
      ctx.beginPath();
      for (let j = 0; j < rows - 1; j++) for (let i = 0; i < cols - 1; i++) {
        const a = f[j * cols + i], b = f[j * cols + i + 1], c = f[(j + 1) * cols + i + 1], d = f[(j + 1) * cols + i];
        const idx = (a > lv ? 8 : 0) | (b > lv ? 4 : 0) | (c > lv ? 2 : 0) | (d > lv ? 1 : 0);
        if (idx === 0 || idx === 15) continue;
        const x = i * cell, y = j * cell;
        const top = [x + cell * (lv - a) / (b - a), y];
        const right = [x + cell, y + cell * (lv - b) / (c - b)];
        const bottom = [x + cell * (lv - d) / (c - d), y + cell];
        const left = [x, y + cell * (lv - a) / (d - a)];
        const seg = (p, q) => { ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); };
        switch (idx) {
          case 1: case 14: seg(left, bottom); break;
          case 2: case 13: seg(bottom, right); break;
          case 3: case 12: seg(left, right); break;
          case 4: case 11: seg(top, right); break;
          case 5: seg(left, top); seg(bottom, right); break;
          case 6: case 9: seg(top, bottom); break;
          case 7: case 8: seg(left, top); break;
          case 10: seg(left, bottom); seg(top, right); break;
        }
      }
      ctx.strokeStyle = index ? major : minor;
      ctx.lineWidth = index ? 1.25 : 0.8;
      ctx.stroke();
    }
  }

  function mount(cv) {
    let t = 0, visible = true, last = 0;
    const drift = cv.dataset.drift === "1" && !reduce;
    const frame = (now) => {
      if (visible && now - last > 70) { t += 0.0016; draw(cv, t); last = now; }
      if (drift) requestAnimationFrame(frame);
    };
    draw(cv, 0);
    if (drift) requestAnimationFrame(frame);
    new IntersectionObserver((e) => { visible = e[0].isIntersecting; }).observe(cv);
    addEventListener("resize", () => draw(cv, t));
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => draw(cv, t));
    new MutationObserver(() => draw(cv, t)).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }

  document.querySelectorAll("canvas[data-topo]").forEach(mount);
})();
