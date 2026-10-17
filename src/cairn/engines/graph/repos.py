"""The map above one repository: every repository in view and how they depend on each other.

``repos(projects)`` works from what each project already has — its graph index (the
``MapIndex`` loaded from ``.cairn/graph/graph.json``) plus a handful of small manifest
files — so it never rebuilds or re-scans anything.

A repository *declares* packages (pyproject name and top-level packages, package.json
names including workspaces, go.mod module paths, Maven/Gradle group ids). Another
repository *uses* one when its code imports it: the graph engine keeps every import it
could not resolve inside the repository as an external node (``acme_core_money`` for
``from acme_core.money import …``, ``ref_acme_ui`` for ``"@acme/ui/price"``,
``go_pkg_github_com_acme_core_money``, ``com.acme.core.Money``). Matching those
against the declarations gives repository → repository links weighted by import sites.

The engine's node-level multi-repository graph (``cairn graph global`` /
``merge-graphs``) answers symbol-level questions across repositories; this module is
the cheap overview it does not provide: no merge, no global file, one pass per index.
"""
from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from cairn.engines.graph.ids import make_id

IMPORT_RELS = frozenset({"imports", "imports_from", "re_exports", "dynamic_import"})
PY_EXTS = frozenset({".py", ".pyi"})
JS_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".astro"})
GO_EXTS = frozenset({".go"})
JVM_EXTS = frozenset({".java", ".kt", ".kts", ".scala", ".groovy"})
# Top-level directories that are never an importable product package.
_NOT_PACKAGES = frozenset({"tests", "test", "testing", "docs", "doc", "scripts", "examples", "example", "samples",
                           "benchmarks", "bench", "tools", "build", "dist", "site-packages", "migrations"})
_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md", "Readme.md")
_MAX_MANIFEST_BYTES = 1_000_000


# ── declarations ───────────────────────────────────────────────────────────────

def _read(root: Path, rel: str) -> str | None:
    p = root / rel
    try:
        if p.stat().st_size > _MAX_MANIFEST_BYTES:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _manifests(root: Path, files: set[str], name: str) -> list[str]:
    """Manifest files called ``name`` known to the index (plus one at the root)."""
    found = {f for f in files if PurePosixPath(f).name == name
             and not any(p in ("node_modules", "vendor", "testdata", "third_party") for p in PurePosixPath(f).parts)}
    if name not in found and (root / name).is_file():
        found.add(name)
    return sorted(found, key=lambda f: (f.count("/"), f))


def _python(root: Path, files: set[str]) -> tuple[dict[str, str], list[str]]:
    """Importable top-level Python module ids -> the name to show, and declared deps."""
    from cairn.engines.graph.manifest_ingest import _PARSERS
    modules: dict[str, str] = {}
    deps: list[str] = []
    bases = [""]
    for rel in _manifests(root, files, "pyproject.toml"):
        text = _read(root, rel)
        if text is None:
            continue
        try:
            info = _PARSERS["python"](text)
        except Exception:  # noqa: BLE001 — a malformed manifest just declares nothing
            info = None
        if not info:
            continue
        base = str(PurePosixPath(rel).parent)
        base = "" if base == "." else base + "/"
        bases.append(base)
        modules.setdefault(make_id(info["name"]), info["name"])
        deps += [d for d in info.get("deps", []) if d]
    for f in files:
        if not f.endswith("/__init__.py"):
            continue
        for base in bases:
            if not f.startswith(base):
                continue
            parts = PurePosixPath(f[len(base):]).parts
            if len(parts) == 3 and parts[0] == "src":
                pkg = parts[1]
            elif len(parts) == 2:
                pkg = parts[0]
            else:
                continue
            if pkg.lower() not in _NOT_PACKAGES and pkg.isidentifier():
                modules.setdefault(make_id(pkg), pkg)
    return modules, deps


