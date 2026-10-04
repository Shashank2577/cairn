"""Git-backed projects: URL validation, clone/refresh from a local bare repository, and push webhooks."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from cairn.platform import gitops, security
from cairn.platform.config import ENV_KEYS, ServerConfig
from cairn.platform.errors import AuthError, Conflict, GitError, InvalidInput, PermissionDenied
from cairn.platform.service import Platform

PW = "correct horse battery"
GIT_ENV = {"GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com", "GIT_COMMITTER_NAME": "Ada",
           "GIT_COMMITTER_EMAIL": "ada@example.com"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          env={**os.environ, **GIT_ENV}, encoding="utf-8", errors="replace").stdout.strip()


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(security, "SCRYPT_N", 2 ** 10)


@pytest.fixture()
def plat(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(security.KEY_ENV, raising=False)
    for var in ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)
    p = Platform()
    yield p
    p.close()


@pytest.fixture()
def remote(tmp_path):
    """A bare 'origin' with main and a feature branch, plus a working clone to push from."""
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "app.py").write_text("print('v1')\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "v1")
    git(work, "checkout", "-q", "-b", "feature")
    (work / "feature.py").write_text("x = 1\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "feature")
    git(work, "checkout", "-q", "main")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, capture_output=True)
    git(work, "remote", "add", "origin", str(bare))
    return work, bare


def push_commit(work: Path, name: str, branch: str = "main") -> str:
    git(work, "checkout", "-q", branch)
    (work / name).write_text(name + "\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", f"add {name}")
    git(work, "push", "-q", "origin", branch)
    return git(work, "rev-parse", "HEAD")


# ---- URL validation --------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://github.com/acme/app.git", "ssh://git@github.com/acme/app.git", "git@github.com:acme/app.git",
    "git+ssh://git@host.example/acme/app", "gitlab.example.com:group/app.git",
])
def test_accepted_urls(url):
    assert gitops.validate_url(url) == url
    assert gitops.looks_like_url(url)


@pytest.mark.parametrize("url,why", [
    ("ext::sh%-c%touch%/tmp/pwned", "remote-helper"),
    ("ext::sh -c touch% /tmp/pwned", "not allowed"),
    ("fd::17", "remote-helper"),
    ("-uhelp", "not allowed"),
    ("--upload-pack=touch /tmp/x", "not allowed"),
    ("https://user:hunter2@github.com/acme/app.git", "credentials"),
    ("https://ghp_abcdef@github.com/acme/app.git", "credentials"),
    ("ssh://git:pw@host/app.git", "credentials"),
    ("file:///etc", "file://"),
    ("/srv/repos/app.git", "not a supported"),
    ("http://github.com/acme/app.git", "unencrypted"),
    ("git://github.com/acme/app.git", "unencrypted"),
    ("ftp://host/app.git", "unsupported"),
    ("https://github.com/acme/app.git\nevil", "not allowed"),
    ("https:///nohost", "no valid host"),
    ("", "empty"),
])
def test_rejected_urls(url, why):
    with pytest.raises(InvalidInput, match=why):
        gitops.validate_url(url)


def test_local_and_insecure_sources_need_permission(tmp_path):
    assert gitops.validate_url("file:///srv/app.git", allow_local=True)
    assert gitops.validate_url(str(tmp_path), allow_local=True)
    assert gitops.validate_url("http://intranet/app.git", allow_insecure=True)


@pytest.mark.parametrize("branch", ["-f", "a..b", "x/", "x.lock", "a b", "@{1}", "/abs"])
def test_rejected_branches(branch):
    with pytest.raises(InvalidInput):
        gitops.validate_branch(branch)


def test_branch_and_name_helpers():
    assert gitops.validate_branch("release/1.2") == "release/1.2" and gitops.validate_branch("  ") is None
    assert gitops.repo_name("git@github.com:acme/app.git") == "app"
    assert gitops.repo_name("https://host/group/sub/tool/") == "tool"


# ---- clone and refresh -----------------------------------------------------------------------------------
def test_clone_and_refresh_helpers(tmp_path, remote):
    work, bare = remote
    dest = tmp_path / "clone"
    assert gitops.clone(f"file://{bare}", dest, allow_local=True) == "main"
    assert (dest / "app.py").exists() and not (dest / "feature.py").exists()
    new = push_commit(work, "b.txt")
    res = gitops.refresh(dest, "main", allow_local=True)
    assert res["changed"] and res["new"] == new and (dest / "b.txt").exists()
    assert not gitops.refresh(dest, "main", allow_local=True)["changed"]
    res = gitops.refresh(dest, "feature", allow_local=True)  # switch the followed branch
    assert (dest / "feature.py").exists() and gitops.current_branch(dest) == "feature"
    with pytest.raises(GitError):
        gitops.clone(f"file://{bare}", dest, allow_local=True)  # not empty


def test_file_protocol_is_blocked_at_the_git_level(tmp_path, remote):
    _, bare = remote
    with pytest.raises(GitError):
        gitops.clone(f"file://{bare}", tmp_path / "nope")  # GIT_ALLOW_PROTOCOL excludes file
    assert not (tmp_path / "nope").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".nope.clone-")]


def test_register_git_project_clones_into_home(plat, remote):
    work, bare = remote
    owner = plat.create_user("o@x.io", "O", PW)
    team = plat.create_team("Acme", owner_id=owner.id)
    p = plat.register_git_project(team.id, f"file://{bare}")  # the local operator may use file://
    assert p.status == "ready" and p.kind == "git" and p.managed and p.git_branch is None  # follows default
    assert gitops.current_branch(Path(p.root)) == "main"
    assert p.slug == "origin" and Path(p.root).parent == plat.home / "repos"
    assert p.data_dir == str(Path(p.root) / ".cairn") and (Path(p.root) / "app.py").exists()
    (Path(p.root) / ".cairn").mkdir()
    (Path(p.root) / ".cairn" / "brain.db").write_text("local state", encoding="utf-8")
    new = push_commit(work, "c.txt")
    res = plat.refresh_project(p.id)
    assert res["pulled"] and res["changed"] and res["new"] == new
    assert (Path(p.root) / ".cairn" / "brain.db").read_text(encoding="utf-8") == "local state"  # untracked state survives
    with pytest.raises(Conflict):
        plat.register_git_project(team.id, f"file://{bare}")
    feature = plat.register_git_project(team.id, f"file://{bare}", branch="feature", name="Feature")
    assert (Path(feature.root) / "feature.py").exists() and feature.slug == "feature"
    root = Path(p.root)
    plat.delete_project(p.id)
    assert not root.exists()


def test_register_git_project_permissions_and_policy(plat, remote):
    _, bare = remote
    owner = plat.create_user("o@x.io", "O", PW)
    team = plat.create_team("Acme", owner_id=owner.id)
    member = plat.create_user("m@x.io", "M", PW)
    plat.add_member(team.id, member.id, "member")
    as_owner = plat.login("o@x.io", PW)[1]
    with pytest.raises(InvalidInput, match="file://"):  # over HTTP, file:// needs server.allow_local_git
        plat.register_git_project(team.id, f"file://{bare}", actor=as_owner)
    with pytest.raises(PermissionDenied):
        plat.register_git_project(team.id, "https://github.com/acme/app.git", clone=False,
                                  actor=plat.login("m@x.io", PW)[1])
    p = plat.register_git_project(team.id, "https://github.com/acme/app.git", clone=False, actor=as_owner)
    assert p.status == "pending"


def test_failed_clone_is_reported(plat, tmp_path):
    owner = plat.create_user("o@x.io", "O", PW)
    team = plat.create_team("Acme", owner_id=owner.id)
    with pytest.raises(GitError):
        plat.register_git_project(team.id, f"file://{tmp_path / 'missing.git'}")
    p = plat.list_projects(team_id=team.id)[0]
    assert p.status == "error" and "clone" in p.status_detail


def test_local_projects_are_never_pulled_or_deleted(plat, tmp_path):
    repo = tmp_path / "mine"
    repo.mkdir()
    p = plat.register_local_project(repo)
    assert plat.refresh_project(p.id) == {"project_id": p.id, "pulled": False, "changed": None}
    plat.delete_project(p.id, purge=True)
    assert repo.exists()


def test_clone_removal_is_confined_to_repos_dir(plat, tmp_path):
    outside = tmp_path / "precious"
    outside.mkdir()
    assert plat._remove_clone(outside) is False and outside.exists()
    assert plat._remove_clone(plat.repos_dir) is False


# ---- webhooks --------------------------------------------------------------------------------------------
@pytest.fixture()
def hooked(plat, tmp_path):
    owner = plat.create_user("o@x.io", "O", PW)
    team = plat.create_team("Acme", owner_id=owner.id)
    repo = tmp_path / "r"
    repo.mkdir()
    p = plat.register_local_project(repo, team_id=team.id)
    secret = plat.rotate_webhook_secret(p.id)
    return p, secret


def push_body(ref: str = "refs/heads/main", **extra) -> bytes:
    return json.dumps({"ref": ref, "after": "abc", "repository": {"default_branch": "main"}, **extra}).encode()


def test_github_signature(plat, hooked):
    p, secret = hooked
    body = push_body()
    sig = security.sign_body(secret, body)
    res = plat.verify_webhook(p.id, {"X-Hub-Signature-256": sig, "X-GitHub-Event": "push",
                                     "X-GitHub-Delivery": "d-1"}, body)
    assert (res.sync, res.provider, res.ref, res.delivery) == (True, "github", "refs/heads/main", "d-1")
    with pytest.raises(AuthError):
        plat.verify_webhook(p.id, {"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"}, body + b" ")
    with pytest.raises(AuthError):
        plat.verify_webhook(p.id, {"X-Hub-Signature-256": "sha256=" + "0" * 64}, body)
    with pytest.raises(AuthError):
        plat.verify_webhook(p.id, {"X-GitHub-Event": "push"}, body)  # unsigned
    entry = plat.audit_entries(project_id=p.id, limit=1)[0]
    assert entry.action == "project.webhook" and entry.detail["sync"] is True


def test_signature_primitives():
    body = b'{"x":1}'
    sig = security.sign_body("s3cret", body)
    assert security.verify_signature("s3cret", body, sig)
    assert security.verify_signature("s3cret", body, sig[7:])  # bare hex (Gitea)
    assert security.verify_signature("s3cret", body, sig.upper().replace("SHA256=", "sha256="))
    assert not security.verify_signature("other", body, sig)
    assert not security.verify_signature("s3cret", body, "sha256=ü")


def test_gitlab_token_and_gitea(plat, hooked):
    p, secret = hooked
    body = push_body()
    res = plat.verify_webhook(p.id, {"X-Gitlab-Token": secret, "X-Gitlab-Event": "Push Hook"}, body)
    assert res.provider == "gitlab" and res.sync
    with pytest.raises(AuthError):
        plat.verify_webhook(p.id, {"X-Gitlab-Token": secret + "x", "X-Gitlab-Event": "Push Hook"}, body)
    sig = security.sign_body(secret, body)[7:]
    res = plat.verify_webhook(p.id, {"X-Gitea-Signature": sig, "X-Gitea-Event": "push"}, body)
    assert res.provider == "gitea" and res.sync


@pytest.mark.parametrize("event,body,branch,sync,reason", [
    ("ping", push_body(), None, False, "ping"),
    ("issues", push_body(), None, False, "ignored"),
    ("push", push_body("refs/tags/v1"), None, False, "not a branch"),
    ("push", push_body("refs/heads/dev"), None, False, "following main"),
    ("push", push_body("refs/heads/dev"), "dev", True, "push to dev"),
    ("push", push_body("refs/heads/main", deleted=True), None, False, "deleted"),
    ("push", b"{}", None, True, "push"),
])
def test_which_deliveries_trigger_a_sync(plat, tmp_path, event, body, branch, sync, reason):
    owner = plat.create_user("o@x.io", "O", PW)
    team = plat.create_team("Acme", owner_id=owner.id)
    repo = tmp_path / "r"
    repo.mkdir()
    p = plat.register_local_project(repo, team_id=team.id)
    if branch:
        plat._conn.execute("UPDATE projects SET git_branch = ? WHERE id = ?", (branch, p.id))
    secret = plat.rotate_webhook_secret(p.id)
    res = plat.verify_webhook(p.id, {"X-Hub-Signature-256": security.sign_body(secret, body),
                                     "X-GitHub-Event": event}, body)
    assert res.sync is sync and reason in res.reason


def test_form_encoded_payload(plat, hooked):
    from urllib.parse import quote
    p, secret = hooked
    body = ("payload=" + quote(push_body("refs/heads/dev").decode())).encode()
    res = plat.verify_webhook(p.id, {"X-Hub-Signature-256": security.sign_body(secret, body), "X-GitHub-Event":
                                     "push", "Content-Type": "application/x-www-form-urlencoded"}, body)
    assert res.ref == "refs/heads/dev" and not res.sync


def test_rotation_and_key_changes_invalidate_old_secrets(plat, hooked, monkeypatch):
    p, old = hooked
    body = push_body()
    new = plat.rotate_webhook_secret(p.id)
    assert new != old and new.startswith("whsec_")
    with pytest.raises(AuthError):
        plat.verify_webhook(p.id, {"X-Hub-Signature-256": security.sign_body(old, body)}, body)
    assert plat.verify_webhook(p.id, {"X-Hub-Signature-256": security.sign_body(new, body)}, body).sync
    plat.close()
    monkeypatch.setenv(security.KEY_ENV, "a different server key, at least 32 chars")
    other = Platform()
    with pytest.raises(AuthError):
        other.verify_webhook(p.id, {"X-Hub-Signature-256": security.sign_body(new, body)}, body)
    other.close()


def test_unconfigured_or_unknown_project(plat, tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    p = plat.register_local_project(repo)
    with pytest.raises(AuthError):
        plat.verify_webhook(p.id, {"X-Gitlab-Token": "x"}, b"{}")
    with pytest.raises(AuthError):
        plat.verify_webhook("p_nope", {"X-Gitlab-Token": "x"}, b"{}")


def test_only_project_admins_rotate(plat, hooked):
    p, _ = hooked
    m = plat.create_user("m@x.io", "M", PW)
    plat.add_member(p.team_id, m.id, "member")
    with pytest.raises(PermissionDenied):
        plat.rotate_webhook_secret(p.id, actor=plat.login("m@x.io", PW)[1])


def test_server_config_allows_local_git_over_http(tmp_path, monkeypatch, remote):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home2"))
    _, bare = remote
    p = Platform(config=ServerConfig(allow_local_git=True))
    owner = p.create_user("o@x.io", "O", PW)
    team = p.create_team("Acme", owner_id=owner.id)
    proj = p.register_git_project(team.id, f"file://{bare}", actor=p.login("o@x.io", PW)[1])
    assert proj.status == "ready"
    p.close()
