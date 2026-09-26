"""Terminal commands for teams, tokens, projects and accounts. The main CLI mounts the Typer apps:

    app.add_typer(team_app, name="team")
    app.add_typer(token_app, name="token")
    app.add_typer(project_app, name="project")
    app.add_typer(user_app, name="user")

They act as the machine's operator (whoever can read ``$CAIRN_HOME/platform.db``), so they are not subject to
role checks, but every change still lands in the audit log.
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import gitops
from .errors import PlatformError
from .models import Team, User
from .rbac import ROLES, SCOPE_PRESETS
from .service import Platform

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

team_app = typer.Typer(help="Teams and members of a shared Cairn server.", no_args_is_help=True)
token_app = typer.Typer(help="API tokens for agents, CI and the MCP endpoint.", no_args_is_help=True)
project_app = typer.Typer(help="Projects served from this Cairn home.", no_args_is_help=True)
user_app = typer.Typer(help="Server accounts.", no_args_is_help=True)

JSON_OPT = typer.Option(False, "--json", help="Machine-readable output.")


@contextmanager
def _platform() -> Iterator[Platform]:
    try:
        plat = Platform.open()
    except ValueError as exc:
        err.print(f"[bold red]{exc}[/]")
        raise typer.Exit(2) from None
    try:
        yield plat
    except (PlatformError, ValueError) as exc:
        err.print(f"[bold red]{exc}[/]")
        raise typer.Exit(1) from None
    finally:
        plat.close()


def _emit(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, default=str) + "\n")


def _when(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"


def _table(*cols: str) -> Table:
    t = Table(box=None, padding=(0, 2), header_style="bold")
    for c in cols:
        t.add_column(c)
    return t


def pick_team(plat: Platform, ref: str | None) -> Team:
    """``--team`` by id or slug; otherwise the only team, or the local owner's Personal team."""
    if ref:
        return plat.resolve_team(ref)
    teams = plat.list_teams()
    if len(teams) == 1:
        return teams[0]
    if plat.local_team_id and plat.get_team(plat.local_team_id):
        return plat.team(plat.local_team_id)
    if not teams:
        plat.ensure_local_owner()
        return plat.team(plat.local_team_id or "")
    raise PlatformError("several teams exist; choose one with --team")


def pick_user(plat: Platform, ref: str | None) -> User:
    """``--user`` by id or email; otherwise the local owner (created on first use)."""
    if ref:
        return plat.resolve_user(ref)
    return plat.user(plat.ensure_local_owner().user_id)


_WILDCARD_HOSTS = {"", "0.0.0.0", "::", "[::]", "*"}


def _url_host(host: str) -> str:
    """A host as it goes in a URL: brackets for IPv6, and this machine's name for a listen-everywhere address."""
    h = host.strip()
    if h in _WILDCARD_HOSTS:
        try:
            h = socket.getfqdn() or socket.gethostname()
        except OSError:
            h = ""
        return h or "127.0.0.1"
    h = h.strip("[]")
    return f"[{h}]" if ":" in h else h


def _running_server(home: Path, default_host: str) -> tuple[str, int] | None:
    """Host and port of the server recorded in ``$CAIRN_HOME/server.json``, if it answers its health check."""
    try:
        data = json.loads((home / "server.json").read_text(encoding="utf-8"))
        port = int(data["port"])
        host = str(data.get("host") or default_host)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    probe = "127.0.0.1" if host.strip() in _WILDCARD_HOSTS else host
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # a local check: never via a proxy
        with opener.open(f"http://{_url_host(probe)}:{port}/api/health", timeout=1.0) as res:
            healthy = json.loads(res.read()).get("ok") is True
    except (OSError, ValueError, AttributeError):
        return None
    return (host, port) if healthy else None


def server_base_url(plat: Platform) -> str:
    """Where people reach this server, for the links the CLI prints: ``public_url`` when set (what the server
    itself builds its links from); else the running server's address (``$CAIRN_HOME/server.json``, when it
    answers); else the configured host and port."""
    if plat.config.public_url:
        return plat.config.public_url.rstrip("/")
    host, port = _running_server(plat.home, plat.config.host) or (plat.config.host, plat.config.port)
    return f"http://{_url_host(host)}:{port}"


def invite_link(plat: Platform, token: str) -> str:
    return f"{server_base_url(plat)}/?invite={token}"