def _js(root: Path, files: set[str]) -> tuple[dict[str, str], list[str]]:
    """package.json names (the root one and its workspaces) -> ids, and declared deps."""
    names: dict[str, str] = {}
    deps: list[str] = []
    pkg_dirs: dict[str, dict] = {}
    for rel in _manifests(root, files, "package.json"):
        text = _read(root, rel)
        if text is None:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            d = str(PurePosixPath(rel).parent)
            pkg_dirs["" if d == "." else d] = data
    root_pkg = pkg_dirs.get("", {})
    ws = root_pkg.get("workspaces") or []
    if isinstance(ws, dict):
        ws = ws.get("packages") or []
    pnpm = _read(root, "pnpm-workspace.yaml") if (root / "pnpm-workspace.yaml").is_file() else None
    if pnpm:
        ws = list(ws) + re.findall(r"^\s*-\s*['\"]?([^'\"#\n]+?)['\"]?\s*$", pnpm, re.M)
    patterns = [str(p).rstrip("/") for p in ws if isinstance(p, str) and not str(p).startswith("!")]
    for d, data in pkg_dirs.items():
        if d and not any(fnmatch.fnmatch(d, p) for p in patterns):
            continue
        name = data.get("name")
        if isinstance(name, str) and name:
            names.setdefault(make_id("ref", name), name)
        for key in ("dependencies", "peerDependencies", "devDependencies"):
            block = data.get(key)
            if isinstance(block, dict):
                deps += [str(k) for k in block]
    return names, deps


def _go(root: Path, files: set[str]) -> tuple[dict[str, str], list[str]]:
    from cairn.engines.graph.manifest_ingest import _PARSERS
    mods: dict[str, str] = {}
    deps: list[str] = []
    for rel in _manifests(root, files, "go.mod"):
        text = _read(root, rel)
        info = _PARSERS["go"](text) if text else None
        if info and info.get("name"):
            mods.setdefault(make_id("go", "pkg", info["name"]), info["name"])
            deps += info.get("deps", [])
    return mods, deps


def _jvm(root: Path, files: set[str]) -> tuple[dict[str, str], list[str]]:
    """Maven/Gradle group ids (lower-cased, dotted) -> shown name, and declared deps."""
    from cairn.engines.graph.manifest_ingest import _PARSERS
    groups: dict[str, str] = {}
    deps: list[str] = []
    for rel in _manifests(root, files, "pom.xml"):
        text = _read(root, rel)
        if not text:
            continue
        try:
            info = _PARSERS["maven"](text)
        except Exception:  # noqa: BLE001
            info = None
        if info and ":" in info["name"]:
            group = info["name"].split(":", 1)[0]
            groups.setdefault(group.lower(), info["name"])
        if info:
            deps += info.get("deps", [])
    for name in ("build.gradle", "build.gradle.kts"):
        for rel in _manifests(root, files, name):
            text = _read(root, rel) or ""
            m = re.search(r"""^\s*group\s*=\s*["']([\w.\-]+)["']""", text, re.M)
            if m:
                groups.setdefault(m.group(1).lower(), m.group(1))
            deps += re.findall(r"""(?:implementation|api|compile)\s*\(?\s*["']([\w.\-]+:[\w.\-]+)""", text)
    return groups, deps


def _product(root: Path) -> dict | None:
    """The product this repository *declares*, if a ``system.yaml`` at its root says so.

    Returns ``{"system", "members", "description"}``. ``members`` is every repository the declaration
    names, including this one, as ``{name, root}``. ``description`` is this repository's declared
    description. ``None`` when this root declares nothing — which is true of every sibling in a product
    except the one that owns the file, so callers must resolve membership across projects rather than
    asking one repository.
    """
    from cairn.engines import systems
    try:
        sysdef = systems.discover(root)
        decl = systems.declarations(root)
    except Exception:
        return None
    if sysdef is None or decl is None:
        return None
    members = []
    try:
        members.append({"name": sysdef.root.name, "root": str(Path(sysdef.root).resolve())})
    except OSError:
        pass
    for s in sysdef.siblings:
        try:
            members.append({"name": s.name, "root": str(Path(s.root).resolve())})
        except OSError:
            continue
    mine = root.resolve()
    description = None
    for m in members:
        if m["root"] == str(mine):
            description = ((decl.repos or {}).get(m["name"]) or {}).get("description")
            break
    return {"system": decl.system or sysdef.name, "members": members, "description": description}


