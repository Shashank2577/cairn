#!/usr/bin/env python3
"""standup — a markdown-based group chat for multiple AI coding agents.

Each agent embodies its git branch name and talks to the others by appending turns to a single shared
markdown file (default ``$CAIRN_HOME/STANDUP.md``, i.e. ``~/.cairn/STANDUP.md``). The file has YAML front
matter holding the shared GOAL and PROMPT the group must converge on; the body is the chat log. Agents
``watch`` the file to listen, ``post`` to speak, ``agree`` to register consensus, and ``summation`` to
close it.

Zero dependencies (Python standard library). No network except the optional ``gh`` call for ``prs``.

Concurrency: every write takes an atomic lock (``mkdir <file>.lock``) so two agents posting at the same
instant can't clobber each other — the exact failure mode that silently reverts work when multiple agents
share a target.

Config / resolution order:
  --file <path>   | CAIRN_STANDUP_FILE  | $CAIRN_HOME/STANDUP.md (default ~/.cairn/STANDUP.md)
  --agent <name>  | CAIRN_STANDUP_AGENT | current git branch | "agent"

Commands:
  worktrees [--since 4h] [--json]                  list worktrees, newest first; --since N{m,h,d,w}
                                                   keeps only those with a commit or uncommitted edit
                                                   in the window ("all" = off)
  prs       [--since 4h] [--json]                  list open GitHub PRs via gh, newest first; --since
                                                   filters by last update
  open    --goal "..." --prompt "..." [--agent N]  create the channel
  join    [--agent N] [--message "..."]            add self + say hello
  post    --message "..." [--agree "..."] [--agent N]   append a turn
  agree   --deliverable "..." [--agent N]          append an AGREE turn
  watch   [--agent N] [--timeout SEC] [--interval SEC]  block until someone ELSE posts; prints their turn
  read    [--tail N] [--since AGENT]               print the chat
  status                                           participants + consensus
  summation --text "..." [--agent N]               close the room (status: agreed)

Exit codes: 0 ok / change seen, 2 watch timeout, 1 usage or error.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ----------------------------------------------------------------------- args
def parse_args(argv: list[str]) -> tuple[str | None, dict]:
    cmd = argv[0] if argv else None
    opts: dict = {}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is None or nxt.startswith("--"):
                opts[key] = True
            else:
                opts[key] = nxt
                i += 1
        i += 1
    return cmd, opts


CMD, OPTS = parse_args(sys.argv[1:])


def cairn_home() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn")


def default_file() -> Path:
    f = OPTS.get("file") if isinstance(OPTS.get("file"), str) else None
    return Path(f or os.environ.get("CAIRN_STANDUP_FILE") or cairn_home() / "STANDUP.md")


def git(args: list[str], cwd: str | None = None) -> str | None:
    try:
        res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")
    except OSError:
        return None
    return res.stdout if res.returncode == 0 else None


def git_branch() -> str | None:
    out = git(["rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip() if out else None


def agent_name() -> str:
    n = OPTS.get("agent") if isinstance(OPTS.get("agent"), str) else None
    return str(n or os.environ.get("CAIRN_STANDUP_AGENT") or git_branch() or "agent").strip()


FILE = default_file()


# --------------------------------------------------------------------- helpers
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read() -> str:
    return FILE.read_text(encoding="utf-8")


def die(msg: str) -> None:
    sys.stderr.write(f"standup: {msg}\n")
    raise SystemExit(1)


class Lock:
    """Atomic lock via mkdir (fails if the dir already exists). Retries with a short backoff so
    simultaneous agents serialize instead of clobbering; a lock older than the deadline is taken."""

    def __init__(self, path: Path):
        self.path = Path(str(path) + ".lock")

    def __enter__(self):
        deadline = time.time() + 10
        while True:
            try:
                os.mkdir(self.path)
                return self
            except FileExistsError:
                if time.time() > deadline:
                    try:  # stale lock? take it rather than deadlock forever
                        os.rmdir(self.path)
                    except OSError:
                        pass
                    try:
                        os.mkdir(self.path)
                    except OSError:
                        pass
                    return self
                time.sleep(0.08)

    def __exit__(self, *exc):
        try:
            os.rmdir(self.path)
        except OSError:
            pass


def split_doc(text: str) -> tuple[str, str]:
    """Split a standup doc into (yaml raw text, body)."""
    m = re.match(r"^---\n([\s\S]*?)\n---\n?([\s\S]*)$", text)
    if not m:
        return "", text
    return m.group(1), m.group(2)


def yaml_scalar(yaml: str, key: str) -> str | None:
    """Minimal front-matter reader: an inline value, or a block scalar (>-, >, |, |-) folded to one line."""
    m = re.search(rf"^{re.escape(key)}:\s*(.*)$", yaml, re.MULTILINE)
    if not m:
        return None
    inline = m.group(1).strip()
    if re.match(r"^[|>][+-]?$", inline):
        after = yaml[m.end():].split("\n")[1:]
        lines = []
        for line in after:
            if re.match(r"^\s+\S", line) or line.strip() == "":
                lines.append(line.strip())
            else:
                break
        return " ".join(lines).strip()
    return re.sub(r"^[\"']|[\"']$", "", inline)


def yaml_list(yaml: str, key: str) -> list[str]:
    """``key:\\n  - a\\n  - b`` (until a non-list line)."""
    m = re.search(rf"^{re.escape(key)}:\s*\n((?:\s*-\s*.+\n?)*)", yaml, re.MULTILINE)
    if not m:
        return []
    return [x for x in (re.sub(r"^\s*-\s*", "", ln).strip() for ln in m.group(1).split("\n")) if x]


_TURN = re.compile(r"^###\s+(.+?)\s+—\s+(\S+)\s*$", re.MULTILINE)


def parse_turns(body: str) -> list[dict]:
    """Chat turns: each starts with ``### <agent> — <iso>``."""
    heads = [(m.group(1).strip(), m.group(2).strip(), m.start(), m.end()) for m in _TURN.finditer(body)]
    turns = []
    for i, (agent, ts, _, end) in enumerate(heads):
        stop = heads[i + 1][2] if i + 1 < len(heads) else len(body)
        turns.append({"agent": agent, "ts": ts, "text": body[end:stop].strip()})
    return turns


