"""Platform service: storage, passwords, sessions, tokens, invitations, members, audit and secrets at rest."""
from __future__ import annotations

import sqlite3
import stat

import pytest

from cairn.platform import security
from cairn.platform.config import ENV_KEYS, ServerConfig
from cairn.platform.db import MIGRATIONS, schema_version
from cairn.platform.errors import (AccountDisabled, AuthError, Conflict, Expired, InvalidInput, NotFound,
                                   PermissionDenied)
from cairn.platform.service import Platform

PW = "correct horse battery"


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(security, "SCRYPT_N", 2 ** 10)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "cairn-home"
    monkeypatch.setenv("CAIRN_HOME", str(h))
    monkeypatch.delenv(security.KEY_ENV, raising=False)
    for var in ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)
    return h


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def plat(home, clock):
    p = Platform(clock=clock)
    yield p
    p.close()


def team_with(plat: Platform, **roles: str):
    """A team owned by ``owner@x.io`` plus one user per keyword (``name=role``)."""
    owner = plat.create_user("owner@x.io", "Owner", PW)
    team = plat.create_team("Acme", owner_id=owner.id)
    users = {"owner": owner}
    for name, role in roles.items():
        u = plat.create_user(f"{name}@x.io", name.title(), PW)
        plat.add_member(team.id, u.id, role)
        users[name] = u
    return team, users


def session(plat: Platform, email: str):
    return plat.login(email, PW)[1]


# ---- storage ---------------------------------------------------------------------------------------------
def test_home_and_database_are_private_and_migrated(plat, home):
    assert plat.db_path == home / "platform.db"
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert stat.S_IMODE(plat.db_path.stat().st_mode) == 0o600
    assert stat.S_IMODE((home / security.KEY_FILE).stat().st_mode) == 0o600
    with sqlite3.connect(plat.db_path) as conn:
        assert schema_version(conn) == len(MIGRATIONS)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "teams", "memberships", "projects", "project_roles", "invitations", "api_tokens",
            "web_sessions", "audit_log", "meta"} <= tables


def test_reopening_keeps_data_and_key(home):
    a = Platform()
    u = a.create_user("a@x.io", "A", PW)
    key = a._key
    a.close()
    b = Platform()
    assert b.user(u.id).email == "a@x.io" and b._key == key
    b.close()


def test_secret_key_from_environment(home, monkeypatch):
    monkeypatch.setenv(security.KEY_ENV, "k" * 40)
    p = Platform()
    assert not (home / security.KEY_FILE).exists()
    assert len(p._key) == 32
    p.close()
    monkeypatch.setenv(security.KEY_ENV, "short")
    with pytest.raises(ValueError):
        Platform()


def test_server_config_layers_and_validation(home, monkeypatch):
    home.mkdir(parents=True)
    (home / "server.toml").write_text('[server]\nmode = "team"\nhost = "0.0.0.0"\nallowed_hosts = "a.io, b.io"\n'
                                      "trust_proxy = true\n", encoding="utf-8")
    cfg = ServerConfig.load(home=home)
    assert cfg.team and cfg.host == "0.0.0.0" and cfg.allowed_hosts == ("a.io", "b.io") and cfg.trust_proxy
    assert ServerConfig.load({"port": "5000"}, home=home).port == 5000
    monkeypatch.setenv("CAIRN_SERVER_PORT", "6000")
    assert ServerConfig.load({"port": 5000}, home=home).port == 6000
    with pytest.raises(ValueError, match="loopback"):
        ServerConfig.from_mapping({"mode": "local", "host": "0.0.0.0"})
    with pytest.raises(ValueError):
        ServerConfig.from_mapping({"mode": "cluster"})
    assert ServerConfig.from_mapping({"mode": "local", "host": "::1"}).mode == "local"


# ---- passwords -------------------------------------------------------------------------------------------
def test_password_hash_format_and_verify(monkeypatch):
    monkeypatch.setattr(security, "SCRYPT_N", 2 ** 15)
    h = security.hash_password(PW)
    assert h.startswith("scrypt$32768$8$1$") and PW not in h
    assert security.verify_password(PW, h)
    assert not security.verify_password(PW + "x", h)
    assert security.hash_password(PW) != h  # fresh salt every time
    assert not security.needs_rehash(h)


