"""Dress the graph engine's own pages in the page's colours.

The engine draws its pages (the whole graph, the call flow, the tree) as self-contained documents with a
fixed dark palette, so inside Cairn's page they look like another product. The page's colours live in
``ui/styles/tokens.css``; this swaps the engine's palette for them, in the document's styles and in the
colours its scripts hand to the drawing library, for the light or the dark theme.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

TOKENS = Path(__file__).with_name("ui") / "styles" / "tokens.css"

# the engine's palette -> the page's token that plays the same part
GRAPH = {"#0f0f1a": "paper", "#1a1a2e": "sheet", "#2a2a4e": "rule", "#3a3a5e": "rule-strong", "#e0e0e0": "ink",
         "#aaa": "muted", "#ccc": "ink-2", "#666": "muted", "#555": "c-other", "#333": "rule-strong",
         "#6366f1": "agent", "#4f46e5": "agent",
         "#4e79a7": "c5", "#e15759": "c4", "#f28e2b": "c0", "#edc948": "c6", "#ff9da7": "c8", "#76b7b2": "c1",
         "#bab0ac": "c-other", "#b07aa1": "c3", "#9c755f": "c10", "#59a14f": "c2"}
CALLFLOW = {"#0f172a": "paper", "#1e293b": "sheet", "#334155": "rule-strong", "#e2e8f0": "ink", "#64748b": "muted",
            "#38bdf8": "agent", "#60a5fa": "agent", "#a78bfa": "spec", "#fbbf24": "code", "#f87171": "risk",
            "#34d399": "memory", "#f472b6": "c8",
            "#422006": "code-wash", "#fde68a": "code", "#450a0a": "risk-wash", "#fee2e2": "risk",
            "#2e1065": "spec-wash", "#ede9fe": "spec", "#064e3b": "memory-wash", "#d1fae5": "memory",
            "#831843": "risk-wash", "#fce7f3": "c8", "#172554": "agent-wash", "#dbeafe": "agent",
            "#3f3f46": "faint", "#a1a1aa": "muted", "#f4f4f5": "ink", "#292524": "faint"}


@lru_cache(maxsize=2)
def _tokens(theme: str) -> dict[str, str]:
    css = TOKENS.read_text(encoding="utf-8")
    start = css.index(':root[data-theme="dark"] {') if theme == "dark" else css.index(":root {")
    block = css[start:css.index("}", start)]
    out = dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", css[css.index(":root {"):css.index("}", css.index(":root {"))])) \
        if theme == "dark" else {}
    out.update(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", block))
    return {k: v.lower() for k, v in out.items()}


def themed(html: str, kind: str, theme: str) -> str:
    """The engine page with the page's palette; unchanged for an unknown theme or view."""
    if theme not in ("light", "dark") or kind not in ("graph", "callflow", "tree"):
        return html
    tokens = _tokens(theme)
    table = CALLFLOW if kind == "callflow" else GRAPH
    swap = {k: tokens[v] for k, v in table.items() if v in tokens}
    pattern = re.compile("|".join(re.escape(k) + r"(?![0-9a-fA-F])" for k in sorted(swap, key=len, reverse=True)), re.I)
    html = pattern.sub(lambda m: swap[m.group(0).lower()], html)
    r, g, b = (int(tokens["paper"][i:i + 2], 16) for i in (1, 3, 5))
    html = html.replace("rgba(15,23,42,0.92)", f"rgba({r},{g},{b},0.92)")
    if theme == "light":
        html = html.replace("theme: 'dark'", "theme: 'base'")   # Mermaid's own dark scheme would fight the light tokens
    font = 'system-ui, -apple-system, "Segoe UI", sans-serif'
    html = re.sub(r'font-family:\s*-apple-system[^;"]*', f"font-family: {font}", html)
    return html.replace("<head>", f'<head><meta name="color-scheme" content="{theme}">', 1)
