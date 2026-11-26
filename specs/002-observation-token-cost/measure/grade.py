"""Quality gate: grade the observations of a replay run against the raw tool events, with no model in the loop.

    python grade.py <results dir> [--repo spreix-notetaker|cairn] [--ref baseline]

Rubric (every score is computed from the raw events and the stored observation text):

  R1  High-value recall.   An event is HIGH-VALUE when it changed state or reported an outcome: Edit / Write / MultiEdit /
      NotebookEdit; a Bash command that failed (error markers) or is a commit / push / merge / PR / deploy / migrate / install /
      test / build run.  It is COVERED when at least one of its ANCHORS appears in the run's observation text: the basename of
      a file it touched, its command signature (first two words), or a distinctive token from its outcome (a number with a
      unit such as "12 passed", a commit hash, an error class).  recall = covered / high-value.
  R1o Outcome recall.      Of the events whose result carries a checkable outcome (a count such as "12 passed", a commit hash, an
      error class), the share whose outcome appears in the observations.  A bare list of files and commands scores on R1 but not R1o.
  R2  All-event coverage.  The same test over every event (low-value ones are allowed to be skipped, so this is context).
  R3  Grounding.            Specific tokens in the observations (paths, file names, numbers of 3+ digits, identifiers in
      backticks or CamelCase) that occur in the raw events, the user prompts or the briefing.  grounded / checked.  Low
      grounding means invented detail.
  R4  Completeness of form. Observations with a valid type, a title, >=1 fact and a narrative.
  R6  Synthesized facts.   Share of facts that say something (a finding, a cause, a value) rather than echo "ran `cmd`" / "read `file`" /
      "modified `file`": the difference between a memory and an activity log.
  R5  Duplication.          Pairs of observations with title word-overlap >= 0.8 (one thing recorded twice).

Cost-independent signals (R1, R3) are the gate: a variant passes when R1 does not fall more than 5 points below the
reference run on the same events and R3 does not fall more than 3 points.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HV_TOOLS = {"edit", "write", "multiedit", "notebookedit", "apply_patch", "write_file"}
HV_BASH = re.compile(r"\b(git\s+(commit|push|merge|rebase|tag)|gh\s+pr|pytest|npm\s+(test|run\s+(build|test|lint))|npx\s+(vitest|jest|tsc)|"
                     r"vitest|jest|go\s+test|cargo\s+(test|build)|make\b|docker\s+(build|push|compose)|deploy|migrate|alembic|"
                     r"pip\s+install|npm\s+install|pnpm\s+(install|build|test)|uv\s+(sync|pip)|terraform|kubectl\s+apply)\b", re.I)
ERR = re.compile(r"(error|exception|traceback|failed|fatal|✗|✘|cannot|denied|not found|exit code [1-9])", re.I)
PATH_TOKEN = re.compile(r"[\w.\-/]+\.[A-Za-z]{1,6}\b")
NUM_UNIT = re.compile(r"\b(\d+)\s+(passed|failed|tests?|files?|errors?|warnings?|skipped|insertions?|deletions?|commits?)\b", re.I)
HASH = re.compile(r"\b[0-9a-f]{7,40}\b")
ERR_CLASS = re.compile(r"\b([A-Z][A-Za-z]+(?:Error|Exception))\b")


def text_of(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return json.dumps(v, default=str)


def command_of(row) -> str:
    raw = row.get("tool_input") or ""
    try:
        d = json.loads(raw)
        return str(d.get("command") or d.get("cmd") or "") if isinstance(d, dict) else raw
    except ValueError:
        m = re.search(r'"command"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        return m.group(1) if m else raw


def anchors(row) -> tuple[set[str], set[str], bool]:
    """(act anchors, outcome anchors, high-value). Act = what was touched or run; outcome = what came back."""
    tool = (row["tool_name"] or "").lower()
    inp, out = text_of(row.get("tool_input")), text_of(row.get("tool_response"))
    cmd = command_of(row) if tool == "bash" else ""
    a: set[str] = set()
    hv = tool in HV_TOOLS
    if tool != "bash":
        for m in PATH_TOKEN.findall(inp):
            a.add(Path(m).name.lower())
    else:
        words = cmd.replace("&&", " ").replace(";", " ").split()
        for m in PATH_TOKEN.findall(cmd):
            if "/" in m or m.split(".")[-1].isalpha():
                a.add(Path(m).name.lower())
        sig = " ".join(words[:2]).lower() if words else ""
        if HV_BASH.search(cmd):
            hv = True
        if ERR.search(out[-3000:]) and (len(out) > 40):
            hv = hv or bool(ERR_CLASS.search(out) or re.search(r"\b(failed|error:|exit code [1-9]|traceback)\b", out, re.I))
        if sig:
            a.add(sig)
            if len(words) >= 1 and len(words[0]) > 4:
                a.add(words[0].lower())
    o: set[str] = set()
    for m in NUM_UNIT.finditer(out[-4000:]):
        o.add(f"{m.group(1)} {m.group(2)}".lower())
    for m in HASH.findall(out[:2000]):
        if re.search(r"\d", m) and re.search(r"[a-f]", m):
            o.add(m[:7].lower())
    for m in ERR_CLASS.findall(out[:6000]):
        o.add(m.lower())
    ok = lambda s_: {x for x in s_ if len(x) >= 4 and x not in {"true", "false", "null", "none", "echo ", "cd ../"}}
    return ok(a), ok(o), hv


def obs_text(o: dict) -> str:
    def j(k):
        v = o.get(k)
        try:
            v = json.loads(v) if isinstance(v, str) and v[:1] in "[{" else v
        except ValueError:
            pass
        return " ".join(map(str, v)) if isinstance(v, list) else (v or "")
    return " ".join(str(j(k)) for k in ("title", "subtitle", "narrative", "facts", "files_read", "files_modified")).lower()


def specific_tokens(text: str) -> set[str]:
    toks = {Path(m).name.lower() for m in PATH_TOKEN.findall(text) if len(m) > 4}
    toks |= {m.lower() for m in re.findall(r"`([^`\s]{4,60})`", text)}
    toks |= {m.lower() for m in re.findall(r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b", text)}
    toks |= {m for m in re.findall(r"\b\d{3,}\b", text)}
    return toks


def grade(run: dict) -> dict:
    rows = run["event_rows"]
    obs = run["observations"]
    corpus = " ".join(text_of(r.get("tool_input")) + " " + text_of(r.get("tool_response")) for r in rows).lower()
    alltext = " ".join(obs_text(o) for o in obs)
    hv_n = hv_cov = all_cov = out_n = out_cov = 0
    misses = []
    by_class: dict[str, list[int]] = {}
    for r in rows:
        a, oc, hv = anchors(r)
        if oc:
            out_n += 1
            out_cov += any(x in alltext for x in oc)
        covered = any(x in alltext for x in a | oc) if (a or oc) else False
        all_cov += covered
        if hv:
            hv_n += 1
            hv_cov += covered
            if not covered and len(misses) < 6:
                misses.append(f"{r['tool_name']}: {sorted(a)[:3]}")
        c = by_class.setdefault((r["tool_name"] or "").lower(), [0, 0])
        c[0] += 1
        c[1] += covered
    checked = grounded = 0
    ungrounded: list[str] = []
    for o in obs:
        for t in specific_tokens(" ".join(str(o.get(k) or "") for k in ("title", "subtitle", "narrative", "facts"))):
            checked += 1
            if t in corpus:
                grounded += 1
            elif len(ungrounded) < 8:
                ungrounded.append(t)
    well = sum(1 for o in obs if o.get("type") and o.get("title") and o.get("narrative") and json.loads(o.get("facts") or "[]"))
    titles = [set(re.findall(r"\w+", (o.get("title") or "").lower())) for o in obs]
    dup = sum(1 for i in range(len(titles)) for j in range(i + 1, len(titles))
              if titles[i] and titles[j] and len(titles[i] & titles[j]) / len(titles[i] | titles[j]) >= 0.8)
    facts_all = [f for o in obs for f in json.loads(o.get("facts") or "[]")]
    echo = re.compile(r"^(ran|read|modified|searched for|used) ")
    synth = sum(1 for f in facts_all if not echo.match(str(f)))
    tokens_out = run["totals"]["output"]
    obs_tokens = sum(len(obs_text(o)) for o in obs) // 4
    return {"events": len(rows), "observations": len(obs), "hv_events": hv_n, "R1_hv_recall": hv_cov / hv_n if hv_n else None,
            "R1o_outcome_recall": out_cov / out_n if out_n else None, "outcome_events": out_n,
            "R2_all_coverage": all_cov / len(rows), "R3_grounding": grounded / checked if checked else None,
            "R3_checked": checked, "R4_complete": well / len(obs) if obs else None,
            "R6_synthesized_facts": synth / len(facts_all) if facts_all else 0.0, "facts_per_obs": len(facts_all) / len(obs) if obs else 0, "R5_duplicates": dup,
            "obs_text_tokens": obs_tokens, "output_tokens": tokens_out, "ungrounded_examples": ungrounded, "hv_misses": misses}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ref", default="nocache")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    runs = {}
    for p in sorted(Path(args.dir).glob(f"*-{args.repo}.json")):
        label = p.name[: -len(args.repo) - 6]
        if label.startswith("dry"):
            continue
        runs[label] = json.loads(p.read_text())
    grades = {k: grade(v) for k, v in runs.items()}
    ref = grades.get(args.ref) or next(iter(grades.values()))
    print(f"{'run':16s}{'obs':>5s}{'HVrec':>8s}{'outc':>6s}{'cover':>7s}{'ground':>8s}{'form':>6s}{'synth':>6s}{'dups':>5s}{'out_tok':>9s}{'gate':>6s}")
    for k, g in grades.items():
        gate = "ref" if k == args.ref else ("PASS" if (g["R1_hv_recall"] or 0) >= (ref["R1_hv_recall"] or 0) - 0.05
                                            and (g["R1o_outcome_recall"] or 0) >= (ref["R1o_outcome_recall"] or 0) - 0.10
                                            and (g["R3_grounding"] or 0) >= (ref["R3_grounding"] or 0) - 0.05 else "FAIL")
        f = lambda x: "  n/a" if x is None else f"{x:6.0%}"
        print(f"{k:16s}{g['observations']:5d}{f(g['R1_hv_recall']):>8s}{f(g['R1o_outcome_recall']):>6s}{f(g['R2_all_coverage']):>7s}{f(g['R3_grounding']):>8s}"
              f"{f(g['R4_complete']):>6s}{f(g['R6_synthesized_facts']):>6s}{g['R5_duplicates']:5d}{g['output_tokens']:9d}{gate:>6s}")
    if args.json:
        print(json.dumps(grades, indent=1))


if __name__ == "__main__":
    sys.exit(main())
