"""The standup digest: what happened, derived from the event log — never self-reported.

Everything is read from the read model (the ``events`` table plus the ``drift.last`` kv, read the
way ``hooks.statusline`` does): commits with their Requirement / Work-Item trailers, the
requirements they touch, memories, timeline facts, agent sessions and the current drift snapshot.
No model calls, no git subprocesses — a sync has already written all of it.
"""
from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta

from rich.text import Text

from .cli import AMBER, INK, MOSS, ROSE, SLATE, brand, console
from .core import day
from .store import Brain

DAY = 86_400  # seconds; --days N is a rolling N*24h window, grouped per calendar day

# Commit-body trailers of the spec workflow, each on its own line after the subject:
#   Requirement: REQ-001, REQ-002
#   Work-Item: org/repo#12
_REQ_TRAILER = re.compile(r"^Requirement:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_WORK_TRAILER = re.compile(r"^Work-Item:[ \t]*(\S+)", re.MULTILINE)


def parse_trailers(body: str) -> dict:
    """The ``Requirement:`` / ``Work-Item:`` trailers of a commit body; missing ones stay empty."""
    reqs: list[str] = []
    for m in _REQ_TRAILER.finditer(body or ""):
        reqs += [part.strip() for part in m.group(1).split(",")]
    return {"requirements": list(dict.fromkeys(r for r in reqs if r)),
            "work_items": list(dict.fromkeys(m.group(1) for m in _WORK_TRAILER.finditer(body or "")))}


def digest(brain: Brain, days: int = 1) -> list[dict]:
    """Per-day digests, newest first, entirely from the read model.

    The window is the last ``days`` * 24h (so --days 3 reaches an event three days old); events are
    grouped by ``core.day`` into one bucket per calendar day the window touches, quiet days included.
    """
    days = max(1, days)
    now = time.time()
    evs = brain.events(since=now - days * DAY, limit=5000)
    drift = len(json.loads(brain.get_kv("drift.last", "[]") or "[]"))
    first = datetime.fromtimestamp(now - days * DAY, tz=UTC).astimezone().date()  # the calendar day() buckets into
    last = datetime.fromtimestamp(now, tz=UTC).astimezone().date()
    out = [{"day": (first + timedelta(offset)).isoformat(), "events": 0, "commits": [], "requirements": [],
            "work_items": [], "memories": [], "facts": [], "sessions": 0, "drift": drift}
           for offset in range((last - first).days + 1)][::-1]
    by_day = {d["day"]: d for d in out}
    for e in evs:  # events() is already ts DESC, so every day's lists are newest first
        d = by_day.get(day(e["ts"]))
        if d is None:
            continue
        d["events"] += 1
        if e["kind"] == "commit":
            trailers = parse_trailers(e.get("body") or "")
            sha = str((e.get("meta") or {}).get("sha") or e["id"].removeprefix("commit:"))
            d["commits"].append({"sha": sha[:12], "title": e["title"][:120], "author": e.get("actor") or "",
                                 "requirements": trailers["requirements"], "work_items": trailers["work_items"]})
            d["requirements"] += [r for r in trailers["requirements"] if r not in d["requirements"]]
            d["work_items"] += [w for w in trailers["work_items"] if w not in d["work_items"]]
        elif e["kind"] == "memory":  # brain.add_memory: one event per memory; meta.kind is the memory kind
            d["memories"].append({"kind": (e.get("meta") or {}).get("kind") or "fact", "text": e["title"][:120]})
        elif e["kind"] == "fact":    # chronicle: one event per extracted timeline fact
            d["facts"].append(e["title"][:120])
        elif e["kind"] == "session":  # journal: one event per agent observation and per session summary
            d["sessions"] += 1
    return out


def render(days: list[dict]) -> None:
    """The terminal standup, in the CLI's voice. A quiet day gets one honest line — never fabricated."""
    console.print(brand("standup"))
    if not days:
        console.print(Text(" no events", style=f"dim {SLATE}"))
        return
    for d in days:
        console.print(Text(f" {d['day']}", style=f"bold {INK}"))
        if not d["events"]:
            console.print(Text("   no events", style=f"dim {SLATE}"))
            continue
        for c in d["commits"]:
            tags = ", ".join([*c["requirements"], *c["work_items"]])
            line = f"   [{MOSS}]✓[/] [{INK}]{c['title']}[/]"
            if c["author"]:
                line += f"  [{SLATE}]{c['author']}[/]"
            if tags:
                line += f"  [{AMBER}]{tags}[/]"
            console.print(line)
        if d["requirements"]:
            console.print(f"   [{AMBER}]requirements:[/] [{INK}]{', '.join(d['requirements'])}[/]")
        for m in d["memories"][:5]:
            console.print(f"   [{AMBER}]●[/] [{INK}]{m['text']}[/] [{SLATE}]{m['kind']}[/]")
        if len(d["memories"]) > 5:
            console.print(Text(f"   … +{len(d['memories']) - 5} more memories", style=f"dim {SLATE}"))
        for f in d["facts"][:5]:
            console.print(f"   [{MOSS}]◆[/] [{INK}]{f}[/]")
        if len(d["facts"]) > 5:
            console.print(Text(f"   … +{len(d['facts']) - 5} more facts", style=f"dim {SLATE}"))
        if d["sessions"]:
            console.print(f"   [{MOSS}]◇[/] [{INK}]{d['sessions']} agent session{'s' if d['sessions'] != 1 else ''}[/]")
        if d["drift"]:
            console.print(f"   [{ROSE}]⚠ {d['drift']} drift finding{'s' if d['drift'] != 1 else ''}[/] "
                          f"[{SLATE}]→ cairn drift[/]")
