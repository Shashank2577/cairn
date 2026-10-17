"""Components inside one container, from the code map's areas and communities (FR-016, T033).

A component is a Leiden community of the code map (the same clustering ``cairn areas`` shows), restricted
to the files that belong to one container, so a monorepo's deploy units do not bleed into each other. Two
things make the result readable rather than merely correct:

- **A name a person recognises.** FR-016 names a component after its most specific meaningful module path,
  skipping the generic folders every language has (src, lib, internal, utils, common, core). When a
  community has no meaningful path left, it takes the name of its busiest symbol and says so, marked
  inferred, so nobody reads a guess as a fact.
- **Dependencies with a "how".** Edges between communities are aggregated: many call sites become one
  arrow whose label is the mechanism that carries them (``calls``, ``imports`` …) rather than a padded
  phrase, with the call sites that justify it as evidence.

Everything here is read from the deterministic map (no model calls, no network). Budgets come from
``diagram/tokens.json`` through :mod:`cairn.system.views`, so the view can never disagree with the
checker or the renderer.

Public: :func:`view` (the diagram description) and :func:`groups` (the components themselves, for the
docs exporter and the tests).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

from ..engines import mapper, systems
from .build import LANG_DISPLAY
from .model import Element, Model, clip
from .views import MAX_REFS, budget, commit_of, fit, fit_accents, fit_boundaries, scope_line, slug, source

GENERIC_SEGMENTS = frozenset({"src", "lib", "libs", "internal", "int", "utils", "util", "common", "core",
                              "pkg", "packages", "main", "java", "kotlin", "resources", "build", "target"})
DROPPED_STEMS = frozenset({"index", "mod", "__main__", "__init__"})
MECHANISM = {"calls": "calls", "indirect_call": "calls", "imports": "imports", "imports_from": "imports",
             "references": "references", "uses": "uses", "inherits": "inherits", "implements": "implements",
             "mixes_in": "mixes in", "depends_on": "depends on", "re_exports": "re-exports"}
MAX_MEMBERS = 400       # members of one community considered for naming, evidence and degree
MAX_EDGES = 4000        # dependency edges aggregated into component arrows


def view(src, pm: Model, product: dict, scope: str | None = None) -> dict:
    """The ``component`` diagram description of one container's main parts."""
    container = find_container(pm, scope)
    groups_ = groups(container, src)
    if not groups_:
        raise ValueError(f"{container.name} has no mapped code to group into components; "
                         f"run `cairn map` in {container.repo or 'its repository'} first")
    elements = [g["element"] for g in groups_]
    if elements:
        elements[0]["focus"] = True          # the biggest part is the one a reader looks for first
    relationships = [r for g in groups_ for r in arrows(g)]
    elements, relationships, collapsed, dropped = fit(elements, relationships)
    elements = fit_accents(elements)
    note = f"components of {container.name}"
    if collapsed:
        note += f" · {collapsed} collapsed into “+{collapsed} more”"
    if dropped:
        note += f" · {dropped} relationships left out (over the arrow budget)"
    inferred = sorted(g["name"] for g in groups_ if g["name_inferred"])
    desc = (f"The main parts inside {container.name}, grouped from the code map's communities and linked by "
            f"their dependencies. Computed from the map with no model calls.")
    if inferred:
        desc += (f" {len(inferred)} name{'s' if len(inferred) != 1 else ''} taken from a symbol rather than a "
                 f"module path and marked inferred: {', '.join(inferred[:4])}.")
    return {"diagram": "component", "title": f"Components of {container.name}",
            "scope": scope_line(product, commit_of(pm), note), "description": desc,
            "source_note": "computed from the code map, 0 model calls", "elements": elements,
            "boundaries": boundary(container, elements), "relationships": relationships}


def find_container(pm: Model, scope: str | None) -> Element:
    """The container a Components view is scoped to.

    With no scope the busiest container wins (most relationships in the model, then id), so
    ``--view components`` answers with the product's main part instead of an error, and the choice is
    the same on every run.
    """
    candidates = [e for e in pm.elements.values() if e.type == "container" and not e.meta.get("not_mapped")]
    if not candidates:
        raise ValueError("this product has no containers")
    if not scope:
        degree: Counter = Counter()
        for r in pm.relationships.values():
            degree[r.from_id] += 1
            degree[r.to_id] += 1
        return max(sorted(candidates, key=lambda e: e.id), key=lambda e: (degree[e.id], e.id))
    low = scope.lower()
    for e in sorted(candidates, key=lambda e: e.id):
        names = (e.id, e.name, e.repo or "", *(e.meta.get("aliases") or []))
        if any(low == n.lower() for n in names if n):
            return e
    raise ValueError(f"no container in this product is called {scope!r}")


