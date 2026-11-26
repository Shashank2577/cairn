"""Tables, diff filtering and sanitizing (T009)."""
from __future__ import annotations

import pytest

from cairn.review import diff as diffmod, store
from cairn.review.model import sanitize
from cairn.store import Brain


def test_tables_exist_and_reopen_is_harmless(tmp_path):
    for _ in range(2):  # the schema is created every time the store opens
        brain = Brain(tmp_path / "b.db")
        names = {r["name"] for r in brain.q("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"reviews", "review_findings", "review_rules"} <= names
        brain.close()


def test_one_review_per_project_head_and_base(tmp_path):
    brain = Brain(tmp_path / "b.db")
    a = store.start_review(brain, project="p", branch="x", base_sha="b1", head_sha="h1")
    b = store.start_review(brain, project="p", branch="x", base_sha="b1", head_sha="h1")
    assert a["id"] == b["id"]
    assert len(store.list_reviews(brain)) == 1
    other = store.start_review(brain, project="p", branch="x", base_sha="b1", head_sha="h2")
    assert other["id"] != a["id"]


@pytest.mark.parametrize("path,reason", [
    ("package-lock.json", "lock file"), ("web/yarn.lock", "lock file"), ("node_modules/x/index.js", "vendored or built output"),
    ("dist/app.js", "vendored or built output"), ("static/app.min.js", "generated file"),
    ("api/schema_pb2.py", "generated file"), ("logo.png", "binary file"), ("src/app.py", None), ("src/dist.py", None)])
def test_skip_reasons(path, reason):
    assert diffmod.skip_reason(path) == reason


def test_a_huge_file_is_skipped_with_a_reason():
    assert diffmod.skip_reason("data/big.py", nlines=diffmod.MAX_FILE_LINES + 1) == "very large file"


def test_parse_patch_tracks_new_line_numbers():
    raw = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +10,3 @@\n ctx\n+one\n-gone\n+two\n"
           "diff --git a/new.txt b/new.txt\nnew file mode 100644\n--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+hi\n")
    a, new = diffmod.parse_patch(raw)
    assert a.added == [(11, "one"), (12, "two")] and a.removed == ["gone"]
    assert new.status == "A" and new.added == [(1, "hi")]


def test_resolve_lists_skipped_files_with_reasons(seeded_cairn):
    root = seeded_cairn.project.root
    from tests.conftest import commit
    commit(root, {"package-lock.json": '{"changed": true}\n', "dist/x.js": "x\n", "shop/new.py": "x = 1\n"}, "more")
    d = diffmod.resolve(seeded_cairn.project)
    assert "shop/new.py" in d.paths and "package-lock.json" not in d.paths
    assert {s["path"]: s["reason"] for s in d.skipped}["package-lock.json"] == "lock file"
    assert d.branch == "feature" and d.base_ref == "main"


def test_unknown_target_and_missing_pr_are_clear_errors(seeded_cairn):
    with pytest.raises(diffmod.DiffError, match="not a branch"):
        diffmod.resolve(seeded_cairn.project, "nope-branch")
    with pytest.raises(diffmod.DiffError, match="Pull request 7"):
        diffmod.resolve(seeded_cairn.project, "7")


def test_sanitize_removes_private_blocks_and_masks_credentials():
    text = "keep <private>do not send</private> password=hunter2hunter2 end"
    out = sanitize(text)
    assert "do not send" not in out and "hunter2hunter2" not in out and "keep" in out
    assert sanitize({"a": ["token: abc12345xyz"]}) == {"a": ["token: ***"]}


def test_diff_secrets_never_survive_sanitize():
    """T006 / SC-008: a GitHub token, a PEM block and a credential URL in a diff are masked before any model sees them."""
    token = "ghp_" + "a1B2c3D4e5" * 4
    pem_body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"
    pem = "-----BEGIN " + "RSA PRIVATE KEY-----\n" + pem_body + "\n-----END " + "RSA PRIVATE KEY-----"
    url = "https://" + "alice" + ":" + "hunter2xyz99" + "@example.com/repo.git"
    diff = "\n".join(f"+{line}" for line in
                     [f'TOKEN = "{token}"', *pem.splitlines(), f'remote = "{url}"', "keep = 1"])
    out = sanitize(diff)
    assert token not in out and pem_body not in out and "hunter2xyz99" not in out
    assert "keep = 1" in out
    nested = sanitize({"files": [{"patch": diff}]})
    assert token not in str(nested) and pem_body not in str(nested)