# ---- team --------------------------------------------------------------------------------------------------
@team_app.command("init")
def team_init(email: str = typer.Option(..., "--email", help="The first owner's email."),
              name: str = typer.Option(..., "--name", help="The first owner's display name."),
              team: Optional[str] = typer.Option(None, "--team", help="Team name (default: your Personal team, "
                                                 "renamed, or '<name>'s team')."),
              set_password: bool = typer.Option(False, "--set-password",
                                                help="Type a password instead of printing a one-time one."),
              as_json: bool = JSON_OPT):
    """Create the first owner (a server admin) for team mode."""
    password = typer.prompt("Password", hide_input=True, confirmation_prompt=True) if set_password else None
    with _platform() as plat:
        user, t, otp = plat.bootstrap_owner(email, name, password=password, team_name=team)
    if as_json:
        _emit({"user": user.public(), "team": t.public(), "one_time_password": otp})
        return
    console.print(f"[bold]Owner[/] {user.email}  ·  [bold]team[/] {t.name} ({t.slug})")
    if otp:
        console.print(f"One-time password: [bold]{otp}[/]  (shown once; it must be changed at first sign-in)")
    console.print("Run the shared server with [bold]mode = \"team\"[/] under [server] in "
                  f"{plat.home / 'server.toml'} (or CAIRN_SERVER_MODE=team).")


@team_app.command("list")
def team_list(as_json: bool = JSON_OPT):
    """Teams on this server."""
    with _platform() as plat:
        rows = [{**t.public(), **plat.team_stats(t.id)} for t in plat.list_teams()]
    if as_json:
        _emit(rows)
        return
    t = _table("slug", "name", "members", "projects", "created")
    for r in rows:
        t.add_row(r["slug"], r["name"], str(r["members"]), str(r["projects"]), _when(r["created_at"]))
    console.print(t)


@team_app.command("create")
def team_create(name: str, owner: Optional[str] = typer.Option(None, "--owner", help="Owner email (default: local owner)."),
                slug: Optional[str] = typer.Option(None, "--slug"), as_json: bool = JSON_OPT):
    """Create a team."""
    with _platform() as plat:
        t = plat.create_team(name, slug=slug, owner_id=pick_user(plat, owner).id)
    _emit(t.public()) if as_json else console.print(f"Created team {t.name} ({t.slug})")


@team_app.command("members")
def team_members(team: Optional[str] = typer.Option(None, "--team"), as_json: bool = JSON_OPT):
    """Members and pending invitations."""
    with _platform() as plat:
        t = pick_team(plat, team)
        ms = [m.public() for m in plat.list_members(t.id)]
        invs = [i.public(plat.now()) for i in plat.list_invitations(t.id)]
    if as_json:
        _emit({"members": ms, "invitations": invs})
        return
    tb = _table("email", "name", "role", "last sign-in")
    for m in ms:
        tb.add_row(m["email"] + (" (disabled)" if m["disabled"] else ""), m["name"], m["role"], _when(m["last_login_at"]))
    for i in invs:
        tb.add_row(i["email"], "(invited)", i["role"], f"expires {_when(i['expires_at'])}")
    console.print(tb)


@team_app.command("invite")
def team_invite(email: str, role: str = typer.Option("member", "--role", help=" | ".join(ROLES)),
                team: Optional[str] = typer.Option(None, "--team"),
                days: Optional[float] = typer.Option(None, "--days", help="Days until the link expires."),
                as_json: bool = JSON_OPT):
    """Invite someone; prints the one-time invite link."""
    with _platform() as plat:
        t = pick_team(plat, team)
        inv, token = plat.invite(t.id, email, role, days=days)
        link = invite_link(plat, token)
    if as_json:
        _emit({"invitation": inv.public(inv.created_at), "token": token, "url": link})
        return
    console.print(f"Invited {inv.email} to {t.slug} as {inv.role}. Send them this link (shown once):")
    console.print(f"  [bold]{link}[/]")


@team_app.command("role")
def team_role(email: str, role: str = typer.Argument(..., help=" | ".join(ROLES)),
              team: Optional[str] = typer.Option(None, "--team")):
    """Change a member's role."""
    with _platform() as plat:
        t = pick_team(plat, team)
        plat.change_role(t.id, plat.resolve_user(email).id, role)
    console.print(f"{email} is now {role} in {t.slug}")


@team_app.command("remove")
def team_remove(email: str, team: Optional[str] = typer.Option(None, "--team")):
    """Remove a member (their tokens for the team are revoked)."""
    with _platform() as plat:
        t = pick_team(plat, team)
        plat.remove_member(t.id, plat.resolve_user(email).id)
    console.print(f"Removed {email} from {t.slug}")


