"""Replicate-aware comparison.  Runs named `x`, `x_r2`, `x_r3` form one family; each metric is reported as mean [min-max].
The reference family's min-max is the NOISE BAND: a candidate passes a quality metric when its mean is not below the
reference family's minimum by more than 5 points (50 events give about 3 points of resolution: one event is one to three points).   python families.py <dir> --repo <name> [--ref nocache]
"""
import argparse
import json
import re
import statistics as st
from pathlib import Path

import grade as G

ap = argparse.ArgumentParser()
ap.add_argument("dir")
ap.add_argument("--repo", required=True)
ap.add_argument("--ref", default="nocache")
ap.add_argument("--json", action="store_true")
args = ap.parse_args()


def cost(t, ttl_mult=2.0):
    return (t["input"] + 0.1 * t["cache_read"] + ttl_mult * t["cache_write"]) / 1e6 + t["output"] / 1e6 * 5


fam: dict[str, list[dict]] = {}
for p in sorted(Path(args.dir).glob(f"*-{args.repo}.json")):
    label = p.name[: -len(args.repo) - 6]
    if label.startswith(("dry", "gemma")):
        continue
    run = json.loads(p.read_text())
    g = G.grade(run)
    t = run["totals"]
    g.update(per_event=cost(t) / run["events"], tok_event=(t["input"] + t["cache_read"] + t["cache_write"] + t["output"]) / run["events"],
             out_event=t["output"] / run["events"], think=t.get("thinking", 0), calls=sum(v["calls"] for v in run["by_task"].values()))
    fam.setdefault(re.sub(r"_r\d+$", "", label), []).append(g)

METRICS = [("R1_hv_recall", "HVrec", True), ("R1o_outcome_recall", "outc", True), ("R3_grounding", "ground", True),
           ("R6_synthesized_facts", "synth", True), ("out_event", "out/ev", False), ("tok_event", "tok/ev", False),
           ("per_event", "$/ev", False)]


def cell(vals, pct):
    vals = [v for v in vals if v is not None]
    if not vals:
        return "n/a"
    m = st.mean(vals)
    lo, hi = min(vals), max(vals)
    if pct:
        return f"{m:4.0%} [{lo:3.0%}-{hi:3.0%}]"
    return f"{m:7.4f}" if m < 1 else f"{m:7.0f}"


print(f"{'family':12s}{'n':>2s} " + " ".join(f"{h:>16s}" if pc else f"{h:>9s}" for _, h, pc in METRICS) + "  gate")
ref = fam.get(args.ref)
for name, runs in fam.items():
    cells = [cell([r[m] for r in runs], pc) for m, _, pc in METRICS]
    if name == args.ref:
        gate = "ref"
    elif ref:
        ok = all(st.mean([r[m] or 0 for r in runs]) >= min(r[m] or 0 for r in ref) - 0.05 for m, _, pc in METRICS[:3])
        gate = "PASS" if ok else "FAIL"
    else:
        gate = ""
    print(f"{name:12s}{len(runs):2d} " + " ".join(f"{c:>16s}" if pc else f"{c:>9s}" for c, (_, _, pc) in zip(cells, METRICS)) + f"  {gate}")
