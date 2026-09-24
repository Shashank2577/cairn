"""The Cairn facade: one object every surface (CLI, MCP, HTTP) talks to.

The context assembler turns a question into a ranked, deduplicated, cited pack that fits a token
budget. Everything here is deterministic; a model only narrates on top (``ask``, ``--explain``).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .engines import history, journal, mapper
from .engines.chronicle import facts_matching
from .engines.memory import MemoryStore
from .linker import link_text
from .project import Project
from .router import Router, estimate_tokens
from .store import Brain

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

    def render(self) -> str:
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
        return "\n".join(out)

    def sections(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for it in self.items:
            out.setdefault(it.section, []).append({"text": it.text, "cite": it.cite, "score": round(it.score, 3)})
        return out


class Cairn:
    def __init__(self, project: Project):
        self.project = project
        project.dir.mkdir(exist_ok=True)
        self.brain = Brain(project.db_path)
        self.router = Router(project, self.brain)
        self.memory = MemoryStore(project, self.brain, self.router)

    @classmethod
    def here(cls, start: Path | None = None) -> "Cairn":
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
            for h in self.brain.search(raw, kinds=["symbol", "file"], limit=3):
                ent = self.brain.entity(h["id"])
                if ent and ent["path"]:
                    files.append(ent["path"])
                    if h["id"].startswith("symbol:"):
                        nodes.append(h["id"][7:])
                    break
        label = idx.label(nodes[0]) if nodes and not files[:1] == [norm] else (files[0] if files else raw)
        return Target(raw, label, nodes=nodes[:3], files=files[:4])

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
            reasons.append(f"{len(strong)} files usually change with it")
        open_tasks = [i for i in intent if "(open)" in i.text]
        if open_tasks:
            score += 0.5
            reasons.append(f"{len(open_tasks)} open spec task{'s' if len(open_tasks) > 1 else ''}")
        level = "HIGH" if score >= 4 else "MEDIUM" if score >= 1.5 else "LOW"
        where = f" ({', '.join(t.files[:2])})" if t.files and t.files[0] != t.label else ""
        header = [f"**Risk: {level}**" + (f" — {'; '.join(reasons)}" if reasons else " — no dependents or warnings recorded")]
        return Pack(f"Impact: {t.label}{where}", items, header, budget,
                    data={"risk": level, "reasons": reasons, "files": t.files, "dependents": len(deps),
                          "dependent_files": dep_files[:50], "target": t.raw})

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
        return Pack(f"Why: {t.label}{where}", items, [], budget, data={"files": t.files, "target": t.raw})

    # ---- context for a task (the one call agents should make first) --------------------------------
    def infer_targets(self, text: str, limit: int = 3) -> list[str]:
        idx = self.map
        found: list[str] = []
        for tok in re.findall(r"[\w./-]+\.[A-Za-z0-9]{1,6}|[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)?", text):
            if len(tok) < 4 or tok.lower() in {"this", "that", "with", "from", "into", "should", "would", "change"}:
                continue
            if tok.lstrip("./") in idx.by_file or (("_" in tok or re.search(r"[a-z][A-Z]", tok) or "." in tok)
                                                   and idx.resolve(tok, limit=1)):
                if tok not in found:
                    found.append(tok)
        if len(found) < limit:
            for h in self.brain.search(text, kinds=["symbol", "file"], limit=limit):
                cand = h["title"] if h["kind"] == "file" else h["id"]
                if cand not in found:
                    found.append(cand)
        return found[:limit]

    def context(self, task: str, targets: list[str] | None = None, budget: int | None = None) -> Pack:
        budget = budget or self.budget_default
        targets = targets or self.infer_targets(task)
        items: list[Item] = []
        per = max(400, budget // max(1, len(targets) + 1))
        labels = []
        for raw in targets[:3]:
            imp = self.impact(raw, depth=1, budget=per)
            wy = self.why(raw, budget=per)
            labels.append(imp.title.split(":", 1)[-1].strip())
            for it in imp.items + wy.items:
                if it.section in ("Dependents", "Tests likely affected", "Historical warnings", "Changes together",
                                  "Rationale", "Intent", "Memory", "Agent sessions", "Facts"):
                    items.append(Item(it.section, it.text, it.score, it.cite))
            if imp.header:
                items.append(Item("Active work", f"{labels[-1]}: {imp.header[0].replace('**', '')}", 1.1))
        for m in self.memory.recall(task, limit=4):
            items.append(Item("Memory", f"[{m['kind']}] {m['text']}", 0.75, f"memory:{m['id']}"))
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
        return Pack(f"Context: {task[:80]}", list(uniq.values()), head, budget, data={"targets": targets})

    def ask(self, question: str, budget: int | None = None, llm: bool = True) -> dict:
        pack = self.context(question, budget=budget)
        text = pack.render()
        out = {"pack": text, "answer": None, "model": None}
        if llm and self.router.available:
            system = ("You are Cairn, the engineering memory of this repository. Answer using ONLY the context "
                      "provided; cite ids in [brackets]; say what is unknown. Be brief and concrete.")
            try:
                out["answer"] = self.router.complete("ask", f"Question: {question}\n\nContext:\n{text}",
                                                     system=system, cached_context=self.brief(), max_tokens=900)
                out["model"] = self.router.model(self.router.tier_for("ask"))
            except Exception as exc:
                out["answer"] = None
                out["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return out

    def narrate(self, pack: Pack, task: str) -> str | None:
        """Optional deep-tier summary on top of a deterministic pack."""
        if not self.router.available:
            return None
        system = ("Summarise this engineering evidence for a developer about to change code. 4 bullets max: "
                  "the real risk, what to check, who/what to consult. Cite ids in [brackets]. No preamble.")
        try:
            return self.router.complete(task, pack.render(), system=system, cached_context=self.brief(),
                                        max_tokens=500)
        except Exception:
            return None

    # ---- memory -----------------------------------------------------------------------------------
    def remember(self, text: str, kind: str = "fact", supersedes: str | None = None, source: str = "user") -> dict:
        idx = self.map
        return self.memory.remember(text, kind=kind, supersedes=supersedes, source=source,
                                    link_symbols=lambda mid, t: link_text(self.brain, idx, f"memory:{mid}", t,
                                                                          "memory-links"))

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
                           "semantic": self.router.deep_enabled()},
                "sessions": {"connected": journal.available(), "observations": c.get("obs", 0),
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
        mems = self.brain.memories(limit=40)
        key_mems = [m for m in mems if m["kind"] in ("convention", "decision", "gotcha")][:6]
        if key_mems:
            lines.append("Team knowledge:")
            lines += [f"- [{m['kind']}] {m['text'][:160]} [memory:{m['id']}]" for m in key_mems]
        if o["drift"]:
            lines.append(f"Open drift findings: {o['drift']} (run `cairn drift`)")
        sess = self.brain.events(kinds=["session"], limit=3)
        if sess:
            lines.append("Recent agent work: " + "; ".join(f"{s['title'][:70]} ({ago(s['ts'])})" for s in sess))
        lines.append("Before editing: call cairn_context (or cairn_impact) for the files/symbols you will touch. "
                     "Record durable learnings with cairn_remember.")
        out, used = [], 0
        for ln in lines:
            cost = estimate_tokens(ln)
            if used + cost > max_tokens:
                break
            out.append(ln)
            used += cost
        return "\n".join(out)

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
