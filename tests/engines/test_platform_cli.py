"""Platform terminal commands (``cairn team|token|project|user``) via Typer's runner."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
import typer
from typer.testing import CliRunner

from cairn.platform import commands, security
from cairn.platform.config import ENV_KEYS
from cairn.platform.service import Platform

runner = CliRunner()
app = typer.Typer()
app.add_typer(commands.team_app, name="team")
app.add_typer(commands.token_app, name="token")
app.add_typer(commands.project_app, name="project")
app.add_typer(commands.user_app, name="user")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "SCRYPT_N", 2 ** 10)
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(security.KEY_ENV, raising=False)
    for var in ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)


def run(*args: str) -> str:
    res = runner.invoke(app, list(args))
    assert res.exit_code == 0, res.output
    return res.output


def run_json(*args: str):
    return json.loads(run(*args, "--json"))


def test_team_init_and_members():
    out = run_json("team", "init", "--email", "lead@x.io", "--name", "Lead", "--team", "Acme")
    assert out["team"]["slug"] == "acme" and out["user"]["is_admin"] and out["user"]["must_change_password"]
    otp = out["one_time_password"]
    plat = Platform()
    assert plat.authenticate("lead@x.io", otp)
    plat.close()
    again = runner.invoke(app, ["team", "init", "--email", "x@x.io", "--name", "X"])
    assert again.exit_code == 1 and "already has an admin" in again.output
    inv = run_json("team", "invite", "sam@x.io", "--role", "admin")
    assert inv["url"] == f"http://127.0.0.1:4747/?invite={inv['token']}" and inv["invitation"]["role"] == "admin"
    members = run_json("team", "members")
    assert [m["email"] for m in members["members"]] == ["lead@x.io"]
    assert [i["email"] for i in members["invitations"]] == ["sam@x.io"]
    assert run_json("team", "list")[0]["members"] == 1


def test_team_init_with_typed_password():
    res = runner.invoke(app, ["team", "init", "--email", "lead@x.io", "--name", "Lead", "--set-password"],
                        input="a good long password\na good long password\n")
    assert res.exit_code == 0, res.output
    plat = Platform()
    assert plat.authenticate("lead@x.io", "a good long password")
    plat.close()


@pytest.fixture()
def health_server():
    """Something answering /api/health on a free port, like a running Cairn server."""
    import http.server
    import threading

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}' if self.path == "/api/health" else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_invite_links_follow_the_running_server(tmp_path, health_server, monkeypatch):
    run("team", "init", "--email", "lead@x.io", "--name", "Lead")
    home = tmp_path / "home"
    (home / "server.json").write_text(json.dumps({"pid": 1, "port": health_server}), encoding="utf-8")
    inv = run_json("team", "invite", "a@x.io")
    assert inv["url"] == f"http://127.0.0.1:{health_server}/?invite={inv['token']}"
    hook = run_json("project", "webhook", str(_registered(tmp_path)))
    assert hook["url"].startswith(f"http://127.0.0.1:{health_server}/api/projects/")
    # listening on every interface: the link names this machine
    monkeypatch.setattr(commands.socket, "getfqdn", lambda: "build-box.example")
    (home / "server.json").write_text(json.dumps({"pid": 1, "port": health_server, "host": "0.0.0.0"}), encoding="utf-8")
    inv = run_json("team", "invite", "b@x.io")
    assert inv["url"].startswith(f"http://build-box.example:{health_server}/?invite=")


def test_invite_links_fall_back_to_configuration(tmp_path):
    run("team", "init", "--email", "lead@x.io", "--name", "Lead")
    home = tmp_path / "home"
    (home / "server.json").write_text(json.dumps({"pid": 1, "port": _free_port()}), encoding="utf-8")  # stale: nothing answers
    (home / "server.toml").write_text('[server]\nport = 4900\n', encoding="utf-8")
    assert run_json("team", "invite", "a@x.io")["url"].startswith("http://127.0.0.1:4900/?invite=")
    (home / "server.toml").write_text('[server]\nmode = "team"\nhost = "::1"\nport = 4901\n', encoding="utf-8")
    assert run_json("team", "invite", "b@x.io")["url"].startswith("http://[::1]:4901/?invite=")
    (home / "server.toml").write_text('[server]\nport = 4900\npublic_url = "https://cairn.example.com/"\n', encoding="utf-8")
    assert run_json("team", "invite", "c@x.io")["url"].startswith("https://cairn.example.com/?invite=")


def test_public_url_wins_over_the_running_server(tmp_path, health_server):
    run("team", "init", "--email", "lead@x.io", "--name", "Lead")
    home = tmp_path / "home"
    (home / "server.json").write_text(json.dumps({"pid": 1, "port": health_server}), encoding="utf-8")
    (home / "server.toml").write_text('[server]\npublic_url = "https://cairn.example.com"\n', encoding="utf-8")
    assert run_json("team", "invite", "a@x.io")["url"].startswith("https://cairn.example.com/?invite=")


def _registered(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return run_json("project", "add", str(repo))["slug"]


def test_team_role_and_remove():
    run("team", "init", "--email", "lead@x.io", "--name", "Lead")
    plat = Platform()
    u = plat.create_user("m@x.io", "M", "correct horse battery")
    plat.add_member(plat.list_teams()[0].id, u.id, "member")
    plat.close()
    assert "now admin" in run("team", "role", "m@x.io", "admin")
    bad = runner.invoke(app, ["team", "role", "lead@x.io", "member"])
    assert bad.exit_code == 1 and "owner" in bad.output
    run("team", "remove", "m@x.io")
    assert [m["email"] for m in run_json("team", "members")["members"]] == ["lead@x.io"]


def test_tokens_issue_list_revoke():
    tok = run_json("token", "issue", "--name", "claude laptop", "--scope", "read", "--expires-days", "0")
    secret = tok["secret"]
    assert security.TOKEN_RE.match(secret) and tok["token"]["expires_at"] is None
    assert tok["token"]["scopes"] == ["project.read", "team.read"]
    listed = run_json("token", "list")
    assert [t["id"] for t in listed] == [tok["token"]["id"]]
    assert secret not in run("token", "list")
    assert "Revoked" in run("token", "revoke", tok["token"]["prefix"])
    assert run_json("token", "list") == []
    plat = Platform()
    assert plat.verify_token(secret) is None
    plat.close()


def test_projects_local_and_git(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    p = run_json("project", "add", str(repo))
    assert p["kind"] == "local" and p["root"] == str(repo.resolve())
    assert run_json("project", "add", str(repo))["id"] == p["id"]  # idempotent
    work = tmp_path / "work"
    work.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@x.io", "GIT_COMMITTER_NAME": "A",
           "GIT_COMMITTER_EMAIL": "a@x.io"}
    subprocess.run(["git", "-C", str(work), "init", "-q", "-b", "main"], check=True, env=env)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "--allow-empty", "-m", "i"], check=True, env=env)
    bare = tmp_path / "svc.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, capture_output=True)
    g = run_json("project", "add", f"file://{bare}", "--name", "Service")
    assert g["kind"] == "git" and g["status"] == "ready" and g["slug"] == "service"
    assert {x["slug"] for x in run_json("project", "list")} == {"repo", "service"}
    assert run_json("project", "refresh", "service")["pulled"] is True
    hook = run_json("project", "webhook", "service")
    assert hook["secret"].startswith("whsec_") and hook["url"].endswith(f"/api/projects/{g['id']}/hooks/git")
    run("project", "remove", "service")
    assert [x["slug"] for x in run_json("project", "list")] == ["repo"]
    bad = runner.invoke(app, ["project", "add", "https://user:tok@github.com/a/b.git"])
    assert bad.exit_code == 1 and "credentials" in bad.output


def test_users(tmp_path):
    run("team", "init", "--email", "lead@x.io", "--name", "Lead")
    plat = Platform()
    plat.create_user("m@x.io", "M", "correct horse battery")
    plat.close()
    assert {u["email"] for u in run_json("user", "list")} == {"lead@x.io", "m@x.io"}
    run("user", "disable", "m@x.io")
    assert next(u for u in run_json("user", "list") if u["email"] == "m@x.io")["disabled"]
    run("user", "enable", "m@x.io")
    otp = run_json("user", "reset-password", "m@x.io")["password"]
    plat = Platform()
    assert plat.authenticate("m@x.io", otp).must_change_password
    plat.close()
    assert runner.invoke(app, ["user", "disable", "nobody@x.io"]).exit_code == 1


def test_several_teams_need_an_explicit_choice():
    run("team", "init", "--email", "lead@x.io", "--name", "Lead", "--team", "Acme")
    run("team", "create", "Zeta", "--owner", "lead@x.io")
    plat = Platform()
    plat._conn.execute("DELETE FROM meta WHERE key = 'local_team_id'")
    plat.close()
    res = runner.invoke(app, ["token", "issue", "--name", "x", "--user", "lead@x.io"])
    assert res.exit_code == 1 and "--team" in res.output
    assert run_json("token", "issue", "--name", "x", "--user", "lead@x.io", "--team", "zeta")["secret"]
