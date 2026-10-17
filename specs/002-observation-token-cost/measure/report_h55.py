"""Haiku 5.5 runs and the escalation cost model.   python report_h55.py <h55 dir>
Prices (ESTIMATED from the Claude Code CLI's own cost report for a trivial call; list prices were not otherwise checked):
  haiku-5-5  $0.10 in / $0.50 out per MTok   sonnet-5-5  $2 in / $10 out per MTok   (cache read 0.1x, write 1.25x or 2x as before)
Escalation: an escalated event is billed on Sonnet 5.5 for its share of input and output tokens; shares come from skip_escalate.py."""
import json
import sys
from pathlib import Path

import grade as G

H = (0.10, 0.50)
S = (2.0, 10.0)
d = Path(sys.argv[1])


def cost(t, p, wmult=2.0):
    return ((t["input"] + 0.1 * t["cache_read"] + wmult * t["cache_write"]) * p[0] + t["output"] * p[1]) / 1e6


# shares of events and of observer input of each escalation rule (skip_escalate.py, all captured events)
RULES = {"none": {"cairn": (0, 0), "spreix-notetaker": (0, 0)},
         "edit/write input >= 4k": {"cairn": (.045, .166), "spreix-notetaker": (.023, .087)},
         "result >= 20k": {"cairn": (.087, .269), "spreix-notetaker": (.045, .191)},
         "any of the three": {"cairn": (.178, .505), "spreix-notetaker": (.117, .415)}}
for repo in ("cairn", "spreix-notetaker"):
    print(f"\n### {repo}")
    print("| run (Haiku 5.5, 50 events) | model calls | input-side tok | cache-written | output | thinking | obs | tok/event | est. $/event | HV | outcome | grounding |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    runs = {}
    for p in sorted(d.glob(f"*-{repo}.json")):
        r = json.loads(p.read_text())
        label = p.name[: -len(repo) - 6]
        runs[label] = r
        t = r["totals"]
        g = G.grade(r)
        ins = t["input"] + t["cache_read"] + t["cache_write"]
        print(f"| {label} | {sum(v['calls'] for v in r['by_task'].values())} | {ins:,} | {t['cache_write']:,} | {t['output']:,} | {t.get('thinking',0):,} | {len(r['observations'])} | "
              f"{(ins+t['output'])/r['events']:,.0f} | ${cost(t,H)/r['events']:.5f} | {g['R1_hv_recall'] or 0:.0%} | {g['R1o_outcome_recall'] or 0:.0%} | {g['R3_grounding'] or 0:.0%} |")
    lean2 = runs.get("h55_lean2")
    if lean2:
        t = lean2["totals"]
        n = lean2["events"]
        print(f"\nEscalation cost model on `h55_lean2` tokens (per event: in {(t['input']+t['cache_read']+t['cache_write'])/n:,.0f}, out {t['output']/n:,.0f}):\n")
        print("| escalation rule | events to Sonnet | input to Sonnet | est. $/event | vs all-Haiku-5.5 |")
        print("|---|---|---|---|---|")
        base = cost(t, H) / n
        for name, sh in RULES.items():
            ev, inp = sh[repo]
            c = (cost({**t, "input": t["input"] * (1 - inp), "output": t["output"] * (1 - inp)}, H) + cost({**t, "input": t["input"] * inp, "output": t["output"] * inp}, S)) / n
            print(f"| {name} | {ev:.1%} | {inp:.1%} | ${c:.5f} | {c/base:.1f}x |")
