"""The limits Cairn calls "budgets", each with a plain name, its value, what it has used and what happens at the limit.

Doctor, the server's overview and the UI settings all read this one list, so the word means the same thing
everywhere. Nothing here calls a model.
"""
from __future__ import annotations

import time

from .project import Project
from .store import Brain

DAY = 24 * 3600


def budgets(project: Project, brain: Brain | None = None, now: float | None = None) -> list[dict]:
    now = now or time.time()
    deep_used = context_used = None
    if brain is not None:
        rows = brain.ledger(since=now - DAY)
        if rows:
            deep_used = sum(int(r["input"] or 0) + int(r["output"] or 0) for r in rows)  # new tokens only
        sent = [int(q["sent_tokens"]) for q in brain.queries(limit=50) if q.get("sent_tokens") is not None]
        if sent:
            context_used = sum(sent) // len(sent)

    def row(key, name, unit, default, used, meaning, when_exceeded, used_note=""):
        return {"key": key, "name": name, "value": int(project.cfg(key, default)), "unit": unit, "used": used,
                "used_note": used_note, "meaning": meaning, "when_exceeded": when_exceeded}

    return [
        row("context.budget", "Context size", "tokens", 1800, context_used,
            "The most Cairn context handed to an agent in one answer (a briefing, an impact or a why). "
            "Used shows the typical answer so far.",
            "Lower-ranked evidence is left out until it fits; the best evidence from each section is kept first.",
            "typical answer"),
        row("deep.budget_tokens", "Deep sync allowance", "tokens", 150000, deep_used,
            "The most model tokens one deep sync (the model-written part of a sync) may spend. "
            "Used shows new model tokens spent by every job in the last 24 hours, not by one sync.",
            "The deep sync stops, keeps what it finished and continues on the next sync. "
            "Everything deterministic is unaffected.",
            "in the last 24 hours"),
        row("deep.wall_seconds", "Deep sync time limit", "seconds", 300, None,
            "The longest a deep sync may run.",
            "It stops, keeps what it finished and the remaining days fill in on later syncs."),
        row("memory.wall_seconds", "Memory learning time limit", "seconds", 300, None,
            "The longest a sync may spend learning decisions and gotchas from the repository.",
            "It stops and the remaining items are learned on the next sync."),
    ]