def test_verify_is_constant_work_and_constant_time(monkeypatch):
    calls: list[int] = []
    real = security._scrypt
    monkeypatch.setattr(security, "_scrypt", lambda *a: calls.append(1) or real(*a))
    compared: list[tuple] = []
    real_cmp = security.hmac.compare_digest
    monkeypatch.setattr(security.hmac, "compare_digest", lambda a, b: compared.append((a, b)) or real_cmp(a, b))
    stored = security.hash_password(PW)
    calls.clear()
    assert not security.verify_password(PW, None)            # no account / no password
    assert not security.verify_password(PW, "garbage")       # corrupt hash
    assert not security.verify_password("x" * 5000, stored)  # oversized input
    assert security.verify_password(PW, stored)
    assert len(calls) == 4 and len(compared) == 4


def test_password_policy(plat):
    with pytest.raises(InvalidInput):
        plat.create_user("a@x.io", "A", "short")
    with pytest.raises(InvalidInput):
        plat.create_user("a@x.io", "A", "a@x.io")
    with pytest.raises(InvalidInput):
        plat.create_user("not-an-email", "A", PW)


def test_authenticate_and_rehash(plat, monkeypatch):
    u = plat.create_user("Ada@X.io", "Ada", PW)
    assert u.email == "ada@x.io"
    assert plat.authenticate("ADA@x.io", PW).id == u.id
    assert plat.authenticate("ada@x.io", "wrong password!") is None
    assert plat.authenticate("nobody@x.io", PW) is None
    monkeypatch.setattr(security, "SCRYPT_N", 2 ** 11)  # stronger parameters roll out on next login
    assert plat.authenticate("ada@x.io", PW)
    stored = plat._row("SELECT password_hash FROM users WHERE id = ?", (u.id,))["password_hash"]
    assert stored.startswith("scrypt$2048$")


def test_disabled_users_cannot_sign_in(plat):
    admin = plat.create_user("root@x.io", "Root", PW, is_admin=True)
    u = plat.create_user("a@x.io", "A", PW)
    token, _ = plat.login("a@x.io", PW)
    plat.disable_user(u.id, actor=session(plat, "root@x.io"))
    assert plat.authenticate("a@x.io", PW) is None
    assert plat.session_principal(token) is None
    with pytest.raises(Conflict):
        plat.disable_user(admin.id)  # last active server admin
    plat.enable_user(u.id)
    assert plat.authenticate("a@x.io", PW)


def test_disabled_account_with_the_right_password_is_told_so(plat, monkeypatch):
    """Right password on a disabled account: say it is disabled. Wrong password: the generic answer. Either way
    exactly one scrypt runs, as for a missing account, so the answer costs the same."""
    owner = plat.create_user("owner@x.io", "Owner", PW)
    team = plat.create_team("Acme", owner_id=owner.id)
    u = plat.create_user("a@x.io", "A", PW)
    plat.disable_user(u.id)
    calls: list[int] = []
    real = security._scrypt
    monkeypatch.setattr(security, "_scrypt", lambda *a: calls.append(1) or real(*a))
    with pytest.raises(AccountDisabled) as exc:
        plat.login("A@x.io", PW)
    assert str(exc.value) == "This account is disabled. Ask an admin to turn it back on."
    assert exc.value.status == 403 and not isinstance(exc.value, AuthError)
    for email, pw in (("a@x.io", "wrong password"), ("nobody@x.io", PW)):
        with pytest.raises(AuthError, match="^invalid email or password$"):
            plat.login(email, pw)
    assert len(calls) == 3
    assert plat.authenticate("a@x.io", PW) is None  # not asked to explain: a disabled account just fails
    failed = [e for e in plat.audit_entries(limit=10) if e.action == "auth.login_failed"]
    assert [e.detail.get("reason") for e in failed] == [None, None, "account disabled"]
    assert plat._row("SELECT COUNT(*) AS n FROM web_sessions")["n"] == 0
    _, token = plat.invite(team.id, "a@x.io")
    with pytest.raises(AccountDisabled):  # accepting an invite with the right password: same answer
        plat.accept_invitation(token, password=PW)
    with pytest.raises(AuthError):
        plat.accept_invitation(token, password="wrong password")
    plat.enable_user(u.id)
    assert plat.login("a@x.io", PW)[1].user_id == u.id


