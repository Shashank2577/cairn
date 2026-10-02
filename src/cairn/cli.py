"""The terminal experience. `cairn` alone does the right thing: set up, or show status."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__

AMBER, MOSS, ROSE, SLATE, INK = "#d9a441", "#8fb39a", "#d08c8c", "#7d8b96", "#e6ecef"
console = Console(highlight=False)
err = Console(stderr=True, highlight=False)
app = typer.Typer(add_completion=False, no_args_is_help=False, rich_markup_mode="rich",
                  help="Cairn — institutional memory for coding agents. Run [bold]cairn[/] in any repo.")
trace_app = typer.Typer(help="Explore the code map.")
agents_app = typer.Typer(help="Agent integrations.")
app.add_typer(trace_app, name="trace")
app.add_typer(agents_app, name="agents")


def _mount_platform() -> None:
    from .platform.commands import project_app, team_app, token_app, user_app
    app.add_typer(team_app, name="team", help="Teams and members (team server).")
    app.add_typer(token_app, name="token", help="API tokens for agents and CI.")
    app.add_typer(project_app, name="project", help="Projects served by this machine's server.")
    app.add_typer(user_app, name="user", help="User accounts (team server admins).")


_mount_platform()

LAYERS = [("map", "Map", "what the code is"), ("specs", "Specs", "what we intended"),
          ("timeline", "Timeline", "what happened"), ("memory", "Memory", "what we learned"),
          ("sessions", "Sessions", "what agents did")]


# ---- helpers -----------------------------------------------------------------------------------------
def _cairn():
    from .core import Cairn
    from .project import Project
    proj = Project.discover()
    if proj is None:
        err.print("[bold red]Not inside a project folder.[/]")
        raise typer.Exit(3)
    return Cairn(proj)


def _need_init(c) -> None:
    if not c.brain.get_kv("sync.last"):
        err.print(f"[{AMBER}]This repo isn't set up yet.[/] Run [bold]cairn[/] first.")
        raise typer.Exit(1)


def _emit_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, default=str) + "\n")


PAGE_ONLY = ("traversal", "target_nodes", "evidence_files")  # drawn by the page; too bulky for CLI readers


def _emit_pack(p) -> None:
    from .router import estimate_tokens
    text = json.dumps({"title": p.title, "sections": p.sections(),
                       **{k: v for k, v in p.data.items() if k not in PAGE_ONLY}}, indent=2, default=str)
    sys.stdout.write(text + "\n")
    p.mark_sent(estimate_tokens(text))


def brand(sub: str = "") -> Text:
    t = Text()
    t.append("▲ ", style=f"bold {AMBER}")
    t.append("cairn", style=f"bold {INK}")
    if sub:
        t.append(f"  {sub}", style=SLATE)
    return t


def render_pack(md: str) -> None:
    """Render a context pack with hierarchy: title, risk badge, sections, dimmed citations."""
    for line in md.splitlines():
        if line.startswith("## "):
            console.print(Text(line[3:], style=f"bold {INK}"))
        elif line.startswith("### "):
            console.print(Text("\n" + line[4:], style=f"bold {AMBER}"))
        elif line.startswith("**Risk:"):
            m = re.match(r"\*\*Risk: (\w+)\*\*(.*)", line)
            if m:
                lvl = m.group(1)
                col = {"HIGH": ROSE, "MEDIUM": AMBER, "LOW": MOSS}.get(lvl, SLATE)
                t = Text()
                t.append(f" {lvl} ", style=f"bold black on {col}")
                t.append(m.group(2), style=INK)
                console.print(t)
        elif line.startswith("- "):
            m = re.match(r"- (.*?)(?: \[([^\]]+)\])?$", line)
            t = Text("  • ", style=SLATE)
            body = m.group(1) if m else line[2:]
            for part in re.split(r"(EXTRACTED|INFERRED)", body):
                t.append(part, style=MOSS if part == "EXTRACTED" else AMBER if part == "INFERRED" else INK)
            if m and m.group(2):
                t.append(f"  {m.group(2)}", style=f"dim {SLATE}")
            console.print(t)
        elif line.startswith("(budget used"):
            console.print(Text(line, style=f"dim {SLATE}"))
        elif line.strip():
            console.print(Text(line.replace("**", ""), style=SLATE))


def stones(ov: dict) -> Table:
    """The five layers as a stacked cairn: filled stones are connected, sized by volume."""
    L = ov["layers"]
    rows = {
        "map": (L["map"]["nodes"], f"{L['map']['files']:,} files · {L['map']['nodes']:,} nodes · {L['map']['areas']} areas"),
        "specs": (L["specs"]["tasks"], f"{L['specs']['features']} features · {L['specs']['done']}/{L['specs']['tasks']} tasks"
                  if L["specs"]["connected"] else "not set up — cairn init"),
        "timeline": (L["timeline"]["commits"], f"{L['timeline']['commits']:,} commits · {L['timeline']['warnings']} warnings"
                     + (f" · {L['timeline']['facts']} facts" if L["timeline"]["deep"] else "")),
        "memory": (L["memory"]["memories"], f"{L['memory']['memories']} memories"
                   + (" · semantic" if L["memory"]["semantic"] else "")),
        "sessions": (L["sessions"]["observations"], f"{L['sessions']['observations']} observations"
                     if L["sessions"]["connected"] else "capture not connected — cairn doctor"),
    }
    t = Table.grid(padding=(0, 2))
    t.add_column(width=10)
    t.add_column(width=14)
    t.add_column()
    for key, name, _ in LAYERS:
        n, desc = rows[key]
        on = L[key]["connected"] and n > 0
        width = 0 if not on else max(2, min(12, len(str(n)) * 2 + 2))
        bar = Text("█" * width, style=AMBER if key != "sessions" else MOSS) + Text("░" * (12 - width), style="grey23")
        t.add_row(Text(name, style=INK if on else SLATE), bar, Text(desc, style=INK if on else f"dim {SLATE}"))
    return t


# ---- default -----------------------------------------------------------------------------------------
@app.callback(invoke_without_command=True)
def main_cb(ctx: typer.Context,
            version: bool = typer.Option(False, "--version", "-V", help="Show version and exit.")):
    if version:
        console.print(f"cairn {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        from .project import Project
        proj = Project.discover()
        if proj is not None and proj.initialized and (proj.dir / "brain.db").exists():
            from .core import Cairn
            if Cairn(proj).brain.get_kv("sync.last"):
                return status(as_json=False)
        return run_init()


# ---- init --------------------------------------------------------------------------------------------
@app.command()
def init(yes: bool = typer.Option(True, "--yes/--ask", help="Zero prompts (default)."),
         agents: Optional[str] = typer.Option(None, help="Comma list: claude,codex,cursor,gemini,vscode. Default: detected."),
         no_hooks: bool = typer.Option(False, "--no-hooks", help="Skip git hooks."),
         no_ui: bool = typer.Option(False, "--no-ui", help="Don't start the local UI."),
         no_capture: bool = typer.Option(False, "--no-capture", help="Don't install agent session capture."),
         no_specs: bool = typer.Option(False, "--no-specs", help="Don't add the spec workflow."),
         no_deep: bool = typer.Option(False, "--no-deep", help="Skip the deep tier (timeline facts) during the first sync.")):
    """One-command setup for a new or existing repository. Idempotent."""
    run_init(agents=agents, no_hooks=no_hooks, no_ui=no_ui, no_capture=no_capture, no_specs=no_specs, no_deep=no_deep)


def run_init(agents: str | None = None, no_hooks: bool = False, no_ui: bool = False, no_capture: bool = False,
             no_specs: bool = False, no_deep: bool = False) -> None:
    from . import agents as agents_mod
    from . import daemon, hooks, sync
    from .core import Cairn
    from .engines import journal, specs
    from .project import Project

    proj = Project.discover()
    if proj is None:
        err.print("[bold red]Run this inside a project folder.[/]")
        raise typer.Exit(3)
    if not proj.is_git:
        console.print(f"[{AMBER}]No git repository here.[/] History and hooks need one: [bold]git init[/]. "
                      "Continuing with map, specs and memory.")
    proj.ensure_dir()
    proj.reload()
    proj.remove_legacy_artifacts()
    c = Cairn(proj)
    steps: list[list] = []  # [label, state, detail]

    def view():
        t = Table.grid(padding=(0, 1))
        t.add_column(width=2)
        t.add_column(width=22)
        t.add_column()
        icons = {"run": (f"[{AMBER}]◌[/]"), "ok": f"[{MOSS}]✓[/]", "skip": f"[{SLATE}]–[/]", "fail": f"[{ROSE}]✗[/]"}
        for label, st, detail in steps:
            t.add_row(icons[st], Text(label, style=INK if st != "skip" else SLATE),
                      Text(detail, style=SLATE if st != "fail" else ROSE))
        return Group(brand(f"setting up {proj.name}"), Text(""), t)

    with Live(view(), console=console, refresh_per_second=12, transient=False) as live:
        def mark(label, st, detail=""):
            for s in steps:
                if s[0] == label:
                    s[1], s[2] = st, detail
                    break
            else:
                steps.append([label, st, detail])
            live.update(view())

        # spec workflow
        if no_specs:
            mark("Spec workflow", "skip", "skipped")
        else:
            mark("Spec workflow", "run", "adding templates and commands…")
            integ = "claude"
            det = agents_mod.detect(proj)
            for cand in ("claude", "codex", "cursor", "gemini", "vscode"):
                if cand in det:
                    integ = {"vscode": "copilot", "cursor": "cursor-agent"}.get(cand, cand)
                    break
            ok, msg = specs.bootstrap(proj, integ)
            if ok:
                mark("Spec workflow", "ok", msg)
            else:
                mark("Spec workflow", "skip", msg)
        # agents (and session capture, which rides on the agent's own hooks)
        chosen = agents.split(",") if agents else agents_mod.detect(proj)
        changed = agents_mod.install(proj, chosen, capture=not no_capture)
        names = [agents_mod.AGENTS[a] for a in chosen if a in agents_mod.AGENTS]
        outside = [f for files in changed.values() for f in files if f.startswith(("/", "~"))]
        mark("Agents", "ok", (", ".join(names) + " + AGENTS.md" if names else "AGENTS.md (no agents detected)")
             + (f" · outside the repository: {', '.join(outside)}" if outside else "")
             + (" · Codex: open codex here and approve Cairn's hooks once under /hooks"
                if agents_mod.codex_hooks_approved(proj) is False else ""))
        if no_capture:
            mark("Session capture", "skip", "skipped")
        elif journal.installed(proj):
            mark("Session capture", "ok", "prompts, files read and changed, commands from "
                 + ", ".join(agents_mod.AGENTS[a] for a, m in agents_mod.memory_status(proj).items() if m["capture"])
                 + " — stored in .cairn/")
        else:
            mark("Session capture", "skip", "no agent with hooks detected; agents show up through their Cairn calls")
        # hooks
        if no_hooks or not proj.is_git:
            mark("Git hooks", "skip", "skipped")
        else:
            added = hooks.install_git_hooks(proj)
            mark("Git hooks", "ok", "sync after commit, merge, checkout" + ("" if added else " (already set)"))
        # sync (fan-out)
        labels = {"map": "Map", "history": "Timeline", "specs": "Specs", "sessions": "Sessions",
                  "links": "Linking", "drift": "Drift check", "timeline": "Timeline facts"}

        def prog(step, st, detail):
            label = labels.get(step, step)
            mark(label, {"start": "run", "done": "ok", "skip": "skip", "fail": "fail"}[st], detail or "working…")
            if not console.is_terminal:
                # rich's Live only emits the final frame when output isn't a terminal, so a piped or
                # CI-run init would log nothing until the end (nothing at all, if it dies mid-way)
                console.print(f" {label}: {st}" + (f" — {detail}" if detail else ""))
        sync.run(c, progress=prog, deep=False if no_deep else None)
        # UI
        if no_ui:
            mark("Local UI", "skip", "skipped")
        else:
            mark("Local UI", "run", "starting…")
            d = daemon.start(proj)
            mark("Local UI", "ok", d["project_url"])

    ov = c.overview()
    console.print()
    console.print(Panel(stones(ov), border_style="grey30", padding=(1, 2), title=brand(proj.name), title_align="left"))
    nxt = Table.grid(padding=(0, 2))
    nxt.add_column(style=f"bold {AMBER}")
    nxt.add_column(style=INK)
    hub = ov["hubs"][0]["label"] if ov["hubs"] else "<file or symbol>"
    nxt.add_row("cairn impact " + hub.strip("().") , "what breaks if it changes")
    nxt.add_row("cairn ui", "see everything on one page")
    nxt.add_row("cairn remember \"…\"", "teach every agent a convention")
    if not ov["layers"]["specs"]["features"]:
        nxt.add_row("/cairn-specify", "start spec-driven work in your agent")
    console.print(nxt)
    if not c.router.available:
        console.print(Text("\nModel features (session summaries, timeline facts, memory reconciliation, narrated "
                           "answers) turn on when you sign in to Claude Code (claude), or set ANTHROPIC_API_KEY / an OpenAI-compatible endpoint.", style=f"dim {SLATE}"))


# ---- status & doctor ---------------------------------------------------------------------------------
@app.command()
def status(as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")):
    """Health of every layer, active spec, drift and the UI address."""
    from . import daemon
    c = _cairn()
    ov = c.overview()
    srv = daemon.info()
    if srv:
        srv["project_url"] = daemon.project_url(srv["url"], daemon.register(c.project))
    if as_json:
        _emit_json({**ov, "server": srv})
        return
    sub = f"synced {_ago(ov['last_sync'])}" if ov["last_sync"] else "not synced"
    console.print(Panel(stones(ov), border_style="grey30", padding=(1, 2), title=brand(f"{ov['project']}  {sub}"),
                        title_align="left"))
    bits = []
    if ov["active_spec"]:
        a = ov["active_spec"]
        # the read model stores feature ids as "spec:<id>" and dialect ids already carry their own
        # "process:" prefix — strip only a real prefix, never a fixed slice
        bits.append(f"[{INK}]Active spec[/] [{AMBER}]{a['id'].removeprefix('spec:')}[/] {a['done']}/{a['total']} tasks")
    if ov["drift"]:
        bits.append(f"[{ROSE}]{ov['drift']} drift findings[/] → cairn drift")
    bits.append(f"UI [{AMBER}]{srv['project_url']}[/]" if srv else "UI [dim]stopped[/] → cairn ui")
    console.print("   ".join(bits))


def _ago(ts: float) -> str:
    from .core import ago
    return ago(ts)


@app.command()
def doctor():
    """Every capability, its state, and the exact command to enable it."""
    import shutil

    from rich.markup import escape

    from . import daemon
    from .engines import journal, specs
    c = _cairn()
    t = Table(box=None, padding=(0, 2), show_header=True, header_style=f"bold {SLATE}")
    t.add_column("")
    t.add_column("Capability", style=INK)
    t.add_column("State")
    t.add_column("Fix", style=AMBER)
    ok, no = f"[{MOSS}]●[/]", f"[{ROSE}]○[/]"

    def row(good, name, state, fix=""):  # state and fix are literal text: `[deep]` is not a style tag
        t.add_row(ok if good else no, escape(name), escape(state), "" if good else escape(fix))
    idx = c.map
    row(len(idx) > 0, "Map", f"{len(idx):,} nodes" if len(idx) else "empty", "cairn sync")
    row(c.project.is_git, "Git history", "ok" if c.project.is_git else "not a git repo", "git init")
    row(specs.initialized(c.project.root), "Spec workflow", "ok" if specs.initialized(c.project.root) else "missing",
        "cairn init")
    legacy = specs.legacy_layout(c.project.root)
    row(not legacy, "Workflow layout", "current" if not legacy else "older layout", "cairn init (migrates it)")
    if c.project.cfg("sessions.capture", True):
        row(journal.installed(c.project), "Session capture", "on (.cairn/sessions.db)" if journal.installed(c.project)
            else "off", "cairn init --agents claude")
    else:  # the config turned capture off; doctor must not report it as on
        row(False, "Session capture", "off (config)", "set [sessions] capture = true in .cairn/config.toml")
    row(c.router.available, "Model", c.router.provider if c.router.available else "none (model features off)",
        "sign in to Claude Code (claude), or set ANTHROPIC_API_KEY / an OpenAI-compatible endpoint")
    from .engines import vectors
    embedder = vectors.embedder_id()
    row(embedder != "hash-v1", "Local embeddings", embedder, "check network once to download the embedding model")
    try:
        import kuzu
        store_ok = True
    except ImportError:
        store_ok = False
    graph_url = c.project.cfg("temporal.url", "") or c.project.cfg("deep.graph_url", "")
    row(store_ok or bool(graph_url), "Timeline store", graph_url or "embedded (.cairn/temporal)",
        "reinstall Cairn (the embedded graph database is missing)")
    srv = daemon.info()
    row(bool(srv), "Local UI", srv["url"] if srv else "stopped", "cairn ui")
    row(bool(shutil.which("cairn")), "cairn on PATH", "yes" if shutil.which("cairn") else "no",
        "uv tool install --python 3.12 cairn-brain")
    from . import agents as agents_mod
    wired = agents_mod.installed(c.project)
    detected = agents_mod.detect(c.project)
    unwired = [a for a in detected if a not in wired]
    state = ", ".join(agents_mod.AGENTS[a] for a in wired) or "none wired"
    if unwired:
        state += (f" · also detected: {', '.join(agents_mod.AGENTS[a] for a in unwired)}"
                  f" (cairn agents install --agents {','.join(unwired)})")
    row(bool(wired), "Agents", state, "cairn agents install --agents claude")
    memory = agents_mod.memory_status(c.project)  # memory at session start, per agent
    for a in (a for a in agents_mod.AGENTS if a in wired or a in detected):
        m = memory[a]
        pending = m.get("approved") is False  # Codex runs a hook only once it is approved in /hooks
        row(bool(m["injection"]) and not pending, f"{agents_mod.AGENTS[a]} memory",
            f"{m['injection'] or 'off'} · capture {'on' if m['capture'] else 'off'}"
            + (" · hooks not approved yet" if pending else ""),
            "open codex here and approve Cairn's hooks once under /hooks" if pending
            else f"cairn agents install --agents {a}")
    console.print(brand("doctor"))
    console.print(t)


# ---- sync --------------------------------------------------------------------------------------------
@app.command("sync")
def sync_cmd(deep: Optional[bool] = typer.Option(None, "--deep/--no-deep", help="Force the deep tier on/off."),
             budget: Optional[int] = typer.Option(None, help="Token budget for the deep tier."),
             no_map_rebuild: bool = typer.Option(False, "--no-map-rebuild", help="Reuse the current map."),
             wait: int = typer.Option(5, "--wait", help="Seconds to wait for an already-running sync (e.g. the post-commit hook's) before giving up."),
             quiet: bool = typer.Option(False, "--quiet", "-q"),
             as_json: bool = typer.Option(False, "--json")):
    """Refresh every layer in parallel (incremental), link, check drift."""
    from . import sync
    c = _cairn()

    def prog(step, st, detail):
        if quiet or as_json:
            return
        if st == "start":  # the deep tier and memory can run for minutes; they must be visible
            console.print(f" [{SLATE}]◌[/] [{INK}]{step:<10}[/] [{SLATE}]{detail or 'working…'}[/]")
            return
        icon = {"done": f"[{MOSS}]✓[/]", "skip": f"[{SLATE}]–[/]", "fail": f"[{ROSE}]✗[/]"}[st]
        console.print(f" {icon} [{INK}]{step:<10}[/] [{SLATE}]{detail}[/]")
    if not quiet and not as_json:
        console.print(brand("sync"))
    res = sync.run(c, deep=deep, budget=budget, rebuild_map=not no_map_rebuild, progress=prog, wait=float(wait))
    if as_json:
        _emit_json(res)
    elif not quiet:
        console.print(Text(f"   done in {res.get('seconds', 0)}s", style=f"dim {SLATE}"))


# ---- questions ---------------------------------------------------------------------------------------
@app.command()
def impact(target: str = typer.Argument(..., help="File path, symbol, Class.method, or an id like task:001/T004"),
           depth: int = typer.Option(2, help="Dependency depth."),
           budget: int = typer.Option(1800, help="Token budget."),
           explain: bool = typer.Option(False, "--explain", help="Add a deep-tier summary (needs a key)."),
           as_json: bool = typer.Option(False, "--json")):
    """What breaks if this changes."""
    from rich.markup import escape
    c = _cairn()
    _need_init(c)
    try:
        p = c.impact(target, depth, budget)
    except ValueError as exc:  # degenerate targets (".". "") — resolve refuses them
        err.print(f"[{ROSE}]{escape(str(exc))}[/]")
        raise typer.Exit(2)
    if as_json:
        _emit_pack(p)
        return
    render_pack(p.render())
    if explain:
        _narrate(c, p, "impact")


@app.command()
def why(target: str = typer.Argument(...), budget: int = typer.Option(1800),
        explain: bool = typer.Option(False, "--explain"), as_json: bool = typer.Option(False, "--json")):
    """Why this code is the way it is."""
    from rich.markup import escape
    c = _cairn()
    _need_init(c)
    try:
        p = c.why(target, budget)
    except ValueError as exc:  # degenerate targets — resolve refuses them
        err.print(f"[{ROSE}]{escape(str(exc))}[/]")
        raise typer.Exit(2)
    if as_json:
        _emit_pack(p)
        return
    render_pack(p.render())
    if explain:
        _narrate(c, p, "why")


def _narrate(c, pack, task):
    if not c.router.available:
        console.print(Text("\n--explain needs a model: sign in to Claude Code (claude), or set ANTHROPIC_API_KEY / an OpenAI-compatible endpoint.", style=f"dim {SLATE}"))
        return
    with console.status(f"[{AMBER}]thinking with {c.router.model(c.router.tier_for(task))}…"):
        text = c.narrate(pack, task)
    if text:
        console.print(Panel(text, border_style=AMBER, title="summary", title_align="left"))


@app.command()
def ask(question: str = typer.Argument(...), budget: int = typer.Option(1800),
        no_llm: bool = typer.Option(False, "--no-llm", help="Only the evidence pack."),
        as_json: bool = typer.Option(False, "--json")):
    """Ask the project memory anything. Evidence first; a narrated answer when a key is set."""
    c = _cairn()
    _need_init(c)
    if as_json:
        _emit_json(c.ask(question, budget, llm=not no_llm))
        return
    with console.status(f"[{AMBER}]gathering…"):
        res = c.ask(question, budget, llm=not no_llm)
    if res.get("answer"):
        console.print(Panel(res["answer"], border_style=AMBER, title=f"answer · {res['model']}", title_align="left"))
        console.print(Text("evidence", style=f"bold {SLATE}"))
    render_pack(res["pack"])


@app.command()
def search(query: str, kinds: Optional[str] = typer.Option(None, help="symbol,file,spec,task,req,commit,memory,obs,fact"),
           limit: int = 20, as_json: bool = typer.Option(False, "--json")):
    """Search every layer."""
    c = _cairn()
    hits = c.search(query, kinds.split(",") if kinds else None, limit)
    if as_json:
        _emit_json(hits)
        return
    for h in hits:
        console.print(f" [{SLATE}]{h['kind']:<8}[/] [{INK}]{h['title']}[/]  [dim]{h.get('path') or h['id']}[/]")


@app.command()
def brief():
    """The compact project briefing agents receive at session start."""
    console.print(_cairn().brief())


# ---- map ---------------------------------------------------------------------------------------------
@trace_app.command("path")
def trace_path(a: str, b: str):
    """Shortest path between two things in the map."""
    from .engines import mapper
    console.print(mapper.engine_text(_cairn().project.root, "path", a, b))


@trace_app.command("explain")
def trace_explain(x: str):
    """A node and its neighbourhood."""
    from .engines import mapper
    console.print(mapper.engine_text(_cairn().project.root, "explain", x))


@trace_app.command("query")
def trace_query(question: str, budget: int = 1500):
    """Scoped subgraph for a plain-language question."""
    from .engines import mapper
    console.print(mapper.engine_text(_cairn().project.root, "query", question, "--budget", str(budget)))


@app.command()
def hubs(top: int = 12):
    """The most-connected code: what everything flows through."""
    c = _cairn()
    for i, h in enumerate(c.map.hubs(top), 1):
        console.print(f" [{AMBER}]{i:>2}[/] [{INK}]{h['label']:<40}[/] [{SLATE}]{h['degree']:>4} links  {h['file']}[/]")


@app.command()
def areas(top: int = 20):
    """Subsystems detected in the map."""
    for a in _cairn().map.areas()[:top]:
        console.print(f" [{INK}]{a['name']:<40}[/] [{SLATE}]{a['size']:>4} nodes  {', '.join(a['top'][:3])}[/]")


@app.command()
def prs(args: list[str] = typer.Argument(None)):
    """Open pull requests with their map impact (needs the GitHub CLI)."""
    from .engines.graph import api as graph_api
    raise typer.Exit(graph_api.prs(list(args or []), root=_cairn().project.root))


@app.command("graph", add_help_option=False,
             context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def graph_cmd(ctx: typer.Context):
    """Code & document knowledge graph: build, extract, query, path, explain, views, wiki, exports (see `cairn graph --help`)."""
    from .engines.graph.cli import main
    raise typer.Exit(main(list(ctx.args)))


# ---- specs -------------------------------------------------------------------------------------------
@app.command()
def specs(spec_id: Optional[str] = typer.Argument(None), as_json: bool = typer.Option(False, "--json")):
    """The spec board: features, stories, task progress and file trace."""
    from .engines import specs as se
    c = _cairn()
    feats = [f for f in se.features(c.project.root) if not spec_id or f["id"].startswith(spec_id)]
    if as_json:
        _emit_json(feats)
        return
    if not feats:
        console.print(f"[{SLATE}]No specs yet.[/] In your agent run [bold]/cairn-constitution[/], then "
                      "[bold]/cairn-specify[/] <what to build>.")
        return
    for f in feats:
        p = f["progress"]
        pct = int(100 * p["done"] / p["total"]) if p["total"] else 0
        bar = Text("█" * (pct // 8), style=AMBER) + Text("░" * (12 - pct // 8), style="grey23")
        console.print(Text.assemble((f["id"], f"bold {AMBER}"), "  ", (f["title"], f"bold {INK}"), "  ", bar,
                                    (f"  {p['done']}/{p['total']}", SLATE)))
        if spec_id:
            for s in f["stories"]:
                ts = [t for t in f["tasks"] if t["story"] == s["id"]]
                d = sum(t["done"] for t in ts)
                console.print(f"   [{SLATE}]{s['priority']}[/] [{INK}]{s['id']} {s['title']}[/] [{SLATE}]{d}/{len(ts)}[/]")
            for t in f["tasks"]:
                mark = f"[{MOSS}]✓[/]" if t["done"] else f"[{SLATE}]○[/]"
                files = ", ".join(p for p, _ in t["files"][:2])
                console.print(f"     {mark} [{INK}]{t['id']}[/] {t['text'][:70]} [dim]{files}[/]")


@app.command("spec", add_help_option=False,
             context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def spec(ctx: typer.Context):
    """Spec-driven workflow: init, new feature, extensions, presets, workflows, bundles, integrations."""
    args = list(ctx.args)
    if args[:1] == ["new"]:
        from .engines import specs as se
        res = se.new_feature(_cairn().project, " ".join(args[1:]))
        console.print(f"[{MOSS}]✓[/] {res.get('BRANCH_NAME', '')} → [{AMBER}]{res.get('SPEC_FILE', '')}[/]")
        console.print(f"[{SLATE}]Next: in your agent, run /cairn-specify {' '.join(args[1:])}[/]")
        return
    from .engines.workflow.cli import main
    raise typer.Exit(main(args))


@app.command()
def drift(spec_id: Optional[str] = typer.Argument(None), deep: bool = typer.Option(False, "--deep"),
          as_json: bool = typer.Option(False, "--json")):
    """Where the code disagrees with its specs."""
    from . import drift as d
    c = _cairn()
    found = d.check(c, spec_id)
    if deep:
        with console.status(f"[{AMBER}]judging requirements with {c.router.model('deep')}…"):
            found += d.semantic(c, spec_id)
    d.record(c, found)
    if as_json:
        _emit_json(found)
        return
    if not found:
        console.print(f"[{MOSS}]✓[/] No drift. Code and specs agree.")
        return
    for f in found:
        col = {"high": ROSE, "medium": AMBER, "low": SLATE}[f["severity"]]
        console.print(f" [{col}]{f['severity']:<6}[/] [{INK}]{f['title']}[/]  [dim]{f['cite']}[/]")
        for e in f["evidence"][:2]:
            console.print(f"        [{SLATE}]{e}[/]")


# ---- memory ------------------------------------------------------------------------------------------
@app.command()
def remember(text: str, kind: str = typer.Option("fact", help="convention|decision|gotcha|preference|fact"),
             supersedes: Optional[str] = typer.Option(None, help="Memory id this replaces.")):
    """Teach every agent something durable."""
    c = _cairn()
    try:
        res = c.remember(text, kind, supersedes)
    except ValueError as exc:
        err.print(f"[{ROSE}]{exc}[/]")
        raise typer.Exit(2)
    console.print(f"[{MOSS}]✓[/] {res['status']} [{AMBER}]memory:{res['id']}[/]")


@app.command()
def recall(query: str, limit: int = 10, as_json: bool = typer.Option(False, "--json")):
    """Find what the team knows about a topic."""
    c = _cairn()
    mems = c.memory.recall(query, limit)
    if as_json:
        _emit_json(mems)
        return
    for m in mems:
        console.print(f" [{AMBER}]{m['kind']:<10}[/] [{INK}]{m['text']}[/]  [dim]memory:{m['id']}[/]")
    if not mems:
        console.print(f"[{SLATE}]Nothing yet. Add one: cairn remember \"…\" --kind convention[/]")


@app.command()
def memories(all: bool = typer.Option(False, "--all", help="Include superseded and forgotten.")):
    """List memories."""
    for m in _cairn().brain.memories(include_inactive=all):
        state = " (superseded)" if m["superseded_by"] else " (forgotten)" if m["forgotten"] else ""
        console.print(f" [{AMBER}]{m['kind']:<10}[/] [{INK}]{m['text']}[/][{SLATE}]{state}[/]  [dim]memory:{m['id']}[/]")


@app.command()
def forget(memory_id: str):
    """Forget a memory (soft delete; also removed from semantic recall)."""
    ok = _cairn().memory.forget(memory_id.removeprefix("memory:"))
    console.print(f"[{MOSS}]✓[/] forgotten" if ok else f"[{ROSE}]no such memory[/]")


# ---- memory engine (power tools behind remember/recall) ------------------------------------------------------
memory_app = typer.Typer(help="The memory engine: add from conversations, search with filters, history, import, seed.")
app.add_typer(memory_app, name="memory")
SCOPE = typer.Option("project", "--scope", help="project | team | user | session")


def _engine_call(fn, *args, scope: str = "project", scope_id: Optional[str] = None, **kwargs):
    from .engines.memstore.commands import CommandError
    c = _cairn()
    eng = c.memory.semantic.engine
    if eng is None:
        err.print(f"[{ROSE}]The memory engine isn't available[/] ({c.memory.semantic.error or 'turned off'}).")
        raise typer.Exit(1)
    try:
        return fn(eng, *args, **kwargs, **c.memory.semantic.scope_ids(scope, scope_id)) if scope \
            else fn(eng, *args, **kwargs)
    except CommandError as exc:
        err.print(f"[{ROSE}]{exc}[/]")
        raise typer.Exit(1) from exc


def _show_memories(res, as_json: bool) -> None:
    if as_json:
        _emit_json(res)
        return
    rows = res.get("results", res) if isinstance(res, dict) else res
    for m in rows if isinstance(rows, list) else [rows]:
        score = f" [{SLATE}]{m['score']:.2f}[/]" if isinstance(m, dict) and m.get("score") is not None else ""
        text = m.get("memory") or m.get("new_memory") or m.get("event") if isinstance(m, dict) else str(m)
        console.print(f" [{INK}]{text}[/]{score} [dim]{m.get('id', '') if isinstance(m, dict) else ''}[/]")


@memory_app.command("add")
def memory_add(text: Optional[str] = typer.Argument(None, help="Text; or use --messages / --file / stdin."),
               messages: Optional[str] = typer.Option(None, help="JSON list of {role, content} messages."),
               file: Optional[str] = typer.Option(None, help="JSON file with messages."),
               metadata: Optional[str] = typer.Option(None, help="JSON metadata."),
               infer: bool = typer.Option(True, "--infer/--no-infer", help="Extract and reconcile facts with a model."),
               expires: Optional[str] = typer.Option(None, help="YYYY-MM-DD"),
               instructions: Optional[str] = typer.Option(None, help="Extra guidance for extraction."),
               scope: str = SCOPE, scope_id: Optional[str] = None,
               as_json: bool = typer.Option(False, "--json")):
    """Add memories from text or a conversation (reconciled against what is already known)."""
    from .engines.memstore import commands as mc
    _show_memories(_engine_call(mc.add, text, messages=messages, file=file, metadata=metadata, infer=infer,
                                expires=expires, instructions=instructions, scope=scope, scope_id=scope_id), as_json)


@memory_app.command("search")
def memory_search(query: str, top_k: int = typer.Option(10, "--top-k"), threshold: float = 0.1,
                  rerank: bool = False, keyword: bool = False, filter: Optional[str] = typer.Option(None, "--filter"),
                  show_expired: bool = False, explain: bool = False, scope: str = SCOPE,
                  scope_id: Optional[str] = None, as_json: bool = typer.Option(False, "--json")):
    """Search memories (semantic + keyword + entity signals, with filters)."""
    from .engines.memstore import commands as mc
    _show_memories(_engine_call(mc.search, query, top_k=top_k, threshold=threshold, rerank=rerank, keyword=keyword,
                                filters=filter, show_expired=show_expired, explain=explain, scope=scope,
                                scope_id=scope_id), as_json)


@memory_app.command("list")
def memory_list(page: int = 1, page_size: int = 100, show_expired: bool = False, scope: str = SCOPE,
                scope_id: Optional[str] = None, as_json: bool = typer.Option(False, "--json")):
    """Every memory in a scope."""
    from .engines.memstore import commands as mc
    _show_memories(_engine_call(mc.list_memories, page=page, page_size=page_size, show_expired=show_expired,
                                scope=scope, scope_id=scope_id), as_json)


@memory_app.command("get")
def memory_get(memory_id: str):
    """One memory by engine id."""
    from .engines.memstore import commands as mc
    _emit_json(_engine_call(mc.get, memory_id, scope=""))


@memory_app.command("update")
def memory_update(memory_id: str, text: Optional[str] = typer.Argument(None),
                  metadata: Optional[str] = None, expires: Optional[str] = None, clear_expiry: bool = False):
    """Change a memory's text, metadata or expiry (kept in its history)."""
    from .engines.memstore import commands as mc
    _emit_json(_engine_call(mc.update, memory_id, text, metadata=metadata, expires=expires,
                            clear_expiry=clear_expiry, scope=""))