def _declared_product_links(items, decl, product_of: dict) -> dict[tuple[str, str], dict]:
    """Repository → repository links declared in a ``system.yaml``.

    ``actors`` and ``relationships`` are facts no amount of parsing can recover — they are the things a
    person decided. They are drawn with ``kind="declared"`` and no inferred weight.
    """
    from cairn.engines import systems
    out: dict[tuple[str, str], dict] = {}
    by_root = {str(Path(root).resolve()): pid for pid, _n, root, _i in items}
    for pid, _name, root, _idx in items:
        prod = decl[pid].get("product")
        if not prod:
            continue
        try:
            d = systems.declarations(Path(root))
        except Exception:
            continue
        name_of = {by_root.get(m["root"]): m["name"] for m in prod["members"]}
        for rel in (getattr(d, "relationships", None) or []):
            src, dst = _declared_endpoints(rel, name_of, by_root)
            if not src or not dst or src == dst or (src, dst) in out:
                continue
            out[(src, dst)] = {"source": src, "target": dst, "kind": "declared", "weight": 1,
                               "packages": [], "evidence": [], "declared": True,
                               "note": rel.get("what") or rel.get("description") or ""}
    return out


def _declared_endpoints(rel: dict, name_of: dict[str, str], by_root: dict[str, str]):
    """The two project ids a declared relationship points at, by repository name or by path."""
    out = []
    for side in ("src", "dst", "source", "target", "from", "to"):
        v = rel.get(side)
        if not isinstance(v, str):
            continue
        if v in name_of:
            out.append(name_of[v])
        elif v in by_root:
            out.append(by_root[v])
    return (out + [None, None])[:2]


def _shared_element_links(items, decl, evidence: int, product_of: dict) -> dict[tuple[str, str], dict]:
    """Repository → repository links from a shared external element in their stored system models.

    Two services that both publish to ``kafka`` are related even though neither imports the other; the
    evidence is the element itself, named in both repositories' own models. Only elements that are not
    the repository's own container count, and both ends must be mapped projects so the graph never points
    at something the reader cannot open.
    """
    from cairn.engines import systems
    # ``product_of`` is resolved across every project, not read off one repository: in a product only
    # the repository that owns system.yaml declares it, so asking each one yields the answer for exactly
    # one of them and nothing for the rest. Both ends must be in the same product, and repositories with
    # no product never link to anything — two unrelated projects that happen to both use PostgreSQL are
    # not one system.
    prod_of = {pid: (product_of.get(pid) or {}).get("system") for pid, *_ in items}

    used: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))  # name -> repo -> element ids
    for pid, _name, root, _idx in items:
        db = Path(root) / ".cairn" / "brain.db"
        if not db.exists():
            continue
        try:
            rows = systems.read_only_rows(
                db, "SELECT id, type, name FROM sm_element WHERE type <> 'code' AND type <> 'container'")
        except Exception:
            continue
        for r in rows:
            used[str(r["name"]).lower()][pid].append(r["id"])

    out: dict[tuple[str, str], dict] = {}
    for name, holders in used.items():
        pids = sorted(holders)
        if len(pids) < 2:
            continue
        same_product = {prod_of.get(p) for p in pids}
        for i, a in enumerate(pids):
            for b in pids[i + 1:]:
                if prod_of.get(a) is None or prod_of.get(a) != prod_of.get(b):
                    continue
                link = out.setdefault((a, b), {"source": a, "target": b, "kind": "shares", "weight": 0,
                                               "packages": [], "evidence": []})
                link["weight"] += 1
                link["packages"].append(name)
                for pid, want in ((a, a), (b, b)):
                    for eid in holders[pid][:evidence]:
                        link["evidence"].append({"file": eid, "line": None, "name": name, "package": name,
                                                 "node": eid})
    return out


