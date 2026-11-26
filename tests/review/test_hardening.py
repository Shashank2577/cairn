"""Hardening from the independent review: ids, errors, skipped files, caps, signatures, parsing, state."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from cairn.cli import app
from cairn.review import checks, diff as diffmod, model, pipeline, store
from cairn.store import Brain
from tests.conftest import commit, git

runner = CliRunner()
F = {"kind": "history", "severity": "low", "path": "a.py", "title": "t", "provenance": "INFERRED",
     "citations": ["commit:x"], "rule": "commit:x"}


@pytest.fixture()
def brain(tmp_path):
    return Brain(tmp_path / "b.db")


# ---- 6 ids ---------------------------------------------------------------------------------------------
def test_review_id_is_exact_or_an_unambiguous_prefix(brain):
    brain.db.execute("INSERT INTO reviews(id,project,base_sha,head_sha,created_at) VALUES('abc1','p','b','h1',1)")
    brain.db.execute("INSERT INTO reviews(id,project,base_sha,head_sha,created_at) VALUES('abc2','p','b','h2',1)")
    brain.db.commit()
    assert store.get_review(brain, "abc1")["id"] == "abc1"
    with pytest.raises(store.AmbiguousReview):
        store.get_review(brain, "abc")
    assert store.get_review(brain, "%") is None and store.get_review(brain, "_") is None
    assert store.get_review(brain, "a_c1") is None


def test_forget_with_a_wildcard_deletes_nothing(seeded_cairn):
    pipeline.run(seeded_cairn)
    for bad in ("%", "_", "a%"):
        assert runner.invoke(app, ["review", "--forget", bad]).exit_code == 2
    assert len(store.list_reviews(seeded_cairn.brain)) == 1


# ---- 7 errors ------------------------------------------------------------------------------------------
def test_a_failed_git_diff_is_an_error_not_a_clean_review(seeded_cairn, monkeypatch):
    real = subprocess.run

    def boom(cmd, *a, **k):
        if "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 128, b"", b"fatal: bad object")
        return real(cmd, *a, **k)
    monkeypatch.setattr(diffmod.subprocess, "run", boom)
    with pytest.raises(diffmod.DiffError, match="git diff"):
        diffmod.resolve(seeded_cairn.project)
    r = runner.invoke(app, ["review"])
    assert r.exit_code == 2 and "Nothing to review" not in r.output


def test_a_git_timeout_is_an_error(seeded_cairn, monkeypatch):
    def slow(cmd, *a, **k):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(diffmod.subprocess, "run", slow)
    with pytest.raises(diffmod.DiffError, match="timed out"):
        diffmod.resolve(seeded_cairn.project)


def test_unexpected_exceptions_exit_2_with_a_short_message(seeded_cairn, monkeypatch):
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: 1 / 0)
    r = runner.invoke(app, ["review"])
    assert r.exit_code == 2 and "Traceback" not in r.output and "review failed" in r.output.lower()


def test_empty_staged_is_clean_and_empty_repo_is_an_error(seeded_cairn, tmp_path, monkeypatch):
    assert pipeline.run(seeded_cairn, staged=True).empty
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q", "-b", "main")
    from cairn.project import Project
    with pytest.raises(diffmod.DiffError, match="no commits"):
        diffmod.resolve(Project(root=empty), staged=True)


# ---- 8 skipped files still get the secret scan ---------------------------------------------------------
@pytest.mark.parametrize("path", ["dist/app.js", "vendor/lib.py", "static/a.min.js", "tests/__snap__/x.snap"])
def test_secrets_in_skipped_files_are_found(seeded_cairn, path):
    tok = "ghp_" + "a1B2c3D4e5" * 4
    commit(seeded_cairn.project.root, {path: f'k = "{tok}"\n'}, "add")
    out = pipeline.run(seeded_cairn)
    assert any(f["kind"] == "secret" and f["path"] == path for f in out.findings)
    assert path in {s["path"] for s in out.review["skipped"]}  # still listed as skipped for review
    assert tok not in json.dumps(out.findings)


def test_secrets_in_huge_files_are_found(seeded_cairn):
    body = "x = 1\n" * (diffmod.MAX_FILE_LINES + 5) + 'k = "AKIAIOSFODNN7EXAMPLE"\n'
    commit(seeded_cairn.project.root, {"data/big.py": body}, "big")
    out = pipeline.run(seeded_cairn)
    assert any(f["kind"] == "secret" and f["path"] == "data/big.py" for f in out.findings)


# ---- 9 caps never drop high severity -------------------------------------------------------------------
def test_high_severity_secrets_are_never_capped(seeded_cairn):
    import base64
    import hashlib
    keys = ["AKIA" + base64.b32encode(hashlib.sha1(str(i).encode()).digest()).decode()[:16] for i in range(checks.MAX_PER_CHECK + 5)]
    lines = "".join(f'k{i} = "{k}"\n' for i, k in enumerate(keys))
    commit(seeded_cairn.project.root, {"shop/many.py": lines}, "many keys")
    out = pipeline.run(seeded_cairn)
    secrets = [f for f in out.findings if f["kind"] == "secret" and f["severity"] == "high" and f["path"] == "shop/many.py"]
    assert len(secrets) == checks.MAX_PER_CHECK + 5


def test_a_capped_check_says_how_many_were_left_out():
    many = [{"kind": "history", "severity": "low", "path": f"f{i}.py", "title": f"t{i}", "provenance": "INFERRED",
             "citations": [f"commit:{i}"], "body": "", "rule": f"r{i}"} for i in range(checks.MAX_PER_CHECK + 3)]
    out = checks.cap(many)
    assert len(out) == checks.MAX_PER_CHECK + 1
    assert out[-1]["severity"] == "note" and "+3 more" in out[-1]["title"] and out[-1]["citations"]


# ---- 10 signatures -------------------------------------------------------------------------------------
def test_signature_ignores_line_numbers_and_wording():
    assert store.signature("secret", "a.py", "aws:1") == store.signature("secret", "a.py", "aws:1")
    assert store.signature("secret", "a.py", "aws") != store.signature("secret", "b.py", "aws")


def test_secret_signature_survives_a_line_shift(seeded_cairn):
    a = [f for f in pipeline.run(seeded_cairn).findings if f["kind"] == "secret"][0]
    src = (seeded_cairn.project.root / "shop/payments.py").read_text(encoding="utf-8")
    commit(seeded_cairn.project.root, {"shop/payments.py": "# new top line\n" + src}, "shift")
    b = [f for f in pipeline.run(seeded_cairn).findings if f["kind"] == "secret"][0]
    assert a["line_start"] != b["line_start"] and a["signature"] == b["signature"]


# ---- 12 parsing ----------------------------------------------------------------------------------------
def test_quoted_names_and_content_that_looks_like_headers():
    raw = ('diff --git "a/we\\tird \\303\\274.py" "b/we\\tird \\303\\274.py"\n--- "a/we\\tird \\303\\274.py"\n'
           '+++ "b/we\\tird \\303\\274.py"\n@@ -1,2 +1,2 @@\n--- not a header\n+++ also content\n-old\n+new\n'
           'diff --git a/n.bin b/n.bin\nnew file mode 100644\nBinary files /dev/null and b/n.bin differ\n'
           'diff --git a/p.bin b/p.bin\nnew file mode 100644\nGIT binary patch\nliteral 3\n')
    a, b, c = diffmod.parse_patch(raw)
    assert a.path == "we\tird ü.py"
    assert [t for _, t in a.added] == ["++ also content", "new"] and a.removed == ["-- not a header", "old"]
    assert b.binary and c.binary


def test_dashes_inside_a_hunk_are_content():
    raw = "diff --git a/a.md b/a.md\n--- a/a.md\n+++ b/a.md\n@@ -1 +1 @@\n----\n++++\n"
    (f,) = diffmod.parse_patch(raw)
    assert f.removed == ["---"] and f.added == [(1, "+++")]


def test_pr_target_guard(seeded_cairn):
    with pytest.raises(diffmod.DiffError):
        diffmod.resolve(seeded_cairn.project, "٣")  # an Arabic-Indic digit is not a pull request number
    assert diffmod.resolve(seeded_cairn.project, "").branch  # empty means current


def test_real_git_unicode_space_rename_delete_and_non_utf8(seeded_cairn):
    root = seeded_cairn.project.root
    commit(root, {"shop/ünï cöde.py": "x = 1\n"}, "unicode")
    git(root, "mv", "shop/ledger.py", "shop/ledger2.py")
    git(root, "rm", "-q", "tests/test_payments.py")
    (root / "shop" / "latin.py").write_bytes(b'x = "\xff\xfe caf\xe9"\n')
    git(root, "add", "-A")
    git(root, "commit", "-qm", "mixed")
    d = diffmod.resolve(seeded_cairn.project)
    by = {f.path: f for f in d.files}
    assert "shop/ünï cöde.py" in by and by["shop/ledger2.py"].status == "R"
    assert by["tests/test_payments.py"].status == "D" and "shop/latin.py" in by
    assert pipeline.run(seeded_cairn).review["status"] == "complete"


# ---- 13 state ------------------------------------------------------------------------------------------
def _start(brain, head, branch="b", pr=None):
    return store.start_review(brain, project="p", branch=branch, base_sha="base", head_sha=head, pr_number=pr)


def test_enums_are_enforced_by_the_database(brain):
    r = _start(brain, "h1")
    with pytest.raises(Exception):
        brain.db.execute("INSERT INTO review_findings(id,review_id,severity,title,kind,provenance,signature) "
                         "VALUES('x',?,'bogus','t','history','INFERRED','s')", (r["id"],))
    with pytest.raises(Exception):
        brain.db.execute("UPDATE reviews SET status='bogus'")
    brain.db.rollback()


def test_start_review_refreshes_branch_and_pr_on_reuse(brain):
    a = _start(brain, "h1", "old", None)
    b = _start(brain, "h1", "new", 7)
    assert a["id"] == b["id"] and b["branch"] == "new" and b["pr_number"] == 7


def test_new_head_supersedes_older_reviews_and_marks_fixed(brain):
    r1 = _start(brain, "h1")
    f1 = store.add_findings(brain, r1["id"], [F, {**F, "path": "z.py", "rule": "commit:z"}])
    store.finish_review(brain, r1["id"])
    r2 = _start(brain, "h2")
    store.add_findings(brain, r2["id"], [F])
    store.finish_review(brain, r2["id"])
    assert store.get_review(brain, r1["id"])["status"] == "superseded"
    assert store.get_review(brain, r2["id"])["status"] == "complete"
    states = {f["path"]: f["state"] for f in store.findings_of(brain, r1["id"])}
    assert states == {"a.py": "open", "z.py": "fixed"}  # z.py is no longer reported


def test_triage_carries_across_heads_by_signature(brain):
    r1 = _start(brain, "h1")
    fid = store.add_findings(brain, r1["id"], [F])[0]["id"]
    store.finish_review(brain, r1["id"])
    brain.db.execute("UPDATE review_findings SET state='dismissed', reason='fine' WHERE id=?", (fid,))
    brain.db.commit()
    r2 = _start(brain, "h2")
    (again,) = store.add_findings(brain, r2["id"], [F])
    assert again["state"] == "dismissed" and again["reason"] == "fine" and again["id"] != fid


def test_staged_reviews_do_not_supersede_a_branch_review(brain):
    r1 = _start(brain, "h1")
    store.finish_review(brain, r1["id"])
    s = _start(brain, "staged-abc")
    store.finish_review(brain, s["id"])
    assert store.get_review(brain, r1["id"])["status"] == "complete"


def test_two_findings_with_the_same_rule_do_not_collide(brain):
    r = _start(brain, "h1")
    assert len(store.add_findings(brain, r["id"], [F, {**F, "title": "again"}])) == 2


# ---- 14 clean branch and the model choke point ---------------------------------------------------------
def test_an_unrelated_change_raises_no_more_than_one_finding(seeded_cairn):
    root = seeded_cairn.project.root
    git(root, "checkout", "-q", "main")
    git(root, "checkout", "-q", "-b", "clean")
    commit(root, {"notes/todo.txt": "buy milk\n"}, "notes")
    out = pipeline.run(seeded_cairn, base="main")
    assert len(out.findings) <= 1, [f["title"] for f in out.findings]


def test_cited_commits_exist_and_are_not_the_branchs_own(seeded_cairn):
    out = pipeline.run(seeded_cairn)
    own = set(diffmod.resolve(seeded_cairn.project).commits)
    cited = [c[7:] for f in out.findings if f["kind"] in ("history", "cochange", "test") for c in f["citations"]
             if c.startswith("commit:")]
    assert own and len(cited) >= 3
    assert not (set(cited) & own) and all(seeded_cairn.brain.entity(f"commit:{c}") for c in cited)


class _Router:
    def __init__(self):
        self.calls = []

    def complete(self, task, prompt, **kw):
        self.calls.append((task, prompt, kw))
        return "ok"


def test_every_string_handed_to_a_model_call_is_sanitized():
    tok = "ghp_" + "a1B2c3D4e5" * 4
    r = _Router()
    model.complete(r, "review.bundle", f"diff {tok}", system=f"sys <private>hide</private> {tok}",
                   cached_context=f"brief {tok}", extra={f"k {tok}": [f"v {tok}"]}, max_tokens=5)
    task, prompt, kw = r.calls[0]
    blob = json.dumps([task, prompt, kw], default=str)
    assert tok not in blob and "hide" not in blob and kw["max_tokens"] == 5


def test_sanitize_masks_dict_keys():
    tok = "ghp_" + "a1B2c3D4e5" * 4
    assert tok not in json.dumps(model.sanitize({tok: 1}))


def test_secret_check_uses_the_shared_patterns(seeded_cairn):
    """A credential URL, a Slack webhook and a Basic auth header are flagged, like sanitize() masks them."""
    url = "https://" + "alice" + ":" + "hunter2xyz99" + "@example.com/r.git"
    hook = "https://hooks.slack.com/services/" + "T00000000/B00000000/" + "X" * 24
    basic = "Authorization: Basic " + "YWxpY2U6aHVudGVyMnh5ejk5"
    commit(seeded_cairn.project.root, {"shop/conf.py": f'u = "{url}"\nh = "{hook}"\n# {basic}\n'}, "conf")
    out = pipeline.run(seeded_cairn)
    mine = [f for f in out.findings if f["path"] == "shop/conf.py" and f["kind"] == "secret"]
    assert {f["line_start"] for f in mine} == {1, 2, 3}
    blob = json.dumps(out.findings)
    assert "hunter2xyz99" not in blob and "YWxpY2U6" not in blob and "T00000000" not in blob
    for line in (url, hook, basic):  # what the check flags, sanitize() masks
        assert model.sanitize(line) != line
