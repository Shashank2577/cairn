"""Deterministic checks: each reads the diff and what Cairn already knows, and returns cited findings.

No check calls a model. A finding names its source (`citations`) and says whether it was read (`EXTRACTED`) or
derived (`INFERRED`). Secrets found in the diff are never copied into a finding: only where they are and what kind.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import PurePosixPath

from .diff import Diff, skip_reason

COCHANGE_MIN_COUNT = 3
COCHANGE_MIN_RATIO = 0.6
MAX_PER_CHECK = 8


@dataclass
class Ctx:
    cairn: object
    diff: Diff

    @property
    def brain(self):
        return self.cairn.brain

    @property
    def project(self):
        return self.cairn.project

    @property
    def live_paths(self) -> list[str]:
        return [f.path for f in self.diff.files if f.status != "D"]

    @property
    def touched(self) -> set[str]:
        return {f.path for f in self.diff.files} | {s["path"] for s in self.diff.skipped}


def _f(kind, severity, path, title, body, provenance, citations, line=None, line_end=None) -> dict:
    return {"kind": kind, "severity": severity, "path": path, "title": title, "body": body, "provenance": provenance,
            "citations": citations, "line_start": line, "line_end": line_end or line, "author_kind": "rule"}


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(re.search(r"(^|/)(tests?|__tests__|spec)/", path) or p.name.startswith("test_")
                or re.search(r"(_test|\.test|\.spec)\.\w+$", p.name))


def _commits_touching(ctx: Ctx, a: str, b: str | None = None, limit: int = 3) -> list[str]:
    own = set(ctx.diff.commits)
    out = []
    for ev in ctx.brain.events(kinds=["commit"], ref=f"file:{a}", limit=300):
        if ev["meta"].get("sha") in own:
            continue
        if b is None or f"file:{b}" in ev["refs"]:
            out.append(ev["id"])
        if len(out) >= limit:
            break
    return out


# ---- T010 / T012: files that usually change together --------------------------------------------------
def cochange(ctx: Ctx) -> list[dict]:
    missing: dict[str, list[tuple[str, int]]] = {}
    for p in ctx.live_paths:
        for c in ctx.brain.cochanged(p, limit=8):
            partner = c["path"]
            if c["count"] < COCHANGE_MIN_COUNT or c["ratio"] < COCHANGE_MIN_RATIO or partner in ctx.touched:
                continue
            if not (ctx.project.root / partner).exists() or skip_reason(partner):
                continue
            missing.setdefault(partner, []).append((p, c["count"]))
    out = []
    for partner, hits in sorted(missing.items(), key=lambda kv: -max(n for _, n in kv[1]))[:MAX_PER_CHECK]:
        src, count = max(hits, key=lambda h: h[1])
        cites = _commits_touching(ctx, src, partner)
        if not cites:
            continue
        is_test = is_test_path(partner)
        title = (f"Tests that usually change with `{src}` are not in this change: `{partner}`" if is_test else
                 f"`{partner}` usually changes with `{src}` but is not in this change")
        body = (f"`{partner}` and `{src}` changed together in {count} past commits. This change edits `{src}` "
                f"and leaves `{partner}` as it is. Check that this is intended.")
        out.append(_f("test" if is_test else "cochange", "medium", src, title, body, "INFERRED", cites))
    return out


_TESTABLE = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".rs", ".java", ".kt", ".cs", ".php"}


def tests_by_name(ctx: Ctx) -> list[dict]:
    """A changed source file whose own test file exists but is not in the change (and no test is in the change)."""
    if any(is_test_path(p) for p in ctx.live_paths):
        return []
    tracked = [t for t in ctx.project.git("ls-files").splitlines() if is_test_path(t)]
    already = {f["path"] for f in cochange(ctx) if f["kind"] == "test"}
    out = []
    for p in ctx.live_paths:
        pp = PurePosixPath(p)
        if pp.suffix not in _TESTABLE or is_test_path(p) or p in already or pp.stem in ("__init__", "index"):
            continue
        pats = (f"test_{pp.stem}.*", f"{pp.stem}_test.*", f"{pp.stem}.test.*", f"{pp.stem}.spec.*")
        hit = next((t for t in tracked if any(fnmatch(PurePosixPath(t).name, g) for g in pats)), None)
        if hit and hit not in ctx.touched:
            out.append(_f("test", "low", p, f"`{hit}` tests `{p}` but is not in this change",
                          f"`{p}` changed and its test file `{hit}` did not. Run it, or add a test for the change.",
                          "INFERRED", [f"file:{hit}"]))
    return out[:3]


# ---- T012: dependency change without a lock update ----------------------------------------------------
_MANIFESTS = {"package.json": ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"),
              "pyproject.toml": ("uv.lock", "poetry.lock"), "Pipfile": ("Pipfile.lock",),
              "Cargo.toml": ("Cargo.lock",), "go.mod": ("go.sum",), "Gemfile": ("Gemfile.lock",),
              "composer.json": ("composer.lock",), "pubspec.yaml": ("pubspec.lock",)}
_DEP_LINE = re.compile(r'^\s*"?[@\w./-]+"?\s*[:=]\s*"[\^~<>=*\d]|^\s*"[@\w.-]+\s*[<>=!~]=?|^\s*[\w.-]+\s*=\s*\{?\s*"?[\^~<>=\d]'
                       r'|^\s*(require\s+)?[\w./-]+\s+v\d|^\s*gem\s+[\'"]|^\s*[\w.-]+\s*[<>=~!]=')


def dependency_lock(ctx: Ctx) -> list[dict]:
    tracked = set(ctx.project.git("ls-files").splitlines())
    out = []
    for f in ctx.diff.files:
        pp = PurePosixPath(f.path)
        locks = _MANIFESTS.get(pp.name)
        if not locks:
            continue
        lines = [t for _, t in f.added] + f.removed
        if not any(_DEP_LINE.search(t) for t in lines):
            continue
        sibling = [str(pp.parent / l) if str(pp.parent) != "." else l for l in locks]
        existing = [l for l in sibling if l in tracked]
        if not existing or any(l in ctx.touched for l in existing):
            continue
        out.append(_f("test", "medium", f.path, f"Dependencies changed in `{f.path}` but `{existing[0]}` was not updated",
                      f"The change edits dependency lines in `{f.path}`. `{existing[0]}` is tracked and unchanged, so "
                      "installs will not match.", "EXTRACTED", [f"file:{existing[0]}", f"diff:{f.path}"]))
    return out


# ---- T011: open drift on touched requirements ---------------------------------------------------------
def drift(ctx: Ctx) -> list[dict]:
    from .. import drift as drift_mod
    owners = {l["src"] for l in ctx.brain.links_to([f"file:{p}" for p in ctx.live_paths], rels=("owns",), limit=200)}
    out = []
    try:
        found = drift_mod.check(ctx.cairn)
    except Exception:
        return []
    for d in found:
        by_task = d.get("cite") in owners
        by_path = any(f"`{p}`" in d["title"] for p in ctx.touched)
        if not (by_task or by_path):
            continue
        path = next((p for p in ctx.live_paths if f"`{p}`" in d["title"]), "")
        if not path:
            path = next((l["dst"][5:] for l in ctx.brain.links_from(d["cite"], rels=("owns",)) if l["dst"][5:] in ctx.touched),
                        ctx.live_paths[0] if ctx.live_paths else "")
        sev = {"high": "high", "medium": "medium"}.get(d["severity"], "low")
        out.append(_f("drift", sev, path, d["title"], "\n".join(d.get("evidence") or []), "EXTRACTED",
                      [d["cite"]] if d.get("cite") else [f"spec:{d['spec']}"]))
    return out[:MAX_PER_CHECK]


# ---- T013: secrets in the diff ------------------------------------------------------------------------
_SECRET_SHAPES = [
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("API secret key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}|\bsk-[A-Za-z0-9_-]{24,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]
_ASSIGN = re.compile(r"(?i)\b[\w-]*(?:secret|token|passw(?:or)?d|api[_-]?key|private[_-]?key|credential)[\w-]*[\"']?\s*[:=]\s*"
                     r"[\"']([^\"'\s]{8,})[\"']")
_PLACEHOLDER = re.compile(r"(?i)\$\{|\{\{|<[^>]+>|example|changeme|change-me|xxxx|your[_-]|placeholder|dummy|sample|"
                          r"redacted|\*{3,}|process\.env|environ|getenv|todo|fixme|\bnone\b|\bnull\b")


def _placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER.search(value)) or len(set(value)) <= 2


def secrets(ctx: Ctx) -> list[dict]:
    out = []
    for f in ctx.diff.files:
        if f.status == "D":
            continue
        for n, text in f.added:
            label = next((name for name, rx in _SECRET_SHAPES if rx.search(text)), None)
            sev = "high"
            if not label:
                m = _ASSIGN.search(text)
                if not m or _placeholder(m.group(1)):
                    continue
                label, sev = "hard-coded credential", ("medium" if is_test_path(f.path) else "high")
            out.append(_f("secret", sev, f.path, f"Possible {label} added on line {n}",
                          f"A value that looks like this kind of credential ({label}) is added at `{f.path}:{n}`. The value is not repeated "
                          "here. Remove it, rotate it if it was real, and read it from the environment instead.",
                          "EXTRACTED", [f"diff:{f.path}:{n}"], line=n))
    return out[:MAX_PER_CHECK]


# ---- T018: past fixes and reverts on the touched code -------------------------------------------------
def history(ctx: Ctx) -> list[dict]:
    from ..engines import history as hist
    own = set(ctx.diff.commits)
    by_commit: dict[str, dict] = {}
    for p in ctx.live_paths:
        for c in hist.risky_for(ctx.brain, [p], limit=6):
            ev = ctx.brain.entity(c["id"])
            if not ev or (ev["meta"].get("sha") or "") in own or c["id"].removeprefix("commit:") in own:
                continue
            slot = by_commit.setdefault(c["id"], {**c, "files": []})
            slot["files"].append(p)
    ranked = sorted(by_commit.values(), key=lambda c: ("revert" not in c["risk"], -c["ts"]))[:MAX_PER_CHECK]
    out = []
    for c in ranked:
        reverted = "revert" in c["risk"]
        what = "revert" if reverted else "fix"
        files = ", ".join(f"`{x}`" for x in c["files"][:3])
        out.append(_f("history", "medium" if reverted else "low", c["files"][0],
                      f"This code has a past {what}: \"{c['title'][:80]}\"",
                      f"{files} changed in commit {c['sha']} ({what}: {c['title'][:120]}). "
                      "Check that this change does not bring that problem back.", "INFERRED", [c["id"]]))
    return out


# ---- T019: conventions and gotchas linked to touched files ---------------------------------------------
_MEM_SEVERITY = {"gotcha": "medium", "convention": "low", "decision": "note"}


def conventions(ctx: Ctx) -> list[dict]:
    paths = ctx.live_paths
    if not paths:
        return []
    mems = [m for m in ctx.cairn.memory.about([], paths, limit=12) if m["kind"] in _MEM_SEVERITY]
    out = []
    for m in mems[:MAX_PER_CHECK]:
        linked = {l["dst"][5:] for l in ctx.brain.links_from(f"memory:{m['id']}", limit=100) if l["dst"].startswith("file:")}
        path = next((p for p in paths if p in linked or PurePosixPath(p).stem in m["text"]), paths[0])
        first = m["text"].strip().splitlines()[0][:110]
        out.append(_f("convention", _MEM_SEVERITY[m["kind"]], path, f"A team {m['kind']} applies here: {first}",
                      m["text"][:600], "INFERRED", [f"memory:{m['id']}"]))
    return out


CHECKS = (cochange, tests_by_name, dependency_lock, drift, secrets, history, conventions)


def run_all(cairn, diff: Diff) -> list[dict]:
    ctx = Ctx(cairn, diff)
    out, seen = [], set()
    for check in CHECKS:
        for f in check(ctx):
            key = (f["kind"], f["path"], f["title"])
            if key not in seen:
                seen.add(key)
                out.append(f)
    return out
