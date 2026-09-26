// The graph libraries are large, so they load the first time the Map view opens.
const FILES = [
  "/ui/vendor/layout-base/layout-base.js",
  "/ui/vendor/cose-base/cose-base.js",
  "/ui/vendor/cytoscape/cytoscape.min.js",
  "/ui/vendor/cytoscape-fcose/cytoscape-fcose.js",
];
let loading = null;

function script(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src; s.async = false;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`Couldn't load ${src}.`));
    document.head.appendChild(s);
  });
}

export function loadCytoscape() {
  if (window.cytoscape && window.cytoscapeFcose) return Promise.resolve(window.cytoscape);
  if (!loading) loading = FILES.reduce((p, src) => p.then(() => script(src)), Promise.resolve()).then(() => {
    window.cytoscape.use(window.cytoscapeFcose);
    return window.cytoscape;
  }).catch(e => { loading = null; throw e; });
  return loading;
}

/** Current theme colours, read from the CSS custom properties. */
export function themeColors() {
  const cs = getComputedStyle(document.documentElement);
  const v = n => cs.getPropertyValue(n).trim();
  const c = Array.from({ length: 12 }, (_, i) => v(`--c${i}`));
  return { ink: v("--ink"), ink2: v("--ink-2"), muted: v("--muted"), paper: v("--paper"), sheet: v("--sheet"), rule: v("--rule-strong"),
    code: v("--code"), agent: v("--agent"), spec: v("--spec"), memory: v("--memory"), risk: v("--risk"), other: v("--c-other"), c };
}

/** Call fn whenever the page theme changes (explicit toggle or system preference). */
export function onThemeChange(fn) {
  const mo = new MutationObserver(() => fn());
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener("change", fn);
  return () => { mo.disconnect(); mq.removeEventListener("change", fn); };
}