def boundary(container: Element, elements: list[dict]) -> list[dict]:
    """The one boundary that is a fact here: these components run inside this container (standard, s.5)."""
    ids = [e["id"] for e in elements if e["type"] == "component"]
    if not ids:
        return []
    return fit_boundaries([{"id": "runtime:" + container.id, "name": container.name, "type": "runtime",
                            "contains": ids}])


def groups(container: Element, src) -> list[dict]:
    """The container's components, before budgets.

    Each is ``{"id", "community", "name", "name_inferred", "module", "members", "size", "element", "links"}``
    where ``element`` is the box the standard asks for and ``links`` maps a neighbouring component's id to its
    aggregated dependency. ``size`` is the community's real size; ``members`` is capped at ``MAX_MEMBERS``
    for naming and evidence. Ordered by size then name, so two runs over the same map give the same answer.
    """
    root = repo_root(src, container.repo or "")
    index = mapper.index(mapper.map_dir(root) / "graph.json") if root else mapper.MapIndex()
    prefix = str(container.meta.get("root") or "").strip("/")
    files = _own_files(index, prefix)
    by_community: dict[int, list[str]] = defaultdict(list)
    for nid, node in index.nodes.items():
        if index.is_code(nid) and (index.file_of(nid) or "") in files:
            by_community[int(node.get("community"))].append(nid)
    if not by_community:
        return []
    _rehome_file_nodes(index, by_community)
    owner = {nid: cid for cid, members in by_community.items() for nid in members}
    deps = _dependencies(index, owner, container.repo or "")
    ordered = sorted(by_community.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    out: list[dict] = []
    for cid, members in ordered:
        members = sorted(members, key=lambda n: (-index.degree(n), n))
        if members and any(not _is_file(index, n) for n in members):
            out.append(_group(index, cid, members[:MAX_MEMBERS], container, prefix, root, len(members)))
    _unique_ids(out)
    ids = {g["community"]: g["id"] for g in out}
    for g in out:
        g["links"] = {ids[other]: bucket for other, bucket in deps.get(g["community"], {}).items()
                      if other in ids}
    out.sort(key=lambda g: (-g["size"], g["name"].lower(), g["id"]))
    return out


def arrows(group: dict) -> list[dict]:
    """One arrow per neighbouring component, labelled with the mechanism and carrying its call sites."""
    out: list[dict] = []
    for peer, bucket in sorted(group["links"].items()):
        mechanisms = [m for m, _ in bucket["mechanisms"].most_common()]
        n = bucket["count"]
        noun = "module" if mechanisms == ["imports"] else "symbol"
        verb = "Imports" if mechanisms == ["imports"] else ("Calls" if "calls" in mechanisms else "Depends on")
        out.append({"from": group["id"], "to": peer,
                    "what": clip(f"{verb} {n} {noun}{'s' if n != 1 else ''}", budget("label_what_chars", 32)),
                    "how": clip(" · ".join(mechanisms), budget("label_how_chars", 36)),
                    "style": "sync", "provenance": "extracted", "rule": "code-map-dependency",
                    "computed": True,
                    "evidence": bucket["evidence"] or [f"code-map community {group['community']}"]})
    return out


def _own_files(index: mapper.MapIndex, prefix: str) -> set[str]:
    """The map's source files that belong to one container: everything under its deploy unit's directory.

    A monorepo deploy unit owns a subdirectory (``meta['root']``); an empty root means the whole
    repository, which is the single-container case. A file counts only when Cairn recognises its
    language, which is the same test the rest of the system model uses, so a manifest the map happens
    to call code (pyproject.toml, a Dockerfile) never becomes a component. Tests are left out for the
    same reason the rest of the model leaves them out: they are not part of the running system.
    """
    from .signals import syntax
    from .signals.files import is_test_path
    files = {f for f, nodes in index.by_file.items()
             if syntax.lang_of(f) and not is_test_path(f) and any(index.is_code(n) for n in nodes)}
    if not prefix:
        return files
    return {f for f in files if f == prefix or f.startswith(prefix + "/")}


def _rehome_file_nodes(index: mapper.MapIndex, by_community: dict[int, list[str]]) -> None:
    """A community that holds nothing but a file node is that file, not a component.

    Clustering sometimes leaves ``db.py`` on its own while its functions sit in another community; the
    file then joins the community its own symbols are in, so the component keeps its module's evidence
    instead of appearing as a one-line box with nothing in it. Files with no symbols at all stay out.
    """
    owners: dict[str, Counter] = defaultdict(Counter)
    for cid, members in by_community.items():
        for nid in members:
            f = index.file_of(nid)
            if f and not _is_file(index, nid):
                owners[f][cid] += 1
    for cid, members in list(by_community.items()):
        lone = [nid for nid in members if _is_file(index, nid) and len(members) == 1]
        if not lone:
            continue
        del by_community[cid]
        for nid in lone:
            home = owners.get(index.file_of(nid) or "", Counter())
            if home:
                by_community[home.most_common(1)[0][0]].append(nid)


def _dependencies(index: mapper.MapIndex, owner: dict[str, int], repo: str) -> dict[int, dict[int, dict]]:
    """Aggregated dependency buckets between communities: ``{from: {to: {mechanisms, evidence, count}}}``.

    Only edges whose two ends are inside the container's own files count, so a call into an external
    library is never drawn as an arrow between two of the container's components.
    """
    agg: dict[tuple[int, int], dict] = defaultdict(lambda: {"mechanisms": Counter(), "evidence": [], "count": 0})
    seen = 0
    for nid, cid in sorted(owner.items()):
        here = index.file_of(nid) or ""
        line = _line(index, nid)
        for edge in index.out.get(nid, ()):
            other = owner.get(edge.other)
            if other is None or other == cid or edge.rel in mapper.STRUCTURE_RELS:
                continue
            bucket = agg[(cid, other)]
            bucket["mechanisms"][MECHANISM.get(edge.rel, edge.rel)] += 1
            bucket["count"] += 1
            if here and len(bucket["evidence"]) < MAX_REFS:
                ref = f"{repo}/{here}:{line or _digits(edge.location)}"
                if ref not in bucket["evidence"]:
                    bucket["evidence"].append(ref)
            seen += 1
            if seen > MAX_EDGES:
                return _buckets(agg)
    return _buckets(agg)


def _digits(value) -> str:
    text = "".join(c for c in str(value or "") if c.isdigit())
    return text or "1"


def _buckets(agg: dict) -> dict[int, dict[int, dict]]:
    out: dict[int, dict[int, dict]] = defaultdict(dict)
    for (a, b), bucket in sorted(agg.items()):
        out[a][b] = bucket
    return out


def _unique_ids(groups_: list[dict]) -> None:
    """Two communities can share a module path; the second gets its community number, never a silent merge."""
    seen: Counter = Counter()
    for g in groups_:
        eid = g["id"]
        seen[eid] += 1
        if seen[eid] > 1:
            g["id"] = f"{eid}-{seen[eid]}"
            g["element"]["id"] = g["id"]


def _group(index: mapper.MapIndex, cid: int, members: list[str], container: Element, prefix: str,
           root: Path | None, size: int) -> dict:
    name, inferred, module = _name(index, members, prefix)
    top = [index.label(n) for n in members if not _is_file(index, n)][:3]
    desc = f"{size} code symbol{'s' if size != 1 else ''}"
    if top:
        desc += f": {', '.join(top)}"
    if inferred:
        desc += f"; named after {name}, no meaningful module path"
    repo = container.repo or ""
    eid = f"{repo}:component:{slug(container.id)}:{slug(name)}"
    d = {"id": eid, "name": name, "type": "component", "tech": _tech(index, members), "desc": clip(desc),
         "provenance": "inferred" if inferred else "extracted", "count": size,
         "evidence": _refs(index, members, repo, root)}
    if inferred:
        d["name_inferred"] = True
    return {"id": eid, "community": cid, "name": name, "name_inferred": inferred, "module": module,
            "members": members, "size": size, "element": d, "links": {}}


def _name(index: mapper.MapIndex, members: list[str], prefix: str) -> tuple[str, bool, str]:
    """(name, inferred, module) for one community: its dominant file's module path, or its busiest symbol.

    FR-016's naming rule. The dominant file is the one most of the community's code lives in, so the name
    points at the part a reader would open. Generic folders are skipped at every depth; when nothing
    meaningful is left there is no name to give, so the community takes its busiest symbol's name and is
    marked inferred.
    """
    per_file: dict[str, list[str]] = defaultdict(list)
    for n in members:
        f = index.file_of(n)
        if f:
            per_file[f].append(n)
    ranked = sorted(per_file, key=lambda f: (-len(per_file[f]), -sum(index.degree(n) for n in per_file[f]), f))
    for f in ranked:
        module = module_path(f, prefix)
        if module:
            return module, False, module
    busiest = max(members, key=lambda n: (index.degree(n), index.label(n)))
    return index.label(busiest), True, ""


def _tech(index: mapper.MapIndex, members: list[str]) -> str:
    from .signals import syntax
    langs = Counter(syntax.FAMILY.get(syntax.lang_of(index.file_of(n) or "") or "", "") for n in members)
    langs.pop("", None)
    if not langs:
        return "code"
    return LANG_DISPLAY.get(langs.most_common(1)[0][0], langs.most_common(1)[0][0].title())


def module_path(path: str, prefix: str = "") -> str:
    """``src/cairn/system/views.py`` -> ``cairn.system.views``: the dotted module a file belongs to.

    Generic folders are dropped from the directory part at every depth and the container's own root is
    dropped too, so a name says what the part is called rather than where every repository keeps its
    source. The file's own stem is kept whatever it is (``main.py`` is a part, ``src/main/java`` is not).
    """
    p = PurePosixPath(path)
    dirs = [x for x in p.parts[:-1] if x != prefix and x.lower() not in GENERIC_SEGMENTS]
    stem = p.stem
    out: list[str] = []
    for part in [*dirs, stem]:
        if not part:
            continue
        if part.lower() in DROPPED_STEMS and (not out or part is stem):
            continue          # index.ts, mod.rs, __init__.py: the module is its folder, not its file name
        if out and out[-1] == part:
            continue          # Java and Go name a file after its package: store/store.go is just "store"
        out.append(part)
    return ".".join(out)


def _refs(index: mapper.MapIndex, members: list[str], repo: str, root: Path | None) -> list[str]:
    """``repo/path:line`` references to the component's own code, skipping ones the file cannot back.

    An empty file (a package marker, a stub) has no line 1 to point at, so its reference drops the line
    rather than pointing somewhere that does not exist; the checker would (rightly) reject it otherwise.
    """
    out: list[str] = []
    for n in members:
        f = index.file_of(n)
        if not f:
            continue
        line = _line(index, n)
        if line and not _has_line(root, f, line):
            line = None
        out.append(f"{repo}/{f}:{line}" if line else f"{repo}/{f}")
        if len(out) >= MAX_REFS:
            break
    return out


def _has_line(root: Path | None, rel: str, line: int) -> bool:
    if root is None:
        return True
    try:
        with open(root / rel, "rb") as fh:
            seen, read = 0, 0
            while seen < line:
                chunk = fh.read(8192)
                if not chunk:
                    return seen >= line
                seen += chunk.count(b"\n")
                read += len(chunk)
                if read > 4 << 20:
                    return True
            return True
    except OSError:
        return False


def _line(index: mapper.MapIndex, nid: str) -> int | None:
    return int(_digits(index.nodes.get(nid, {}).get("source_location")) or 0) or None


def _is_file(index: mapper.MapIndex, nid: str) -> bool:
    return index.label(nid) == PurePosixPath(index.file_of(nid) or "").name


def repo_root(src, repo: str) -> Path | None:
    """The checkout of ``repo``: the viewing repository, or a sibling declared in system.yaml."""
    root, name, _brain = source(src)
    if not repo or repo == name:
        return root
    declared = systems.discover(root)
    for s in declared.siblings if declared else ():
        if s.name == repo or s.root.name == repo:
            return s.root
    return None