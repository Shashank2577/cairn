"""Token and cost table for a results directory.   python summarize.py <dir> --repo <name> [--ref nocache]

Token counts are MEASURED (the provider's own usage report per call, summed over every model call a run made, condensing
calls included).  Dollar figures are ESTIMATED from those counts at Anthropic's Haiku 4.5 list prices: $1 input, $5 output,
cache read 0.1x, cache write 1.25x (5-minute) or 2x (1-hour; the Claude Code CLI writes 1-hour entries, measured in cache_probe.py).
Runs on other providers are priced at the same Haiku rates only for comparison of token volume, and marked.
"""
import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("dir")
ap.add_argument("--repo", required=True)
ap.add_argument("--ref", default="nocache")
args = ap.parse_args()


def cost(t):
    return (t["input"] + 0.1 * t["cache_read"] + 2.0 * t["cache_write"]) / 1e6 + t["output"] / 1e6 * 5


runs = {}
for p in sorted(Path(args.dir).glob(f"*-{args.repo}.json")):
    label = p.name[: -len(args.repo) - 6]
    if label.startswith("dry"):
        continue
    runs[label] = json.loads(p.read_text())
ref = runs.get(args.ref)
print(f"{'run':14s}{'calls':>6s}{'in-side':>9s}{'cache_w':>9s}{'output':>8s}{'think':>7s}{'obs':>5s}{'tok/event':>10s}{'$/event':>9s}{'vs ref':>8s}")
for k, r in runs.items():
    t = r["totals"]
    calls = sum(v["calls"] for v in r["by_task"].values()) if r["by_task"] else 0
    ins = t["input"] + t["cache_read"] + t["cache_write"]
    c = cost(t)
    if k.startswith("gemma"):
        c = float("nan")
    rel = f"{c / cost(ref['totals']) - 1:+.0%}" if ref and c == c and cost(ref["totals"]) else "n/a"
    print(f"{k:14s}{calls:6d}{ins:9d}{t['cache_write']:9d}{t['output']:8d}{t.get('thinking', 0):7d}{len(r['observations']):5d}"
          f"{(ins + t['output']) / r['events']:10.0f}{(c / r['events'] if c == c else float('nan')):9.4f}{rel:>8s}")
