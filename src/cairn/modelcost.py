"""What Cairn's own model calls cost, as the UI and `cairn models --ledger` show it.

Labels, always: **tokens are measured** (the provider's own usage report, summed in the ledger); **dollars are estimated**
(tokens x a list price, or a price the project configured). A model with no known price gets no dollar figure. Subscription
users pay in plan usage, not dollars; the token counts and the before/after ratios carry over.

Cache multipliers on the input price follow the published rates: read 0.1x, 5-minute write 1.25x, 1-hour write 2x.
Thinking tokens are part of output tokens (billed as output), so they are shown, never added again.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from .project import Project
from .store import Brain

# USD per million tokens (input, output): Anthropic list prices. A project can override with [models.prices].
LIST_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),     # up to 100K-token prompts; higher beyond (not modelled)
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-6": (5.0, 25.0),
    "claude-fable-5-1": (10.0, 50.0), "claude-fable-5": (10.0, 50.0),
}
READ, WRITE_5M, WRITE_1H = 0.1, 1.25, 2.0

# What spec 002 measured replaying the same captured events through the old and the new defaults: two repositories x two
# windows, averaged. Tokens are measured; dollars are estimated at list price.
# Source: specs/002-observation-token-cost/findings.md §9.
STUDY = {
    "source": "specs/002-observation-token-cost/findings.md §9 (same events replayed, 2 repositories x 2 windows, averaged)",
    "before": {"label": "Before: Haiku 4.5, cache writes on, thinking on", "tokens_per_event": 5108, "usd_per_event": 0.01218},
    "after_haiku_4_5": {"label": "After: Haiku 4.5, cache writes off, thinking off", "tokens_per_event": 3280, "usd_per_event": 0.00468},
    "after_haiku_5_5": {"label": "After: Haiku 5.5 (the new default)", "tokens_per_event": 4596, "usd_per_event": 0.000583},
}
_DATE = re.compile(r"-\d{8}$")


def price(model: str, project: Project | None = None) -> tuple[float, float] | None:
    """(input, output) USD per million tokens: the project's own price, else the list price, else None."""
    custom = (project.cfg("models.prices", {}) if project else {}) or {}
    for key in (model, _DATE.sub("", model)):
        v = custom.get(key)
        if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            return float(v[0]), float(v[1])
    return LIST_PRICES.get(model) or LIST_PRICES.get(_DATE.sub("", model))


def dollars(row: dict, p: tuple[float, float], unknown_write: str = "1h") -> float:
    """Estimated USD of one aggregated ledger row. A cache write with no recorded TTL (rows from before the column) is
    charged at ``unknown_write``: the Claude Code CLI measured 1-hour entries, the API's default is 5 minutes."""
    pin, pout = p
    write, write_1h = row.get("cache_write", 0), min(row.get("cache_write_1h", 0), row.get("cache_write", 0))
    rest = (WRITE_1H if unknown_write == "1h" else WRITE_5M)
    cost = (row.get("input", 0) * pin + row.get("output", 0) * pout + row.get("cache_read", 0) * pin * READ
            + write_1h * pin * WRITE_1H + (write - write_1h) * pin * rest)
    return cost / 1_000_000


def _sums(rows: list[dict]) -> dict:
    keys = ("input", "output", "cache_read", "cache_write", "cache_write_1h", "thinking", "calls")
    return {k: sum(r.get(k) or 0 for r in rows) for k in keys}


def _total(r: dict) -> int:
    return r["input"] + r["output"] + r["cache_read"] + r["cache_write"]


def _capture_counts(project: Project, lo: float, hi: float | None) -> tuple[int, int] | None:
    """(tool events, observations) the session capture recorded in [lo, hi); None when there is no capture store."""
    path = project.dir / "sessions.db"
    if not path.exists():
        return None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            top = hi if hi is not None else 4_102_444_800
            q = "SELECT COUNT(*) FROM {} WHERE created_at_epoch >= ? AND created_at_epoch < ?"
            return (db.execute(q.format("tool_uses"), (int(lo), int(top))).fetchone()[0],
                    db.execute(q.format("observations"), (int(lo), int(top))).fetchone()[0])
        finally:
            db.close()
    except sqlite3.Error:
        return None