def _ensure_nl(s: str) -> str:
    return s if s.endswith("\n") else s + "\n"


def append_turn(agent: str, message: str, agree: str | None = None) -> None:
    """Append a turn under "## Chat" (taking the lock), adding the author to participants if missing."""
    with Lock(FILE):
        yaml, body = split_doc(read())
        new_yaml = yaml
        if agent not in yaml_list(yaml, "participants"):
            new_yaml = re.sub(r"^participants:\s*\n((?:\s*-\s*.+\n?)*)",
                              lambda m: re.sub(r"\n?$", f"\n  - {agent}\n", m.group(0), count=1), yaml, count=1,
                              flags=re.MULTILINE)
            if new_yaml == yaml:  # no participants block — append one
                new_yaml = yaml.rstrip() + f"\nparticipants:\n  - {agent}\n"
        block = f"\n### {agent} — {now_iso()}\n\n{message.strip()}\n"
        if agree:
            block += f"\nAGREE: {agree.strip()}\n"
        new_body = body
        if not re.search(r"^##\s+Chat\s*$", new_body, re.MULTILINE):
            new_body += "\n## Chat\n"
        new_body = new_body.rstrip() + "\n" + block
        FILE.write_text(f"---\n{_ensure_nl(new_yaml)}---\n{new_body}", encoding="utf-8")


# -------------------------------------------------------------------- commands
def parse_window_ms(s) -> float | None:
    """"1h", "4h", "24h", "7d", "30m", "2w" -> milliseconds. "all" / "any" / "none" (or nothing) -> None, no
    filter. Anything unrecognized -> None with a warning, so a typo widens rather than hides worktrees."""
    if not s or s is True:
        return None
    v = str(s).strip().lower()
    if v in ("all", "any", "none", "*"):
        return None
    m = re.match(r"^(\d+)\s*([mhdw])$", v)
    if not m:
        sys.stderr.write(f'standup: unrecognized window "{s}" — showing all worktrees\n')
        return None
    unit = {"m": 60e3, "h": 3600e3, "d": 86400e3, "w": 604800e3}[m.group(2)]
    return int(m.group(1)) * unit


def human_age(ms: float) -> str:
    if not ms:
        return "unknown"
    s = max(0, round((time.time() * 1000 - ms) / 1000))
    if s < 60:
        return f"{s}s ago"
    m = round(s / 60)
    if m < 60:
        return f"{m}m ago"
    h = round(m / 60)
    if h < 48:
        return f"{h}h ago"
    return f"{round(h / 24)}d ago"


