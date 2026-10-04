"""The Cairn facade: one object every surface (CLI, MCP, HTTP) talks to.

The context assembler turns a question into a ranked, deduplicated, cited pack that fits a token
budget. Everything here is deterministic; a model only narrates on top (``ask``, ``--explain``).
"""
from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .engines import history, journal, mapper
from .engines.chronicle import facts_matching
from .engines.memory import MemoryStore
from .linker import GENERIC, link_text
from .project import Project
from .router import Router, estimate_tokens
from .store import Brain

ASK_SYSTEM = ("You are Cairn, the engineering memory of this repository, answering a HUMAN developer in chat. "
              "Answer the question directly and completely. The context pack lists what is known and sometimes "
              "what is unknown; the cited repository documents are appended in full — use them to CLOSE those "
              "gaps instead of repeating them as unknowns. If a cited document answers part of the question, "
              "present that part as known, with the document path. Never tell the human to call tools or run "
              "commands — you are the tool. Cite ids in [brackets]. Be brief and concrete.")
NARRATE_SYSTEM = ("Summarise this engineering evidence for a developer about to change code. 4 bullets max: "
                  "the real risk, what to check, who/what to consult. Cite ids in [brackets]. No preamble.")


def in_repo(path: str) -> bool:
    """A repository-relative path (not absolute, home-relative or climbing out of the repository)."""
    p = (path or "").replace("\\", "/")
    return bool(p) and not p.startswith(("/", "~")) and not re.match(r"^[A-Za-z]:", p) and ".." not in p.split("/")


# Question and filler words that must never become context targets on their own ("what owns dispatch"
# used to resolve "what" against README headings); GENERIC covers code-y fillers, this covers prose ones.
_QUESTION_STOP = frozenset((
    "what", "where", "when", "who", "whose", "why", "how", "which", "owns", "owner", "owned",
    "does", "did", "doing", "done", "the", "and", "or", "are", "was", "were", "is", "been", "being",
    "has", "had", "have", "will", "shall", "may", "might", "must", "can", "could", "should", "would",
    "about", "after", "before", "between", "during", "without", "within", "of", "to", "in", "on",
    "at", "by", "as", "it", "its", "this", "that", "these", "those", "there", "here",
    "please", "show", "tell", "give", "find", "list"))


def ask_prompt(question: str, context: str) -> str:
    return f"Question: {question}\n\nContext:\n{context}"


_DOC_REF = re.compile(r"(?:^|[\s`(])([A-Za-z0-9_\-./]+\.(?:md|txt))(?=[\s)`,.:]|$)")


