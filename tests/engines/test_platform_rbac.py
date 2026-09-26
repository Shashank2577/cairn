"""Role-based access: the full permission matrix, per-project overrides and API-token scopes."""
from __future__ import annotations

import pytest

from cairn.platform import security
from cairn.platform.config import ENV_KEYS
from cairn.platform.errors import Conflict, InvalidInput, NotFound, PermissionDenied
from cairn.platform.rbac import ACTIONS, MATRIX, ROLES, normalize_scopes, role_allows
from cairn.platform.service import Platform

PW = "correct horse battery"

# Written out by hand on purpose: this is the contract, not a copy of the implementation.
EXPECTED = {
    "viewer": {"project.read", "team.read"},
    "member": {"project.read", "project.write", "project.sync", "project.capture", "team.read"},
    "admin": {"project.read", "project.write", "project.sync", "project.capture", "project.admin",
              "team.read", "team.members", "team.tokens", "team.audit"},
    "owner": set(ACTIONS),
}
PROJECT_ACTIONS = [a for a in ACTIONS if a.startswith("project.")]
TEAM_ACTIONS = [a for a in ACTIONS if a.startswith("team.")]


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(security, "SCRYPT_N", 2 ** 10)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """Team 'acme' with one user per role and two projects; a second team 'zeta' with its own project."""
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(security.KEY_ENV, raising=False)
    for var in ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)
    plat = Platform()
    users = {}
    for role in ROLES:
        users[role] = plat.create_user(f"{role}@x.io", role.title(), PW)
    team = plat.create_team("Acme", owner_id=users["owner"].id)
    for role in ("viewer", "member", "admin"):
        plat.add_member(team.id, users[role].id, role)
    outsider = plat.create_user("out@x.io", "Out", PW)
    zeta = plat.create_team("Zeta", owner_id=outsider.id)
    projects = {}
    for name, tid in (("api", team.id), ("web", team.id), ("z", zeta.id)):
        d = tmp_path / name
        d.mkdir()
        projects[name] = plat.register_local_project(d, team_id=tid)
    principals = {role: plat.login(f"{role}@x.io", PW)[1] for role in ROLES}
    principals["outsider"] = plat.login("out@x.io", PW)[1]
    yield plat, team, zeta, users, projects, principals
    plat.close()


def test_matrix_constant_matches_contract():
    for role in ROLES:
        assert set(MATRIX[role]) == EXPECTED[role], role
        for action in ACTIONS:
            assert role_allows(role, action) == (action in EXPECTED[role])
    assert not role_allows(None, "project.read") and not role_allows("none", "project.read")
    with pytest.raises(ValueError):
        role_allows("owner", "project.fly")


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("action", list(ACTIONS))
def test_full_matrix_through_the_service(world, role, action):
    plat, team, zeta, users, projects, principals = world
    p = principals[role]
    expected = action in EXPECTED[role]
    if action.startswith("project."):
        assert plat.can(p, action, project_id=projects["api"].id) is expected
        assert plat.can(p, action, team_id=team.id) is expected  # team-level (e.g. registering a project)
        assert plat.can(p, action, project_id=projects["z"].id) is False  # other team's project
    else:
        assert plat.can(p, action, team_id=team.id) is expected
        assert plat.can(p, action, project_id=projects["api"].id) is expected  # resolved to the project's team
    assert plat.can(p, action, team_id=zeta.id) is False
    assert plat.can(principals["outsider"], action, team_id=team.id) is False


def test_can_needs_context_and_known_actions(world):
    plat, team, *_rest, principals = world
    assert plat.can(principals["owner"], "project.read") is False
    assert plat.can(None, "project.read", team_id=team.id) is False
    with pytest.raises(ValueError):
        plat.can(principals["owner"], "team.fly", team_id=team.id)


def test_require_hides_what_you_cannot_see(world):
    plat, team, zeta, users, projects, principals = world
    with pytest.raises(NotFound):
        plat.require(principals["outsider"], "project.write", project_id=projects["api"].id)
    with pytest.raises(PermissionDenied):
        plat.require(principals["viewer"], "project.write", project_id=projects["api"].id)
    with pytest.raises(NotFound):
        plat.require(principals["viewer"], "team.members", team_id=zeta.id)
    with pytest.raises(PermissionDenied):
        plat.require(principals["viewer"], "team.members", team_id=team.id)
    with pytest.raises(NotFound):
        plat.require(principals["owner"], "project.read", project_id="p_missing")


