"""Score a computed Containers view against a fixture's GROUND_TRUTH.yaml (SC-001).

Matching rules (stated here so the numbers can be audited):

- An element matches a ground-truth element when the TYPES are equal and the truth id, normalised
  (lower case, letters and digits only), equals the view element's normalised name, the last part of its
  id, or one of its aliases (compose service or workload names, recorded by the build). A type mismatch
  is a miss and, if extracted, a false positive.
- A relationship matches when both endpoints match (in the truth's direction). Several view relationships
  between the same pair count once for recall; for precision each extracted view relationship is judged.
- Precision is computed over EXTRACTED claims only. Claims labelled inferred or ambiguous are counted
  separately (their share of the view must stay at or below 10%); declared claims come from system.yaml
  and are neither counted as extracted nor as labelled.
- People are scored only when the truth lists them (they can only be declared).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

LABELLED = ("inferred", "ambiguous")


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def load_truth(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _keys(el: dict, aliases: dict) -> set[str]:
    keys = {norm(el["name"]), norm(el["id"].rsplit(":", 1)[-1])}
    keys |= {norm(a) for a in aliases.get(el["id"], [])}
    return keys


def score(view: dict, truth: dict, aliases: dict | None = None) -> dict:
    aliases = aliases or {}
    els = view["elements"]
    t_els = truth.get("elements") or []
    match: dict[str, str] = {}          # truth id -> view id
    for t in t_els:
        for e in els:
            if e["type"] == t["type"] and norm(t["id"]) in _keys(e, aliases):
                match[t["id"]] = e["id"]
                break
    matched_view = set(match.values())
    extracted_els = [e for e in els if e.get("provenance") == "extracted"]
    el_tp = sum(1 for e in extracted_els if e["id"] in matched_view)
    t_pairs = {(match.get(r["from"]), match.get(r["to"])) for r in truth.get("relationships") or []}
    found_pairs = {(r["from"], r["to"]) for r in view["relationships"]}
    rel_hits = [r for r in truth.get("relationships") or []
                if (match.get(r["from"]), match.get(r["to"])) in found_pairs]
    extracted_rels = [r for r in view["relationships"] if r.get("provenance") == "extracted"]
    rel_tp = sum(1 for r in extracted_rels if (r["from"], r["to"]) in t_pairs)
    labelled = sum(1 for x in els + view["relationships"] if x.get("provenance") in LABELLED)
    total = len(els) + len(view["relationships"])
    return {
        "element_recall": len(match) / len(t_els) if t_els else 1.0,
        "element_precision": el_tp / len(extracted_els) if extracted_els else 1.0,
        "relationship_recall": len(rel_hits) / len(truth.get("relationships") or []) if truth.get("relationships")
        else 1.0,
        "relationship_precision": rel_tp / len(extracted_rels) if extracted_rels else 1.0,
        "labelled_share": labelled / total if total else 0.0,
        "missed_elements": sorted(t["id"] for t in t_els if t["id"] not in match),
        "false_elements": sorted(e["name"] for e in extracted_els if e["id"] not in matched_view),
        "missed_relationships": sorted(f"{r['from']}->{r['to']}" for r in truth.get("relationships") or []
                                       if r not in rel_hits),
        "false_relationships": sorted(f"{r['from']}->{r['to']}" for r in extracted_rels
                                      if (r["from"], r["to"]) not in t_pairs),
        "counts": {"elements": len(els), "relationships": len(view["relationships"]), "labelled": labelled},
    }


def summary(name: str, s: dict) -> str:
    return (f"{name}: elements recall {s['element_recall']:.2f} precision {s['element_precision']:.2f}; "
            f"relationships recall {s['relationship_recall']:.2f} precision {s['relationship_precision']:.2f}; "
            f"labelled {s['labelled_share']:.0%}; missed {s['missed_elements'] + s['missed_relationships']}; "
            f"false {s['false_elements'] + s['false_relationships']}")