def worktree_activity_ms(path: str) -> float:
    """Newest of the last commit time and any uncommitted change (staged, modified or untracked): a branch
    with live unpushed edits counts as active even when its last commit is old. 0 when unknown."""
    last = 0.0
    sec = (git(["log", "-1", "--format=%ct"], cwd=path) or "").strip()
    if sec:
        last = max(last, int(sec) * 1000)
    out = git(["status", "--porcelain"], cwd=path) or ""
    for line in out.split("\n"):
        if not line.strip():
            continue
        p = line[3:].strip()  # "XY <path>" or, for renames, "XY old -> new"
        if " -> " in p:
            p = p[p.index(" -> ") + 4:]
        p = re.sub(r'^"|"$', "", p)
        try:
            last = max(last, os.stat(os.path.join(path, p)).st_mtime * 1000)
        except OSError:
            pass
    return last


def git_worktrees() -> list[dict]:
    """Git worktrees as ``{branch, path}`` (detached / bare entries are skipped)."""
    out = git(["worktree", "list", "--porcelain"])
    if out is None:
        return []
    items, cur = [], {}
    for line in out.split("\n"):
        if line.startswith("worktree "):
            cur = {"path": line[9:].strip()}
        elif line.startswith("branch "):
            cur["branch"] = line[7:].replace("refs/heads/", "").strip()
        elif line.strip() == "":
            if cur.get("path") and cur.get("branch"):
                items.append(cur)
            cur = {}
    if cur.get("path") and cur.get("branch"):
        items.append(cur)
    return items


def gh_prs() -> list | None:
    """Open PRs via the gh CLI; None when gh is unavailable / unauthenticated / there is no GitHub remote."""
    try:
        res = subprocess.run(["gh", "pr", "list", "--state", "open", "--limit", "200", "--json",
                              "number,title,headRefName,updatedAt,author,isDraft"], capture_output=True, text=True,
                             check=False, encoding="utf-8", errors="replace")
    except OSError:
        return None
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except ValueError:
        return []


def _parse_ms(iso: str | None) -> float:
    if not iso:
        return 0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return 0


def cmd_prs() -> None:
    prs = gh_prs()
    if prs is None:
        die("gh unavailable, unauthenticated, or no GitHub remote (try `gh auth login`)")
    window = parse_window_ms(OPTS.get("since"))
    now = time.time() * 1000
    rows = []
    for p in prs:
        updated = _parse_ms(p.get("updatedAt"))
        rows.append({"number": p.get("number"), "title": p.get("title"), "branch": p.get("headRefName"),
                     "author": (p.get("author") or {}).get("login", ""), "isDraft": bool(p.get("isDraft")),
                     "updatedAt": p.get("updatedAt"), "updatedMs": updated,
                     "age": human_age(updated) if updated else "unknown"})
    if window is not None:
        rows = [p for p in rows if p["updatedMs"] and now - p["updatedMs"] <= window]
    rows.sort(key=lambda p: -p["updatedMs"])
    if OPTS.get("json"):
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        sys.stderr.write((f'no open PRs updated within "{OPTS.get("since")}"' if window is not None
                          else "no open PRs") + "\n")
        return
    for p in rows:
        draft = " [draft]" if p["isDraft"] else ""
        print(f"#{p['number']}\t{p['age']:<8}\t{p['branch']}\t{p['title']}{draft}")


def cmd_worktrees() -> None:
    here = os.getcwd()
    window = parse_window_ms(OPTS.get("since"))
    now = time.time() * 1000
    rows = []
    for w in git_worktrees():
        ms = worktree_activity_ms(w["path"])
        rows.append({"branch": w["branch"], "path": w["path"], "current": w["path"] == here,
                     "lastActivity": datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                     if ms else None,
                     "lastActivityMs": ms, "age": human_age(ms)})
    if window is not None:  # scope to the window (commits OR uncommitted edits inside it), newest first
        rows = [w for w in rows if w["lastActivityMs"] and now - w["lastActivityMs"] <= window]
    rows.sort(key=lambda w: -(w["lastActivityMs"] or 0))
    if OPTS.get("json"):
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        sys.stderr.write((f'no worktrees active within "{OPTS.get("since")}" — widen the window or use --since all'
                          if window is not None else "no worktrees found") + "\n")
        return
    for w in rows:
        mine = "  (current)" if w["current"] else ""
        print(f"{w['age']:<8}\t{w['branch']}\t{w['path']}{mine}")