@team_app.command("delete")
def team_delete(team: str, yes: bool = typer.Option(False, "--yes", help="Confirm deletion.")):
    """Delete a team with its projects' registrations and managed clones."""
    if not yes:
        err.print("Add --yes to delete the team, its memberships, tokens and managed clones.")
        raise typer.Exit(1)
    with _platform() as plat:
        t = plat.resolve_team(team)
        plat.delete_team(t.id)
    console.print(f"Deleted team {t.slug}")


# ---- token -------------------------------------------------------------------------------------------------
@token_app.command("issue")
def token_issue(name: str = typer.Option(..., "--name", help="What the token is for, e.g. 'claude-code laptop'."),
                user: Optional[str] = typer.Option(None, "--user", help="Acting user (default: local owner)."),
                team: Optional[str] = typer.Option(None, "--team"),
                project: Optional[str] = typer.Option(None, "--project", help="Limit to one project."),
                scope: Optional[list[str]] = typer.Option(None, "--scope", help="Action or preset ("
                                                          + ", ".join(SCOPE_PRESETS) + "); repeatable."),
                expires_days: Optional[float] = typer.Option(None, "--expires-days", help="0 = never."),
                as_json: bool = JSON_OPT):
    """Issue an API token. The secret is printed once."""
    with _platform() as plat:
        u = pick_user(plat, user)
        t = pick_team(plat, team)
        pid = plat.resolve_project(project, team_id=t.id).id if project else None
        tok, secret = plat.issue_token(u.id, t.id, name, project_id=pid, scopes=scope or None,
                                       expires_days=expires_days)
        now = plat.now()
    if as_json:
        _emit({"token": tok.public(now), "secret": secret})
        return
    console.print(f"Token [bold]{tok.name}[/] for {u.email} in {t.slug}"
                  + (f" (project {project})" if pid else "") + f" · scopes {', '.join(tok.scopes)}"
                  + (f" · expires {_when(tok.expires_at)}" if tok.expires_at else " · never expires"))
    console.print(f"  [bold]{secret}[/]")
    console.print("Shown once. Send it as 'Authorization: Bearer <token>'.")


@token_app.command("list")
def token_list(user: Optional[str] = typer.Option(None, "--user"), team: Optional[str] = typer.Option(None, "--team"),
               revoked: bool = typer.Option(False, "--revoked", help="Include revoked tokens."), as_json: bool = JSON_OPT):
    """Tokens (never their secrets)."""
    with _platform() as plat:
        uid = plat.resolve_user(user).id if user else None
        tid = plat.resolve_team(team).id if team else None
        now = plat.now()
        rows = [t.public(now) for t in plat.list_tokens(user_id=uid, team_id=tid, include_revoked=revoked)]
    if as_json:
        _emit(rows)
        return
    tb = _table("id", "token", "name", "user", "scopes", "status", "last used", "expires")
    for r in rows:
        tb.add_row(r["id"], r["display"], r["name"], r["user_email"] or r["user_id"], ",".join(r["scopes"]), r["status"],
                   _when(r["last_used_at"]), _when(r["expires_at"]))
    console.print(tb)


@token_app.command("revoke")
def token_revoke(ref: str = typer.Argument(..., help="Token id, prefix, or the token itself.")):
    """Revoke a token immediately."""
    with _platform() as plat:
        tok = plat.revoke_token(ref)
    console.print(f"Revoked {tok.display} ({tok.name})")


# ---- project -----------------------------------------------------------------------------------------------
@project_app.command("add")
def project_add(target: str = typer.Argument(..., help="A local folder or a git URL to clone."),
                team: Optional[str] = typer.Option(None, "--team"), name: Optional[str] = typer.Option(None, "--name"),
                slug: Optional[str] = typer.Option(None, "--slug"),
                branch: Optional[str] = typer.Option(None, "--branch", help="Branch to follow (git URLs)."),
                as_json: bool = JSON_OPT):
    """Register a project: a folder on this machine, or a git URL (cloned under $CAIRN_HOME/repos)."""
    with _platform() as plat:
        t = pick_team(plat, team)
        if gitops.looks_like_url(target) or target.startswith("file://"):
            if not as_json:
                console.print(f"Cloning {target} …")
            p = plat.register_git_project(t.id, target, branch=branch, name=name, slug=slug)
        else:
            if branch:
                raise PlatformError("--branch only applies to git URLs")
            p = plat.register_local_project(Path(target), team_id=t.id, name=name, slug=slug)
    if as_json:
        _emit(p.public())
        return
    console.print(f"Project [bold]{p.slug}[/] in {t.slug} · {p.kind} · {p.root}")


