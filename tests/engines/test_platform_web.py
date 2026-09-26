"""The platform's HTTP surface on a minimal app: local vs team mode, sessions, CSRF, origin/host checks, rate
limiting, invitations, tokens, projects, webhooks and audit."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from cairn.platform import security, web
from cairn.platform.config import ENV_KEYS, ServerConfig
from cairn.platform.rbac import Principal
from cairn.platform.service import Platform

PW = "correct horse battery"
GIT_ENV = {"GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com", "GIT_COMMITTER_NAME": "Ada",
           "GIT_COMMITTER_EMAIL": "ada@example.com"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "SCRYPT_N", 2 ** 10)
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(security.KEY_ENV, raising=False)
    for var in ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)


def make_app(plat: Platform, config: ServerConfig, synced: list) -> FastAPI:
    """What the lead's server looks like from the platform's point of view."""
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/")
    def index():
        return {"ui": True}

    @app.get("/api/p/{pid}/data")
    def data(pid: str, principal: Principal = Depends(web.require("project.read"))):
        return {"pid": pid, "who": principal.email, "kind": principal.kind}

    @app.post("/api/p/{pid}/data")
    def write(pid: str, principal: Principal = Depends(web.require("project.write"))):
        return {"ok": True}

    @app.get("/api/session")  # public like the real one: the route itself decides
    def session_(principal: Principal | None = Depends(web.optional_principal)):
        return {"signed_in": principal is not None,
                "must_change_password": bool(principal and principal.must_change_password)}

    web.install(app, plat, config, on_sync=lambda p, reason: synced.append((p.id, reason)))
    return app


class Env:
    def __init__(self, tmp_path: Path, **cfg):
        self.config = ServerConfig(**{"mode": "team", "login_attempts": 3, "login_window": 60, **cfg})
        self.plat = Platform(config=self.config)
        self.owner = self.plat.create_user("owner@x.io", "Owner", PW, is_admin=True)
        self.team = self.plat.create_team("Acme", owner_id=self.owner.id)
        self.users = {}
        for name, role in (("admin", "admin"), ("member", "member"), ("viewer", "viewer")):
            u = self.plat.create_user(f"{name}@x.io", name.title(), PW)
            self.plat.add_member(self.team.id, u.id, role)
            self.users[name] = u
        self.plat.create_user("out@x.io", "Out", PW)
        repo = tmp_path / "repo"
        repo.mkdir()
        self.project = self.plat.register_local_project(repo, team_id=self.team.id)
        self.synced: list = []
        self.app = make_app(self.plat, self.config, self.synced)
        self.tmp = tmp_path

    def client(self, base_url: str = "http://localhost") -> TestClient:
        return TestClient(self.app, base_url=base_url)

    def login(self, email: str, password: str = PW, base_url: str = "http://localhost") -> tuple[TestClient, str]:
        c = self.client(base_url)
        r = c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return c, r.json()["csrf_token"]


@pytest.fixture()
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.plat.close()


# ---- team mode: authentication ---------------------------------------------------------------------------
def test_team_mode_requires_authentication(env):
    c = env.client()
    assert c.get("/api/health").status_code == 200
    assert c.get("/").json() == {"ui": True}
    for path in ("/api/auth/me", f"/api/p/{env.project.id}/data", "/api/teams", "/api/projects", "/openapi.json",
                 "/api/docs", "/mcp", "/api/audit"):
        r = c.get(path)
        assert r.status_code == 401, path
        assert r.headers["www-authenticate"].startswith("Bearer")
    assert c.post(f"/api/p/{env.project.id}/data").status_code == 401


def test_login_sets_a_hardened_session_cookie(env):
    c = env.client()
    assert c.post("/api/auth/login", json={"email": "member@x.io", "password": "wrong password"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": "member@x.io", "password": PW})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("cairn_session=") and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Secure" not in cookie  # plain-http loopback only
    assert "Path=/" in cookie and "Max-Age=" in cookie
    me = c.get("/api/auth/me")
    body = me.json()
    assert body["kind"] == "session" and body["email"] == "member@x.io" and body["mode"] == "team"
    assert body["csrf_token"] == r.json()["csrf_token"] and body["teams"][0]["role"] == "member"
    assert me.headers["x-frame-options"] == "DENY" and me.headers["x-content-type-options"] == "nosniff"
    assert me.headers["cache-control"] == "no-store"
    assert c.get(f"/api/p/{env.project.id}/data").json()["who"] == "member@x.io"


def test_cookie_is_secure_and_host_prefixed_off_localhost(env):
    c, _ = env.login("member@x.io", base_url="https://cairn.example.com")
    r = c.post("/api/auth/login", json={"email": "member@x.io", "password": PW})
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-cairn_session=") and "Secure" in cookie
    assert c.get("/api/auth/me").status_code == 200
    plain = env.client("http://cairn.example.com").post("/api/auth/login", json={"email": "member@x.io",
                                                                                 "password": PW})
    assert "Secure" in plain.headers["set-cookie"]  # auto mode: anything but http loopback


def test_csrf_is_required_for_cookie_state_changes(env):
    c, csrf = env.login("owner@x.io")
    assert c.post("/api/teams", json={"name": "Ops"}).status_code == 403
    assert c.post("/api/teams", json={"name": "Ops"}, headers={"X-CSRF-Token": "forged"}).status_code == 403
    r = c.post("/api/teams", json={"name": "Ops"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201 and r.json()["slug"] == "ops"
    c2, csrf2 = env.login("owner@x.io")
    assert c2.post("/api/teams", json={"name": "X"}, headers={"X-CSRF-Token": csrf}).status_code == 403
    assert csrf != csrf2


def test_cross_origin_state_changes_are_refused(env):
    c, csrf = env.login("member@x.io")
    url = f"/api/p/{env.project.id}/data"
    h = {"X-CSRF-Token": csrf}
    assert c.post(url, headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert c.post(url, headers={**h, "Origin": "null"}).status_code == 403
    assert c.post(url, headers={**h, "Referer": "https://evil.example/page"}).status_code == 403
    assert c.post(url, headers={**h, "Origin": "http://localhost"}).status_code == 200
    assert c.post(url, headers={**h, "Referer": "http://localhost/app"}).status_code == 200
    anon = env.client()
    r = anon.post("/api/auth/login", json={"email": "member@x.io", "password": PW},
                  headers={"Origin": "https://evil.example"})
    assert r.status_code == 403  # login CSRF


def test_configured_origins_and_hosts(tmp_path):
    e = Env(tmp_path, allowed_origins=("https://ui.example",), allowed_hosts=("cairn.example", "*.corp.example"))
    c = e.client("http://cairn.example")
    assert c.get("/api/health").status_code == 200
    assert e.client("http://a.corp.example").get("/api/health").status_code == 200
    assert e.client("http://evil.example").get("/api/health").status_code == 400
    r = c.post("/api/auth/login", json={"email": "member@x.io", "password": PW}, headers={"Origin": "https://ui.example"})
    assert r.status_code == 200
    e.plat.close()


def test_login_rate_limiting(env):
    c = env.client()
    bad = {"email": "member@x.io", "password": "wrong password"}
    for _ in range(3):
        assert c.post("/api/auth/login", json=bad).status_code == 401
    r = c.post("/api/auth/login", json={"email": "member@x.io", "password": PW})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert c.post("/api/auth/login", json={"email": "viewer@x.io", "password": PW}).status_code == 200
    limiter = env.app.state.cairn_login_guard.limiter
    now = limiter.clock()
    limiter.clock = lambda: now + 61
    assert c.post("/api/auth/login", json={"email": "member@x.io", "password": PW}).status_code == 200


def test_rate_limit_per_client_across_accounts(env):
    c = env.client()
    for i in range(15):
        c.post("/api/auth/login", json={"email": f"nobody{i}@x.io", "password": "wrong password"})
    assert c.post("/api/auth/login", json={"email": "member@x.io", "password": PW}).status_code == 429


def test_logout_and_session_management(env):
    c, csrf = env.login("member@x.io")
    other, _ = env.login("member@x.io")
    sessions = c.get("/api/auth/sessions").json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    theirs = next(s for s in sessions if not s["current"])
    assert c.delete(f"/api/auth/sessions/{theirs['id']}", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    r = c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and "cairn_session=" in r.headers.get("set-cookie", "")
    assert c.get("/api/auth/me").status_code == 401


def test_session_fixation_is_prevented(env):
    c, _ = env.login("member@x.io")
    old = c.cookies.get("cairn_session")
    c.post("/api/auth/login", json={"email": "member@x.io", "password": PW})
    assert c.cookies.get("cairn_session") != old
    assert env.plat.session_principal(old) is None


def test_disabled_account_sign_in_answers(env, monkeypatch):
    env.plat.disable_user(env.users["member"].id)
    calls: list[int] = []
    real = security._scrypt
    monkeypatch.setattr(security, "_scrypt", lambda *a: calls.append(1) or real(*a))
    c = env.client()
    right = c.post("/api/auth/login", json={"email": "member@x.io", "password": PW})
    assert right.status_code == 403
    assert right.json()["detail"] == "This account is disabled. Ask an admin to turn it back on."
    assert "set-cookie" not in right.headers
    wrong = c.post("/api/auth/login", json={"email": "member@x.io", "password": "wrong password"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "invalid email or password"
    assert len(calls) == 2  # one scrypt per attempt either way
    for _ in range(2):  # attempts on a disabled account still count toward the throttle
        c.post("/api/auth/login", json={"email": "member@x.io", "password": PW})
    assert c.post("/api/auth/login", json={"email": "member@x.io", "password": PW}).status_code == 429


def test_session_route_is_public_and_never_cached(env):
    anon = env.client()
    r = anon.get("/api/session")
    assert r.status_code == 200 and r.json()["signed_in"] is False and r.headers["cache-control"] == "no-store"
    admin, csrf = env.login("owner@x.io")
    otp = admin.post(f"/api/users/{env.users['member'].id}/reset-password", headers={"X-CSRF-Token": csrf}).json()
    c, _ = env.login("member@x.io", otp["password"])
    assert c.get("/api/teams").status_code == 403  # mid password change: everything else is closed
    assert c.get("/api/session").json() == {"signed_in": True, "must_change_password": True}


def test_must_change_password_gate(env):
    admin, csrf = env.login("owner@x.io")
    uid = env.users["member"].id
    otp = admin.post(f"/api/users/{uid}/reset-password", headers={"X-CSRF-Token": csrf}).json()["password"]
    c, mcsrf = env.login("member@x.io", otp)
    assert c.get("/api/auth/me").json()["must_change_password"] is True
    r = c.get("/api/teams")
    assert r.status_code == 403 and "password change" in r.json()["detail"]
    h = {"X-CSRF-Token": mcsrf}
    assert c.post("/api/auth/password", json={"current_password": "nope nope nope", "new_password": "x" * 12},
                  headers=h).status_code == 403
    assert c.post("/api/auth/password", json={"current_password": otp, "new_password": "fresh password 1"},
                  headers=h).status_code == 200
    assert c.get("/api/teams").status_code == 200


# ---- tokens ----------------------------------------------------------------------------------------------
def test_bearer_tokens(env):
    c, csrf = env.login("member@x.io")
    r = c.post("/api/tokens", json={"name": "agent", "scopes": ["read"]}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201
    secret, tok = r.json()["secret"], r.json()["token"]
    assert tok["display"].startswith("cairn_") and "secret_hash" not in tok and secret not in json.dumps(tok)
    bearer = env.client()
    h = {"Authorization": f"Bearer {secret}"}
    assert bearer.get(f"/api/p/{env.project.id}/data", headers=h).json()["kind"] == "token"
    assert bearer.post(f"/api/p/{env.project.id}/data", headers=h).status_code == 403  # scope, no CSRF needed
    assert bearer.post("/api/tokens", json={"name": "x"}, headers=h).status_code == 403
    assert bearer.get("/api/auth/me", headers=h).json()["token"]["scopes"] == ["project.read", "team.read"]
    bad = bearer.get("/api/teams", headers={"Authorization": "Bearer cairn_zzzzzzzz_" + "a" * 40})
    assert bad.status_code == 401 and "invalid_token" in bad.headers["www-authenticate"]
    listed = c.get("/api/tokens").json()
    assert [t["id"] for t in listed] == [tok["id"]] and listed[0]["last_used_at"]
    assert c.delete(f"/api/tokens/{tok['id']}", headers={"X-CSRF-Token": csrf}).json()["status"] == "revoked"
    assert bearer.get(f"/api/p/{env.project.id}/data", headers=h).status_code == 401


def test_agent_token_can_write_without_csrf(env):
    tok, secret = env.plat.issue_token(env.users["member"].id, env.team.id, "agent")
    r = env.client().post(f"/api/p/{env.project.id}/data", headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 200


def test_admins_see_and_revoke_team_tokens(env):
    tok, _ = env.plat.issue_token(env.users["member"].id, env.team.id, "laptop")
    a, csrf = env.login("admin@x.io")
    assert [t["id"] for t in a.get(f"/api/tokens?team={env.team.id}&all=true").json()] == [tok.id]
    v, vcsrf = env.login("viewer@x.io")
    assert v.get(f"/api/tokens?team={env.team.id}&all=true").status_code == 403
    assert v.delete(f"/api/tokens/{tok.id}", headers={"X-CSRF-Token": vcsrf}).status_code == 404
    assert a.delete(f"/api/tokens/{tok.id}", headers={"X-CSRF-Token": csrf}).status_code == 200


def test_principal_from_bearer_helper(env):
    _, secret = env.plat.issue_token(env.users["viewer"].id, env.team.id, "mcp", scopes=["read"])
    p = web.principal_from_bearer(env.plat, f"Bearer {secret}", ip="1.2.3.4")
    assert p.email == "viewer@x.io" and p.ip == "1.2.3.4"
    assert web.principal_from_bearer(env.plat, None) is None


# ---- invitations -----------------------------------------------------------------------------------------
def test_invitation_signup_flow(env):
    a, csrf = env.login("admin@x.io")
    r = a.post(f"/api/teams/{env.team.id}/members", json={"email": "new@x.io", "role": "member"},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201
    token, url = r.json()["token"], r.json()["url"]
    assert url == f"http://localhost/?invite={token}"
    assert a.post(f"/api/teams/{env.team.id}/members", json={"email": "boss@x.io", "role": "owner"},
                  headers={"X-CSRF-Token": csrf}).status_code == 403
    anon = env.client()
    info = anon.get(f"/api/invites/{token}").json()
    assert info == {"team": {"id": env.team.id, "slug": "acme", "name": "Acme"}, "email": "new@x.io",
                    "role": "member", "expires_at": info["expires_at"], "account_exists": False}
    r = anon.post(f"/api/invites/{token}/accept", json={"name": "New", "password": PW})
    assert r.status_code == 200 and r.json()["created"] and r.json()["csrf_token"]
    assert anon.get("/api/auth/me").json()["email"] == "new@x.io"
    assert env.client().post(f"/api/invites/{token}/accept", json={"password": PW}).status_code == 409
    assert env.client().get("/api/invites/not-a-token").status_code == 404
    members = a.get(f"/api/teams/{env.team.id}/members").json()
    assert "new@x.io" in [m["email"] for m in members["members"]] and members["invitations"] == []


def test_signed_in_accept_must_match_email(env):
    _, token = env.plat.invite(env.team.id, "someone@x.io")
    c, csrf = env.login("out@x.io")
    r = c.post(f"/api/invites/{token}/accept", json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403


# ---- teams and members -----------------------------------------------------------------------------------
def test_team_endpoints(env):
    o, csrf = env.login("owner@x.io")
    h = {"X-CSRF-Token": csrf}
    teams = o.get("/api/teams").json()
    assert teams[0]["slug"] == "acme" and teams[0]["members"] == 4 and teams[0]["projects"] == 1
    t = o.get(f"/api/teams/{env.team.id}").json()
    assert t["role"] == "owner" and "team.delete" in t["permissions"]
    assert o.patch(f"/api/teams/{env.team.id}", json={"name": "Acme Co"}, headers=h).json()["name"] == "Acme Co"
    v, vcsrf = env.login("viewer@x.io")
    assert v.patch(f"/api/teams/{env.team.id}", json={"name": "Nope"}, headers={"X-CSRF-Token": vcsrf}).status_code == 403
    assert v.get(f"/api/teams/{env.team.id}").json()["permissions"] == ["project.read", "team.read"]
    outsider, _ = env.login("out@x.io")
    assert outsider.get(f"/api/teams/{env.team.id}").status_code == 404
    assert o.delete(f"/api/teams/{env.team.id}", headers=h).status_code == 400
    assert o.delete(f"/api/teams/{env.team.id}?confirm=acme", headers=h).json() == {"deleted": env.team.id}


def test_member_management_endpoints(env):
    a, csrf = env.login("admin@x.io")
    h = {"X-CSRF-Token": csrf}
    uid = env.users["member"].id
    assert a.patch(f"/api/teams/{env.team.id}/members/{uid}", json={"role": "viewer"}, headers=h).status_code == 200
    assert env.plat.member_role(env.team.id, uid) == "viewer"
    r = a.patch(f"/api/teams/{env.team.id}/members/{env.owner.id}", json={"role": "member"}, headers=h)
    assert r.status_code == 403
    assert a.delete(f"/api/teams/{env.team.id}/members/{uid}", headers=h).status_code == 200
    o, ocsrf = env.login("owner@x.io")
    r = o.delete(f"/api/teams/{env.team.id}/members/{env.owner.id}", headers={"X-CSRF-Token": ocsrf})
    assert r.status_code == 409 and "owner" in r.json()["detail"]
    inv, _ = env.plat.invite(env.team.id, "q@x.io")
    assert a.delete(f"/api/teams/{env.team.id}/invitations/{inv.id}", headers=h).status_code == 200


# ---- projects --------------------------------------------------------------------------------------------
def test_project_visibility_and_permissions(env):
    pid = env.project.id
    v, vcsrf = env.login("viewer@x.io")
    p = v.get(f"/api/projects/{pid}").json()
    assert p["role"] == "viewer" and p["permissions"] == ["project.read"] and p["root"] is None
    assert v.patch(f"/api/projects/{pid}", json={"name": "X"}, headers={"X-CSRF-Token": vcsrf}).status_code == 403
    outsider, ocsrf = env.login("out@x.io")
    assert outsider.get(f"/api/projects/{pid}").status_code == 404
    assert outsider.get(f"/api/p/{pid}/data").status_code == 404
    assert outsider.patch(f"/api/projects/{pid}", json={"name": "X"}, headers={"X-CSRF-Token": ocsrf}).status_code == 404
    assert outsider.get("/api/projects").json() == []
    a, csrf = env.login("admin@x.io")
    p = a.get(f"/api/projects/{pid}").json()
    assert p["root"] and "project.admin" in p["permissions"]
    assert a.patch(f"/api/projects/{pid}", json={"name": "Renamed"}, headers={"X-CSRF-Token": csrf}).json()["name"] == "Renamed"
    r = a.put(f"/api/projects/{pid}/members/{env.users['viewer'].id}", json={"role": "member"},
              headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert v.get(f"/api/projects/{pid}").json()["role"] == "member"
    members = {m["email"]: m for m in a.get(f"/api/projects/{pid}/members").json()}
    assert members["viewer@x.io"]["override"] == "member"
    assert a.delete(f"/api/projects/{pid}/members/{env.users['viewer'].id}",
                    headers={"X-CSRF-Token": csrf}).status_code == 200


def test_registering_server_paths_needs_a_server_admin(env):
    target = env.tmp / "other"
    target.mkdir()
    a, csrf = env.login("admin@x.io")
    r = a.post("/api/projects", json={"team_id": env.team.id, "path": str(target)}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403
    o, ocsrf = env.login("owner@x.io")
    r = o.post("/api/projects", json={"team_id": env.team.id, "path": str(target)}, headers={"X-CSRF-Token": ocsrf})
    assert r.status_code == 201
    assert (r.json()["id"], "registered") in env.synced
    assert o.post("/api/projects", json={"team_id": env.team.id}, headers={"X-CSRF-Token": ocsrf}).status_code == 400
    r = o.post("/api/projects", json={"team_id": env.team.id, "git_url": "file:///etc"},
               headers={"X-CSRF-Token": ocsrf})
    assert r.status_code == 400 and "file://" in r.json()["detail"]
    r = o.post("/api/projects", json={"team_id": env.team.id, "git_url": "https://u:p@github.com/a/b.git"},
               headers={"X-CSRF-Token": ocsrf})
    assert r.status_code == 400


def test_webhook_endpoint_triggers_sync(env, monkeypatch):
    pid = env.project.id
    a, csrf = env.login("admin@x.io")
    r = a.post(f"/api/projects/{pid}/webhook", headers={"X-CSRF-Token": csrf}).json()
    secret, url = r["secret"], r["url"]
    assert url == f"http://localhost/api/projects/{pid}/hooks/git"
    anon = env.client()
    body = json.dumps({"ref": "refs/heads/main", "repository": {"default_branch": "main"}}).encode()
    headers = {"X-Hub-Signature-256": security.sign_body(secret, body), "X-GitHub-Event": "push",
               "Content-Type": "application/json", "Origin": "https://github.com"}
    r = anon.post(f"/api/projects/{pid}/hooks/git", content=body, headers=headers)
    assert r.status_code == 202 and r.json()["sync"] is True
    assert (pid, "webhook") in env.synced and env.plat.project(pid).last_synced_at is not None
    ping = anon.post(f"/api/projects/{pid}/hooks/git", content=body,
                     headers={**headers, "X-GitHub-Event": "ping"})
    assert ping.status_code == 200 and ping.json()["sync"] is False
    bad = anon.post(f"/api/projects/{pid}/hooks/git", content=body + b" ", headers=headers)
    assert bad.status_code == 401
    assert anon.post("/api/projects/p_nope/hooks/git", content=body, headers=headers).status_code == 401
    monkeypatch.setattr(web, "MAX_WEBHOOK_BODY", 10)
    assert anon.post(f"/api/projects/{pid}/hooks/git", content=body, headers=headers).status_code == 413


def test_git_project_over_http(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    env_ = {**os.environ, **GIT_ENV}
    for cmd in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", "-C", str(work), *cmd], check=True, env=env_, capture_output=True)
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, capture_output=True)
    srv = tmp_path / "srv"
    srv.mkdir()
    e = Env(srv, allow_local_git=True)
    o, csrf = e.login("owner@x.io")
    r = o.post("/api/projects", json={"team_id": e.team.id, "git_url": f"file://{bare}"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201 and r.json()["kind"] == "git"
    pid = r.json()["id"]
    assert e.plat.project(pid).status == "ready" and (pid, "registered") in e.synced
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(bare)], check=True)
    (work / "new.py").write_text("x = 1\n")
    for cmd in (["add", "-A"], ["commit", "-qm", "new"], ["push", "-q", "origin", "main"]):
        subprocess.run(["git", "-C", str(work), *cmd], check=True, env=env_, capture_output=True)
    assert o.post(f"/api/projects/{pid}/refresh", headers={"X-CSRF-Token": csrf}).status_code == 202
    assert (Path(e.plat.project(pid).root) / "new.py").exists() and (pid, "manual") in e.synced
    v, vcsrf = e.login("viewer@x.io")
    assert v.post(f"/api/projects/{pid}/refresh", headers={"X-CSRF-Token": vcsrf}).status_code == 403
    root = Path(e.plat.project(pid).root)
    assert o.delete(f"/api/projects/{pid}", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert not root.exists()
    e.plat.close()


# ---- users and audit -------------------------------------------------------------------------------------
def test_user_admin_endpoints(env):
    o, csrf = env.login("owner@x.io")
    h = {"X-CSRF-Token": csrf}
    assert len(o.get("/api/users").json()) == 5
    uid = env.users["viewer"].id
    v, _ = env.login("viewer@x.io")
    assert o.post(f"/api/users/{uid}/disable", headers=h).status_code == 200
    assert v.get("/api/auth/me").status_code == 401
    assert o.post(f"/api/users/{uid}/enable", headers=h).status_code == 200
    m, mcsrf = env.login("member@x.io")
    assert m.get("/api/users").status_code == 403
    assert m.post(f"/api/users/{uid}/disable", headers={"X-CSRF-Token": mcsrf}).status_code == 403


def test_audit_endpoint(env):
    env.plat.issue_token(env.users["member"].id, env.team.id, "t")
    a, _ = env.login("admin@x.io")
    entries = a.get(f"/api/audit?team={env.team.id}").json()
    assert {"team.create", "member.add", "project.register", "token.issue"} <= {e["action"] for e in entries}
    assert a.get("/api/audit").status_code == 403
    m, _ = env.login("member@x.io")
    assert m.get(f"/api/audit?team={env.team.id}").status_code == 403
    o, _ = env.login("owner@x.io")
    assert any(e["action"] == "auth.login" for e in o.get("/api/audit?limit=50").json())
    env.client().post("/api/auth/login", json={"email": "out@x.io", "password": PW})  # not in this team
    team_sign_ins = {(e["action"], e["actor"]) for e in a.get(f"/api/audit?team={env.team.id}&limit=500").json()
                     if e["action"].startswith("auth.")}
    assert {("auth.login", "admin@x.io"), ("auth.login", "member@x.io")} <= team_sign_ins
    assert not any(actor == "out@x.io" for _, actor in team_sign_ins)


# ---- local mode ------------------------------------------------------------------------------------------
@pytest.fixture()
def local(tmp_path):
    config = ServerConfig(mode="local")
    plat = Platform(config=config)
    repo = tmp_path / "repo"
    repo.mkdir()
    synced: list = []
    app = make_app(plat, config, synced)
    project = plat.register_local_project(repo)
    yield plat, app, project
    plat.close()


def test_local_mode_needs_no_login(local):
    plat, app, project = local
    c = TestClient(app, base_url="http://127.0.0.1:4747")
    me = c.get("/api/auth/me").json()
    assert me["kind"] == "local" and me["mode"] == "local" and me["csrf_token"] is None and me["is_admin"]
    assert c.get(f"/api/p/{project.id}/data").json()["kind"] == "local"
    assert c.post(f"/api/p/{project.id}/data").status_code == 200  # no CSRF token needed
    assert c.post("/api/teams", json={"name": "Side"}).status_code == 201
    assert [p["slug"] for p in c.get("/api/projects").json()] == ["repo"]
    assert c.post("/api/auth/login", json={"email": "a@x.io", "password": PW}).status_code == 400


def test_local_mode_blocks_dns_rebinding_and_foreign_pages(local):
    plat, app, project = local
    for base in ("http://localhost:4747", "http://[::1]:4747", "http://127.0.0.1"):
        assert TestClient(app, base_url=base).get("/api/auth/me").status_code == 200, base
    assert TestClient(app, base_url="http://evil.example:4747").get("/api/auth/me").status_code == 400
    assert TestClient(app, base_url="http://testserver").get("/api/health").status_code == 400
    c = TestClient(app, base_url="http://127.0.0.1:4747")
    r = c.post(f"/api/p/{project.id}/data", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.post(f"/api/p/{project.id}/data", headers={"Origin": "http://127.0.0.1:4747"}).status_code == 200


def test_local_mode_tokens_still_narrow(local):
    plat, app, project = local
    owner = plat.local_principal()
    _, secret = plat.issue_token(owner.user_id, plat.local_team_id, "reader", scopes=["read"])
    c = TestClient(app, base_url="http://127.0.0.1")
    h = {"Authorization": f"Bearer {secret}"}
    assert c.get(f"/api/p/{project.id}/data", headers=h).json()["kind"] == "token"
    assert c.post(f"/api/p/{project.id}/data", headers=h).status_code == 403
    assert c.get("/api/teams", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_extra_local_hosts_for_test_clients(tmp_path):
    config = ServerConfig(mode="local", allowed_hosts=("testserver",))
    plat = Platform(config=config)
    app = make_app(plat, config, [])
    assert TestClient(app).get("/api/auth/me").json()["kind"] == "local"
    plat.close()


def test_router_without_the_gate_middleware(tmp_path):
    """Dependencies still authenticate (and enforce CSRF) if only the router is mounted."""
    config = ServerConfig(mode="team")
    plat = Platform(config=config)
    plat.create_user("a@x.io", "A", PW)
    app = FastAPI()
    app.state.cairn_platform, app.state.cairn_config = plat, config
    app.include_router(web.router)
    c = TestClient(app, base_url="http://localhost")
    assert c.get("/api/auth/me").status_code == 401
    csrf = c.post("/api/auth/login", json={"email": "a@x.io", "password": PW}).json()["csrf_token"]
    assert c.get("/api/auth/me").json()["email"] == "a@x.io"
    assert c.post("/api/auth/password", json={"new_password": "y" * 12, "current_password": PW}).status_code == 403
    assert c.post("/api/auth/password", json={"new_password": "y" * 12, "current_password": PW},
                  headers={"X-CSRF-Token": csrf}).status_code == 200
    plat.close()


def test_public_url_pins_the_host_in_team_mode(tmp_path):
    e = Env(tmp_path, public_url="https://cairn.example.com")
    assert e.client("https://cairn.example.com").get("/api/health").status_code == 200
    assert e.client("http://127.0.0.1:4747").get("/api/health").status_code == 200
    assert e.client("http://rebound.evil.example").get("/api/health").status_code == 400
    e.plat.close()