def test_disabled_user_loses_everything(world):
    plat, team, zeta, users, projects, principals = world
    plat.disable_user(users["admin"].id)
    assert not any(plat.can(principals["admin"], a, team_id=team.id) for a in ACTIONS)
    assert plat.list_projects(principals["admin"]) == []


# ---- project overrides -----------------------------------------------------------------------------------
def test_override_lowers_and_raises_for_one_project(world):
    plat, team, zeta, users, projects, principals = world
    api, web = projects["api"].id, projects["web"].id
    plat.set_project_role(api, users["member"].id, "viewer")
    assert not plat.can(principals["member"], "project.write", project_id=api)
    assert plat.can(principals["member"], "project.write", project_id=web)
    plat.set_project_role(api, users["viewer"].id, "admin")
    assert plat.can(principals["viewer"], "project.admin", project_id=api)
    assert not plat.can(principals["viewer"], "project.write", project_id=web)
    assert not plat.can(principals["viewer"], "team.members", team_id=team.id)  # team actions ignore overrides
    assert not plat.can(principals["viewer"], "team.members", project_id=api)
    assert plat.effective_role(users["viewer"].id, project_id=api) == "admin"
    plat.set_project_role(api, users["viewer"].id, None)
    assert plat.effective_role(users["viewer"].id, project_id=api) == "viewer"


def test_override_none_hides_a_project(world):
    plat, team, zeta, users, projects, principals = world
    api = projects["api"].id
    plat.set_project_role(api, users["admin"].id, "none")
    assert not plat.can(principals["admin"], "project.read", project_id=api)
    assert [p.slug for p in plat.list_projects(principals["admin"])] == ["web"]
    with pytest.raises(NotFound):
        plat.require(principals["admin"], "project.read", project_id=api)
    listed = {m["email"]: m for m in plat.project_members(api)}
    assert listed["admin@x.io"]["role"] is None and listed["admin@x.io"]["override"] == "none"


def test_override_rules(world):
    plat, team, zeta, users, projects, principals = world
    api = projects["api"].id
    with pytest.raises(Conflict):
        plat.set_project_role(api, users["owner"].id, "viewer")  # owners are never overridden
    with pytest.raises(InvalidInput):
        plat.set_project_role(api, users["member"].id, "owner")
    outsider = plat.user_by_email("out@x.io")
    with pytest.raises(InvalidInput):
        plat.set_project_role(api, outsider.id, "viewer")
    with pytest.raises(PermissionDenied):
        plat.set_project_role(api, users["viewer"].id, "member", actor=principals["member"])
    plat.set_project_role(api, users["viewer"].id, "admin", actor=principals["admin"])
    assert plat.can(principals["viewer"], "project.admin", project_id=api)
    with pytest.raises(PermissionDenied):  # an override-made project admin can't lock out higher team roles
        plat.set_project_role(api, users["admin"].id, "none", actor=principals["viewer"])
    with pytest.raises(PermissionDenied):
        plat.set_project_role(api, users["member"].id, "viewer", actor=principals["viewer"])
    with pytest.raises(PermissionDenied):  # and the power stays on that one project
        plat.set_project_role(projects["web"].id, users["viewer"].id, "admin", actor=principals["viewer"])
    with pytest.raises(PermissionDenied):  # nobody grants more than they hold
        plat.set_project_role(api, users["member"].id, "admin", actor=principals["member"])


def test_list_projects_reports_effective_roles(world):
    plat, team, zeta, users, projects, principals = world
    plat.set_project_role(projects["web"].id, users["member"].id, "admin")
    roles = {p.slug: p.role for p in plat.list_projects(principals["member"])}
    assert roles == {"api": "member", "web": "admin"}
    assert {p.slug for p in plat.list_projects(principals["owner"], team_id=team.id)} == {"api", "web"}
    assert {p.slug for p in plat.list_projects(principals["outsider"])} == {"z"}


# ---- token scopes ----------------------------------------------------------------------------------------
def test_scope_presets():
    assert normalize_scopes(["read"]) == ("project.read", "team.read")
    assert normalize_scopes(["ci", "project.read"]) == ("project.read", "project.sync")
    assert normalize_scopes(["all", "read"]) == ("*",)
    assert normalize_scopes(None) == ("project.capture", "project.read", "project.write", "team.read")
    with pytest.raises(ValueError):
        normalize_scopes([])
    with pytest.raises(ValueError):
        normalize_scopes(["root"])


