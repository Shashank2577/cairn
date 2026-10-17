"""The no-model review on the seeded repository (T017: SC-001, SC-003, SC-008) and the CLI."""
from __future__ import annotations

import json
import re
import time

from typer.testing import CliRunner

from cairn.cli import app
from cairn.review import pipeline, store
from tests.review.fixtures import seeded

runner = CliRunner()


def _by_kind(findings):
    out = {}
    for f in findings:
        out.setdefault(f["kind"], []).append(f)
    return out


def test_seeded_issues_are_found_with_citations(seeded_cairn):
    t0 = time.time()
    out = pipeline.run(seeded_cairn)
    assert time.time() - t0 < 5  # SC-001 (on the fixture; the 5,000-file figure is measured by hand)
    k = _by_kind(out.findings)
    titles = " | ".join(f["title"] for f in out.findings)
    found = {
        "cochange": any(seeded.SEEDED["cochange"] in f["title"] for f in k.get("cochange", [])),
        "test": any(seeded.SEEDED["test"] in f["title"] for f in k.get("test", [])),
        "history": any(seeded.SEEDED["history"] in f["title"] for f in k.get("history", [])),
        "convention": any(seeded.SEEDED["convention"] in f["title"] for f in k.get("convention", [])),
        "secret": any(seeded.SEEDED["secret"] in f["title"] for f in k.get("secret", [])),
        "lock": any(seeded.SEEDED["lock"] in f["title"] for f in k.get("test", [])),
    }
    assert all(found.values()), (found, titles)  # SC-003: every seeded issue
    assert len(out.findings) - len(found) <= 1, titles  # at most one unrelated finding
    for f in out.findings:  # SC-002
        assert f["provenance"] in ("EXTRACTED", "INFERRED") and f["citations"]


def test_history_and_cochange_cite_commits_not_on_the_branch(seeded_cairn):
    out = pipeline.run(seeded_cairn)
    own = set(__import__("cairn.review.diff", fromlist=["x"]).resolve(seeded_cairn.project).commits)
    for f in out.findings:
        for c in f["citations"]:
            if c.startswith("commit:"):
                assert c[7:] not in own


def test_secret_value_is_never_copied_into_a_finding(seeded_cairn):
    out = pipeline.run(seeded_cairn)
    blob = json.dumps(out.findings)
    assert "AKIAIOSFODNN7EXAMPLE" not in blob


def test_placeholders_are_not_secrets(seeded_cairn):
    from tests.conftest import commit
    commit(seeded_cairn.project.root, {"shop/cfg.py": 'password = "changeme-please"\ntoken = os.environ["T"]\n'
                                                       'api_key = "${API_KEY}"\n'}, "config")
    out = pipeline.run(seeded_cairn)
    assert not any(f["path"] == "shop/cfg.py" for f in out.findings if f["kind"] == "secret")


def test_real_looking_assignment_is_a_secret(seeded_cairn):
    from tests.conftest import commit
    commit(seeded_cairn.project.root, {"shop/cfg.py": 'db_password = "s3cr3tValue99xy"\n'}, "config")
    out = pipeline.run(seeded_cairn)
    assert any(f["path"] == "shop/cfg.py" and f["kind"] == "secret" for f in out.findings)


def test_rerun_keeps_one_review_and_one_set_of_findings(seeded_cairn):
    a = pipeline.run(seeded_cairn)
    b = pipeline.run(seeded_cairn)
    assert a.review["id"] == b.review["id"]
    assert len(store.list_reviews(seeded_cairn.brain)) == 1
    assert len(b.findings) == len(a.findings)


def test_nothing_to_review_says_so(repo, cairn):
    out = pipeline.run(cairn, base="HEAD")
    assert out.empty and "Nothing to review" in out.note


def test_cli_text_run_list_show_and_forget(seeded_cairn):
    r = runner.invoke(app, ["review"])
    assert r.exit_code == 0, r.output
    assert "A model review was not run" in r.output and "AWS access key" in r.output
    rid = re.search(r"Review (\w+):", r.output).group(1)
    assert rid in runner.invoke(app, ["review", "list"]).output
    assert "integer cents" in runner.invoke(app, ["review", "show", rid]).output
    assert runner.invoke(app, ["review", "show", "zzzz"]).exit_code == 2
    assert runner.invoke(app, ["review", "--forget", rid]).exit_code == 0
    assert "No reviews yet" in runner.invoke(app, ["review", "list"]).output


def test_cli_ci_fails_on_high_and_honors_fail_on(seeded_cairn):
    r = runner.invoke(app, ["review", "--ci"])
    data = json.loads(r.output)
    assert r.exit_code == 1 and data["failed"] and data["model_used"] is False and data["counts"]["high"] >= 1
    assert runner.invoke(app, ["review", "--ci", "--fail-on", "never"]).exit_code == 0
    assert runner.invoke(app, ["review", "--fail-on", "high"]).exit_code == 0  # advisory without --ci
    assert runner.invoke(app, ["review", "--ci", "--fail-on", "bogus"]).exit_code == 2


def test_cli_unknown_base_is_exit_2(seeded_cairn):
    r = runner.invoke(app, ["review", "--base", "no-such-ref"])
    assert r.exit_code == 2


def test_staged_changes_are_reviewed(seeded_cairn):
    from tests.conftest import git
    root = seeded_cairn.project.root
    (root / "shop" / "s.py").write_text('github = "ghp_' + "a1B2c3D4e5" * 4 + '"\n', encoding="utf-8")
    git(root, "add", "shop/s.py")
    out = pipeline.run(seeded_cairn, staged=True)
    assert any(f["kind"] == "secret" for f in out.findings) and out.review["head_sha"].startswith("staged-")
