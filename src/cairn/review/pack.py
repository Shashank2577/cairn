"""Text forms of a review: the terminal listing and a compact pack that fits the context budget."""
from __future__ import annotations

from . import store

MARK = {"high": "HIGH", "medium": "MED ", "low": "LOW ", "note": "NOTE"}
NO_MODEL = ("A model review was not run: only the checks that need no model. "
            "Configure a provider (cairn models) to add one.")


def _loc(f: dict) -> str:
    if not f["path"]:
        return "(change)"
    return f"{f['path']}:{f['line_start']}" if f.get("line_start") else f["path"]


def finding_line(f: dict) -> str:
    who = "model" if f["author_kind"] == "model" else "rule"
    return f"{MARK[f['severity']]} {_loc(f)} — {f['title']} [{f['provenance']}, {who}] {' '.join(f['citations'])}"


def footer(review: dict) -> str:
    if review["mode"] == "model":
        return f"A model was used: {review['tokens_in'] + review['tokens_out']} tokens."
    return NO_MODEL


def text(review: dict, findings: list[dict]) -> str:
    lines = [f"Review {review['id']}: {review['branch']} ({review['base_sha'][:8]}..{review['head_sha'][:8]}), "
             f"{review['status']}"]
    if not findings:
        lines.append("No findings.")
    for f in findings:
        lines.append(f"[{f['id']}] {finding_line(f)}")
        if f["body"]:
            lines.append(f"       {f['body'].splitlines()[0][:200]}")
    if review["skipped"]:
        lines.append(f"Skipped {len(review['skipped'])} file(s): " +
                     ", ".join(f"{s['path']} ({s['reason']})" for s in review["skipped"][:5]))
    lines.append(footer(review))
    return "\n".join(lines)


def pack(review: dict, findings: list[dict], budget: int = 1800) -> str:
    """Findings most serious first, cut to fit ``budget`` tokens (4 characters each, as the rest of Cairn counts)."""
    head = f"Review {review['id']} {review['branch']} {review['base_sha'][:8]}..{review['head_sha'][:8]} ({review['mode']})"
    limit, out, used = budget * 4, [head], len(head)
    kept = 0
    for f in findings:
        line = f"[{f['id']}] {finding_line(f)}"
        if used + len(line) > limit:
            break
        out.append(line)
        used += len(line) + 1
        kept += 1
    if kept < len(findings):
        out.append(f"+{len(findings) - kept} more (cairn review show {review['id']})")
    return "\n".join(out)


def as_json(review: dict, findings: list[dict], fail_on: str, failed: bool) -> dict:
    return {"review": {k: review[k] for k in ("id", "branch", "base_sha", "head_sha", "mode", "status", "trigger",
                                              "tokens_in", "tokens_out", "skipped", "context")},
            "counts": _count(findings),
            "findings": findings, "fail_on": fail_on, "failed": failed,
            "model_used": review["mode"] == "model"}


def _count(findings: list[dict]) -> dict:
    c = {s: 0 for s in store.SEVERITIES}
    for f in findings:
        if f["state"] != "dismissed":
            c[f["severity"]] += 1
    return c
