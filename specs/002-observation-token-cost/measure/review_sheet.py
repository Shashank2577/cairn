"""Blind human-review sheet: observations from two arms of the same events, shuffled, each next to the raw events of its batch.

    python review_sheet.py <results dir> <repo> <armA> <armB> [--per-arm 10] --out sheet.md --key key.json

The sheet hides which arm wrote which observation (items are labelled R1, R2 ...). The key file maps labels back.
Grade each item 0-2 on the three questions printed under it:
  Q1 Accuracy   - does every claim match the raw events shown?        (0 wrong claim, 1 vague, 2 accurate)
  Q2 Usefulness - would this help a later agent that has not seen the session?  (0 no, 1 a little, 2 yes)
  Q3 Specific   - names the file / value / error rather than "the agent ran a command"?  (0 no, 1 partly, 2 yes)
"""
import argparse
import json
import random
from pathlib import Path


def brief(row, n=130):
    def short(v):
        v = v if isinstance(v, str) else json.dumps(v)
        return " ".join(v.split())[:n]
    return f"- **{row['tool_name']}** `{short(row.get('tool_input'))}` → {short(row.get('tool_response'))}"


def items(run, arm, per_arm, rng):
    by_id = {r["tool_use_id"]: r for r in run["event_rows"]}
    out = []
    for b in run["batches"]:
        ev = [by_id[e] for e in b["events"] if e in by_id]
        for oid in b.get("observation_ids", []):
            o = next((x for x in run["observations"] if x["id"] == oid), None)
            if o:
                out.append({"arm": arm, "obs": o, "events": ev})
    rng.shuffle(out)
    return out[:per_arm]


ap = argparse.ArgumentParser()
ap.add_argument("dir")
ap.add_argument("repo")
ap.add_argument("arms", nargs=2)
ap.add_argument("--per-arm", type=int, default=10)
ap.add_argument("--seed", type=int, default=3)
ap.add_argument("--out", required=True)
ap.add_argument("--key", required=True)
a = ap.parse_args()
rng = random.Random(a.seed)
pool = []
for arm in a.arms:
    run = json.loads((Path(a.dir) / f"{arm}-{a.repo}.json").read_text())
    pool += items(run, arm, a.per_arm, rng)
rng.shuffle(pool)
lines = [f"# Blind review sheet — {a.repo}", "", "Grade each item 0-2: **Q1 accuracy** (claims match the events), **Q2 usefulness** (helps a later agent), **Q3 specificity** "
         "(names the file/value/error). Arms are hidden.", ""]
key = {}
for i, it in enumerate(pool, 1):
    o = it["obs"]
    facts = json.loads(o.get("facts") or "[]")
    lines += [f"## R{i}", "", "**Raw events in this batch**", "", *[brief(e) for e in it["events"][:6]], "",
              f"**Observation** — _{o['type']}_ — **{o['title']}**", "", f"{o.get('subtitle') or ''}", "",
              *[f"- {f}" for f in facts], "", f"{o.get('narrative') or ''}", "",
              "Q1 accuracy: _  Q2 usefulness: _  Q3 specific: _", ""]
    key[f"R{i}"] = it["arm"]
Path(a.out).write_text("\n".join(lines))
Path(a.key).write_text(json.dumps(key, indent=1))
print(f"{len(pool)} items -> {a.out}")