def _cohort(project: Project, rows: list[dict], lo: float, hi: float | None, unknown: str, count: bool = True) -> dict | None:
    if not rows:
        return None
    s = _sums(rows)
    priced = [(r, price(r["model"], project)) for r in rows]
    usd = None if any(p is None for _, p in priced) else sum(dollars(r, p, unknown) for r, p in priced)
    out: dict[str, Any] = {**s, "tokens": _total(s), "tokens_per_call": round(_total(s) / max(1, s["calls"])),
                           "usd": usd, "usd_per_call": None if usd is None else usd / max(1, s["calls"]),
                           "models": sorted({r["model"] for r in rows}), "events": None, "observations": None,
                           "tokens_per_event": None, "usd_per_event": None, "tokens_per_observation": None,
                           "usd_per_observation": None}
    counts = _capture_counts(project, lo, hi) if count else None
    if counts:
        out["events"], out["observations"] = counts
        if counts[0]:
            out["tokens_per_event"] = _total(s) / counts[0]
            out["usd_per_event"] = None if usd is None else usd / counts[0]
        if counts[1]:
            out["tokens_per_observation"] = _total(s) / counts[1]
            out["usd_per_observation"] = None if usd is None else usd / counts[1]
    return out


def summary(brain: Brain, project: Project, since: float = 0, provider: str | None = None) -> dict:
    """Tiers, tasks, and the observer before/after split, with tokens measured and dollars estimated."""
    unknown = "5m" if provider in ("anthropic", "openai") else "1h"
    rows = [dict(r) for r in brain.q(
        "SELECT task, tier, model, ok, COUNT(*) calls, SUM(input_tokens) input, SUM(output_tokens) output, "
        "SUM(cache_read) cache_read, SUM(cache_write) cache_write, SUM(cache_write_1h) cache_write_1h, "
        "SUM(thinking_tokens) thinking, MIN(ts) first, MAX(ts) last, (cache_write > 0) cached "
        "FROM ledger WHERE ts>=? GROUP BY task, tier, model, cached", (since,))]

    def usd_of(group: list[dict]) -> float | None:
        priced = [(r, price(r["model"], project)) for r in group]
        return None if any(p is None for _, p in priced) or not group else sum(dollars(r, p, unknown) for r, p in priced)

    tiers = []
    for (tier, model), grp in _group(rows, ("tier", "model")).items():
        s, p = _sums(grp), price(model, project)
        tiers.append({"tier": tier, "model": model, **s, "tokens": _total(s), "price": list(p) if p else None,
                      "usd": usd_of(grp)})
    tiers.sort(key=lambda t: -t["calls"])
    tasks = []
    for (task,), grp in _group(rows, ("task",)).items():
        s = _sums(grp)
        tasks.append({"task": task, **s, "tokens": _total(s), "usd": usd_of(grp)})
    tasks.sort(key=lambda t: -t["tokens"])

    obs = [r for r in rows if r["task"] == "recall_observe"]
    old, new = [r for r in obs if r["cached"]], [r for r in obs if not r["cached"]]
    boundary = min((r["first"] for r in new), default=None)
    mixed = bool(old and new and max(r["last"] for r in old) > boundary)
    before = _cohort(project, old, min((r["first"] for r in old), default=0), boundary, unknown, count=not mixed)
    after = _cohort(project, new, boundary or 0, None, unknown, count=not mixed)
    total = _sums(rows)
    return {"labels": {"tokens": "measured", "dollars": "estimated"}, "tiers": tiers, "tasks": tasks[:8],
            "observer": {"before": before, "after": after, "mixed": mixed,
                         "split": "cache writes on (before) vs off (after); events are counted from the first call of the "
                                  "new behaviour onward"},
            "study": STUDY, "totals": {**total, "tokens": _total(total), "usd": usd_of(rows)},
            "thinking_note": "thinking tokens are included in output tokens and billed as output; the API does not report "
                             "them separately, the Claude Code CLI does"}


def _group(rows: list[dict], keys: tuple[str, ...]) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = {}
    for r in rows:
        out.setdefault(tuple(r[k] for k in keys), []).append(r)
    return out