@project_app.command("list")
def project_list(team: Optional[str] = typer.Option(None, "--team"), as_json: bool = JSON_OPT):
    """Registered projects."""
    with _platform() as plat:
        tid = plat.resolve_team(team).id if team else None
        slugs = {t.id: t.slug for t in plat.list_teams()}
        rows = [{**p.public(), "team_slug": slugs.get(p.team_id)} for p in plat.list_projects(team_id=tid)]
    if as_json:
        _emit(rows)
        return
    tb = _table("team", "slug", "kind", "status", "source", "last synced")
    for r in rows:
        src = r["git_url"] + (f" @ {r['git_branch']}" if r["git_branch"] else "") if r["git_url"] else r["root"]
        tb.add_row(r["team_slug"] or "", r["slug"], r["kind"], r["status"], src, _when(r["last_synced_at"]))
    console.print(tb)


@project_app.command("remove")
def project_remove(ref: str, team: Optional[str] = typer.Option(None, "--team"),
                   keep_files: bool = typer.Option(False, "--keep-files", help="Keep a managed clone on disk.")):
    """Unregister a project. Local folders are never deleted; managed clones are, unless --keep-files."""
    with _platform() as plat:
        tid = plat.resolve_team(team).id if team else None
        p = plat.resolve_project(ref, team_id=tid)
        plat.delete_project(p.id, purge=not keep_files)
    console.print(f"Removed project {p.slug}")


@project_app.command("refresh")
def project_refresh(ref: str, team: Optional[str] = typer.Option(None, "--team"), as_json: bool = JSON_OPT):
    """Pull the latest commits into a managed clone."""
    with _platform() as plat:
        tid = plat.resolve_team(team).id if team else None
        res = plat.refresh_project(plat.resolve_project(ref, team_id=tid).id)
    if as_json:
        _emit(res)
        return
    if not res.get("pulled"):
        console.print("Local project: nothing to pull (it is your working tree).")
    else:
        console.print(f"{res.get('branch')}: {str(res.get('old') or '')[:10]} → {str(res.get('new') or '')[:10]}"
                      + ("" if res.get("changed") else " (unchanged)"))


@project_app.command("webhook")
def project_webhook(ref: str, team: Optional[str] = typer.Option(None, "--team"), as_json: bool = JSON_OPT):
    """Create or rotate the project's push-webhook secret (printed once)."""
    with _platform() as plat:
        tid = plat.resolve_team(team).id if team else None
        p = plat.resolve_project(ref, team_id=tid)
        secret = plat.rotate_webhook_secret(p.id)
        base = server_base_url(plat)
    url = f"{base}/api/projects/{p.id}/hooks/git"
    if as_json:
        _emit({"url": url, "secret": secret})
        return
    console.print(f"Payload URL: [bold]{url}[/]  (content type application/json, push events)")
    console.print(f"Secret:      [bold]{secret}[/]  (shown once; GitLab calls it the secret token)")


# ---- user --------------------------------------------------------------------------------------------------
@user_app.command("list")
def user_list(as_json: bool = JSON_OPT):
    """Accounts on this server."""
    with _platform() as plat:
        rows = [u.public() for u in plat.list_users()]
    if as_json:
        _emit(rows)
        return
    tb = _table("email", "name", "admin", "status", "last sign-in")
    for r in rows:
        tb.add_row(r["email"], r["name"], "yes" if r["is_admin"] else "", "disabled" if r["disabled"] else "active",
                   _when(r["last_login_at"]))
    console.print(tb)


@user_app.command("disable")
def user_disable(email: str):
    """Disable an account and end its sessions (its tokens stop working)."""
    with _platform() as plat:
        plat.disable_user(plat.resolve_user(email).id)
    console.print(f"Disabled {email}")


@user_app.command("enable")
def user_enable(email: str):
    """Re-enable an account."""
    with _platform() as plat:
        plat.enable_user(plat.resolve_user(email).id)
    console.print(f"Enabled {email}")


@user_app.command("reset-password")
def user_reset_password(email: str, as_json: bool = JSON_OPT):
    """Replace a password with a one-time one (must be changed at next sign-in)."""
    with _platform() as plat:
        otp = plat.reset_password(plat.resolve_user(email).id)
    _emit({"password": otp}) if as_json else console.print(f"One-time password for {email}: [bold]{otp}[/]")