def test_only_admins_manage_users(plat):
    plat.create_user("root@x.io", "Root", PW, is_admin=True)
    u = plat.create_user("a@x.io", "A", PW)
    with pytest.raises(PermissionDenied):
        plat.disable_user(u.id, actor=session(plat, "a@x.io"))
    with pytest.raises(PermissionDenied):
        plat.list_users(actor=session(plat, "a@x.io"))
    root = session(plat, "root@x.io")
    with pytest.raises(Conflict):
        plat.disable_user(root.user_id, actor=root)


def test_change_and_reset_password(plat):
    u = plat.create_user("a@x.io", "A", PW)
    t1, p1 = plat.login("a@x.io", PW)
    t2, _ = plat.login("a@x.io", PW)
    with pytest.raises(AuthError):
        plat.change_password(u.id, "wrong password", "another good one")
    plat.change_password(u.id, PW, "another good one", keep_session_id=p1.session_id)
    assert plat.session_principal(t1) is not None and plat.session_principal(t2) is None
    assert plat.authenticate("a@x.io", "another good one")
    tok_team = plat.create_team("T", owner_id=u.id)
    _, secret = plat.issue_token(u.id, tok_team.id, "t")
    with pytest.raises(PermissionDenied):
        plat.change_password(u.id, "another good one", "yet another one", actor=plat.verify_token(secret))
    otp = plat.reset_password(u.id)
    assert plat.session_principal(t1) is None
    assert plat.user(u.id).must_change_password
    _, p = plat.login("a@x.io", otp)
    assert p.must_change_password
    plat.change_password(u.id, otp, "brand new password")
    assert not plat.user(u.id).must_change_password


# ---- local (solo) mode -----------------------------------------------------------------------------------
def test_local_owner_is_idempotent(plat, tmp_path):
    a = plat.ensure_local_owner()
    b = plat.ensure_local_owner()
    assert a.user_id == b.user_id and a.kind == "local" and a.is_admin
    teams = plat.list_teams(a)
    assert [(t.name, t.role) for t in teams] == [("Personal", "owner")]
    repo = tmp_path / "repo"
    repo.mkdir()
    p1 = plat.register_local_project(repo)
    p2 = plat.register_local_project(str(repo) + "/")
    assert p1.id == p2.id and p1.team_id == plat.local_team_id and p1.data_dir == str(repo.resolve() / ".cairn")
    assert plat.can(plat.local_principal(), "project.admin", project_id=p1.id)
    with pytest.raises(InvalidInput):
        plat.register_local_project(tmp_path / "missing")


def test_local_team_is_recreated_after_deletion(plat):
    plat.ensure_local_owner()
    old = plat.local_team_id
    plat.delete_team(old)
    assert plat.local_principal() and plat.local_team_id not in (None, old)


def test_bootstrap_adopts_local_owner(plat, tmp_path):
    plat.ensure_local_owner()
    repo = tmp_path / "repo"
    repo.mkdir()
    proj = plat.register_local_project(repo)
    user, team, otp = plat.bootstrap_owner("lead@x.io", "Lead", team_name="Acme")
    assert otp and user.must_change_password and user.is_admin and not user.is_local
    assert team.id == proj.team_id and team.slug == "acme"
    assert plat.authenticate("lead@x.io", otp)
    with pytest.raises(Conflict):
        plat.bootstrap_owner("other@x.io", "Other")


def test_bootstrap_without_local_owner(plat):
    user, team, otp = plat.bootstrap_owner("lead@x.io", "Lead", password=PW)
    assert otp is None and not user.must_change_password
    assert plat.member_role(team.id, user.id) == "owner"


# ---- teams and members -----------------------------------------------------------------------------------
def test_team_crud_and_slugs(plat):
    owner = plat.create_user("o@x.io", "O", PW)
    a = plat.create_team("Acme Inc", owner_id=owner.id)
    b = plat.create_team("Acme Inc", owner_id=owner.id)
    assert (a.slug, b.slug) == ("acme-inc", "acme-inc-2")
    with pytest.raises(Conflict):
        plat.create_team("X", slug="acme-inc", owner_id=owner.id)
    with pytest.raises(InvalidInput):
        plat.create_team("X", slug="Bad Slug!", owner_id=owner.id)
    t = plat.update_team(a.id, name="Acme", slug="acme", settings={"k": 1}, actor=session(plat, "o@x.io"))
    assert (t.name, t.slug, t.settings) == ("Acme", "acme", {"k": 1})
    plat.delete_team(b.id)
    assert plat.get_team(b.id) is None