def declarations(root: "str | Path", idx) -> dict:
    """What a repository offers to others and what it depends on, by ecosystem, plus any
    ``system.yaml`` product it belongs to.

    The package keys are read from code. The ``product`` key is read from the declaration file, because
    a service mesh has no shared packages to import and would otherwise come back as an island: the ASG
    services call each other over HTTP, so nothing in the source says they are one product except the
    file that says so.
    """
    root = Path(root)
    files = set(idx.by_file)
    py, py_deps = _python(root, files)
    js, js_deps = _js(root, files)
    go, go_deps = _go(root, files)
    jvm, jvm_deps = _jvm(root, files)
    return {"python": py, "js": js, "go": go, "jvm": jvm,
            "deps": {"python": py_deps, "js": js_deps, "go": go_deps, "jvm": jvm_deps},
            "product": _product(root)}


# ── description / summary ──────────────────────────────────────────────────────

def readme_description(root: "str | Path", limit: int = 240) -> str:
    """The README's first prose paragraph (headings, badges, HTML and code skipped)."""
    root = Path(root)
    for name in _README_NAMES:
        p = root / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:20_000]
        except OSError:
            continue
        in_code = False
        para: list[str] = []
        lines = text.splitlines()
        underline = re.compile(r"[=\-~^\"'`#*+]{3,}")
        skip_next = False
        for i, line in enumerate(lines):
            s = line.strip()
            if skip_next:
                skip_next = False
                continue
            if s and i + 1 < len(lines) and underline.fullmatch(lines[i + 1].strip()) and not in_code:
                skip_next = True  # a setext / reStructuredText heading
                if para:
                    break
                continue
            if s.startswith("```") or s.startswith("~~~"):
                in_code = not in_code
                continue
            if in_code:
                continue
            if not s:
                if para:
                    break
                continue
            if (s.startswith(("#", "<", "![", "[![", "|", ">", "---", "===", "..", ":"))
                    or re.fullmatch(r"[=\-~*_]{3,}", s)):
                if para:
                    break
                continue
            para.append(s)
        if para:
            out = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", " ".join(para))
            out = re.sub(r"[*_`]{1,3}", "", out).strip()
            return out if len(out) <= limit else out[: limit - 1].rsplit(" ", 1)[0] + "…"
    return ""


def _top_communities(idx, n: int = 3) -> list[dict]:
    try:
        from cairn.graphviews import communities
        nodes = communities(idx, limit=n)["nodes"]
        return [{"id": c["community"], "label": c["label"], "area": c.get("area", ""), "size": c["size"]}
                for c in nodes[:n]]
    except Exception:  # noqa: BLE001 — fall back to the index's own areas
        return [{"id": a["id"], "label": a["name"], "area": "", "size": a["size"]} for a in idx.areas()[:n]]


# ── matching ───────────────────────────────────────────────────────────────────

def _eco(path: str | None) -> str | None:
    ext = PurePosixPath(path or "").suffix.lower()
    if ext in PY_EXTS:
        return "python"
    if ext in JS_EXTS:
        return "js"
    if ext in GO_EXTS:
        return "go"
    if ext in JVM_EXTS:
        return "jvm"
    return None


def _is_external(node: dict) -> bool:
    return bool(node.get("external")) or not node.get("source_file")


def _line(location: str | None) -> int | None:
    loc = (location or "").lstrip("L")
    return int(loc) if loc.isdigit() else None


def _prefixes(value: str, sep: str):
    """``value`` and each shorter ``sep``-bounded prefix, longest first."""
    yield value
    i = value.rfind(sep)
    while i > 0:
        yield value[:i]
        i = value.rfind(sep, 0, i)


