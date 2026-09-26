// App-wide context: session, current project, role checks, the live event bus and toasts.
import { createContext } from "preact";
import { useCallback, useContext, useEffect, useRef, useState } from "preact/hooks";

export const AppCtx = createContext(null);
export const useApp = () => useContext(AppCtx);

/** Load data for a view. Keeps the previous result while reloading so pages don't flash. */
export function useFetch(loader, deps) {
  const [st, setSt] = useState({ data: undefined, error: null, loading: true });
  const seq = useRef(0);
  const run = useCallback(() => {
    const my = ++seq.current;
    setSt(s => ({ ...s, loading: true, error: null }));
    Promise.resolve().then(loader).then(
      data => { if (my === seq.current) setSt({ data, error: null, loading: false }); },
      error => { if (my === seq.current) setSt({ data: undefined, error, loading: false }); });
  }, deps);
  useEffect(run, [run]);
  const setData = useCallback(d => setSt(s => ({ ...s, data: typeof d === "function" ? d(s.data) : d })), []);
  return { ...st, reload: run, setData };
}

/** Subscribe to the current project's live events (sync, observation, summary, memory, query). */
export function useLive(fn, deps = []) {
  const { live } = useApp();
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => live.subscribe(ev => ref.current(ev)), [live, ...deps]);
}

export function useMedia(q) {
  const [m, setM] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const f = () => setM(mq.matches);
    mq.addEventListener("change", f);
    return () => mq.removeEventListener("change", f);
  }, [q]);
  return m;
}
export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Close a popover on outside click or Escape. */
export function useDismiss(open, close, ref) {
  useEffect(() => {
    if (!open) return;
    const onDown = e => { if (ref.current && !ref.current.contains(e.target)) close(); };
    const onKey = e => { if (e.key === "Escape") { close(); } };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown, true); document.removeEventListener("keydown", onKey); };
  }, [open]);
}

/** Animate a number towards its target (skipped when the viewer prefers reduced motion). */
export function useCountUp(target, ms = 900) {
  const [v, setV] = useState(() => reducedMotion() ? target : 0);
  const from = useRef(0);
  useEffect(() => {
    if (reducedMotion() || !Number.isFinite(target)) { setV(target); return; }
    const start = performance.now(), a = from.current, b = target;
    let raf = 0;
    const tick = t => {
      const k = Math.min(1, (t - start) / ms), e = 1 - Math.pow(1 - k, 3);
      const x = a + (b - a) * e;
      setV(x); from.current = x;
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target]);
  return v;
}

/** Remember a small per-viewer preference in localStorage. */
export function usePref(key, initial) {
  const [v, setV] = useState(() => { try { const s = localStorage.getItem("cairn." + key); return s == null ? initial : JSON.parse(s); } catch (e) { return initial; } });
  const set = useCallback(x => { setV(x); try { localStorage.setItem("cairn." + key, JSON.stringify(x)); } catch (e) { /* storage may be blocked */ } }, [key]);
  return [v, set];
}