@memory_app.command("delete")
def memory_delete(memory_id: Optional[str] = typer.Argument(None), all_: bool = typer.Option(False, "--all"),
                  dry_run: bool = False, scope: str = SCOPE, scope_id: Optional[str] = None):
    """Delete one memory, or every memory in a scope with --all."""
    from .engines.memstore import commands as mc
    _emit_json(_engine_call(mc.delete, memory_id, all_=all_, dry_run=dry_run, scope=scope if all_ else "",
                            scope_id=scope_id))


@memory_app.command("history")
def memory_history(memory_id: str):
    """How a memory changed: every ADD, UPDATE and DELETE (read-model id or engine id)."""
    c = _cairn()
    mid = memory_id.removeprefix("memory:")
    if c.brain.memory(mid):
        _emit_json(c.memory.history(mid))
        return
    from .engines.memstore import commands as mc
    _emit_json(_engine_call(mc.history, mid, scope=""))


@memory_app.command("import")
def memory_import(path: str, infer: bool = False, scope: str = SCOPE, scope_id: Optional[str] = None):
    """Import memories from a JSON export."""
    from .engines.memstore import commands as mc
    _emit_json(_engine_call(mc.import_file, path, infer=infer, scope=scope, scope_id=scope_id))


@memory_app.command("entities")
def memory_entities():
    """Who and what memories are scoped to, with counts."""
    from .engines.memstore import commands as mc
    _emit_json(_engine_call(mc.entities, scope=""))