def test_team_creation_policy(plat, home):
    plat.create_user("root@x.io", "Root", PW, is_admin=True)
    plat.create_user("a@x.io", "A", PW)
    with pytest.raises(PermissionDenied):
        plat.create_team("Mine", actor=session(plat, "a@x.io"))
    assert plat.create_team("Ops", actor=session(plat, "root@x.io")).slug == "ops"
    open_plat = Platform(config=ServerConfig(team_creation="anyone"))
    t = open_plat.create_team("Mine", actor=session(open_plat, "a@x.io"))
    assert open_plat.member_role(t.id, open_plat.user_by_email("a@x.io").id) == "owner"
    open_plat.close()


def test_last_owner_protection(plat):
    team, users = team_with(plat, bob="admin", carol="member")
    owner = users["owner"]
    with pytest.raises(Conflict):
        plat.change_role(team.id, owner.id, "admin")
    with pytest.raises(Conflict):
        plat.remove_member(team.id, owner.id)
    with pytest.raises(Conflict):
        plat.remove_member(team.id, owner.id, actor=session(plat, "owner@x.io"))  # leaving
    with pytest.raises(Conflict):
        plat.disable_user(owner.id)
    plat.change_role(team.id, users["bob"].id, "owner")
    plat.disable_user(users["bob"].id)
    with pytest.raises(Conflict):  # a disabled co-owner doesn't count
        plat.change_role(team.id, owner.id, "member")
    plat.enable_user(users["bob"].id)
    plat.change_role(team.id, owner.id, "member")
    assert plat.member_role(team.id, owner.id) == "member"


def test_admins_manage_at_or_below_their_role(plat):
    team, users = team_with(plat, bob="admin", carol="member", dan="admin")
    bob = session(plat, "bob@x.io")
    plat.change_role(team.id, users["carol"].id, "viewer", actor=bob)
    plat.change_role(team.id, users["dan"].id, "member", actor=bob)  # peer admin
    with pytest.raises(PermissionDenied):
        plat.change_role(team.id, users["carol"].id, "owner", actor=bob)
    with pytest.raises(PermissionDenied):
        plat.change_role(team.id, users["owner"].id, "member", actor=bob)
    with pytest.raises(PermissionDenied):
        plat.remove_member(team.id, users["owner"].id, actor=bob)
    carol = session(plat, "carol@x.io")
    with pytest.raises(PermissionDenied):
        plat.change_role(team.id, users["dan"].id, "viewer", actor=carol)
    plat.remove_member(team.id, users["carol"].id, actor=carol)  # anyone may leave
    assert plat.member_role(team.id, users["carol"].id) is None


def test_removing_a_member_revokes_their_access(plat, tmp_path):
    team, users = team_with(plat, carol="member")
    repo = tmp_path / "r"
    repo.mkdir()
    proj = plat.register_local_project(repo, team_id=team.id)
    plat.set_project_role(proj.id, users["carol"].id, "admin")
    tok, secret = plat.issue_token(users["carol"].id, team.id, "laptop")
    plat.remove_member(team.id, users["carol"].id)
    assert plat.verify_token(secret) is None
    assert plat.token(tok.id).revoked_at is not None
    assert plat._row("SELECT COUNT(*) AS n FROM project_roles")["n"] == 0


# ---- invitations -----------------------------------------------------------------------------------------
def test_invitation_lifecycle_new_account(plat, clock):
    team, users = team_with(plat)
    owner = session(plat, "owner@x.io")
    inv, token = plat.invite(team.id, "New@X.io", "member", actor=owner)
    assert inv.email == "new@x.io" and inv.status(clock()) == "pending"
    assert plat.invitation_by_token(token).id == inv.id
    with pytest.raises(InvalidInput):
        plat.accept_invitation(token, name="New", password="short")
    user, role, created = plat.accept_invitation(token, name="New", password=PW)
    assert created and role == "member" and plat.member_role(team.id, user.id) == "member"
    assert plat.authenticate("new@x.io", PW)
    with pytest.raises(Conflict):
        plat.accept_invitation(token, name="Again", password=PW)
    with pytest.raises(Conflict):
        plat.invite(team.id, "new@x.io", actor=owner)  # already a member


