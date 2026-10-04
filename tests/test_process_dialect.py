"""The process dialect (foundry-style repos): detection, coverage checks, work items and the
fallback in specs.features. Every fixture is a throwaway tmp tree — never the real clone.

Ground truth is foundry-program's requirements/index.md, requirements/coverage.yaml and its own
checker (dashboards/status.py run_check): satisfaction is the fraction of checks that pass,
computed, never judged.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from cairn import sync
from cairn.core import Cairn
from cairn.drift import check as drift_check
from cairn.engines import process_dialect as pd
from cairn.engines import specs

ENV = {**os.environ,
       "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
       "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com",
       "ANTHROPIC_API_KEY": "", "CAIRN_API_KEY": "", "OPENAI_API_KEY": "",
       "CAIRN_EMBEDDER": "hash", "CAIRN_NO_CLI_MODELS": "1"}

INDEX = """\
# Foundry — Requirement Index (REQ v0)

| ID | Requirement | PRD § |
|---|---|---|
| REQ-001 | All agent coordination happens through the tracker; every action leaves a durable artifact. | §1 |
| REQ-002 | Roles are harness-neutral packs with distinct signed identities. | §3 |
| REQ-003 | Harness adapter: Claude Code, Codex, DeepSeek (et al.) interchangeable behind one dispatch contract; single-harness mode fully functional. | §11.2 |

## Traceability convention

- Branch: `story/FDY-<issue#>-<slug>`
- Commit trailers: `Work-Item: <org>/<repo>#<n>`, `Requirement: REQ-0XX, REQ-0YY`
"""

ONE_REQ = "| ID | Requirement | PRD § |\n|---|---|---|\n| REQ-001 | Everything is traceable. | §1 |\n"

# REQ-001: the exists check passes (dashboards/build.py is seeded), the grep check misses on purpose.
COVERAGE = """\
version: 0
requirements:
  REQ-001:
    summary: Coordination through the tracker
    checks:
      - {exists: dashboards/build.py}
      - {grep: {glob: dashboards/build.py, pattern: "def collect"}}
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          env=ENV, check=True, encoding="utf-8", errors="replace").stdout


def commit(root: Path, files: dict[str, str], msg: str, body: str = "") -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", f"{msg}\n\n{body}" if body else msg)


def make_repo(root: Path, *, index: str = INDEX, coverage: str | None = COVERAGE,
              files: dict[str, str] | None = None, init: bool = False) -> Path:
    root = Path(root)
    (root / "requirements").mkdir(parents=True)
    (root / "requirements" / "index.md").write_text(index, encoding="utf-8")
    if coverage is not None:
        (root / "requirements" / "coverage.yaml").write_text(coverage, encoding="utf-8")
    for rel, text in (files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if init:
        git(root, "init", "-q", "-b", "main")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "Seed the requirement index")
    return root


@pytest.fixture(autouse=True)
def _offline_env(tmp_path, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))  # Path.home() on Windows


# ---- 1. detection -----------------------------------------------------------------------------------