@memory_app.command("check")
def memory_check(fix: bool = typer.Option(False, "--fix", help="Re-create wrong or missing engine records.")):
    """Check that every memory's engine record still says what the memory says (repair with --fix)."""
    _emit_json(_cairn().memory.check_engine(fix=fix))


@memory_app.command("seed")
def memory_seed():
    """Learn decisions, clarifications, conventions and gotchas from this repository now."""
    from .engines.memory import seed_from_repo
    c = _cairn()
    _emit_json(seed_from_repo(c.project, c.brain, c.router, store=c.memory))


# ---- timeline & sessions -----------------------------------------------------------------------------
from .engines.temporal.cli import timeline_app


@timeline_app.callback(invoke_without_command=True)
def timeline(ctx: typer.Context, target: Optional[str] = typer.Option(None, help="file:<path>, spec:<id>, … — a bare path or symbol works too"),
             days: int = typer.Option(30, help="How far back."), limit: int = 40):
    """What happened: commits, spec progress, sessions, decisions, facts, drift. Subcommands work the fact graph."""
    if ctx.invoked_subcommand:
        return
    from .core import day
    c = _cairn()
    evs = c.brain.events(since=time.time() - days * 86400, ref=_timeline_ref(c, target), limit=limit)
    col = {"commit": INK, "spec": AMBER, "session": MOSS, "memory": AMBER, "fact": MOSS, "fact_end": SLATE,
           "drift": ROSE, "workflow_run": AMBER}
    for e in evs:
        risk = e["meta"].get("risk") if e["kind"] == "commit" else None
        flag = f" [{ROSE}]{','.join(risk)}[/]" if risk else ""
        console.print(f" [{SLATE}]{day(e['ts'])}[/] [{col.get(e['kind'], INK)}]{e['kind']:<8}[/] {e['title'][:90]}{flag}")