def cmd_open() -> None:
    agent = agent_name()
    goal, prompt = OPTS.get("goal"), OPTS.get("prompt")
    if not isinstance(goal, str) or not isinstance(prompt, str):
        die("open needs --goal and --prompt")
    if FILE.exists():
        if not OPTS.get("force"):
            die(f"channel exists: {FILE} (use --force to rotate it aside)")
        stamp = now_iso().replace(":", "-").replace("T", "-").replace("Z", "")
        archived = Path(re.sub(r"\.md$", f"-{stamp}.md", str(FILE)))
        FILE.rename(archived)
        sys.stderr.write(f"rotated existing channel → {archived}\n")
    FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = now_iso()

    def fold(s: str) -> str:
        return " ".join(s.split())
    doc = f"""---
channel: STANDUP
opened_by: {agent}
opened_at: {ts}
status: open
goal: >-
  {fold(goal)}
prompt: >-
  {fold(prompt)}
participants:
  - {agent}
protocol: |
  1. Before each turn, READ the whole file. Only respond to messages posted
     after your previous turn.
  2. Append your turn at the end of "## Chat" as:
         ### <agent-name> — <ISO-8601 UTC>
         <your message>
  3. To register consensus, include a line exactly: "AGREE: <deliverable>"
  4. When ALL participants have an AGREE line for the same deliverable, the
     last agent to agree appends "## SUMMATION" and flips status: agreed.
  5. New agent joining? Add yourself to participants and say Hello in Chat.
---

# Standup — group chat

The room is open. Post under **## Chat** following the protocol above.

## Chat

### {agent} — {ts}

Hello! 👋 I'm `{agent}`. The room is open — goal and prompt are in the
front matter. Counter-proposals welcome. I'm listening.
"""
    FILE.write_text(doc, encoding="utf-8")
    print(f'opened {FILE} as "{agent}"')


def cmd_join() -> None:
    agent = agent_name()
    if not FILE.exists():
        die(f'no channel at {FILE} — run "open" first')
    msg = OPTS.get("message") if isinstance(OPTS.get("message"), str) else \
        f"Hello! 👋 I'm `{agent}`. Joining the room and listening."
    append_turn(agent, msg)
    print(f'joined as "{agent}"')


def cmd_post() -> None:
    agent = agent_name()
    if not isinstance(OPTS.get("message"), str):
        die("post needs --message")
    if not FILE.exists():
        die(f"no channel at {FILE}")
    append_turn(agent, OPTS["message"], OPTS.get("agree") if isinstance(OPTS.get("agree"), str) else None)
    print(f'posted as "{agent}"')


def cmd_agree() -> None:
    agent = agent_name()
    d = OPTS.get("deliverable")
    if not isinstance(d, str):
        die("agree needs --deliverable")
    if not FILE.exists():
        die(f"no channel at {FILE}")
    msg = OPTS.get("message") if isinstance(OPTS.get("message"), str) else "I'm in. AGREE on the deliverable below."
    append_turn(agent, msg, d)
    print(f'agreed as "{agent}": {d}')


def cmd_watch() -> None:
    agent = agent_name()
    if not FILE.exists():
        die(f"no channel at {FILE}")
    timeout = float(OPTS.get("timeout") or 1800)
    interval = float(OPTS.get("interval") or 5)
    base = len(parse_turns(split_doc(read())[1]))
    start = time.time()
    sys.stderr.write(f'watching {FILE} as "{agent}" (every {interval:g}s, timeout {timeout:g}s)…\n')
    while True:
        if time.time() - start > timeout:
            print("TIMEOUT — no one else posted.")
            raise SystemExit(2)
        time.sleep(interval)
        try:
            text = read()
        except OSError:
            continue
        turns = parse_turns(split_doc(text)[1])
        if len(turns) <= base:
            continue
        fresh = turns[base:]
        base = len(turns)
        others = [t for t in fresh if t["agent"] != agent]  # ignore our own turns
        if not others:
            continue
        print(f"NEW ({len(others)}) after {round(time.time() - start)}s:\n")
        for t in others:
            print(f"### {t['agent']} — {t['ts']}\n{t['text']}\n")
        raise SystemExit(0)