def test_detect_requires_the_index(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    assert pd.detect(root) is False
    (root / "requirements").mkdir()
    (root / "requirements" / "index.md").write_text("| ID | Requirement |\n|---|---|\n| REQ-001 | x | y |\n", encoding="utf-8")
    assert pd.detect(root) is True


# ---- 2. the index table and coverage.yaml become features and tasks ---------------------------------

def test_features_from_index_and_coverage(tmp_path):
    root = make_repo(tmp_path / "r1", files={"dashboards/build.py": "def render(m):\n    return m\n"})
    feats = pd.features(root)
    assert [f["id"] for f in feats] == ["process:req-001", "process:req-002", "process:req-003"]
    f1 = feats[0]
    assert f1["title"] == "All agent coordination happens through the tracker; every action leaves a durable artifact."
    assert f1["path"] == "requirements" and f1["status"] == "" and f1["stories"] == [] and not f1["has_plan"]
    assert f1["artifacts"] == ["coverage.yaml", "index.md"]
    assert [r["id"] for r in f1["requirements"]] == ["REQ-001"]
    t1, t2 = f1["tasks"]
    assert (t1["id"], t1["done"]) == ("T-REQ001-1", True)    # dashboards/build.py is present
    assert (t2["id"], t2["done"]) == ("T-REQ001-2", False)   # 'def collect' is not in it
    assert t1["files"] == [("dashboards/build.py", True)]
    assert f1["progress"] == {"done": 1, "total": 2}


def test_requirements_without_coverage_checks_fall_back_honestly(tmp_path):
    feats = pd.features(make_repo(tmp_path / "r2", coverage=None))
    assert all(len(f["tasks"]) == 1 and f["tasks"][0]["done"] is False for f in feats)
    assert all("coverage.yaml" in f["tasks"][0]["text"] for f in feats)
    assert all(f["progress"] == {"done": 0, "total": 1} for f in feats)


def test_broken_coverage_yaml_never_crashes_the_board(tmp_path):
    feats = pd.features(make_repo(tmp_path / "r3", coverage="version: 0\nrequirements: [oops\n"))
    assert all(f["tasks"][0]["done"] is False for f in feats)


def test_long_requirement_text_is_clipped_to_the_title(tmp_path):
    feats = pd.features(make_repo(tmp_path / "r4", coverage=None))
    assert len(feats[2]["title"]) <= 120 and feats[2]["title"].endswith("…")
    assert feats[2]["requirements"][0]["text"].startswith("Harness adapter:")  # full text survives


# ---- 3. count and glob checks -----------------------------------------------------------------------

def test_evaluate_check_kinds(tmp_path):
    root = tmp_path / "t"
    (root / "a").mkdir(parents=True)
    (root / "a" / "b.txt").write_text("has the needle inside", encoding="utf-8")
    (root / "a" / "c.txt").write_bytes(b"\xff\xfe\x00binary")
    (root / "a" / "d.txt").write_text("nothing here", encoding="utf-8")
    assert pd.evaluate_check({"exists": "a/b.txt"}, root) is True
    assert pd.evaluate_check({"exists": "a/missing.txt"}, root) is False
    assert pd.evaluate_check({"exists": "a"}, root) is True                 # directories count
    assert pd.evaluate_check({"glob": "a/*.txt"}, root) is True
    assert pd.evaluate_check({"glob": "a/*.md"}, root) is False
    assert pd.evaluate_check({"glob": "a/b.txt"}, root) is True             # plain path, no wildcards
    assert pd.evaluate_check({"count": {"glob": "a/b*.txt", "at_least": 1}}, root) is True
    assert pd.evaluate_check({"count": {"glob": "a/b*.txt", "at_least": 2}}, root) is False  # boundary
    assert pd.evaluate_check({"grep": {"glob": "a/*.txt", "pattern": "needle"}}, root) is True
    assert pd.evaluate_check({"grep": {"glob": "a/b.txt", "pattern": "absent"}}, root) is False
    assert pd.evaluate_check({"grep": {"glob": "a/c.txt", "pattern": "x"}}, root) is False   # binary skipped
    assert pd.evaluate_check({"delivered_by_agent": 1}, root) is False      # needs GitHub, fails loudly
    assert pd.evaluate_check({"wat": 1}, root) is False
    assert pd.evaluate_check({"count": {"glob": "a/*.txt"}}, root) is False  # malformed: missing at_least


def test_count_and_glob_checks_score_the_feature(tmp_path):
    cov = """\
version: 0
requirements:
  REQ-001:
    summary: role packs
    checks:
      - {count: {glob: "role-packs/*/pack.yaml", at_least: 2}}
      - {glob: "policies/*.yaml"}
      - {glob: "policies/missing*.yaml"}
"""
    root = make_repo(tmp_path / "r5", index=ONE_REQ, coverage=cov,
                     files={"role-packs/dev/pack.yaml": "x", "role-packs/qa/pack.yaml": "x",
                            "policies/gates.yaml": "y"})
    feats = pd.features(root)
    assert [t["done"] for t in feats[0]["tasks"]] == [True, True, False]  # count boundary: exactly 2
    assert feats[0]["progress"] == {"done": 2, "total": 3}


# ---- 4. work items ----------------------------------------------------------------------------------

def test_work_items_merged_open_and_req_linkage(tmp_path):
    root = make_repo(tmp_path / "r6", init=True,
                     files={"dashboards/build.py": "x", "policies/gates.yaml": "y"})
    git(root, "checkout", "-q", "-b", "story/FDY-18-tracker-sync")
    commit(root, {"dashboards/build.py": "synced"}, "Add tracker sync",
           "Work-Item: acme/foundry#18\nRequirement: REQ-001\nAgent-Role: developer\nHarness: claude-code/1.0")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "-m", "Merge pull request #18", "story/FDY-18-tracker-sync")
    git(root, "checkout", "-q", "-b", "bug/FDY-200-unbound-var")
    commit(root, {"policies/gates.yaml": "fixed"}, "Fix unbound var",
           "Work-Item: acme/foundry#200\nRequirement: REQ-002\nAgent-Role: developer\nHarness: claude-code/1.0")
    git(root, "checkout", "-q", "main")

    feats = pd.features(root)
    req1 = next(f for f in feats if f["id"] == "process:req-001")
    fdy18 = next(t for t in req1["tasks"] if t["id"] == "fdy-18")
    assert fdy18["done"] is True and fdy18["text"] == "story FDY-18 tracker-sync (merged)"
    assert fdy18["reqs"] == ["REQ-001"] and fdy18["files"] == []
    req2 = next(f for f in feats if f["id"] == "process:req-002")
    fdy200 = next(t for t in req2["tasks"] if t["id"] == "fdy-200")
    assert fdy200["done"] is False and fdy200["text"] == "bug FDY-200 unbound-var (open)"
    assert all(t["id"] != "fdy-200" for t in req1["tasks"])               # no cross-linking leakage
    assert req1["progress"] == {"done": 2, "total": 3}  # 1 of 2 checks + the merged work item


