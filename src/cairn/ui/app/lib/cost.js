// Dollar figures in the UI. The server prices tokens at list prices for models whose price is confirmed and sends
// null for the rest; nothing here guesses a price. Every figure is an estimate, so it always reads "≈".
export function usd(v) {
  if (v == null || !Number.isFinite(v)) return null;
  if (v === 0) return "$0";
  if (v < 0.01) return "<$0.01";
  return "≈ $" + (v < 100 ? v.toFixed(2) : Math.round(v).toLocaleString());
}

/** Tokens at a per-million-token rate. */
export function tokensUsd(tokens, perMTok) {
  return perMTok == null || tokens == null ? null : tokens * perMTok / 1e6;
}

/** "$2 in / $10 out per million tokens", or null for a model with no confirmed price. */
export function rateLabel(p, short) {
  if (!p) return null;
  const n = x => x < 1 ? x.toFixed(2) : String(+x.toFixed(2));
  if (short) return `$${n(p.input)} in / $${n(p.output)} out per M`;
  return `$${n(p.input)} in / $${n(p.output)} out per million tokens`;
}