def test_invitation_existing_account_and_email_binding(plat):
    team, users = team_with(plat)
    plat.create_user("sam@x.io", "Sam", PW)
    plat.create_user("eve@x.io", "Eve", PW)
    _, token = plat.invite(team.id, "sam@x.io", "viewer")
    with pytest.raises(AuthError):
        plat.accept_invitation(token, password="not the password")
    with pytest.raises(PermissionDenied):
        plat.accept_invitation(token, user_id=plat.user_by_email("eve@x.io").id)
    user, role, created = plat.accept_invitation(token, user_id=plat.user_by_email("sam@x.io").id)
    assert role == "viewer" and not created


def test_invitation_expiry_revocation_and_replacement(plat, clock):
    team, _ = team_with(plat)
    inv1, t1 = plat.invite(team.id, "x@x.io", days=1)
    inv2, t2 = plat.invite(team.id, "x@x.io", days=1)
    with pytest.raises(NotFound):
        plat.invitation_by_token(t1)  # replaced by the newer one
    assert [i.id for i in plat.list_invitations(team.id)] == [inv2.id]
    clock.advance(86400 + 1)
    with pytest.raises(Expired):
        plat.accept_invitation(t2, name="X", password=PW)
    _, t3 = plat.invite(team.id, "x@x.io")
    plat.revoke_invitation(plat.invitation_by_token(t3).id)
    with pytest.raises(NotFound):
        plat.invitation_by_token(t3)
    with pytest.raises(NotFound):
        plat.invitation_by_token("made-up")


def test_invite_permissions(plat):
    team, _ = team_with(plat, bob="admin", carol="member")
    with pytest.raises(PermissionDenied):
        plat.invite(team.id, "x@x.io", "owner", actor=session(plat, "bob@x.io"))
    with pytest.raises(PermissionDenied):
        plat.invite(team.id, "x@x.io", "viewer", actor=session(plat, "carol@x.io"))
    plat.invite(team.id, "x@x.io", "admin", actor=session(plat, "bob@x.io"))
    outsider = plat.create_user("out@x.io", "Out", PW)
    with pytest.raises(NotFound):
        plat.invite(team.id, "y@x.io", actor=plat.login(outsider.email, PW)[1])


# ---- web sessions ----------------------------------------------------------------------------------------
def test_login_logout_and_expiry(plat, clock):
    u = plat.create_user("a@x.io", "A", PW)
    with pytest.raises(AuthError):
        plat.login("a@x.io", "wrong password")
    token, p = plat.login("a@x.io", PW, user_agent="pytest", ip="10.0.0.1")
    assert p.kind == "session" and p.user_id == u.id and plat.user(u.id).last_login_at == clock()
    assert plat.session_principal(token).session_id == p.session_id
    assert [s.user_agent for s in plat.list_sessions(u.id)] == ["pytest"]
    assert plat.logout(token) and plat.session_principal(token) is None and not plat.logout(token)
    assert plat.audit_entries(limit=1)[0].actor == "a@x.io"
    token, _ = plat.login("a@x.io", PW)
    clock.advance(plat.config.session_days * 86400 + 1)
    assert plat.session_principal(token) is None
    assert plat._row("SELECT COUNT(*) AS n FROM web_sessions")["n"] == 0


def test_csrf_tokens_are_bound_to_the_session(plat):
    plat.create_user("a@x.io", "A", PW)
    _, p1 = plat.login("a@x.io", PW)
    _, p2 = plat.login("a@x.io", PW)
    c1 = plat.csrf_token(p1)
    assert c1 and plat.check_csrf(p1, c1) and not plat.check_csrf(p2, c1) and not plat.check_csrf(p1, None)
    assert plat.csrf_token(plat.ensure_local_owner()) is None