def test_squash_merged_work_item_is_merged_via_its_trailer(tmp_path):
    root = make_repo(tmp_path / "r7", init=True)
    git(root, "checkout", "-q", "-b", "story/FDY-55-side-quest")
    commit(root, {"side.txt": "branch work"}, "Branch work")               # never merged
    git(root, "checkout", "-q", "main")
    commit(root, {"dashboards/build.py": "squash"}, "Add the side quest (squash)",
           "Work-Item: acme/foundry#55\nRequirement: REQ-003")             # squash: tip is NOT an ancestor
    feats = pd.features(root)
    req3 = next(f for f in feats if f["id"] == "process:req-003")
    fdy55 = next(t for t in req3["tasks"] if t["id"] == "fdy-55")
    assert fdy55["done"] is True and "merged" in fdy55["text"]             # the trailer on HEAD is the trace


def test_unlinked_work_items_get_their_own_feature(tmp_path):
    root = make_repo(tmp_path / "r8", init=True)
    git(root, "checkout", "-q", "-b", "chore/FDY-77-tidy-readme")
    commit(root, {"README.md": "tidy"}, "Tidy the readme")                 # no Requirement trailer
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "-m", "Merge PR #77", "chore/FDY-77-tidy-readme")
    feats = pd.features(root)
    extra = next(f for f in feats if f["id"] == "process:work-items")
    task = extra["tasks"][0]
    assert (task["id"], task["done"]) == ("fdy-77", True)
    assert task["reqs"] == [] and "chore FDY-77 tidy-readme (merged)" == task["text"]
    assert all(t["id"] != "fdy-77" for f in feats if f["id"] != "process:work-items" for t in f["tasks"])


# ---- 5. the specs.features fallback -----------------------------------------------------------------

def test_specs_features_fall_back_to_the_dialect(tmp_path):
    feats = specs.features(make_repo(tmp_path / "r9", files={"dashboards/build.py": "x"}))
    assert [f["id"] for f in feats] == ["process:req-001", "process:req-002", "process:req-003"]


def test_specs_features_fall_back_when_the_specs_dir_is_empty(tmp_path):
    root = make_repo(tmp_path / "r10", files={"dashboards/build.py": "x"})
    (root / "specs").mkdir()
    feats = specs.features(root)
    assert feats and feats[0]["id"] == "process:req-001"


def test_specs_features_still_prefer_spec_kit(tmp_path):
    root = make_repo(tmp_path / "r11")
    d = root / "specs" / "001-refunds"
    d.mkdir(parents=True)
    (d / "spec.md").write_text("# Feature Specification: Refunds\n\n**Status**: Draft\n", encoding="utf-8")
    assert [f["id"] for f in specs.features(root)] == ["001-refunds"]      # the dialect is not mixed in


def test_specs_features_on_a_plain_repo(tmp_path):
    (tmp_path / "src").mkdir()
    assert specs.features(tmp_path) == []


def test_dialect_features_match_the_spec_kit_shape(tmp_path):
    dialect_root = make_repo(tmp_path / "d1", files={"dashboards/build.py": "x"})
    sk = tmp_path / "d2"
    d = sk / "specs" / "001-x"
    d.mkdir(parents=True)
    (d / "spec.md").write_text("# Feature Specification: X\n\n**Status**: Draft\n\n- **FR-001**: Must do x.\n", encoding="utf-8")
    (d / "tasks.md").write_text("# Tasks\n\n## Phase 1\n\n- [x] T001 Add the thing\n", encoding="utf-8")
    dialect, speckit = pd.features(dialect_root)[0], specs.features(sk)[0]
    assert set(dialect) == set(speckit)
    assert set(dialect["tasks"][0]) == set(speckit["tasks"][0])
    assert dialect["progress"] == {"done": 1, "total": 2} and speckit["progress"] == {"done": 1, "total": 1}


# ---- 6. the read model: ingest and drift over dialect features --------------------------------------

def test_ingest_records_progress_for_dialect_features(tmp_path):
    root = make_repo(tmp_path / "r12", init=True, files={"dashboards/build.py": "def render(m):\n    return m\n"})
    c = Cairn.here(root)
    c.project.ensure_dir()
    sync.run(c)
    computed = {f["id"]: f["progress"] for f in specs.features(root)}
    rows = {r["id"]: r for r in c.brain.q(
        "SELECT id, refs, meta FROM events WHERE kind='spec' AND id LIKE 'spec:process:%'")}
    assert set(rows) == {f"spec:{fid}:progress" for fid in computed}
    for fid, prog in computed.items():
        meta = json.loads(rows[f"spec:{fid}:progress"]["meta"])
        assert (meta["done"], meta["total"]) == (prog["done"], prog["total"])
    assert any("T-REQ001-1" in r["id"] for r in c.brain.q("SELECT id FROM entities WHERE id LIKE 'task:process:%'"))
    assert c.brain.q("SELECT id FROM entities WHERE id = 'spec:process:req-001'")
    assert drift_check(c) == []  # dialect features flow through drift without findings or crashes
