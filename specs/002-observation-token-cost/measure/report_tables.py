"""Markdown tables for findings.md, generated from the result files (so no number is typed by hand).
python report_tables.py <matrix dir> <heldout dir> <gemma dir>"""
import json
import re
import statistics as st
import sys
from pathlib import Path

import grade as G

matrix, held, gem = (Path(p) for p in sys.argv[1:4])
REPOS = ("cairn", "spreix-notetaker")


def load(d, repo):
    out = {}
    for p in sorted(d.glob(f"*-{repo}.json")):
        label = p.name[: -len(repo) - 6]
        if label.startswith("dry"):
            continue
        out[label] = json.loads(p.read_text())
    return out


def cost(t):  # ESTIMATED: Haiku 4.5 list prices, 1-hour cache writes at 2x (what the CLI writes)
    return (t["input"] + 0.1 * t["cache_read"] + 2.0 * t["cache_write"]) / 1e6 + t["output"] / 1e6 * 5


def fam(runs, name):
    return [v for k, v in runs.items() if re.sub(r"_r\d+$", "", k) == name]


def mean_of(rs, f):
    return st.mean(f(r) for r in rs)


def row(runs, name):
    rs = fam(runs, name)
    ev = rs[0]["events"]
    ins = mean_of(rs, lambda r: r["totals"]["input"] + r["totals"]["cache_read"] + r["totals"]["cache_write"])
    cw = mean_of(rs, lambda r: r["totals"]["cache_write"])
    out = mean_of(rs, lambda r: r["totals"]["output"])
    th = mean_of(rs, lambda r: r["totals"].get("thinking", 0))
    calls = mean_of(rs, lambda r: sum(v["calls"] for v in r["by_task"].values()))
    obs = mean_of(rs, lambda r: len(r["observations"]))
    c = mean_of(rs, lambda r: cost(r["totals"]))
    g = [G.grade(r) for r in rs]
    return dict(n=len(rs), calls=calls, ins=ins, cw=cw, out=out, th=th, obs=obs, tok_ev=(ins + out) / ev, tok_obs=(ins + out) / obs if obs else float("nan"),
                usd_ev=c / ev, usd_obs=c / obs if obs else float("nan"), hv=mean_of(g, lambda x: x["R1_hv_recall"] or 0),
                oc=mean_of(g, lambda x: x["R1o_outcome_recall"] or 0), gr=mean_of(g, lambda x: x["R3_grounding"] or 0))


for repo in REPOS:
    for title, d, names in ((f"{repo} — main window", matrix, ("baseline", "nocache", "think0", "lean", "lean2")),
                            (f"{repo} — held-out window", held, ("baseline", "nocache", "lean2"))):
        runs = load(d, repo)
        print(f"\n**{title}** (50 events; n = runs averaged)\n")
        print("| config | n | model calls | input-side tok | of which cache-written | output tok | thinking tok | observations | tok/event | tok/observation | est. $/event | HV recall | outcome recall | grounding |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        base = row(runs, "baseline")
        for nme in names:
            if not fam(runs, nme):
                continue
            r = row(runs, nme)
            d_ = "" if nme == "baseline" else f" ({r['usd_ev'] / base['usd_ev'] - 1:+.0%})"
            print(f"| {nme} | {r['n']} | {r['calls']:.0f} | {r['ins']:,.0f} | {r['cw']:,.0f} | {r['out']:,.0f} | {r['th']:,.0f} | {r['obs']:.0f} | {r['tok_ev']:,.0f} | {r['tok_obs']:,.0f} | ${r['usd_ev']:.4f}{d_} | {r['hv']:.0%} | {r['oc']:.0%} | {r['gr']:.0%} |")