# ---- API tokens ------------------------------------------------------------------------------------------
def test_token_issue_verify_revoke(plat, clock):
    team, users = team_with(plat, carol="member")
    tok, secret = plat.issue_token(users["carol"].id, team.id, "agent", scopes=["agent"])
    assert security.TOKEN_RE.match(secret) and secret.startswith(f"cairn_{tok.prefix}_")
    assert tok.scopes == ("project.capture", "project.read", "project.write", "team.read")
    assert tok.expires_at == pytest.approx(clock() + 90 * 86400)
    p = plat.verify_token(secret)
    assert p.kind == "token" and p.user_id == users["carol"].id and p.token_team_id == team.id
    assert plat.token(tok.id).last_used_at == clock()
    assert plat.principal_from_bearer(f"Bearer {secret}").token_id == tok.id
    assert plat.principal_from_bearer(f"Basic {secret}") is None
    assert plat.verify_token(secret[:-1] + ("A" if secret[-1] != "A" else "B")) is None
    assert plat.verify_token("cairn_nothere1_" + "a" * 40) is None
    assert plat.resolve_token(tok.prefix).id == tok.id
    plat.revoke_token(tok.prefix)
    assert plat.verify_token(secret) is None
    assert plat.list_tokens(user_id=users["carol"].id) == []
    assert len(plat.list_tokens(user_id=users["carol"].id, include_revoked=True)) == 1


def test_token_expiry_and_never_expiring(plat, clock):
    team, users = team_with(plat)
    _, short = plat.issue_token(users["owner"].id, team.id, "short", expires_days=1)
    tok, forever = plat.issue_token(users["owner"].id, team.id, "forever", expires_days=0)
    assert tok.expires_at is None
    clock.advance(86400 + 1)
    assert plat.verify_token(short) is None and plat.verify_token(forever) is not None


def test_token_rules(plat, tmp_path):
    team, users = team_with(plat, carol="member", bob="admin", dora="member")
    carol = session(plat, "carol@x.io")
    with pytest.raises(PermissionDenied):
        plat.issue_token(users["bob"].id, team.id, "not mine", actor=carol)
    with pytest.raises(InvalidInput):
        plat.issue_token(users["carol"].id, team.id, "x", scopes=["project.fly"])
    other = plat.create_team("Other", owner_id=users["owner"].id)
    with pytest.raises(NotFound):
        plat.issue_token(users["carol"].id, other.id, "x")
    _, secret = plat.issue_token(users["carol"].id, team.id, "x")
    as_token = plat.verify_token(secret)
    with pytest.raises(PermissionDenied):
        plat.issue_token(users["carol"].id, team.id, "minted by a token", actor=as_token)
    tok, _ = plat.issue_token(users["carol"].id, team.id, "y")
    with pytest.raises(NotFound):  # members can't see or revoke others' tokens
        plat.revoke_token(tok.id, actor=session(plat, "dora@x.io"))
    plat.revoke_token(tok.id, actor=session(plat, "bob@x.io"))  # admins hold team.tokens
    with pytest.raises(PermissionDenied):
        plat.list_tokens(team_id=team.id, actor=carol)
    assert plat.list_tokens(team_id=team.id, actor=session(plat, "bob@x.io"))



# ---- audit -----------------------------------------------------------------------------------------------
def test_audit_trail(plat):
    team, users = team_with(plat, bob="admin", carol="member")
    owner = session(plat, "owner@x.io")
    plat.invite(team.id, "z@x.io", actor=owner)
    plat.issue_token(users["carol"].id, team.id, "t")
    with pytest.raises(AuthError):
        plat.login("carol@x.io", "bad password here", ip="10.1.1.1")
    actions = [e.action for e in plat.audit_entries(team_id=team.id)]
    assert {"team.create", "member.add", "member.invite", "token.issue"} <= set(actions)
    failed = plat.audit_entries(limit=5)[0]
    assert failed.action == "auth.login_failed" and failed.ip == "10.1.1.1" and failed.target == "email:carol@x.io"
    invite = next(e for e in plat.audit_entries(team_id=team.id) if e.action == "member.invite")
    assert invite.actor == "owner@x.io" and invite.detail == {"email": "z@x.io", "role": "member"}
    with pytest.raises(PermissionDenied):
        plat.audit_entries(team_id=team.id, actor=session(plat, "carol@x.io"))
    assert plat.audit_entries(team_id=team.id, actor=session(plat, "bob@x.io"))
    with pytest.raises(PermissionDenied):
        plat.audit_entries(actor=session(plat, "bob@x.io"))  # the global log is for server admins
    older = plat.audit_entries(team_id=team.id, before=plat.audit_entries(team_id=team.id)[0].ts + 1, limit=2)
    assert len(older) == 2