def cmd_read() -> None:
    if not FILE.exists():
        die(f"no channel at {FILE}")
    turns = parse_turns(split_doc(read())[1])
    since = OPTS.get("since")
    if isinstance(since, str):  # turns after the named agent's last post
        last = max((i for i, t in enumerate(turns) if t["agent"] == since), default=-1)
        if last >= 0:
            turns = turns[last + 1:]
    if OPTS.get("tail") and OPTS["tail"] is not True:
        turns = turns[-int(OPTS["tail"]):]
    for t in turns:
        print(f"### {t['agent']} — {t['ts']}\n{t['text']}\n")


def cmd_status() -> None:
    if not FILE.exists():
        die(f"no channel at {FILE}")
    yaml, body = split_doc(read())
    participants = yaml_list(yaml, "participants")
    state = yaml_scalar(yaml, "status") or "open"
    turns = parse_turns(body)
    agree_by_agent: dict[str, str] = {}  # latest AGREE per agent
    for t in turns:
        m = re.search(r"^AGREE:\s*(.+)$", t["text"], re.MULTILINE)
        if m:
            agree_by_agent[t["agent"]] = m.group(1).strip()

    def norm(s: str) -> str:
        return " ".join(s.lower().split())
    values = [agree_by_agent.get(p) for p in participants]
    all_agreed = bool(participants) and all(values) and len({norm(v) for v in values if v}) == 1
    print(f"channel : {FILE}")
    print(f"status  : {state}")
    print(f"goal    : {yaml_scalar(yaml, 'goal') or '(see file)'}")
    print(f"turns   : {len(turns)}")
    print(f"participants ({len(participants)}):")
    for p in participants:
        a = agree_by_agent.get(p)
        print(f"  - {p}{f'  ✓ AGREE: {a}' if a else '  … no agree yet'}")
    print("consensus: REACHED — all participants agree. Write a ## SUMMATION." if all_agreed
          else "consensus: not yet")


def cmd_summation() -> None:
    agent = agent_name()
    if not isinstance(OPTS.get("text"), str):
        die("summation needs --text")
    if not FILE.exists():
        die(f"no channel at {FILE}")
    with Lock(FILE):
        yaml, body = split_doc(read())
        new_yaml = re.sub(r"^status:\s*.+$", "status: agreed", yaml, count=1, flags=re.MULTILINE)
        block = f"\n## SUMMATION\n\n_by {agent} — {now_iso()}_\n\n{OPTS['text'].strip()}\n"
        FILE.write_text(f"---\n{_ensure_nl(new_yaml)}---\n{body.rstrip()}\n{block}", encoding="utf-8")
    print("summation written; status → agreed")


USAGE = """standup — markdown group chat for multiple coding agents

usage: standup.py <command> [--flags]

  open    --goal "..." --prompt "..."   create the channel (you say hello)
                                        [--force rotates an existing room aside]
  worktrees [--since 4h] [--json]       list worktrees, newest first; --since
                                        N{m,h,d,w} keeps only those active in the
                                        window (commit OR uncommitted edit)
  prs     [--since 4h] [--json]         list open GitHub PRs (via gh), newest
                                        first; --since filters by last update
  join    [--message "..."]             add yourself + say hello
  post    --message "..." [--agree "..."]   append a turn
  agree   --deliverable "..."           append an AGREE turn
  watch   [--timeout SEC] [--interval SEC]  block until someone ELSE posts
  read    [--tail N] [--since AGENT]    print the chat
  status                                participants + consensus check
  summation --text "..."                close the room (status: agreed)

agent name defaults to your git branch; override with --agent or CAIRN_STANDUP_AGENT.
file defaults to $CAIRN_HOME/STANDUP.md (~/.cairn/STANDUP.md); override with --file or CAIRN_STANDUP_FILE."""

TABLE = {"open": cmd_open, "worktrees": cmd_worktrees, "prs": cmd_prs, "join": cmd_join, "post": cmd_post,
         "agree": cmd_agree, "watch": cmd_watch, "read": cmd_read, "status": cmd_status, "summation": cmd_summation}


def main() -> int:
    if not CMD or CMD in ("help", "--help", "-h"):
        print(USAGE)
        return 0 if CMD else 1
    fn = TABLE.get(CMD)
    if fn is None:
        die(f'unknown command "{CMD}"\n\n{USAGE}')
    fn()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