def test_token_scopes_narrow_but_never_widen(world):
    plat, team, zeta, users, projects, principals = world
    api = projects["api"].id
    _, read = plat.issue_token(users["owner"].id, team.id, "read-only", scopes=["read"])
    p = plat.verify_token(read)
    assert plat.can(p, "project.read", project_id=api) and plat.can(p, "team.read", team_id=team.id)
    assert not plat.can(p, "project.write", project_id=api)
    assert not plat.can(p, "team.delete", team_id=team.id)
    _, everything = plat.issue_token(users["viewer"].id, team.id, "viewer-all", scopes=["all"])
    v = plat.verify_token(everything)
    assert plat.can(v, "project.read", project_id=api) and not plat.can(v, "project.write", project_id=api)
    _, ci = plat.issue_token(users["member"].id, team.id, "ci", scopes=["ci"])
    c = plat.verify_token(ci)
    assert plat.can(c, "project.sync", project_id=api) and not plat.can(c, "project.capture", project_id=api)


def test_project_scoped_token(world):
    plat, team, zeta, users, projects, principals = world
    api, web = projects["api"].id, projects["web"].id
    _, secret = plat.issue_token(users["admin"].id, team.id, "api only", project_id=api, scopes=["all"])
    p = plat.verify_token(secret)
    assert p.token_project_id == api
    assert plat.can(p, "project.admin", project_id=api)
    assert not plat.can(p, "project.read", project_id=web)
    assert not plat.can(p, "project.admin", team_id=team.id)
    assert not any(plat.can(p, a, team_id=team.id) for a in TEAM_ACTIONS)
    assert [x.slug for x in plat.list_projects(p)] == ["api"]
    with pytest.raises(NotFound):
        plat.issue_token(users["admin"].id, team.id, "wrong team", project_id=projects["z"].id)


def test_token_is_bound_to_its_team(world):
    plat, team, zeta, users, projects, principals = world
    plat.add_member(zeta.id, users["member"].id, "admin")
    _, secret = plat.issue_token(users["member"].id, team.id, "acme")
    p = plat.verify_token(secret)
    assert plat.can(p, "project.read", project_id=projects["api"].id)
    assert not plat.can(p, "project.read", project_id=projects["z"].id)
    assert plat.can(principals["member"], "project.read", project_id=projects["z"].id)  # the session can
    assert [t.slug for t in plat.list_teams(p)] == ["acme"]


def test_tokens_follow_role_changes(world):
    plat, team, zeta, users, projects, principals = world
    api = projects["api"].id
    _, secret = plat.issue_token(users["member"].id, team.id, "agent")
    assert plat.can(plat.verify_token(secret), "project.write", project_id=api)
    plat.change_role(team.id, users["member"].id, "viewer")
    assert not plat.can(plat.verify_token(secret), "project.write", project_id=api)
    plat.set_project_role(api, users["member"].id, "none")
    assert not plat.can(plat.verify_token(secret), "project.read", project_id=api)


# ---- existence oracles (found in review) -----------------------------------------------------------------
def test_no_membership_oracle_for_outsiders(world):
    plat, team, zeta, users, projects, principals = world
    stranger = plat.create_user("stranger@x.io", "S", PW)
    for call in (lambda: plat.change_role(team.id, users["member"].id, "viewer", actor=principals["outsider"]),
                 lambda: plat.change_role(team.id, stranger.id, "viewer", actor=principals["outsider"]),
                 lambda: plat.remove_member(team.id, users["member"].id, actor=principals["outsider"]),
                 lambda: plat.remove_member(team.id, stranger.id, actor=principals["outsider"])):
        with pytest.raises(NotFound, match="team not found"):
            call()
    with pytest.raises(PermissionDenied):  # a member can't learn more than "not allowed" either
        plat.change_role(team.id, stranger.id, "viewer", actor=principals["member"])


def test_no_server_path_probing(world, tmp_path):
    plat, team, zeta, users, projects, principals = world
    z_root = projects["z"].root
    for path in (z_root, str(tmp_path / "does-not-exist"), "/etc"):
        with pytest.raises(PermissionDenied):
            plat.register_local_project(path, team_id=team.id, actor=principals["owner"])
    admin = plat.create_user("root@x.io", "Root", PW, is_admin=True)
    plat.add_member(team.id, admin.id, "owner")
    root = plat.login("root@x.io", PW)[1]
    with pytest.raises(NotFound):  # registered in a team the admin can't see: not found, not "conflict"
        plat.register_local_project(z_root, team_id=team.id, actor=root)
    with pytest.raises(InvalidInput):
        plat.register_local_project(tmp_path / "does-not-exist", team_id=team.id, actor=root)