def test_team_audit_includes_members_sign_ins_only(plat, clock):
    """Sign-ins carry no team: a team's log shows those of its current members (a failed attempt when it tried a
    member's account), each once, and never a non-member's."""
    team, users = team_with(plat, carol="member")
    other = plat.create_user("out@x.io", "Out", PW)
    zeta = plat.create_team("Zeta", owner_id=other.id)
    both = plat.create_user("both@x.io", "Both", PW)
    plat.add_member(team.id, both.id, "viewer")
    plat.add_member(zeta.id, both.id, "viewer")

    def step():
        clock.advance(1)

    token, _ = plat.login("carol@x.io", PW)
    step()
    plat.logout(token)
    step()
    for email in ("CAROL@x.io", "out@x.io", "nobody@x.io"):  # a failed try against a member, an outsider, no one
        with pytest.raises(AuthError):
            plat.login(email, "wrong password")
        step()
    plat.login("out@x.io", PW)
    step()
    plat.login("both@x.io", PW)

    def sign_ins(team_id):
        entries = plat.audit_entries(team_id=team_id, limit=500)
        assert len({e.id for e in entries}) == len(entries)  # no duplicates
        return [(e.action, e.target) for e in entries if e.action.startswith("auth.")]

    carol, both_id = users["carol"].id, both.id
    assert sign_ins(team.id) == [("auth.login", f"user:{both_id}"), ("auth.login_failed", "email:carol@x.io"),
                                 ("auth.logout", f"user:{carol}"), ("auth.login", f"user:{carol}")]
    assert sign_ins(zeta.id) == [("auth.login", f"user:{both_id}"), ("auth.login", f"user:{other.id}"),
                                 ("auth.login_failed", "email:out@x.io")]
    # team events are still there, interleaved by time, and paging with `before` keeps working
    everything = plat.audit_entries(team_id=team.id, limit=500)
    assert {"team.create", "member.add"} <= {e.action for e in everything}
    page = plat.audit_entries(team_id=team.id, limit=2)
    rest = plat.audit_entries(team_id=team.id, before=page[-1].ts, limit=500)
    assert [e.id for e in page + rest] == [e.id for e in everything]
    # "current member": once Carol leaves, her sign-ins leave the team's log (the rows themselves stay)
    plat.remove_member(team.id, carol)
    assert all(t not in (f"user:{carol}", "email:carol@x.io") for _, t in sign_ins(team.id))
    assert any(e.target == f"user:{carol}" for e in plat.audit_entries(limit=500))


# ---- secrets at rest -------------------------------------------------------------------------------------
def test_no_secret_is_stored_in_plain_text(plat, tmp_path):
    team, users = team_with(plat, carol="member")
    repo = tmp_path / "r"
    repo.mkdir()
    proj = plat.register_local_project(repo, team_id=team.id)
    secrets_seen = [PW]
    cookie, p = plat.login("carol@x.io", PW)
    secrets_seen += [cookie, plat.csrf_token(p)]
    tok, full = plat.issue_token(users["carol"].id, team.id, "t")
    secrets_seen += [full, full.split("_", 2)[2]]
    _, invite_token = plat.invite(team.id, "new@x.io")
    secrets_seen.append(invite_token)
    plat.accept_invitation(invite_token, name="New", password="new user password 1")
    secrets_seen.append("new user password 1")
    secrets_seen.append(plat.rotate_webhook_secret(proj.id))
    otp = plat.reset_password(users["carol"].id)
    secrets_seen.append(otp)
    plat.change_password(users["carol"].id, otp, "final password 123")
    secrets_seen.append("final password 123")

    dump = []
    with plat._lock:
        for (table,) in plat._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for row in plat._conn.execute(f"SELECT * FROM {table}").fetchall():
                dump.append(" ".join(str(v) for v in tuple(row)))
    text = "\n".join(dump)
    raw = b"".join(f.read_bytes() for f in plat.home.iterdir() if f.name.startswith("platform.db"))
    for s in secrets_seen:
        assert s not in text, s
        assert s.encode() not in raw, s
    for rec in (tok.public(0), plat.user(users["carol"].id).public(), proj.public()):
        assert not any(k in rec for k in ("password_hash", "secret_hash", "token_hash", "webhook_nonce"))
