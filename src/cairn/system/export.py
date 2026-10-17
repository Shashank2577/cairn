"""Architecture documents from the system model (FR-029, T040).

One command writes the set: a context, containers, components, key flows, modules, configuration and
external systems description, each with its diagram and an evidence table, and each recording the commit
it describes. A section with nothing to report is omitted, never padded. Zero model calls: the
``--narrate`` opt-in (FR-030) is the only path that may call one, and it is a separate module
(:mod:`cairn.system.narrate`) the exporter only calls when asked.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .diagram.alt import evidence_table, md as md_cell
from .diagram.mermaid_out import to_mermaid
from .diagram.svg import render as svg_render
from .link import link
from .model import Model
from .views import commit_of, view as system_view

SECTIONS = ("context", "containers", "components", "flows", "code")
KIND_LABEL = {"container": "Container", "data-store": "Data store", "channel": "Channel",
              "library": "Library", "person": "Person", "external-system": "External system",
              "component": "Component", "system": "System", "code": "Code claim"}
MAX_FLOW_DOCS = 12          # distinct flow shapes that get a document of their own; the rest stay in the index
FLOWS_SLUG = "flows"


@dataclass
class Doc:
    """One exported document. ``desc`` is None for the flows index, which has a table and no diagram."""

    slug: str
    title: str
    level: str
    summary: str
    md: str
    desc: dict | None = None
    evidence: list[str] = field(default_factory=list)

    @property
    def mmd(self) -> str:
        return to_mermaid(self.desc) if self.desc else ""

    @property
    def svg(self) -> str:
        return svg_render(self.desc) if self.desc else ""


@dataclass
class Bundle:
    commit: str                 # the commit the model describes (HEAD when the model does not record one)
    head: str
    product: dict
    docs: list[Doc]
    flow_entries: int = 0       # entry points the model has
    flow_shapes: int = 0        # distinct element/step signatures among them

    @property
    def readme(self) -> str:
        return _index(self.product, self.commit, self.docs, self.flow_entries, self.flow_shapes)


def commits_behind(root: Path, commit: str) -> int | None:
    """How many commits HEAD is ahead of ``commit``; None when git cannot say."""
    if not commit:
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-list", "--count", f"{commit}..HEAD"],
                             capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        return int(out)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def bundle(root: Path, *, levels: tuple[str, ...] = SECTIONS) -> Bundle:
    """The architecture document set for the product this repository belongs to, in memory.

    Pure computation from the stored model: no model calls, no files. :func:`export` writes it; the
    server previews and zips the same object, so what the page shows is what the command writes.
    """
    from .build import head_commit
    root = Path(root).resolve()
    pm = link(root)
    product = getattr(pm, "product", {"system": root.name, "repos": [root.name], "skipped": []})
    head = head_commit(root)
    commit = commit_of(pm) or head or "uncommitted"   # the commit the model was built at, which may trail HEAD
    docs: list[Doc] = []
    used: set[str] = {FLOWS_SLUG, "README"}
    entries = shapes = 0
    for level in levels:
        descs = _describe(root, pm, product, level)
        if not descs:
            continue
        if level == "flows":
            entries = len(descs)
            groups = _shapes(descs)
            shapes = len(groups)
            kept = groups[:MAX_FLOW_DOCS]
            docs += [_doc(g[0], level, product, commit, used, g) for g in kept]
            docs.append(_flows_index(groups, kept, product, commit, docs))
            continue
        docs += [_doc(d, level, product, commit, used) for d in descs]
    return Bundle(commit=commit, head=head, product=product, docs=docs, flow_entries=entries, flow_shapes=shapes)


def export(root: Path, out: Path, *, levels: tuple[str, ...] = SECTIONS, narrate: bool = False) -> dict:
    """Write the architecture document set for the product this repository belongs to.

    Returns ``{"written": [...], "commit": str, "narrated": bool}`` — the files actually written
    (empty sections are not), the commit every document records, and whether narrative ran.
    """
    root = Path(root).resolve()
    b = bundle(root, levels=levels)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for doc in b.docs:
        files = {f"{doc.slug}.md": doc.md}
        if doc.desc:
            files |= {f"{doc.slug}.mmd": doc.mmd, f"{doc.slug}.svg": doc.svg}
        for name, text in files.items():
            (out / name).write_text(text, encoding="utf-8")
        written += list(files)
    (out / "README.md").write_text(b.readme, encoding="utf-8")
    written.append("README.md")

    narrated = False
    if narrate:
        from . import narrate as narrate_mod
        narrated = narrate_mod.narrate_export(root, out, link(root))
    return {"written": sorted(written), "commit": b.commit, "narrated": narrated}


def _describe(root: Path, pm: Model, product: dict, level: str) -> list[dict]:
    """Every diagram description of one level, or nothing when the model has nothing to say about it."""
    try:
        if level == "flows":
            from .flows import flows
            return flows(root, pm, product)
        return [system_view(root, level, model=pm)]
    except ValueError:
        return []


def _signature(desc: dict) -> tuple:
    """What a flow shows: its elements and its steps. Two entry points with the same one tell one story."""
    flow = desc.get("flow") or {}
    return (tuple(sorted(e["id"] for e in desc.get("elements", []))),
            tuple((m["from"], m["to"], m.get("what"), m.get("how"), m.get("style")) for m in flow.get("messages", [])))


def _shapes(descs: list[dict]) -> list[list[dict]]:
    """Flows grouped by signature, in the order of first appearance (busiest first)."""
    groups: dict[tuple, list[dict]] = {}
    for d in descs:
        groups.setdefault(_signature(d), []).append(d)
    return list(groups.values())


def _slug(title: str, level: str, used: set[str]) -> str:
    text = "".join(c if c.isalnum() else "-" for c in (title or level).lower())
    text = "-".join(x for x in text.split("-") if x)[:60].rstrip("-") or level
    slug, n = text, 2
    while slug in used:
        slug, n = f"{text[:56].rstrip('-')}-{n}", n + 1
    used.add(slug)
    return slug


def _refs(desc: dict) -> list[str]:
    """Every evidence reference the diagram cites, once each, in the order they appear."""
    seen: dict[str, None] = {}
    for part in list(desc.get("elements", [])) + list((desc.get("flow") or {}).get("messages", [])) \
            + list(desc.get("relationships", [])):
        for r in part.get("evidence", []):
            seen.setdefault(str(r), None)
    return list(seen)


def _doc(desc: dict, level: str, product: dict, commit: str, used: set[str], same: list[dict] | None = None) -> Doc:
    slug = _slug(desc.get("title"), level, used)
    also = len(same) - 1 if same else 0
    note = (f"The same elements and steps apply to {also} other entry point{'s' if also != 1 else ''}; "
            f"see the [flows index]({FLOWS_SLUG}.md).") if also else None
    return Doc(slug=slug, title=desc["title"], level=level, summary=desc.get("scope", ""), desc=desc,
               md=_document(desc, slug, commit, note), evidence=_refs(desc))


def _document(desc: dict, slug: str, commit: str, note: str | None = None) -> str:
    """One Markdown document: title, scope, the diagram and the evidence table."""
    lines = [f"# {desc['title']}", "",
             f"> {desc['scope']}", "",
             desc.get("description") or "", ""]
    if note:
        lines += [f"**{note}**", ""]
    if desc.get("source_note"):
        lines += [f"_{desc['source_note']}_", ""]
    lines += ["## Diagram", "", _embed_svg(slug), "",
              "## Evidence", "", evidence_table(desc)]
    lines += ["---", f"Records commit `{commit[:12]}`. Generated by `cairn docs export`; "
              "every statement cites evidence or is marked inferred."]
    return "\n".join(lines) + "\n"


def _embed_svg(slug: str) -> str:
    return f"![{slug}]({slug}.svg)"


def _flow_name(desc: dict) -> str:
    return desc["title"].removeprefix("Flow of ")


def _flows_index(groups: list[list[dict]], kept: list[list[dict]], product: dict, commit: str,
                 docs: list[Doc]) -> Doc:
    """One document for every flow: the distinct shapes (with a document each, up to the cap) and a table
    of every entry point, so nothing is dropped and nothing is repeated."""
    slug_of = {id(g): d.slug for g, d in zip(kept, [d for d in docs if d.level == "flows"])}
    total = sum(len(g) for g in groups)
    lines = ["# Flows", "",
             f"> {total} entry point{'s' if total != 1 else ''} reach {len(groups)} distinct "
             f"set{'s' if len(groups) != 1 else ''} of elements and steps", "",
             "Entry points whose elements and steps are identical share one diagram; only flows that differ "
             f"get a document of their own (at most {MAX_FLOW_DOCS}, busiest first). Computed with no model calls.",
             "", "## Distinct flows", "",
             "| Flow | Steps | Inferred | Entry points | Document | Evidence |", "|---|---|---|---|---|---|"]
    for g in groups:
        d = g[0]
        msgs = d["flow"]["messages"]
        inferred = sum(1 for m in msgs if m["provenance"] == "inferred")
        refs = _refs(d)[:2]
        doc = slug_of.get(id(g))
        lines.append(f"| {md_cell(_flow_name(d))} | {len(msgs)} | {inferred} | {len(g)} | "
                     + (f"[{md_cell(doc)}]({doc}.md)" if doc else "not drawn separately") + " | "
                     + ("; ".join(md_cell(r) for r in refs) or "no evidence recorded") + " |")
    lines += ["", "## Every entry point", "", "| Entry point | Same as |", "|---|---|"]
    for g in groups:
        for d in g:
            doc = slug_of.get(id(g))
            lines.append(f"| {md_cell(_flow_name(d))} | "
                         + (f"[{md_cell(doc)}]({doc}.md)" if doc else md_cell(_flow_name(g[0]))) + " |")
    lines += ["", "---", f"Records commit `{commit[:12]}`. Generated by `cairn docs export`; "
              "every statement cites evidence or is marked inferred."]
    return Doc(slug=FLOWS_SLUG, title="Flows", level="flows",
               summary=f"{total} entry point{'s' if total != 1 else ''}, {len(groups)} distinct", md="\n".join(lines) + "\n",
               evidence=[r for g in groups for r in _refs(g[0])[:2]])


def _index(product: dict, commit: str, docs: list[Doc], entries: int = 0, shapes: int = 0) -> str:
    name = product.get("system") or "System"
    lines = [f"# Architecture of {name}", "",
             f"Computed from code, manifests and deploy files at commit `{commit[:12]}` with 0 model calls. "
             "Every statement cites evidence or is marked inferred; sections with nothing to report are omitted.",
             "", "## Documents", ""]
    for d in sorted(docs, key=lambda d: (SECTIONS.index(d.level), d.slug != FLOWS_SLUG, d.slug)):
        lines.append(f"- [{d.title}]({d.slug}.md)" + (f" — {d.summary}" if d.level == "flows" and d.slug == FLOWS_SLUG else ""))
    if product.get("skipped"):
        lines += ["", "## Not mapped", ""]
        for s in product["skipped"]:
            lines.append(f"- **{s['repo']}** — {s['state']}")
    return "\n".join(lines) + "\n"


def configuration_table(pm: Model) -> str:
    """The configuration table: names read by code or set by deploy files, never values."""
    rows: dict[str, set[str]] = {}
    for e in pm.elements.values():
        for ev in e.evidence:
            if ev.kind == "config" and ev.entry:
                rows.setdefault(ev.entry, set()).add(e.name)
    if not rows:
        return ""
    out = ["| Configuration | Used by |", "|---|---|"]
    for name in sorted(rows):
        out.append(f"| `{name}` | {', '.join(sorted(rows[name]))} |")
    return "\n".join(out) + "\n"
