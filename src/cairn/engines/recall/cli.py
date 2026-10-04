"""``cairn sessions ...`` command handlers (mounted by `cli.py` as ``cairn sessions``; no arguments lists recent sessions).

Every subcommand prints text (or JSON with ``--json``) and returns an exit code.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .projects import find_store_root


def _root(value: str | None) -> Path:
    root = Path(value).resolve() if value else find_store_root(os.getcwd())
    if root is None or not (root / ".cairn").is_dir():
        raise SystemExit("no Cairn repository here (run `cairn init` first)")
    return root


def _print(obj: Any, as_json: bool = False) -> None:
    if as_json or not isinstance(obj, str):
        if isinstance(obj, dict) and not as_json and "content" in obj:
            print(obj["content"][0]["text"])
            return
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cairn sessions", description="Agent session memory.")
    p.add_argument("--root", default=None, help="repository root (default: the one containing the cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("worker", help="turn queued events into observations and summaries")
    w.add_argument("--loop", action="store_true")
    w.add_argument("--no-model", action="store_true")
    w.add_argument("--poll", type=float, default=1.0)
    sub.add_parser("status", help="queue, worker and observer health")

    s = sub.add_parser("search", help="search observations, summaries and prompts")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--type", default=None)
    s.add_argument("--obs-type", default=None)
    s.add_argument("--project", default=None)
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--since", default=None)
    s.add_argument("--until", default=None)
    s.add_argument("--order", default=None, choices=["relevance", "date_desc", "date_asc"])
    s.add_argument("--json", action="store_true")

    t = sub.add_parser("timeline", help="what happened around an observation, session or time")
    t.add_argument("--anchor", default=None)
    t.add_argument("--query", default=None)
    t.add_argument("--before", type=int, default=5)
    t.add_argument("--after", type=int, default=5)

    g = sub.add_parser("get", help="full observations by id")
    g.add_argument("ids", nargs="+")
    tu = sub.add_parser("tool-uses", help="raw tool input/output by id")
    tu.add_argument("ids", nargs="+")

    c = sub.add_parser("context", help="print the session-start context")
    c.add_argument("--full", action="store_true")
    c.add_argument("--colors", action="store_true")
    c.add_argument("--project", default=None)

    ss = sub.add_parser("sessions", help="recent sessions")
    ss.add_argument("--limit", type=int, default=20)
    ss.add_argument("--json", action="store_true")
    sd = sub.add_parser("session", help="one session in full")
    sd.add_argument("id", type=int)

    st = sub.add_parser("settings", help="show or change [recall] settings")
    st.add_argument("assignments", nargs="*", help="key=value ...")
    sub.add_parser("modes", help="available observation modes")

    ex = sub.add_parser("export", help="export memories as JSON")
    ex.add_argument("query", nargs="?", default="")
    ex.add_argument("--out", required=True)
    ex.add_argument("--project", default=None)
    im = sub.add_parser("import", help="import memories from an export file")
    im.add_argument("file")

    q = sub.add_parser("queue", help="inspect or clean the work queue")
    q.add_argument("--clear-failed", action="store_true")
    q.add_argument("--clear-all", action="store_true")
    q.add_argument("--retry-failed", action="store_true")

    lg = sub.add_parser("logs", help="worker log")
    lg.add_argument("--lines", type=int, default=100)
    lg.add_argument("--clear", action="store_true")

    rm = sub.add_parser("remember", help="save a manual memory")
    rm.add_argument("text")
    rm.add_argument("--title", default=None)
    sub.add_parser("reindex", help="rebuild the semantic index")

    tr = sub.add_parser("transcripts", help="transcript watch / replay")
    tr.add_argument("args", nargs=argparse.REMAINDER)
    cm = sub.add_parser("claude-md", help="folder context files")
    cm.add_argument("action", choices=["generate", "clean"])
    cm.add_argument("--dry-run", action="store_true")
    ad = sub.add_parser("adopt", help="adopt memories of merged git worktrees")
    ad.add_argument("args", nargs=argparse.REMAINDER)
    co = sub.add_parser("corpus", help="knowledge corpora")
    co.add_argument("action", choices=["build", "list", "show", "delete", "rebuild", "prime", "query", "reprime"])
    co.add_argument("name", nargs="?")
    co.add_argument("rest", nargs="*")
    co.add_argument("--description", default="")
    co.add_argument("--project", default=None)
    co.add_argument("--types", default=None)
    co.add_argument("--concepts", default=None)
    co.add_argument("--files", default=None)
    co.add_argument("--query", default=None)
    co.add_argument("--date-start", default=None)
    co.add_argument("--date-end", default=None)
    co.add_argument("--limit", type=int, default=None)
    ig = sub.add_parser("integrate", help="install hooks for another agent")
    ig.add_argument("args", nargs=argparse.REMAINDER)
    sk = sub.add_parser("skills", help="install the recall skills")
    sk.add_argument("action", choices=["list", "install", "uninstall"])
    sk.add_argument("dest", nargs="?", default=None)
    pu = sub.add_parser("push", help="send captured sessions to the team server ([team] server/project)")
    pu.add_argument("--status", action="store_true", help="show what is waiting and the last push")
    pu.add_argument("--batch", type=int, default=None, help="events per request")
    pu.add_argument("--json", action="store_true")
    hk = sub.add_parser("hook", help="run a hook event (JSON on stdin)")
    hk.add_argument("platform")
    hk.add_argument("event")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.cmd
    if cmd == "hook":
        from .hooks import main as hook_main
        return hook_main(["--platform", args.platform, args.event])
    if cmd == "transcripts":
        from .transcripts import run_transcript_command
        rest = list(args.args)
        return run_transcript_command(rest[0] if rest else None, rest[1:])
    if cmd == "adopt":
        from .worktrees import cli_adopt
        return cli_adopt(list(args.args))
    if cmd == "integrate":
        from .integrations import main as integrate_main
        return integrate_main(list(args.args))
    if cmd == "skills":
        from . import integrations
        if args.action == "list":
            _print(integrations.list_skills())
            return 0
        dest = Path(args.dest) if args.dest else _root(args.root) / ".claude" / "skills"
        if args.action == "install":
            _print({"installed": integrations.install_skills(dest)})
        else:
            _print({"removed": integrations.uninstall_skills(dest)})
        return 0
    root = _root(args.root)
    if cmd == "worker":
        from .worker import main as worker_main
        argv2 = ["--root", str(root), "--poll", str(args.poll)] + (["--loop"] if args.loop else ["--once"])
        return worker_main(argv2 + (["--no-model"] if args.no_model else []))
    if cmd == "push":
        from . import remote
        if args.status:
            _print(remote.status(root), True)
            return 0
        res = remote.push(root, **({"batch": args.batch} if args.batch else {}))
        if args.json:
            _print(res, True)
        elif res.get("busy"):
            print("another push is running")
        elif res.get("ok"):
            print(f"pushed {res['pushed']} events ({res['accepted']} recorded, {res['skipped']} skipped, "
                  f"{res['duplicates']} already there, {res['rejected']} rejected); {res['pending']} waiting")
        else:
            print(f"push failed: {res.get('error')}" + (f" ({res['pending']} events waiting)" if "pending" in res
                                                        else ""), file=sys.stderr)
        return 0 if res.get("ok") else 1
    if cmd == "status":
        from . import viewer
        _print({**viewer.processing_status(root), "stats": viewer.stats(root)["database"]})
        return 0
    if cmd == "search":
        from . import viewer
        params: dict[str, Any] = {"query": args.query, "limit": args.limit}
        for k, v in (("type", args.type), ("obs_type", args.obs_type), ("project", args.project),
                     ("dateStart", args.since), ("dateEnd", args.until), ("orderBy", args.order)):
            if v:
                params[k] = v
        if args.json:
            params["format"] = "json"
        _print(viewer.search(root, params), args.json)
        return 0
    if cmd == "timeline":
        from . import viewer
        params = {"depth_before": args.before, "depth_after": args.after}
        if args.anchor:
            params["anchor"] = args.anchor
        if args.query:
            params["query"] = args.query
        _print(viewer.timeline(root, params))
        return 0
    if cmd in ("get", "tool-uses"):
        from .mcp import call_tool
        res = call_tool(root, "get_observations" if cmd == "get" else "get_tool_uses",
                        {"ids": [int(i) if i.isdigit() else i for i in args.ids]})
        print(res["content"][0]["text"])
        return 1 if res.get("isError") else 0
    if cmd == "context":
        from . import viewer
        print(viewer.context_inject(root, [args.project] if args.project else None, colors=args.colors,
                                    full=args.full))
        return 0
    if cmd == "sessions":
        from . import viewer
        res = viewer.sessions(root, 0, args.limit)
        if args.json:
            _print(res, True)
        else:
            for s in res["items"]:
                print(f"#{s['id']:<5} {s['status']:<9} {s['observation_count']:>3} obs {s['summary_count']:>2} sum "
                      f"{s['pending_count']:>3} queued  {(s.get('user_prompt') or '')[:70]}")
        return 0
    if cmd == "session":
        from . import viewer
        _print(viewer.session_detail(root, args.id) or {"error": "not found"}, True)
        return 0
    if cmd == "settings":
        from . import viewer
        if args.assignments:
            updates = dict(a.split("=", 1) for a in args.assignments if "=" in a)
            try:
                _print(viewer.update_settings(root, updates)["settings"], True)
            except KeyError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        else:
            _print(viewer.get_settings(root)["settings"], True)
        return 0
    if cmd == "modes":
        from .modes import available_modes
        _print(available_modes(), True)
        return 0
    if cmd == "export":
        from . import viewer
        data = viewer.export_memories(root, args.query or None, args.project)
        Path(args.out).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        print(f"exported {data['totalObservations']} observations, {data['totalSessions']} sessions, "
              f"{data['totalSummaries']} summaries, {data['totalPrompts']} prompts to {args.out}")
        return 0
    if cmd == "import":
        from . import viewer
        _print(viewer.import_memories(root, json.loads(Path(args.file).read_text(encoding="utf-8"))), True)
        return 0
    if cmd == "queue":
        from . import viewer
        if args.retry_failed:
            _print(viewer.retry_failed(root), True)
        elif args.clear_failed or args.clear_all:
            _print(viewer.clear_queue(root, failed_only=not args.clear_all), True)
        else:
            _print(viewer.queue(root), True)
        return 0
    if cmd == "logs":
        from . import viewer
        if args.clear:
            _print(viewer.clear_logs(root), True)
        else:
            print("\n".join(viewer.logs(root, args.lines)["lines"]))
        return 0
    if cmd == "remember":
        from . import viewer
        _print(viewer.save_memory(root, args.text, args.title), True)
        return 0
    if cmd == "reindex":
        from .store import Store
        from .vectorsync import VectorSync, index_dir
        with Store.open(root) as st:
            st.db.execute("DELETE FROM vector_docs")
            vs = VectorSync(root, st)
            idx = vs.index()
            if idx is not None:
                idx.clear()
                idx.save()
            _print(vs.backfill(limit=1_000_000), True)
        print(f"index: {index_dir(root)}")
        return 0
    if cmd == "claude-md":
        from . import folders
        return (folders.generate_claude_md if args.action == "generate" else folders.clean_claude_md)(root,
                                                                                                      args.dry_run)
    if cmd == "corpus":
        from . import corpus
        from .mcp import call_tool
        if args.action == "build":
            payload = {"name": args.name, "description": args.description}
            for k, v in (("project", args.project), ("types", args.types), ("concepts", args.concepts),
                         ("files", args.files), ("query", args.query), ("dateStart", args.date_start),
                         ("dateEnd", args.date_end), ("limit", args.limit)):
                if v:
                    payload[k] = v
            res = call_tool(root, "build_corpus", payload)
        elif args.action == "list":
            res = call_tool(root, "list_corpora", {})
        elif args.action == "show":
            _print(corpus.get_corpus(root, args.name), True)
            return 0
        elif args.action == "delete":
            _print(corpus.delete_corpus(root, args.name), True)
            return 0
        elif args.action == "query":
            res = call_tool(root, "query_corpus", {"name": args.name, "question": " ".join(args.rest)})
        else:
            res = call_tool(root, f"{args.action}_corpus", {"name": args.name})
        print(res["content"][0]["text"])
        return 1 if res.get("isError") else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
