"""Before/after table with the SHIPPED defaults.  python report_after.py <matrix> <heldout> <after dir>
before = the old default (`baseline` runs, Haiku 4.5, CLI cache on, thinking on); after = replay with the new defaults and no harness patch.
Tokens are measured; dollars are estimated at list prices (Haiku 4.5 $1/$5, Haiku 5.5 $0.10/$0.50; 1-hour cache writes 2x)."""
import json
import sys
from pathlib import Path

import grade as G

matrix, held, after = (Path(p) for p in sys.argv[1:4])


def cost(t, p):
    return ((t["input"] + 0.1 * t["cache_read"] + 2.0 * t["cache_write"]) * p[0] + t["output"] * p[1]) / 1e6


P45, P55 = (1.0, 5.0), (0.10, 0.50)
for repo in ("cairn", "spreix-notetaker"):
    for win, bdir, adir in (("main", matrix, after / "main"), ("held-out", held, after / "held")):
        rows = [("before (old default)", bdir / f"baseline-{repo}.json", P45), ("after: Safe (Haiku 4.5)", adir / f"after_safe-{repo}.json", P45),
                ("after: Economy (Haiku 4.5)", adir / f"after_econ-{repo}.json", P45), ("after: Safe on Haiku 5.5", adir / f"after_safe55-{repo}.json", P55)]
        print(f"\n**{repo} — {win} window**\n\n| run | calls | input-side tok | cache-written | output | thinking | obs | tok/event | est. $/event | HV | outcome | grounding |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
        for name, path, price in rows:
            if not path.exists():
                continue
            r = json.loads(path.read_text())
            t = r["totals"]
            g = G.grade(r)
            ins = t["input"] + t["cache_read"] + t["cache_write"]
            print(f"| {name} | {sum(v['calls'] for v in r['by_task'].values())} | {ins:,} | {t['cache_write']:,} | {t['output']:,} | {t.get('thinking',0):,} | {len(r['observations'])} | "
                  f"{(ins+t['output'])/r['events']:,.0f} | ${cost(t,price)/r['events']:.5f} | {g['R1_hv_recall'] or 0:.0%} | {g['R1o_outcome_recall'] or 0:.0%} | {g['R3_grounding'] or 0:.0%} |")