def cited_docs(root: Path, pack_text: str, question: str, *, max_files: int = 2,
               per_file_tokens: int = 3000) -> str:
    """Full text of the repository documents the pack cites (bounded): an answer that says 'the process is
    documented in docs/x.md \u00a72' must include docs/x.md, or the narrator can only list unknowns."""
    wanted: list[str] = []
    for m in _DOC_REF.finditer(pack_text):
        p = m.group(1)
        if in_repo(p) and p not in wanted:
            wanted.append(p)
    out: list[str] = []
    keywords = [w for w in re.findall(r"[a-z0-9]{4,}", question.lower())
                if w not in _QUESTION_STOP and w not in GENERIC]
    for rel in wanted[:max_files]:
        f = root / rel
        try:
            body = f.read_text(errors="replace")
        except OSError:
            continue
        cap = per_file_tokens * 4
        if len(body) > cap and keywords:  # pull the densest keyword window, not the head
            best, score, low = 0, -1, body.lower()
            step = max(1, (len(body) - cap) // 40)
            for off in range(0, max(1, len(body) - cap + 1), step):
                win = body[off:off + cap].lower()
                sc = sum(win.count(k) for k in keywords)
                if sc > score:
                    best, score = off, sc
            body = ("…\n" if best else "") + body[best:best + cap] + "\n…"
        out.append(f"### {rel}\n{body}")
        if sum(len(o) for o in out) > per_file_tokens * 4 * max_files:
            break
    return ("\n\n## Cited repository documents\n" + "\n\n".join(out)) if out else ""


SECTION_ORDER = ["Rationale", "Dependents", "Tests likely affected", "Changes together", "Historical warnings",
                 "Origin", "Recent changes", "Intent", "Memory", "Agent sessions", "Facts", "Active work"]


def ago(ts: float | None) -> str:
    if not ts:
        return ""
    d = time.time() - ts
    for unit, secs in (("y", 31_536_000), ("mo", 2_592_000), ("d", 86_400), ("h", 3_600), ("m", 60)):
        if d >= secs:
            return f"{int(d // secs)}{unit} ago"
    return "just now"


def day(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""


@dataclass
class Item:
    section: str
    text: str
    score: float = 1.0
    cite: str = ""

    @property
    def line(self) -> str:
        return f"- {self.text}" + (f" [{self.cite}]" if self.cite else "")


@dataclass
class Target:
    raw: str
    label: str
    nodes: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    entity: dict | None = None

    @property
    def found(self) -> bool:
        return bool(self.nodes or self.files or self.entity)


@dataclass
class Pack:
    title: str
    items: list[Item]
    header: list[str] = field(default_factory=list)
    budget: int = 1800
    data: dict = field(default_factory=dict)
    # Called once, with the tokens actually sent, the first time the pack is rendered for a reader.
    on_render: Callable[[Pack, int], None] | None = field(default=None, repr=False, compare=False)

    def _select(self) -> tuple[dict[str, list[Item]], dict[str, list[Item]], int]:
        used = estimate_tokens(self.title) + sum(estimate_tokens(h) for h in self.header) + 12
        by_section: dict[str, list[Item]] = {}
        for it in self.items:
            by_section.setdefault(it.section, []).append(it)
        for v in by_section.values():
            v.sort(key=lambda i: -i.score)
        chosen: dict[str, list[Item]] = {s: [] for s in by_section}
        # pass 1: breadth — the top 2 of every section; pass 2: depth by score
        queue = [it for s in by_section for it in by_section[s][:2]]
        queue += sorted((it for s in by_section for it in by_section[s][2:]), key=lambda i: -i.score)
        for it in queue:
            cost = estimate_tokens(it.line) + (6 if not chosen[it.section] else 0)
            if used + cost > self.budget:
                continue
            chosen[it.section].append(it)
            used += cost
        return by_section, chosen, used

    def render(self) -> str:
        by_section, chosen, used = self._select()
        out = [f"## {self.title}", *self.header]
        for s in sorted(chosen, key=lambda s: SECTION_ORDER.index(s) if s in SECTION_ORDER else 99):
            items = chosen[s]
            if not items:
                continue
            more = len(by_section[s]) - len(items)
            out.append(f"### {s}" + (f" (+{more} more)" if more > 0 else ""))
            out += [i.line for i in items]
        if len(out) <= 1 + len(self.header):
            out.append("_Nothing recorded yet for this target. It will fill in as the project is used._")
        out.append(f"(budget used: {used}/{self.budget} tokens)")
        self.mark_sent(used)
        return "\n".join(out)

    def mark_sent(self, tokens: int) -> None:
        """This pack reached a reader as ``tokens`` tokens. Only the first delivery is recorded."""
        if self.on_render:
            log, self.on_render = self.on_render, None
            log(self, tokens)

    def accounting(self) -> dict:
        """What the budget kept and dropped, per section — the same selection `render` makes."""
        by_section, chosen, used = self._select()
        return {"used": used, "budget": self.budget,
                "sections": {s: {"kept": len(chosen[s]), "total": len(v)} for s, v in by_section.items()}}

    def sections(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for it in self.items:
            out.setdefault(it.section, []).append({"text": it.text, "cite": it.cite, "score": round(it.score, 3)})
        return out


class Cairn:
    surface = "cli"  # who reads the packs: cli | mcp | ui | hook — set by each entry point

    def __init__(self, project: Project):
        self.project = project
        project.dir.mkdir(exist_ok=True)
        self.brain = Brain(project.db_path)
        self.router = Router(project, self.brain)
        self.memory = MemoryStore(project, self.brain, self.router)

    def reconfigure(self) -> None:
        """Re-read the project's settings into this instance (model provider, budgets, memory engine)."""
        self.project.reload()
        self.router = Router(self.project, self.brain)
        self.memory.router = self.router
        self.memory.semantic.router = self.router
        self.memory.semantic.reset()

    def close(self) -> None:
        """Release open stores (read model, memory engine). The instance must not be used afterwards."""
        with contextlib.suppress(Exception):
            self.memory.semantic.reset()
        self.brain.close()

    @classmethod
    def here(cls, start: Path | None = None) -> Cairn:
        proj = Project.discover(start)
        if proj is None:
            raise SystemExit("Not inside a project folder.")
        return cls(proj)

    @property
    def map(self) -> mapper.MapIndex:
        return mapper.index(self.project.map_json)

    @property
    def budget_default(self) -> int:
        return int(self.project.cfg("context.budget", 1800))

    # ---- resolution -------------------------------------------------------------------------------
    def resolve(self, raw: str) -> Target:
        raw = raw.strip()
        if len(raw) < 2 or not re.search(r"[A-Za-z0-9]", raw):
            # ".", "/", "…" and friends resolve to arbitrary first-matches; refuse them before map lookup
            raise ValueError(f"'{raw}' is not a file, symbol or id — try `cairn search` to find a target.")
        idx = self.map
        if re.match(r"^(spec|task|req|story|memory|commit|obs|session|fact|file|symbol):", raw):
            ent = self.brain.entity(raw)
            if raw.startswith("file:"):
                p = raw[5:]
                return Target(raw, p, nodes=idx.resolve(p), files=[p], entity=ent)
            if raw.startswith("symbol:"):
                nid = raw[7:]
                return Target(raw, idx.label(nid), nodes=[nid], files=[f] if (f := idx.file_of(nid)) else [], entity=ent)
            files = [l["dst"][5:] for l in self.brain.links_from(raw, rels=("owns", "touches", "modifies"))]
            return Target(raw, ent["name"] if ent else raw, files=files, entity=ent)
        nodes = idx.resolve(raw)
        files: list[str] = []
        norm = raw.replace("\\", "/").lstrip("./")
        if (self.project.root / norm).is_file():
            files.append(norm)
        for n in nodes[:3]:
            f = idx.file_of(n)
            if f and f not in files:
                files.append(f)
        if not nodes and not files:
            wanted = {w for w in re.findall(r"[a-z0-9]+", raw.lower()) if len(w) > 2}
            for h in self.brain.search(raw, kinds=["symbol", "file"], limit=3):
                ent = self.brain.entity(h["id"])
                have = set(re.findall(r"[a-z0-9]+", f"{ent['name']} {ent['path']}".lower())) if ent else set()
                if ent and ent["path"] and wanted <= have:  # every word asked for, not a loose text match
                    files.append(ent["path"])
                    if h["id"].startswith("symbol:"):
                        nodes.append(h["id"][7:])
                    break
        label = idx.label(nodes[0]) if nodes and not files[:1] == [norm] else (files[0] if files else raw)
        return Target(raw, label, nodes=nodes[:3], files=files[:4])

    # ---- accounting -------------------------------------------------------------------------------
    def source_cost(self, files) -> tuple[int, int]:
        """Estimated tokens (bytes / 4, the same rule packs use) of the repo files behind an answer."""
        total = n = 0
        for f in dict.fromkeys(f for f in files if f):
            try:
                size = (self.project.root / f).stat().st_size
            except OSError:
                continue
            total += max(1, size // 4)
            n += 1
        return total, n

    def _logger(self, kind: str, target: str, files) -> Callable[[Pack, int], None] | None:
        if self.surface == "ui":
            return None  # the page shows a pack for inspection; nobody is handed it
        files = list(files)

        def log(_pack: Pack, sent: int) -> None:
            src, n = self.source_cost(files)
            with contextlib.suppress(sqlite3.Error):  # a busy database never gets in the way of an answer
                self.brain.log_query(self.surface, kind, target, sent, src if n else None, n)
        return log

    def _cited_files(self, items: list[Item]) -> list[str]:
        """Spec documents behind intent citations (task -> tasks.md, requirement/story -> spec.md)."""
        out = []
        for it in items:
            kind = it.cite.split(":", 1)[0]
            if kind in ("task", "req", "story", "spec"):
                ent = self.brain.entity(it.cite)
                if ent and ent["path"]:
                    out.append(f"{ent['path']}/{'tasks.md' if kind == 'task' else 'spec.md'}")
        return out

    # ---- shared gatherers -------------------------------------------------------------------------
    def _intent(self, files: list[str], limit: int = 6) -> list[Item]:
        items: list[Item] = []
        tasks = self.brain.links_to([f"file:{f}" for f in files], rels=("owns",), limit=50)
        seen = set()
        for l in tasks:
            t = self.brain.entity(l["src"])
            if not t or t["id"] in seen:
                continue
            seen.add(t["id"])
            spec = t["id"].split(":", 1)[1].split("/")[0]
            state = "done" if t["meta"].get("done") else "open"
            story = t["meta"].get("story")
            extra = ""
            if story:
                st = self.brain.entity(f"story:{spec}/{story}")
                extra = f" — {story}: {st['name']}" if st else f" — {story}"
            items.append(Item("Intent", f"{t['name']} ({state}){extra}", 0.9 if state == "open" else 0.7, t["id"]))
            for r in self.brain.links_from(t["id"], rels=("implements",)):
                if r["dst"].startswith("req:") and r["dst"] not in seen:
                    seen.add(r["dst"])
                    req = self.brain.entity(r["dst"])
                    if req:
                        items.append(Item("Intent", req["name"], 0.85, req["id"]))
        return items[:limit]

    def _sessions(self, files: list[str]) -> list[Item]:
        out = []
        for o in journal.for_files(self.brain, files):
            facts = f" — {'; '.join(o['facts'][:2])}" if o["facts"] else ""
            out.append(Item("Agent sessions", f"{o['title']}{facts} ({ago(o['ts'])})",
                            0.6 + 0.3 * _recency(o["ts"]), o["id"]))
        return out

    def _memories(self, labels: list[str], files: list[str]) -> list[Item]:
        weight = {"gotcha": 1.0, "decision": 0.95, "convention": 0.9, "preference": 0.7, "fact": 0.6}
        return [Item("Memory", f"[{m['kind']}] {m['text']} ({day(m['created_at'])})", weight.get(m["kind"], 0.6),
                     f"memory:{m['id']}") for m in self.memory.about(labels, files)]

    def _facts(self, labels: list[str]) -> list[Item]:
        out = []
        for f in facts_matching(self.brain, [l for l in labels if len(l) > 3][:3]):
            window = ""
            if f.get("valid_at"):
                window = f" (since {day(f['valid_at'])}" + (f", until {day(f['invalid_at'])})" if f.get("invalid_at") else ")")
            out.append(Item("Facts", f"{f['fact']}{window}", 0.5 if f.get("invalid_at") else 0.65, f["id"]))
        return out

    # ---- impact -----------------------------------------------------------------------------------
    def impact(self, target: str, depth: int = 2, budget: int | None = None) -> Pack:
        t = self.resolve(target)
        budget = budget or self.budget_default
        if not t.found:
            return Pack(f"Impact: {target}", [], [f"Couldn't find `{target}` in the map, specs or history. "
                                                  "Try a file path or `cairn search`."], budget)
        idx = self.map
        items: list[Item] = []
        deps = idx.dependents(t.nodes, depth=depth) if t.nodes else []
        if t.nodes and len(deps) < 3:
            # The map may attach `self.x()` calls to a same-named sibling (e.g. sync/async twins).
            # Surface those callers as INFERRED so they are not silently missed.
            seen = {d["id"] for d in deps} | set(t.nodes)
            for n in t.nodes[:1]:
                twins = [o for o in idx.by_label.get(mapper.key(idx.label(n)), [])
                         if o not in t.nodes and idx.file_of(o) == idx.file_of(n)]
                for d in idx.dependents(twins, depth=1):
                    if d["id"] not in seen:
                        seen.add(d["id"])
                        deps.append({**d, "provenance": "INFERRED", "rel": d["rel"] + " (same-name sibling)"})
        dep_files = sorted({d["file"] for d in deps if d["file"]} - set(t.files))
        for d in deps:
            where = f"{d['file']}:{(d['location'] or '').lstrip('L')}" if d["file"] else ""
            sec = "Tests likely affected" if d["file"] and re.search(r"(^|/)(tests?|spec|__tests__)(/|_)|_test\.|\.test\.|\.spec\.", d["file"]) else "Dependents"
            items.append(Item(sec, f"{d['label']} {where} — {d['rel']} {d['via']} (depth {d['depth']}, {d['provenance']})",
                              (1.0 if d["depth"] == 1 else 0.6) * (1.0 if d["provenance"] == "EXTRACTED" else 0.8),
                              f"symbol:{d['id']}"))
        co_all = []
        for f in t.files[:3]:
            for c in self.brain.cochanged(f, limit=6):
                if c["path"] not in t.files and c["count"] >= 2:
                    co_all.append(c)
                    items.append(Item("Changes together", f"{c['path']} — changed together in {c['count']} commits "
                                      f"({int(c['ratio'] * 100)}%)", 0.4 + 0.6 * c["ratio"], f"file:{c['path']}"))
        warns = history.risky_for(self.brain, t.files)
        for w in warns:
            items.append(Item("Historical warnings", f"{w['sha']} {w['title']} — {', '.join(w['risk'])}, "
                              f"{w['author']}, {day(w['ts'])}", 1.0 if "revert" in w["risk"] else 0.85, w["id"]))
        intent = self._intent(t.files)
        items += intent
        items += self._sessions(t.files)
        labels = [t.label] + [idx.label(n) for n in t.nodes[:2]]
        items += self._memories(labels, t.files)
        items += self._facts(labels)
        # risk level
        score, reasons = 0.0, []
        if deps:
            score += min(len(deps), 30) / 10
            reasons.append(f"{len(deps)} dependents" + (f" in {len(dep_files)} other files" if dep_files else ""))
        if warns:
            score += 1.2 * len(warns)
            reasons.append(f"{len(warns)} past fix/revert{'s' if len(warns) > 1 else ''}")
        strong = [c for c in co_all if c["ratio"] >= 0.5]
        if strong:
            score += 0.4 * len(strong)
            reasons.append(f"{len(strong)} file{'s usually change' if len(strong) != 1 else ' usually changes'} with it")
        open_tasks = [i for i in intent if "(open)" in i.text]
        if open_tasks:
            score += 0.5
            reasons.append(f"{len(open_tasks)} open spec task{'s' if len(open_tasks) > 1 else ''}")
        level = "HIGH" if score >= 4 else "MEDIUM" if score >= 1.5 else "LOW"
        where = f" ({', '.join(t.files[:2])})" if t.files and t.files[0] != t.label else ""
        header = [f"**Risk: {level}**" + (f" — {'; '.join(reasons)}" if reasons else " — no dependents or warnings recorded")]
        evidence = [*t.files, *dep_files, *(c["path"] for c in co_all), *self._cited_files(intent)]
        return Pack(f"Impact: {t.label}{where}", items, header, budget,
                    data={"risk": level, "reasons": reasons, "files": t.files, "dependents": len(deps),
                          "dependent_files": dep_files[:50], "target": t.raw, "label": t.label,
                          "target_nodes": [{"id": n, "label": idx.label(n), "file": idx.file_of(n)} for n in t.nodes],
                          "traversal": deps, "evidence_files": list(dict.fromkeys(evidence))},
                    on_render=self._logger("impact", t.raw, evidence))

    # ---- why --------------------------------------------------------------------------------------
    def why(self, target: str, budget: int | None = None) -> Pack:
        t = self.resolve(target)
        budget = budget or self.budget_default
        if not t.found:
            return Pack(f"Why: {target}", [], [f"Couldn't find `{target}`."], budget)
        idx = self.map
        items: list[Item] = []
        nodes = list(t.nodes)
        for n in t.nodes[:1]:
            nodes += idx.members(n)[:30]
        for r in idx.rationale(nodes):
            items.append(Item("Rationale", f"\"{r['text']}\" — {r['file']}:{(r['location'] or '').lstrip('L')} "
                              f"(on {r['for']})", 1.0, f"rationale:{r['id']}"))
        if t.files:
            line = None
            if t.nodes:
                loc = idx.nodes.get(t.nodes[0], {}).get("source_location") or ""
                line = int(loc[1:]) if loc[1:].isdigit() else None
            for o in history.origins(self.project, t.files[0], line):
                tag = "introduced the file" if o.get("introduced") else f"wrote {o.get('lines', 0)} of these lines"
                items.append(Item("Origin", f"{o['sha']} {o.get('title', '')} — {o.get('author', '')}, "
                                  f"{day(o.get('ts'))}, {tag}", 0.9 if o.get("introduced") else 0.8,
                                  f"commit:{o['sha']}"))
            for c in history.recent_for(self.brain, t.files[0], limit=4):
                items.append(Item("Recent changes", f"{c['sha']} {c['title']} — {c['author']}, {ago(c['ts'])}",
                                  0.6 + 0.3 * _recency(c["ts"]), c["id"]))
        items += self._intent(t.files)
        labels = [t.label] + [idx.label(n) for n in t.nodes[:2]]
        items += self._memories(labels, t.files)
        items += self._sessions(t.files)
        items += self._facts(labels)
        where = f" ({', '.join(t.files[:2])})" if t.files and t.files[0] != t.label else ""
        evidence = [*t.files, *self._cited_files(items)]
        return Pack(f"Why: {t.label}{where}", items, [], budget,
                    data={"files": t.files, "target": t.raw, "evidence_files": list(dict.fromkeys(evidence))},
                    on_render=self._logger("why", t.raw, evidence))

    # ---- context for a task (the one call agents should make first) --------------------------------
    def infer_targets(self, text: str, limit: int = 3) -> list[str]:
        idx = self.map
        found: list[str] = []
        for tok in re.findall(r"[\w./-]+\.[A-Za-z0-9]{1,6}|[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)?", text):
            if len(tok) < 4 or tok.lower() in _QUESTION_STOP or tok.lower() in GENERIC:
                continue
            if tok.lstrip("./") in idx.by_file or (("_" in tok or re.search(r"[a-z][A-Z]", tok) or "." in tok)
                                                   and idx.resolve(tok, limit=1)):
                if tok not in found:
                    found.append(tok)
        if len(found) < limit:
            for h in self.brain.search(text, kinds=["symbol", "file"], limit=limit):
                cand = h["title"] if h["kind"] == "file" else h["id"]
                # a heading anchor named "what" (or any content-free label) is not a target, even
                # when full-text search surfaced it for a question built out of question words
                label_toks = set(re.findall(r"[a-z0-9]+", h["title"].lower()))
                if cand not in found and (label_toks - _QUESTION_STOP - GENERIC):
                    found.append(cand)
        return found[:limit]

    def context(self, task: str, targets: list[str] | None = None, budget: int | None = None) -> Pack:
        budget = budget or self.budget_default
        targets = targets or self.infer_targets(task)
        items: list[Item] = []
        per = max(400, budget // max(1, len(targets) + 1))
        labels = []
        evidence: list[str] = []
        for raw in targets[:3]:
            imp = self.impact(raw, depth=1, budget=per)
            wy = self.why(raw, budget=per)
            evidence += imp.data.get("evidence_files", []) + wy.data.get("evidence_files", [])
            labels.append(imp.title.split(":", 1)[-1].strip())
            for it in imp.items + wy.items:
                if it.section in ("Dependents", "Tests likely affected", "Historical warnings", "Changes together",
                                  "Rationale", "Intent", "Memory", "Agent sessions", "Facts"):
                    items.append(Item(it.section, it.text, it.score, it.cite))
            if imp.header:
                items.append(Item("Active work", f"{labels[-1]}: {imp.header[0].replace('**', '')}", 1.1))
        for m in self.memory.recall(task, limit=4):
            items.append(Item("Memory", f"[{m['kind']}] {m['text']}", 0.75, f"memory:{m['id']}"))
        task_toks = {w for w in re.findall(r"[a-z0-9]{4,}", task.lower()) if w not in _QUESTION_STOP and w not in GENERIC}
        if task_toks:
            with contextlib.suppress(sqlite3.Error):
                prior = [r["target"] for r in self.brain.q(
                    "SELECT target, MAX(ts) AS m FROM queries WHERE kind='ask' GROUP BY target ORDER BY m DESC LIMIT 12")
                    if r["target"] and task_toks & {w for w in re.findall(r"[a-z0-9]{4,}", r["target"].lower())
                                                    if w not in _QUESTION_STOP and w not in GENERIC}][:3]
            for q0 in prior:
                items.append(Item("Past questions", q0, 0.7, "ask"))
        active = self.active_spec()
        if active:
            items.append(Item("Active work", f"Spec {active['id']}: {active['name']} — {active['done']}/{active['total']}"
                              f" tasks done", 0.8, active["id"]))
        uniq: dict[tuple[str, str], Item] = {}
        for it in items:
            k = (it.section, it.cite or it.text)
            if k not in uniq or uniq[k].score < it.score:
                uniq[k] = it
        head = [f"Targets: {', '.join(targets) if targets else 'none resolved — showing project memory'}"]
        return Pack(f"Context: {task[:80]}", list(uniq.values()), head, budget,
                    data={"targets": targets, "evidence_files": list(dict.fromkeys(evidence))},
                    on_render=self._logger("context", task, evidence))

    def _remember_qa(self, question: str, answer: str) -> None:
        """Compound: every answered question becomes recallable knowledge for the next one."""
        with contextlib.suppress(Exception):
            if self.brain.q("SELECT 1 FROM memories WHERE text LIKE ? LIMIT 1", (f"Q: {question}%",)):
                return  # the same question already compounded
            self.memory.remember(f"Q: {question}\nA: {' '.join(str(answer).split())[:600]}",
                                 kind="fact", source="ask", provenance="EXTRACTED",
                                 reconcile=False, metadata={"qa": question})

    def ask(self, question: str, budget: int | None = None, llm: bool = True) -> dict:
        pack = self.context(question, budget=budget)
        text = pack.render()
        text += cited_docs(self.project.root, text, question)
        out = {"pack": text, "answer": None, "model": None}
        with contextlib.suppress(sqlite3.Error):  # the audit trail: every question, every surface
            self.brain.log_query(self.surface, "ask", question,
                                 sum(estimate_tokens(ln) for ln in text.splitlines()), None)
        if llm and self.router.available:
            try:
                out["answer"] = self.router.complete("ask", ask_prompt(question, text), system=ASK_SYSTEM,
                                                     cached_context=self.brief(), max_tokens=900)
                out["model"] = self.router.model(self.router.tier_for("ask"))
                if out["answer"]:
                    self._remember_qa(question, out["answer"])
            except Exception as exc:
                out["answer"] = None
                out["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return out

    def narrate(self, pack: Pack, task: str) -> str | None:
        """Optional deep-tier summary on top of a deterministic pack."""
        if not self.router.available:
            return None
        try:
            return self.router.complete(task, pack.render(), system=NARRATE_SYSTEM, cached_context=self.brief(),
                                        max_tokens=500)
        except Exception:
            return None

    def ask_stream(self, question: str, budget: int | None = None, cancel=None) -> Iterator[dict]:
        """``ask`` for the page: the evidence first, then the answer as the model writes it."""
        pack = self.context(question, budget=budget)
        text = pack.render()
        yield from self._narration("ask", ask_prompt(question, text), ASK_SYSTEM, 900, pack, cancel, evidence=text)

    def narrate_stream(self, pack: Pack, task: str, cancel=None) -> Iterator[dict]:
        """``narrate`` for the page, as the model writes it."""
        yield from self._narration(task, pack.render(), NARRATE_SYSTEM, 500, pack, cancel)

    def cite_labels(self, pack: Pack) -> dict[str, str]:
        """A readable name for each citation in a pack, so the page can label what the model cites."""
        idx, out = self.map, {}
        for it in pack.items:
            if not it.cite or it.cite in out:
                continue
            kind, _, ref = it.cite.partition(":")
            if kind == "symbol" and ref in idx.nodes:
                out[it.cite] = idx.label(ref)
            elif kind == "file":
                out[it.cite] = ref
            else:
                text = re.sub(r"^\[\w+\]\s*", "", it.text)
                out[it.cite] = re.sub(r"^\W+", "", text)[:70].rstrip()
        return out

    def _narration(self, task: str, prompt: str, system: str, max_tokens: int, pack: Pack, cancel=None,
                   evidence: str | None = None) -> Iterator[dict]:
        """Events: ``start`` (model, a label for each citation, and the evidence for questions), ``text``
        chunks, then ``done`` or ``error``. Setting ``cancel`` stops the model call."""
        tier = self.router.tier_for(task)
        start = {"type": "start", "model": self.router.model(tier), "tier": tier, "labels": self.cite_labels(pack)}
        yield start if evidence is None else {**start, "pack": evidence}
        try:
            for chunk in self.router.stream(task, prompt, system=system, cached_context=self.brief(),
                                            max_tokens=max_tokens, cancel=cancel):
                yield {"type": "text", "text": chunk}
        except Exception as exc:  # the page shows why; the ledger has already recorded the failed call
            yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"[:300]}
            return
        yield {"type": "done"}

    # ---- memory -----------------------------------------------------------------------------------
    def remember(self, text: str, kind: str = "fact", supersedes: str | None = None, source: str = "user",
                 scope: str = "project") -> dict:
        idx = self.map
        res = self.memory.remember(text, kind=kind, supersedes=supersedes, source=source, scope=scope,
                                   link_symbols=lambda mid, t: link_text(self.brain, idx, f"memory:{mid}", t,
                                                                         "memory-links"))
        with contextlib.suppress(Exception):  # agents that read memory from a file see it now, not at the next sync
            from . import agents
            agents.refresh_context(self.project, self)
        return res

    # ---- overview ---------------------------------------------------------------------------------
    def active_spec(self) -> dict | None:
        best = None
        for s in self.brain.entities("spec"):
            p = s["meta"].get("progress", {})
            done, total = p.get("done", 0), p.get("total", 0)
            if total and done < total:
                cand = {"id": s["id"], "name": s["name"], "done": done, "total": total, "path": s["path"]}
                if best is None or s["id"] > best["id"]:
                    best = cand
        return best

    def overview(self) -> dict:
        idx = self.map
        c = self.brain.counts()
        feats = self.brain.entities("spec")
        tasks = self.brain.entities("task", limit=50_000)
        from .engines import specs as specs_engine
        drift = json.loads(self.brain.get_kv("drift.last", "[]") or "[]")
        return {
            "project": self.project.name,
            "root": str(self.project.root),
            "last_sync": float(self.brain.get_kv("sync.last", "0") or 0),
            "sync_failed": json.loads(self.brain.get_kv("sync.failed", "{}") or "{}"),
            "layers": {
                "map": {"connected": len(idx) > 0, "nodes": len(idx), "edges": idx.edge_count(),
                        "files": len(idx.by_file), "areas": len({n.get("community") for n in idx.nodes.values()}),
                        "languages": idx.languages()},
                "specs": {"connected": specs_engine.initialized(self.project.root), "features": len(feats),
                          "tasks": len(tasks), "done": sum(1 for t in tasks if t["meta"].get("done")),
                          "constitution": json.loads(self.brain.get_kv("specs.constitution", "null") or "null")},
                "timeline": {"connected": self.project.is_git, "commits": c.get("commit", 0),
                             "warnings": self.brain.one("SELECT COUNT(*) n FROM events WHERE kind='commit' AND "
                                                        "meta LIKE '%\"risk\": [\"%'")["n"],
                             "facts": c.get("fact", 0), "deep": self.router.deep_enabled()},
                "memory": {"connected": True, "memories": c.get("memory_active", 0),
                           "semantic": bool(self.memory.semantic.enabled),
                           "reconcile": bool(self.memory.semantic.can_reconcile)},
                "sessions": {"connected": journal.installed(self.project) or journal.available(self.project),
                             "observations": c.get("obs", 0),
                             "sessions": c.get("session", 0)},
            },
            "drift": len(drift),
            "active_spec": self.active_spec(),
            "hubs": idx.hubs(8),
        }

    def brief(self, max_tokens: int = 550) -> str:
        """Compact, stable project briefing — used at agent session start and as a cached prefix."""
        o = self.overview()
        L = o["layers"]
        lines = [f"# Cairn brief: {o['project']}",
                 f"Map: {L['map']['files']} files, {L['map']['nodes']} nodes; hubs: "
                 + ", ".join(f"{h['label']} ({h['file']})" for h in o["hubs"][:5])]
        if L["specs"]["features"]:
            lines.append(f"Specs: {L['specs']['features']} features, {L['specs']['done']}/{L['specs']['tasks']} tasks done")
        if o["active_spec"]:
            a = o["active_spec"]
            lines.append(f"Active spec: {a['id'][5:]} — {a['name']} ({a['done']}/{a['total']}), dir {a['path']}")
        const = L["specs"].get("constitution")
        if const:
            lines.append("Principles: " + "; ".join(const["principles"][:6]))
        mems = self.brain.memories(limit=200)
        # rules first (how the team works), then the newest facts someone chose to remember
        key_mems = [m for m in mems if m["kind"] in ("convention", "decision", "gotcha")][:6]
        key_mems += [m for m in mems if m["kind"] == "fact"][:4]
        if key_mems:
            lines.append("Team knowledge:")
            lines += [f"- [{m['kind']}] {m['text'][:160]} [memory:{m['id']}]" for m in key_mems]
        if o["drift"]:
            lines.append(f"Open drift findings: {o['drift']} (run `cairn drift`)")
        sess = self.brain.events(kinds=["session"], limit=3)
        if sess:
            lines.append("Recent agent work: " + "; ".join(f"{s['title'][:70]} ({ago(s['ts'])})" for s in sess))
        closing = ("Before editing: call cairn_context (or cairn_impact) for the files/symbols you will touch. "
                   "Record durable learnings with cairn_remember.")
        out, used = [], estimate_tokens(closing)  # the instruction always fits; the rest fills the budget in order
        for ln in lines:
            cost = estimate_tokens(ln)
            if used + cost > max_tokens:
                break
            out.append(ln)
            used += cost
        return "\n".join([*out, closing])

    # ---- views for the page ----------------------------------------------------------------------
    def architecture(self) -> dict:
        """The file graph, with what the other layers know about each file."""
        g = self.map.file_graph()
        agent: dict[str, dict[str, int]] = {}
        for r in self.brain.q("SELECT dst, rel, COUNT(*) n FROM links WHERE rel IN ('reads','modifies') "
                              "AND dst LIKE 'file:%' GROUP BY dst, rel"):
            agent.setdefault(r["dst"][5:], {})[r["rel"]] = r["n"]
        owned = {r["dst"][5:]: r["n"] for r in self.brain.q(
            "SELECT dst, COUNT(DISTINCT src) n FROM links WHERE rel='owns' AND dst LIKE 'file:%' GROUP BY dst")}
        stats = {r["path"]: dict(r) for r in self.brain.q("SELECT path, commits, risky FROM filestats")}
        folder = g["level"] == "folder"

        def roll(table: dict, path: str, key: str | None = None) -> int:
            rows = [v for k, v in table.items() if (str(Path(k).parent) == path if folder else k == path)]
            return sum((v.get(key, 0) if key else v) for v in rows) if rows else 0
        for n in g["nodes"]:
            u = n["id"]
            n.update(commits=roll(stats, u, "commits"), fixes=roll(stats, u, "risky"),
                     agent_reads=roll(agent, u, "reads"), agent_edits=roll(agent, u, "modifies"),
                     tasks=roll(owned, u), test=bool(re.search(r"(^|/)tests?(/|$)|(^|/)test_|_test\.", u)))
        return g

    def agent_activity(self, limit: int = 80) -> dict:
        """What agents did here, from Cairn's own session capture."""
        obs = sorted(self.brain.entities("obs"), key=lambda e: -float(e["meta"].get("ts") or 0))[:limit]
        files: dict[str, dict[str, int]] = {}
        for r in self.brain.q("SELECT dst, rel, COUNT(*) n FROM links WHERE rel IN ('reads','modifies') "
                              "AND dst LIKE 'file:%' GROUP BY dst, rel"):
            path = r["dst"][5:]
            if in_repo(path):  # agents also read their own config and scratch files: not this project's
                files.setdefault(path, {"reads": 0, "modifies": 0})[r["rel"]] = r["n"]
        return {
            "source": {"path": ".cairn/sessions.db", "connected": journal.installed(self.project),
                       "captured": journal.available(self.project)},
            "observations": [{"id": e["id"], "title": e["name"], **e["meta"]} for e in obs],
            "sessions": [{"id": e["id"], "title": e["name"], **e["meta"]} for e in self.brain.entities("session")],
            "files": sorted(({"path": k, **v} for k, v in files.items()),
                            key=lambda f: -(f["modifies"] * 2 + f["reads"]))[:40],
        }

    def savings(self, limit: int = 60) -> dict:
        """Every pack handed out, with the size of the source files it summarised."""
        return {"totals": self.brain.query_totals(), "recent": self.brain.queries(limit)}

    def search(self, q: str, kinds: list[str] | None = None, limit: int = 20) -> list[dict]:
        hits = self.brain.search(q, kinds=kinds, limit=limit)
        if not kinds or "symbol" in kinds:
            idx = self.map
            for nid in idx.resolve(q, limit=3):
                sid = f"symbol:{nid}"
                if not any(h["id"] == sid for h in hits):
                    hits.insert(0, {"id": sid, "kind": "symbol", "title": idx.label(nid), "score": 99.0})
        for h in hits:
            e = self.brain.entity(h["id"])
            h["path"] = e["path"] if e else None
        return hits[:limit]


def _recency(ts: float | None, half_life_days: float = 30) -> float:
    if not ts:
        return 0.0
    return 0.5 ** (max(0.0, time.time() - ts) / 86_400 / half_life_days)