def _match(eco: str, target_id: str, label: str, provided: dict[str, dict[str, tuple[str, str]]]):
    """``(provider project id, declared name, matched key)`` for an external import
    target, else None. The longest declaration wins (``acme_core`` beats ``acme``)."""
    table = provided.get(eco) or {}
    if not table:
        return None
    if eco == "js":  # ids are per package root already: ref_<package>
        hit = table.get(target_id)
        return (*hit, target_id) if hit else None
    if eco == "jvm":
        for prefix in _prefixes((label or "").lower(), "."):
            if prefix in table:
                return (*table[prefix], prefix)
        return None
    for prefix in _prefixes(target_id, "_"):  # python module ids, go_pkg_ ids: a module or a sub-module
        if prefix in table:
            return (*table[prefix], prefix)
    return None


def _display(eco: str, target_id: str, label: str, key: str, shown: str) -> str:
    """A readable imported name: the declared name plus the sub-module part."""
    rest = target_id[len(key):].lstrip("_")
    if eco == "python":
        return key + ("." + rest if rest else "")
    if eco == "go":
        return shown + ("/" + rest.replace("_", "/") if rest else "")
    if eco == "js":
        return shown
    return label or target_id


def repos(projects: Iterable[tuple[str, str, "str | Path", Any]], *, evidence: int = 5,
          shared: bool = False, shared_cap: int = 3) -> dict:
    """Repository-level map for ``projects`` — ``(project id, name, root, MapIndex)`` each.

    Nodes: one per repository with size, languages, its three largest communities,
    what it declares and a README description. Links: ``imports`` (A's code imports
    something B declares; weight = import sites, i.e. importing file × imported
    module), ``depends`` (A's manifest lists B's package but no import site was
    found), and with ``shared=True`` a weak ``shared`` kind for repositories that
    declare the same external dependencies (at most ``shared_cap`` per repository).
    """
    items = [(str(pid), str(name), Path(root), idx) for pid, name, root, idx in projects]
    decl = {pid: declarations(root, idx) for pid, _name, root, idx in items}

    # Who provides what. A name two repositories both declare (a fork, a vendored
    # copy) is ambiguous and links to neither rather than to an arbitrary one.
    provided: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    ambiguous: set[tuple[str, str]] = set()
    for pid, *_ in items:
        for eco in ("python", "js", "go", "jvm"):
            for key, shown in decl[pid][eco].items():
                prev = provided[eco].get(key)
                if prev is not None and prev[0] != pid:
                    ambiguous.add((eco, key))
                provided[eco].setdefault(key, (pid, shown))
    for eco, key in ambiguous:
        provided[eco].pop(key, None)

    # ---- products declared in system.yaml -------------------------------------------------------------
    # Only the repository that owns the declaration file knows the product; its siblings each carry their
    # own .cairn and know nothing. So membership is resolved here, across every project the caller passed,
    # by matching declared member roots against roots we already have.
    products: dict[str, dict] = {}
    for pid, _name, root, _idx in items:
        prod = decl[pid].get("product")
        if not prod:
            continue
        entry = products.setdefault(prod["system"], {"system": prod["system"], "members": {}})
        for m in prod["members"]:
            prev = entry["members"].get(m["root"])
            if prev is None or (prev.get("description") is None and m.get("description")):
                entry["members"][m["root"]] = m
    by_root = {str(Path(root).resolve()): pid for pid, _n, root, _i in items}
    product_of: dict[str, dict] = {}
    for entry in products.values():
        for mroot, m in entry["members"].items():
            owner = by_root.get(mroot)
            if owner is None:
                continue
            product_of[owner] = {"system": entry["system"], "description": m.get("description"),
                                 "siblings": sum(1 for r in entry["members"] if r != mroot)}

    nodes = []
    for pid, name, root, idx in items:
        d = decl[pid]
        nodes.append({
            "id": pid,
            "name": name,
            "root": str(root),
            "nodes": len(idx),
            "edges": idx.edge_count(),
            "files": len(idx.by_file),
            "languages": idx.languages(),
            "communities": _top_communities(idx),
            "packages": [{"ecosystem": eco, "name": shown}
                         for eco in ("python", "js", "go", "jvm") for shown in sorted(set(d[eco].values()))],
            "description": readme_description(root),
            "product": product_of.get(pid),
            "built_at_commit": idx.built_at_commit,
        })

    links: dict[tuple[str, str], dict] = {}
    for pid, _name, _root, idx in items:
        for src, edges in idx.out.items():
            src_file = idx.file_of(src)
            eco = _eco(src_file)
            if eco is None:
                continue
            for e in edges:
                if e.rel not in IMPORT_RELS:
                    continue
                tgt = idx.nodes.get(e.other)
                if tgt is None or not _is_external(tgt):
                    continue
                label = tgt.get("label") or e.other
                hit = _match(eco, e.other, label, provided)
                if hit is None or hit[0] == pid:
                    continue
                owner, shown, matched = hit
                link = links.setdefault((pid, owner), {"source": pid, "target": owner, "kind": "imports",
                                                       "weight": 0, "packages": [], "evidence": []})
                link["weight"] += 1
                if shown not in link["packages"]:
                    link["packages"].append(shown)
                if len(link["evidence"]) < evidence:
                    link["evidence"].append({"file": src_file, "line": _line(e.location),
                                             "name": _display(eco, e.other, label, matched, shown),
                                             "package": shown, "node": e.other})

    # Manifest-level dependencies on a sibling's package, without an import site we could see.
    by_name: dict[tuple[str, str], str] = {}
    for eco in ("python", "js", "go", "jvm"):
        for key, (owner, shown) in provided[eco].items():
            by_name.setdefault((eco, shown.lower()), owner)
            if eco in ("python", "jvm"):
                by_name.setdefault((eco, key), owner)
    for pid, *_ in items:
        for eco, deps in decl[pid]["deps"].items():
            for dep in deps:
                owner = by_name.get((eco, dep.lower()))
                if owner is None and eco == "python":
                    owner = by_name.get((eco, make_id(dep)))
                if owner is None and eco == "jvm" and ":" in dep:
                    owner = by_name.get((eco, dep.split(":", 1)[0].lower()))
                if owner is None or owner == pid:
                    continue
                link = links.get((pid, owner))
                if link is not None:
                    link["declared"] = True
                    continue
                links[(pid, owner)] = {"source": pid, "target": owner, "kind": "depends", "weight": 1,
                                       "packages": [dep], "evidence": [], "declared": True}

    # A service mesh has no shared packages, so import-derived edges come back empty and the graph draws
    # a row of islands. What the code *does* show is that two repositories talk to the same external
    # thing — two services on one Kafka topic, three on one PostgreSQL. Those are real, and they are what
    # makes the picture a system instead of a list. Declared relationships in system.yaml win over both.
    if not links:
        links.update(_shared_element_links(items, decl, evidence, product_of))
        links.update(_declared_product_links(items, decl, product_of))

    out_links = sorted(links.values(), key=lambda l: (-l["weight"], l["source"], l["target"]))

    if shared:
        own = {pid: {n.lower() for eco in ("python", "js", "go", "jvm") for n in decl[pid][eco].values()}
               for pid, *_ in items}
        dep_sets = {pid: {d.lower() for deps in decl[pid]["deps"].values() for d in deps} for pid, *_ in items}
        internal = set().union(*own.values()) if own else set()
        pairs = []
        ids = [pid for pid, *_ in items]
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                common = sorted((dep_sets[a] & dep_sets[b]) - internal)
                if len(common) >= 2:
                    pairs.append((len(common), a, b, common))
        per_repo: Counter = Counter()
        for n, a, b, common in sorted(pairs, reverse=True):
            if per_repo[a] >= shared_cap or per_repo[b] >= shared_cap:
                continue
            per_repo[a] += 1
            per_repo[b] += 1
            out_links.append({"source": a, "target": b, "kind": "shared", "weight": n,
                              "packages": common[:evidence], "evidence": []})

    return {"level": "repos", "nodes": nodes, "links": out_links}
