"""Optional narrative for the docs export (FR-030, T042).

With a model available and the user's opt-in, this adds names and one-line responsibilities for
components and short descriptions of the main flows to the exported documents. Every narrated sentence
cites the computed elements it describes and is labelled inferred. Disabling it changes no element and
no relationship: the narrative is a layer of text over a model that is identical without it.

The cost is recorded in the ledger under the ``system.narrate`` task (fast tier) and capped by the
``[system] narrate_tokens`` budget (default 4,000 tokens per export). When the budget runs out the
remaining sections keep their computed text.
"""
from __future__ import annotations

from pathlib import Path

from ..router import Budget
from .model import Model

TASK = "system.narrate"
DEFAULT_BUDGET = 4_000


def narrate_export(root: Path, out: Path, pm: Model) -> bool:
    """Add narrative to the exported documents. Returns True when any narrative was written.

    Only runs when a model is available (``Router.complete`` raising means none) and the user opted in
    (``cairn docs export --narrate``). Never changes the model; every sentence is labelled inferred and
    cites the computed elements it describes.
    """
    from ..core import Cairn
    try:
        c = Cairn.here(Path(root))
    except Exception:
        return False
    if c.router.provider is None:
        return False
    budget = Budget(total=int(c.project.cfg("system.narrate_tokens", DEFAULT_BUDGET)))

    written = False
    for md_file in sorted(Path(out).glob("*.md")):
        if md_file.name in ("README.md", "flows.md"):
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
            narrated = _narrate_document(c, pm, text, budget)
            if narrated != text:
                md_file.write_text(narrated, encoding="utf-8")
                written = True
        except Exception:  # a model hiccup never breaks the export
            continue
    return written


def _narrate_document(c, pm: Model, text: str, budget: Budget) -> str:
    """One document with narrated paragraphs inserted after the description, labelled and cited."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        return text
    title = lines[0][2:]
    elements = _elements_for(pm, title)
    if not elements:
        return text
    names = ", ".join(e["name"] for e in elements[:8])
    prompt = (f"In one or two sentences, describe what this part of the system does, for a new team member. "
              f"Cite the elements by name. Label every sentence [inferred].\n\n"
              f"Section: {title}\nElements: {names}\n")
    try:
        answer = c.router.complete(TASK, prompt, budget=budget, max_tokens=200).strip()
    except Exception:
        return text
    if not answer:
        return text
    cited = [e for e in elements if e["name"] in answer]
    if not cited:
        return text  # the model named nothing computed: the sentence is dropped, not kept
    cite = ", ".join(f"{e['name']} ({e['id']})" for e in cited[:4])
    paragraph = f"\n_Narrative (inferred, cites {cite}): {answer}_\n"
    out = []
    inserted = False
    for i, line in enumerate(lines):
        out.append(line)
        if not inserted and line.startswith("> ") and i + 1 < len(lines) and not lines[i + 1].startswith(">"):
            out.append(paragraph)
            inserted = True
    return "\n".join(out) if inserted else text


def _elements_for(pm: Model, title: str) -> list[dict]:
    """The computed elements a document's title names, so narrative cites what exists."""
    words = set(title.lower().replace("-", " ").split())
    out = []
    for e in pm.elements.values():
        name_words = set(e.name.lower().replace("-", " ").split())
        if words & name_words:
            out.append({"id": e.id, "name": e.name, "type": e.type})
    return sorted(out, key=lambda x: (x["type"] != "container", x["name"]))
