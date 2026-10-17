"""Lever: a skip list.  Per tool (and per Bash command class): how many captured events, how much of the observer's
input they cost (characters after the production per-field cap), and how often the model's batch produced nothing
(tool_uses.observation_id is NULL when the batch was skipped as noise).  MEASURED from the snapshot's tool_uses.

    python tool_share.py <sessions.db> [<sessions.db> ...]
"""
import re
import sqlite3
import sys
from collections import defaultdict

CAP = 16_000
NOISE = re.compile(r"^\s*(cd\s+\S+\s*(&&|;)\s*)?(ls|pwd|echo|cat|head|tail|wc|which|date|sleep|clear|true|git\s+(status|log|diff|branch|show|rev-parse|remote)|"
                   r"find|stat|file|tree|du|df|ps|lsof|env|printenv|type|test|\[)\b")


def cls(tool, inp):
    if tool.lower() != "bash":
        return tool.lower()
    m = re.search(r'"command"\s*:\s*"((?:[^"\\]|\\.)*)"', inp or "")
    cmd = (m.group(1) if m else inp or "").replace("\\n", "\n")
    return "bash:read-only/listing" if NOISE.match(cmd) else "bash:other"


for path in sys.argv[1:]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    stat = defaultdict(lambda: [0, 0, 0])  # events, chars, with an observation
    tot = [0, 0]
    for tool, inp, out, obs in db.execute("SELECT tool_name, tool_input, tool_response, observation_id FROM tool_uses"):
        k = cls(tool, inp)
        chars = min(len(inp or ""), CAP) + min(len(out or ""), CAP)
        s = stat[k]
        s[0] += 1
        s[1] += chars
        s[2] += obs is not None
        tot[0] += 1
        tot[1] += chars
    print(f"== {path}: {tot[0]:,} events, {tot[1]/4/1e6:.1f}M observer-input tokens (chars/4, after the {CAP}-char cap)")
    print(f"{'class':26s}{'events':>8s}{'%events':>9s}{'%input':>8s}{'avg tok':>9s}{'batch kept':>12s}")
    for k, (n, ch, kept) in sorted(stat.items(), key=lambda kv: -kv[1][1])[:12]:
        print(f"{k:26s}{n:8d}{n/tot[0]:9.1%}{ch/tot[1]:8.1%}{ch/4/n:9.0f}{kept/n:12.0%}")