app.add_typer(timeline_app, name="timeline")


def _timeline_ref(c, target: str | None) -> str | None:
    """The event ref for a --target, accepting the same bare paths and symbols impact accepts —
    events are keyed by ids like ``file:todo/cli.py``, which users never type from memory."""
    if not target or re.match(r"^[a-z]+:", target):
        return target
    try:
        t = c.resolve(target)
    except ValueError:
        return target  # let the empty result speak, like the other target-taking commands
    if t.files:
        return f"file:{t.files[0]}"
    if t.nodes:
        return f"symbol:{t.nodes[0]}"
    return target


@app.command()
def standup(days: int = typer.Option(1, help="How far back: 1 = the last 24h, 2 adds yesterday."),
            as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")):
    """What happened, from the event log: commits and their requirements, memories, facts, sessions."""
    from . import standup as su
    out = su.digest(_cairn().brain, days)
    if as_json:
        _emit_json(out)
        return
    su.render(out)


@app.command("sessions", add_help_option=False,
             context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def sessions(ctx: typer.Context):
    """What agents did here: recent sessions (no arguments), search, timeline, context, worker, transcripts, settings."""
    from .engines.recall.cli import main
    raise typer.Exit(main(list(ctx.args) or ["sessions"]))


# ---- servers & agents --------------------------------------------------------------------------------
@app.command()
def ui(no_open: bool = typer.Option(False, "--no-open")):
    """Open the UI for this repository (starts the local server if needed; one server serves every repo)."""
    from . import daemon
    c = _cairn()
    d = daemon.start(c.project)
    console.print(f"[{MOSS}]✓[/] {d['project_url']}")
    if not no_open:
        webbrowser.open(d["project_url"])


@app.command()
def up():
    """Start the local server in the background and add this repository to it."""
    from . import daemon
    d = daemon.start(_cairn().project)
    console.print(f"[{MOSS}]✓[/] running at [{AMBER}]{d['project_url']}[/]")


@app.command()
def down():
    """Stop the local server (it serves every registered repository)."""
    from . import daemon
    console.print(f"[{MOSS}]✓[/] stopped" if daemon.stop() else f"[{SLATE}]not running[/]")


@app.command()
def serve(host: Optional[str] = typer.Option(None, help="Bind address (team mode for anything but loopback)."),
          port: Optional[int] = typer.Option(None, help="Port (default 4747)."),
          team: bool = typer.Option(False, "--team", help="Team mode: sign-in and roles required.")):
    """Run the server in the foreground: every registered project, the API, the MCP endpoint and the UI."""
    from .server import serve as run
    if team:
        os.environ["CAIRN_SERVER_MODE"] = "team"
    run(host=host, port=port)


@app.command()
def mcp():
    """Run the MCP server on stdio (agents launch this)."""
    from .mcp_server import main
    main()


@agents_app.command("install")
def agents_install(agents: Optional[str] = typer.Option(None, help="claude,codex,cursor,gemini,vscode")):
    """Wire agents to Cairn (idempotent)."""
    from . import agents as a
    c = _cairn()
    changed = a.install(c.project, agents.split(",") if agents else None)
    for agent, files in changed.items():
        console.print(f" [{MOSS}]✓[/] [{INK}]{a.AGENTS.get(agent, 'All agents')}[/] [{SLATE}]{', '.join(files)}[/]")
    if not changed:
        console.print(f"[{SLATE}]Everything already wired.[/]")


@agents_app.command("connect")
def agents_connect(server: str = typer.Option(..., help="Team server URL, e.g. https://cairn.example.com"),
                   project: str = typer.Option(..., help="Project id on that server (see `cairn project list`)."),
                   env_var: str = typer.Option("CAIRN_TOKEN", help="Environment variable holding the API token.")):
    """Use a team server's shared memory from this repository's agents (token read from an env var)."""
    from . import agents as a
    changed = a.connect(_cairn().project, server, project, env_var)
    for f in changed:
        console.print(f" [{MOSS}]✓[/] {f}")
    console.print(f"[{SLATE}]Each developer: create a token on the server (Team → Tokens, or `cairn token issue`) "
                  f"and export {env_var}=<token>.[/]")


@agents_app.command("list")
def agents_list():
    """Detected agents."""
    from . import agents as a
    det = a.detect(_cairn().project)
    for k, v in a.AGENTS.items():
        console.print(f" {'[' + MOSS + ']●[/]' if k in det else '[' + SLATE + ']○[/]'} {v}")


@app.command()
def models(ledger: bool = typer.Option(False, "--ledger", help="Show calls and tokens per tier.")):
    """Which model does which job, and what it cost."""
    c = _cairn()
    t = Table(box=None, padding=(0, 2), header_style=f"bold {SLATE}")
    t.add_column("Tier", style=f"bold {AMBER}")
    t.add_column("Model", style=INK)
    t.add_column("Jobs", style=SLATE)
    for r in c.router.table():
        t.add_row(r["tier"], r["model"], ", ".join(r["tasks"]))
    console.print(brand("models" + ("" if c.router.available else "  (no key: deterministic tier only)")))
    console.print(t)
    if ledger:
        rows = c.brain.ledger()
        lt = Table(box=None, padding=(0, 2), header_style=f"bold {SLATE}")
        for col in ("tier", "model", "calls", "input", "output", "cache_read"):
            lt.add_column(col)
        for r in rows:
            lt.add_row(*(f"{r[k]:,}" if isinstance(r[k], int) else str(r[k] or 0) for k in
                         ("tier", "model", "calls", "input", "output", "cache_read")))
        console.print(lt if rows else Text("No model calls yet.", style=SLATE))


# ---- hook entry points -------------------------------------------------------------------------------
@app.command(hidden=True)
def statusline():
    from . import hooks
    from .core import Cairn
    from .project import Project
    data = sys.stdin.read() if not sys.stdin.isatty() else ""
    cwd = _statusline_cwd(data)
    proj = Project.discover(Path(cwd) if cwd else None)
    if proj is None or not proj.db_path.exists():
        print("▲ cairn · not set up")
        return
    print(hooks.statusline(Cairn(proj), data))


def _statusline_cwd(data: str) -> str | None:
    """Claude Code pipes JSON here; any non-object payload (null, a list, a bare string) means no cwd."""
    try:
        info = json.loads(data) if data.strip() else None
    except json.JSONDecodeError:
        return None
    if not isinstance(info, dict):
        return None
    workspace = info.get("workspace")
    if not isinstance(workspace, dict):
        return None
    cwd = workspace.get("current_dir")
    return cwd if isinstance(cwd, str) else None


@app.command(hidden=True)
def hook(event: str):
    from . import hooks, sync
    from .core import Cairn
    from .project import Project
    proj = Project.discover()
    if proj is None or not proj.db_path.exists():
        return
    if event == "git":
        sync.spawn_background(proj)
    elif event == "session-start":
        print(hooks.session_start(Cairn(proj)))


@app.command()
def uninstall(purge: bool = typer.Option(False, "--purge", help="Also delete .cairn/ state.")):
    """Remove Cairn's agent wiring and hooks (and state with --purge)."""
    import shutil

    from . import agents as a
    from . import hooks
    from .platform import Platform, ServerConfig
    c = _cairn()
    platform = Platform(config=ServerConfig.load())
    rec = platform.project_by_root(c.project.root)
    if rec:
        platform.delete_project(rec.id, purge=False)  # unlists it; never deletes the repository
    platform.close()
    removed = a.uninstall(c.project) + [f".git/hooks/{h}" for h in hooks.remove_git_hooks(c.project)]
    for r in removed:
        console.print(f" [{MOSS}]✓[/] removed {r}")
    if purge:
        c.brain.close()
        shutil.rmtree(c.project.dir, ignore_errors=True)
        console.print(f" [{MOSS}]✓[/] deleted .cairn/")
    console.print(f"[{SLATE}]Spec files and the map folder are left in place; delete them manually if unwanted.[/]")


def main() -> None:
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    app()


if __name__ == "__main__":
    main()
