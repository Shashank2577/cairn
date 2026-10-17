"""Run a review: resolve the change, run the deterministic checks, attach project context, store the result.

No model is called on this path. `run` is the one entry point for the CLI, and later the page, the agent tool
and the server.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import checks, diff as diffmod, store

FAIL_ORDER = {"high": 0, "medium": 1, "low": 2, "note": 3}


@dataclass
class Outcome:
    review: dict | None            # None when there was nothing to review
    findings: list[dict]
    note: str = ""

    @property
    def empty(self) -> bool:
        return self.review is None


def context_for(cairn, paths: list[str]) -> dict:
    """The spec tasks that own the touched files and the recent agent work on them (kept with the review)."""
    from ..engines import journal
    brain = cairn.brain
    tasks, seen = [], set()
    for l in brain.links_to([f"file:{p}" for p in paths], rels=("owns",), limit=100):
        t = brain.entity(l["src"])
        if t and t["id"] not in seen:
            seen.add(t["id"])
            tasks.append({"id": t["id"], "title": t["name"], "done": bool(t["meta"].get("done"))})
    sessions = [{"id": o["id"], "title": o["title"], "ts": o["ts"]} for o in journal.for_files(brain, paths, limit=5)]
    return {"tasks": tasks[:8], "sessions": sessions}


def run(cairn, target: str | None = None, base: str | None = None, staged: bool = False, trigger: str = "cli") -> Outcome:
    project = cairn.project
    d = diffmod.resolve(project, target, base, staged)
    if d.empty:
        return Outcome(None, [], f"Nothing to review: no changes against {d.base_ref}.")
    review = store.start_review(cairn.brain, project=project.name, branch=d.branch, base_sha=d.base_sha,
                                head_sha=d.head_sha, trigger=trigger, pr_number=d.pr_number)
    try:
        found = checks.run_all(cairn, d)
        findings = store.add_findings(cairn.brain, review["id"], found)
        review = store.finish_review(cairn.brain, review["id"], skipped=d.skipped, context=context_for(cairn, d.paths))
    except BaseException:
        store.finish_review(cairn.brain, review["id"], status="failed")
        raise
    return Outcome(review, findings)


def should_fail(findings: list[dict], fail_on: str) -> bool:
    if fail_on == "never":
        return False
    limit = FAIL_ORDER.get(fail_on, 0)
    return any(FAIL_ORDER[f["severity"]] <= limit for f in findings if f["state"] != "dismissed")
