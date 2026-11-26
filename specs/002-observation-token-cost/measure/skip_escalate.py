"""Outcome-based skip rule and Sonnet-escalation rule, evaluated deterministically over every captured event.
    python skip_escalate.py <sessions.db> [<sessions.db> ...]
Skip rule (an event is skipped from the MODEL path and gets a one-line deterministic record instead):
  S1 trivial read-only command (cd/ls/pwd/echo/cat/head/tail/wc/which/date/sleep/git status|log|diff|branch|show|rev-parse|remote, find, stat…) with a successful, short result
  S2 any command with an empty or tiny (< 120 chars) successful result that is not state-changing and not a test/build/commit/deploy run
  S3 repeat: same tool + same input + same result as an earlier event of the same session
A command that writes (redirect, tee, sed -i, rm, mv, cp, mkdir, touch, git add/commit/push, install) is never skipped.
Escalation rule (batch goes to the stronger model): any event in it has an Edit/Write input >= EDIT chars, a result >= OUT chars, or an error marker with a result >= ERR chars.
"""
import hashlib
import json
import re
import sys
from collections import defaultdict

import grade as G

TRIVIAL = re.compile(r"^\s*(cd\s+\S+\s*(&&|;)\s*)*(ls|pwd|echo|cat|head|tail|wc|which|date|sleep|clear|true|stat|file|tree|du|df|ps|env|printenv|type|find|"
                     r"git\s+(status|log|diff|branch|show|rev-parse|remote|stash\s+list)|gh\s+(pr|run|issue)\s+(list|view|status))\b")
WRITES = re.compile(r"(>|\btee\b|sed\s+-i|\brm\b|\bmv\b|\bcp\b|\bmkdir\b|\btouch\b|git\s+(add|commit|push|merge|rebase|checkout|reset|stash\s+(push|pop))|install|\bkill\b|\bchmod\b)")
CAP = 16_000


def cmd_of(row):
    return G.command_of(row) if (row["tool_name"] or "").lower() == "bash" else ""


def run(path):
    import sqlite3
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    seen, skip = set(), defaultdict(lambda: [0, 0])
    tot_ev = tot_ch = 0
    hv_skipped = 0
    esc = {k: [0, 0] for k in ("edit>=4k", "out>=20k", "err&out>=8k", "any")}
    for row in db.execute("SELECT content_session_id, tool_name, tool_input, tool_response FROM tool_uses ORDER BY content_session_id, created_at_epoch, id"):
        r = dict(row)
        inp, out = r["tool_input"] or "", r["tool_response"] or ""
        ch = min(len(inp), CAP) + min(len(out), CAP)
        tot_ev += 1
        tot_ch += ch
        cmd = cmd_of(r)
        bash = (r["tool_name"] or "").lower() == "bash"
        _, _, hv = G.anchors(r)
        erry = bool(G.ERR.search(out[-3000:])) and len(out) > 40
        why = None
        key = hashlib.md5((r["content_session_id"] + r["tool_name"] + inp + "\0" + out).encode()).hexdigest()
        if key in seen:
            why = "S3 repeat"
        elif bash and not WRITES.search(cmd) and not erry and TRIVIAL.match(cmd) and len(out) < 4000:
            why = "S1 trivial read-only"
        elif bash and not WRITES.search(cmd) and not erry and len(out) < 120 and not G.HV_BASH.search(cmd):
            why = "S2 tiny result"
        seen.add(key)
        if why:
            skip[why][0] += 1
            skip[why][1] += ch
            hv_skipped += hv and why != "S3 repeat"
        e1 = (r["tool_name"] or "").lower() in ("edit", "write", "multiedit") and len(inp) >= 4000
        e2 = len(out) >= 20000
        e3 = erry and len(out) >= 8000
        for k, hit in (("edit>=4k", e1), ("out>=20k", e2), ("err&out>=8k", e3), ("any", e1 or e2 or e3)):
            if hit:
                esc[k][0] += 1
                esc[k][1] += ch
    print(f"== {path}: {tot_ev:,} events, {tot_ch/4/1e6:.1f}M observer-input tokens (chars/4, after the {CAP}-char cap)")
    allsk = [sum(v[0] for v in skip.values()), sum(v[1] for v in skip.values())]
    for k, (n, ch) in sorted(skip.items()):
        print(f"  skip {k:22s} {n:6d} events {n/tot_ev:6.1%}   input {ch/tot_ch:6.1%}")
    print(f"  skip TOTAL                  {allsk[0]:6d} events {allsk[0]/tot_ev:6.1%}   input {allsk[1]/tot_ch:6.1%}   high-value events caught by S1/S2: {hv_skipped}")
    for k, (n, ch) in esc.items():
        print(f"  escalate {k:12s}  {n:6d} events {n/tot_ev:6.1%}   input {ch/tot_ch:6.1%}")


for p in sys.argv[1:]:
    run(p)
