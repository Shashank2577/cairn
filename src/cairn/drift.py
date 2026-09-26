"""Drift: where reality (code, history) disagrees with intent (specs).

Deterministic checks run on every sync. The semantic check (deep tier) asks a judgment model about
a handful of requirements only — the ones whose code changed most recently — to bound cost.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from .engines import specs as specs_engine
from .router import Budget

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _finding(kind, severity, spec, title, evidence, cite=""):
    fid = f"{kind}:{spec}:{hashlib.sha1(title.encode()).hexdigest()[:8]}"  # stable across processes
    return {"id": fid, "kind": kind, "severity": severity, "spec": spec, "title": title, "evidence": evidence,
            "cite": cite}


def check(cairn, spec: str | None = None) -> list[dict]:
    project, brain = cairn.project, cairn.brain
    out: list[dict] = []
    for f in specs_engine.features(project.root):
        if spec and not f["id"].startswith(spec):
            continue
        tasks_rel = f"{f['path']}/tasks.md"
        tasks_ts = float(project.git("log", "-1", "--format=%at", "--", tasks_rel).strip() or 0)
        referenced = {r for t in f["tasks"] for r in t["reqs"]} | set(f["plan_mentions"])
        for t in f["tasks"]:
            cite = f"task:{f['id']}/{t['id']}"
            if not t["done"]:
                continue
            for path, exists in t["files"]:
                if not exists and not (project.root / path).exists():
                    out.append(_finding("missing-file", "high", f["id"], f"{t['id']} is done but `{path}` does not exist",
                                        [f"{tasks_rel}: {t['id']} {t['text'][:120]}"], cite))
                    continue
                st = brain.filestat(path)
                if st and tasks_ts and st["last_ts"] > tasks_ts + 86_400:
                    out.append(_finding("changed-after-done", "low", f["id"],
                                        f"`{path}` changed after {t['id']} was completed — re-verify",
                                        [f"task last updated {time.strftime('%Y-%m-%d', time.localtime(tasks_ts))}",
                                         f"file last changed {time.strftime('%Y-%m-%d', time.localtime(st['last_ts']))}"],
                                        cite))
        if referenced:  # only when the feature uses requirement ids in plan/tasks
            for r in f["requirements"]:
                if r["id"].startswith("FR") and r["id"] not in referenced:
                    out.append(_finding("uncovered", "medium", f["id"], f"{r['id']} has no task or plan reference",
                                        [r["text"][:200]], f"req:{f['id']}/{r['id']}"))
        if f["progress"]["total"] and f["progress"]["done"] == f["progress"]["total"] and \
                re.search(r"draft", f["status"], re.I):
            out.append(_finding("status", "low", f["id"], "All tasks are done but the spec is still marked Draft",
                                [f"{f['path']}/spec.md: Status {f['status']}"], f"spec:{f['id']}"))
    out.sort(key=lambda d: (SEVERITY_ORDER[d["severity"]], d["spec"]))
    return out


def semantic(cairn, spec: str | None = None, max_reqs: int = 5, budget: Budget | None = None) -> list[dict]:
    """Deep tier: judge whether recently-changed code still satisfies its requirements."""
    if not cairn.router.available:
        return []
    project, brain = cairn.project, cairn.brain
    budget = budget or Budget(int(project.cfg("deep.budget_tokens", 150000)) // 3)
    findings = []
    for f in specs_engine.features(project.root):
        if spec and not f["id"].startswith(spec):
            continue
        scored = []
        for r in f["requirements"]:
            if not r["id"].startswith("FR"):
                continue
            files = sorted({p for t in f["tasks"] if r["id"] in t["reqs"] for p, ok in t["files"] if ok})
            if not files:
                continue
            recent = max(((brain.filestat(p) or {}).get("last_ts", 0) for p in files), default=0)
            scored.append((recent, r, files))
        for _, r, files in sorted(scored, key=lambda x: -x[0])[:max_reqs]:
            code = []
            for p in files[:3]:
                try:
                    code.append(f"--- {p}\n" + (project.root / p).read_text(errors="replace")[:3000])
                except OSError:
                    pass
            prompt = (f"Requirement {r['id']}: {r['text']}\n\nCode:\n" + "\n".join(code) +
                      '\n\nReply with JSON only: {"verdict":"ok"|"violation"|"unclear","evidence":"<=40 words"}')
            try:
                raw = cairn.router.complete("drift", prompt, system="You review code against requirements. Be strict "
                                            "about violations, never speculate beyond the code shown.",
                                            max_tokens=200, budget=budget)
                data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            except Exception:
                continue
            if data.get("verdict") == "violation":
                findings.append(_finding("semantic", "high", f["id"], f"{r['id']} may be violated",
                                         [data.get("evidence", ""), *files], f"req:{f['id']}/{r['id']}"))
    return findings


def record(cairn, findings: list[dict]) -> None:
    cairn.brain.set_kv("drift.last", json.dumps(findings))
    now = time.time()
    ids = [f"drift:{d['id']}" for d in findings]
    # a finding keeps the time it was first detected; re-running the check must not move it on the timeline
    seen = {r["id"]: r["ts"] for r in cairn.brain.q(
        f"SELECT id, ts FROM events WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    cairn.brain.add_events([{"id": f"drift:{d['id']}", "ts": seen.get(f"drift:{d['id']}", now), "kind": "drift",
                             "title": d["title"], "body": "\n".join(d["evidence"]),
                             "refs": [d["cite"]] if d["cite"] else [],
                             "meta": {"severity": d["severity"], "spec": d["spec"]}, "source": "drift"}
                            for d in findings if d["severity"] != "low"])
