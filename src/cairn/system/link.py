"""Product-level linking at read time (FR-012 to FR-015).

Each repository stores only its own claims. ``link`` combines this repository's model with the models
its ``system.yaml`` siblings stored (opened read-only, never written), applies the declarations, and
links containers by the same rules inside one repository and across repositories:

- ``http-route-match``: an outbound call (method + normalised path) matches an inbound route;
- ``http-config``: the call's base-URL configuration or URL host names a deploy unit (no route needed);
- shared stores, channels and outside services merge into one element (same compose service or host,
  the configuration a sibling's compose file sets, or a single instance of that technology);
- ``package-dep``: a container's dependency on a library a sibling provides (build time);
- ``declared``: actors and relationships from ``system.yaml``.

More than one candidate gives one relationship per candidate marked ``ambiguous`` with every candidate
in ``meta.candidates`` (FR-014); no candidate gives an "Unresolved HTTP target" element, ``inferred``.
Publishers and subscribers are both linked to the channel element; consumer edges carry
``rank_reverse`` so layouts rank them after the channel.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from ..engines import systems
from .model import Element, Evidence, Model, Relationship, clip

UNRESOLVED_ID = "product:external:unresolved-http"
METHOD_VERB = {"POST": "creates", "GET": "reads", "PUT": "updates", "PATCH": "updates", "DELETE": "deletes"}
_GENERIC_SEGMENTS = frozenset({"api", "v1", "v2", "v3", "v4", "rest", "public", "internal", "{}", ""})


def host_key(label: str) -> str:
    return hashlib.sha1(f"host:{label.strip().lower()}".encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------------------------- loading
def load_readonly(db: Path) -> Model | None:
    """A sibling's stored model, read through a read-only connection; None when it has none."""
    try:
        els = systems.read_only_rows(db, "SELECT * FROM sm_element")
        rels = systems.read_only_rows(db, "SELECT * FROM sm_relationship")
        evs = systems.read_only_rows(db, "SELECT * FROM sm_evidence")
    except sqlite3.Error:
        return None
    if not els:
        return None
    by: dict[str, list[Evidence]] = defaultdict(list)
    for r in evs:
        by[r["claim_id"]].append(Evidence(repo=r["repo"], kind=r["kind"], file=r["file"], line=r["line"],
                                          entry=r["entry"], commit=r["commit_confirmed"] or ""))
    m = Model()
    for r in els:
        try:
            m.elements[r["id"]] = Element(id=r["id"], type=r["type"], name=r["name"], level=r["level"], repo=r["repo"],
                                          kind=r["kind"], tech=r["tech"], desc=r["descr"], parent=r["parent"],
                                          provenance=r["provenance"], evidence=by.get(r["id"], []),
                                          stale_reason=r["stale_reason"], stale_since=r["stale_since"],
                                          built_at_commit=r["built_at_commit"] or "", meta=json.loads(r["meta"] or "{}"))
        except (ValueError, TypeError):
            continue
    for r in rels:
        try:
            meta = json.loads(r["meta"] or "{}")
            rel = Relationship(from_id=r["from_id"], to_id=r["to_id"], rule=r["rule"], how=r["how"], what=r["what"],
                               style=r["style"], level=r["level"], repo=r["repo"], provenance=r["provenance"],
                               evidence=by.get(r["id"], []), data_class=r["data_class"],
                               stale_reason=r["stale_reason"], stale_since=r["stale_since"],
                               built_at_commit=r["built_at_commit"] or "",
                               meta={k: v for k, v in meta.items() if k != "key"}, key=meta.get("key", ""))
            m.relationships[rel.id] = rel
        except (ValueError, TypeError):
            continue
    return m


def own_model(root: Path, repo: str, brain=None) -> Model:
    """This repository's model: from its store when it has one, else built now (read-only)."""
    from .model import load
    if brain is not None:
        m = load(brain, repo)
        if m.elements:
            return m
    db = Path(root) / ".cairn" / "brain.db"
    if db.is_file():
        m2 = load_readonly(db)
        if m2 is not None:
            return m2
    from .build import build_repo
    return build_repo(root, repo)


# --------------------------------------------------------------------------------------------- linking
class _Linker:
    def __init__(self, root: Path, repo: str, own: Model):
        self.root, self.repo = Path(root).resolve(), repo
        self.out = Model()
        self.models: dict[str, Model] = {repo: own}
        self.roots: dict[str, Path] = {repo: self.root}
        self.alias: dict[str, str] = {}
        self.product: dict = {"system": repo, "repos": [repo], "skipped": [], "declared_in": None,
                              "declared_skipped": []}

    def ev(self, kind: str, line: int | None, entry: str) -> Evidence:
        decl = self.product.get("declared_in") or "system.yaml"
        rel = Path(decl)
        try:
            rel = rel.relative_to(self.root)
        except ValueError:
            rel = Path(rel.name)
        return Evidence(repo=self.repo, kind=kind, file=rel.as_posix(), line=line, entry=entry)

    # -- 1. gather
    def gather(self, siblings: bool) -> None:
        sysdef = systems.discover(self.root)
        self.decl = systems.declarations(self.root)
        if sysdef is None:
            return
        self.product.update(system=sysdef.name, declared_in=sysdef.declared_in)
        if not siblings:
            return
        for s in sysdef.siblings:
            m = load_readonly(s.db)
            if m is None:
                self.not_mapped(s.name, str(s.root), "not mapped yet", "run `cairn init` there")
                continue
            names = {e.repo for e in m.elements.values() if e.repo}
            name = sorted(names)[0] if names else s.name
            self.models[name] = m
            self.roots[name] = s.root.resolve()
            self.product["repos"].append(name)
        for sk in sysdef.skipped:
            label = sk.get("name") or Path(sk["path"]).name
            if "directory not found" in sk["reason"]:
                self.not_mapped(label, sk.get("root", sk["path"]), "not found", f"{sk['path']} does not exist")
            else:
                self.not_mapped(label, sk.get("root", sk["path"]), "not mapped yet", "run `cairn init` there")

    def not_mapped(self, name: str, path: str, state: str, hint: str) -> None:
        eid = f"{name}:container:{name}"
        line = None
        if self.decl is not None:
            line = (self.decl.repos.get(name) or {}).get("line")
        self.out.add(Element(id=eid, type="container", name=name, repo=name, provenance="inferred",
                             desc=f"{state}: {hint}", tech=state,
                             evidence=[self.ev("declared", line, f"repos: {Path(path).name}")],
                             meta={"not_mapped": state, "command": "cairn init" if state != "not found" else None}))
        self.product["skipped"].append({"repo": name, "state": state})
        self.product["repos"].append(name)

    # -- 2. merge everything into one model
    def combine(self) -> None:
        for m in self.models.values():
            for e in m.elements.values():
                self.out.elements.setdefault(e.id, e)
            for r in m.relationships.values():
                self.out.relationships.setdefault(r.id, r)

    def containers(self) -> list[Element]:
        return [e for e in self.out.elements.values() if e.type == "container" and e.level == "container"]

    def primary(self, repo: str) -> Element | None:
        """A repository's main container: the one named after it, else its only container."""
        cs = [e for e in self.containers() if e.repo == repo and not e.meta.get("not_mapped")]
        named = [e for e in cs if e.name == repo]
        if named:
            return named[0]
        return cs[0] if len(cs) == 1 else None

    # -- 3. deploy references: compose services built from a sibling's directory
    def deploy_refs(self) -> dict[tuple[str, str], str]:
        targets: dict[tuple[str, str], str] = {}
        by_root = {str(p): name for name, p in self.roots.items()}
        for e in [x for x in self.out.elements.values() if x.kind == "deploy-ref"]:
            home = self.roots.get(e.repo)
            build = e.meta.get("build")
            if home is None or not build:
                continue
            try:
                sib_root = str((home / build).resolve())
            except OSError:
                continue
            sib = by_root.get(sib_root)
            if sib is None:
                continue
            main = self.primary(sib)
            if main is not None:
                aliases = set(main.meta.get("aliases") or [])
                aliases.add(e.meta.get("service") or e.name)
                main.meta["aliases"] = sorted(aliases)
                main.add_evidence(*e.evidence[:1])
            for name, tgt in (e.meta.get("config_targets") or {}).items():
                targets[(sib, name)] = tgt
            for name, hk in (e.meta.get("config_host_keys") or {}).items():
                targets.setdefault((sib, name), "hostkey:" + hk)
        return targets

    # -- 4. shared stores, channels and outside services
    def merge_stores(self, cfg_targets: dict) -> None:
        groups: dict[str, list[Element]] = defaultdict(list)
        for e in self.out.elements.values():
            merge = e.meta.get("merge") if e.level == "container" else None
            if merge:
                groups[merge].append(e)
        for key, els in groups.items():
            els.sort(key=lambda x: (not x.meta.get("instance"), x.id))
            if key.startswith("service:"):
                self._fold(els[0], els[1:])
                continue
            instanced = [e for e in els if e.meta.get("instance")]
            by_inst: dict[str, list[Element]] = defaultdict(list)
            for e in instanced:
                by_inst[e.meta["instance"].lower()].append(e)
            canon = {k: v[0] for k, v in by_inst.items()}
            for k, v in by_inst.items():
                self._fold(v[0], v[1:])
            hk = {host_key(k): c for k, c in canon.items()}
            for e in [x for x in els if not x.meta.get("instance")]:
                tgt = None
                for h in e.meta.get("host_keys") or []:
                    tgt = tgt or hk.get(h)
                for c in e.meta.get("configs") or []:
                    t = cfg_targets.get((e.repo, c))
                    if t and t in self.out.elements:
                        tgt = tgt or self.out.elements[t]
                if tgt is None and len(canon) == 1:
                    tgt = next(iter(canon.values()))
                if tgt is None and not canon:
                    # no instance anywhere: one element per technology, the sharing itself is inferred
                    first = next(x for x in els if not x.meta.get("instance"))
                    if first is not e:
                        first.meta = {**first.meta, "shared": "inferred"}
                        self._fold(first, [e])
                    continue
                if tgt is None:
                    e.meta["candidates"] = sorted(c.id for c in canon.values())
                    continue
                self._fold(tgt, [e])

    def _fold(self, canon: Element, others: list[Element]) -> None:
        for o in others:
            if o.id == canon.id:
                continue
            canon.add_evidence(*o.evidence)
            if o.type == "channel" and canon.type == "data-store":
                canon.type = "channel"
                canon.desc = o.desc
            aliases = set(canon.meta.get("aliases") or []) | set(o.meta.get("aliases") or [])
            canon.meta = {**canon.meta, "aliases": sorted(aliases),
                          "repos": sorted(set(canon.meta.get("repos") or [canon.repo]) | {o.repo})}
            self.alias[o.id] = canon.id
            self.out.elements.pop(o.id, None)

    def rewrite(self) -> None:
        rels = list(self.out.relationships.values())
        self.out.relationships = {}
        for r in rels:
            r.from_id = self.alias.get(r.from_id, r.from_id)
            r.to_id = self.alias.get(r.to_id, r.to_id)
            if r.from_id == r.to_id:
                continue
            tgt = self.out.elements.get(r.to_id)
            if tgt is not None and tgt.meta.get("candidates") and r.provenance == "extracted":
                r.provenance = "ambiguous"
                r.meta = {**r.meta, "candidates": tgt.meta["candidates"]}
            self.out.relate(r)

    # -- 5. HTTP
    def link_http(self, cfg_targets: dict) -> None:
        names: dict[str, set[str]] = defaultdict(set)
        for c in self.containers():
            for n in [c.name, *(c.meta.get("aliases") or [])]:
                names[host_key(n)].add(c.id)
        groups: dict[tuple[str, str, str], dict] = {}
        for call in [e for e in self.out.elements.values() if e.kind == "http-call" and e.parent]:
            src = call.parent
            m = call.meta
            if m.get("self_target"):
                continue
            pinned = m.get("target")
            if pinned and pinned not in self.out.elements:
                pinned = self.alias.get(pinned)
            if not pinned and m.get("base"):
                t = cfg_targets.get((call.repo, m["base"]))
                if t and t.startswith("hostkey:"):
                    hits = names.get(t[8:], set()) - {src}
                    t = next(iter(hits)) if len(hits) == 1 else None
                pinned = t if t and t in self.out.elements else None
            if not pinned and m.get("host_key"):
                hits = names.get(m["host_key"], set()) - {src}
                pinned = next(iter(hits)) if len(hits) == 1 else None
            if pinned == src:
                continue
            cands = self._route_candidates(m.get("method"), m.get("path"), src)
            if pinned and pinned in self.out.elements and self.out.elements[pinned].type in (
                    "data-store", "channel", "external-system"):
                # a store reached over HTTP (Elasticsearch, MinIO): the configuration names it directly
                self._add(groups, src, pinned, "http-config", "extracted", call, [], [])
                continue
            if pinned and pinned in self.out.elements and self.out.elements[pinned].type != "container":
                pinned = None
            if pinned:
                matched = [r for r in cands if r[0] == pinned] or self._suffix(m.get("path"), m.get("method"), pinned)
                rule = "http-route-match" if matched else "http-config"
                self._add(groups, src, pinned, rule, "extracted", call, [r[1] for r in matched], [])
            elif len({c for c, _ in cands}) == 1:
                self._add(groups, src, cands[0][0], "http-route-match", "extracted", call, [r[1] for r in cands], [])
            elif cands:
                all_c = sorted({c for c, _ in cands})
                for c in all_c:
                    self._add(groups, src, c, "http-route-match", "ambiguous", call,
                              [r[1] for r in cands if r[0] == c], all_c)
            else:
                self._unresolved(groups, src, call)
        for (src, tgt, prov), g in sorted(groups.items()):
            paths = sorted({f"{c.meta.get('method')} {c.meta.get('path') or '(dynamic URL)'}" for c in g["calls"]})
            how = clip("HTTP · " + _summarise(paths), 36)
            what = _http_what(g["calls"]) if prov != "inferred" else None
            evs = [ev for c in g["calls"] for ev in c.evidence[:2]] + [ev for r in g["routes"] for ev in r.evidence[:1]]
            rule = "http-route-match" if "http-route-match" in g["rules"] else sorted(g["rules"])[0]
            meta = {"calls": len(g["calls"]), "rules": sorted(g["rules"])}
            if g["candidates"]:
                meta["candidates"] = g["candidates"]
            self.out.relate(Relationship(from_id=src, to_id=tgt, rule=rule, how=how, what=what, style="sync",
                                         repo=self.out.elements[src].repo if src in self.out.elements else "",
                                         provenance=prov, evidence=list(dict.fromkeys(evs)), meta=meta,
                                         key=prov))

    def _route_candidates(self, method, path, src) -> list[tuple[str, Element]]:
        if path is None:
            return []
        out = []
        for e in self.out.elements.values():
            if e.kind != "route" or not e.parent or e.parent == src:
                continue
            rm, rp = e.meta.get("method"), e.meta.get("path")
            if rp != path:
                continue
            if method and rm and method != "ANY" and rm != "ANY" and rm != method:
                continue
            out.append((self.alias.get(e.parent, e.parent), e))
        return out

    def _suffix(self, path, method, container) -> list[tuple[str, Element]]:
        """With the target pinned by configuration, a call may omit a prefix the base URL carries."""
        if not path:
            return []
        out = []
        for e in self.out.elements.values():
            if e.kind != "route" or e.parent != container:
                continue
            rp, rm = e.meta.get("path") or "", e.meta.get("method")
            if method and rm and "ANY" not in (method, rm) and rm != method:
                continue
            if rp.endswith(path) and (len(rp) == len(path) or rp[-len(path) - 1] == "/" or path == "/"):
                out.append((container, e))
            elif path.endswith(rp) and rp != "/" and path[-len(rp) - 1] == "/":
                out.append((container, e))
        return out

    def _add(self, groups, src, tgt, rule, prov, call, routes, cands) -> None:
        g = groups.setdefault((src, tgt, prov), {"calls": [], "routes": [], "rules": set(), "candidates": []})
        g["calls"].append(call)
        g["routes"].extend(routes)
        g["rules"].add(rule)
        if cands:
            g["candidates"] = sorted(set(g["candidates"]) | set(cands))

    def _unresolved(self, groups, src, call) -> None:
        if UNRESOLVED_ID not in self.out.elements:
            self.out.add(Element(id=UNRESOLVED_ID, type="external-system", name="Unresolved HTTP target",
                                 desc="Call target could not be decided from code, configuration or routes",
                                 provenance="inferred", evidence=list(call.evidence[:1]),
                                 meta={"unresolved": True, "external": True}))
        else:
            self.out.elements[UNRESOLVED_ID].add_evidence(*call.evidence[:1])
        self._add(groups, src, UNRESOLVED_ID, "http-unresolved", "inferred", call, [], [])

    # -- 6. package dependencies on a sibling library
    def link_packages(self) -> None:
        libs: dict[str, Element] = {}
        for e in self.out.elements.values():
            if e.type == "library":
                for p in e.meta.get("packages") or []:
                    libs[p] = e
        if not libs:
            return
        for c in self.containers():
            deps = c.meta.get("deps") or {}
            for dep, where in sorted(deps.items()):
                lib = libs.get(dep)
                if lib is None or lib.repo == c.repo:
                    continue
                ev = Evidence(repo=c.repo, kind="import" if where.get("line") else "manifest", file=where.get("file"),
                              line=where.get("line"), entry=None if where.get("line") else where.get("entry"))
                self.out.relate(Relationship(from_id=c.id, to_id=lib.id, rule="package-dep", how=clip(f"package {dep}", 36),
                                             style="build", repo=c.repo, evidence=[ev], key=dep))

    # -- 7. declarations
    def apply_declared(self) -> None:
        d = self.decl
        if d is None:
            return
        self.product["declared_skipped"] = list(d.skipped)
        for name, meta in d.repos.items():
            targets = [e for e in self.containers() if e.repo == name]
            main = self.primary(name)
            if meta.get("role") == "library" and main is not None:
                main.type = "library"
                main.kind = None
            if meta.get("kind"):
                for e in ([main] if main is not None else targets[:1]):
                    e.kind = meta["kind"]
                    e.meta = {**e.meta, "kind_inferred": False, "kind_declared": True}
            if meta.get("description"):
                for e in ([main] if main is not None else targets[:1]):
                    e.desc = meta["description"]
                    e.meta = {**e.meta, "desc_declared": True}
        for a in d.actors:
            aid = f"actor:{_slug(a['name'])}"
            ev = self.ev("declared", a.get("line"), f"actors: {a['name']}")
            self.out.add(Element(id=aid, type="person", name=a["name"], level="context", desc=a.get("desc"),
                                 provenance="declared", evidence=[ev]))
            if a.get("uses"):
                tgt = self.resolve_name(a["uses"])
                if tgt is None:
                    self.product["declared_skipped"].append({"entry": f"actors: {a['name']}",
                                                             "reason": f"unknown element {a['uses']!r}"})
                    continue
                self.out.relate(Relationship(from_id=aid, to_id=tgt, rule="declared", what=a.get("what"),
                                             how=a.get("how"), style="sync", provenance="declared", evidence=[ev]))
        for r in d.relationships:
            frm, to = self.resolve_name(r["from"]), self.resolve_name(r["to"])
            ev = self.ev("declared", r.get("line"), f"relationships: {r['from']} -> {r['to']}")
            if frm is None or to is None:
                missing = r["from"] if frm is None else r["to"]
                self.product["declared_skipped"].append({"entry": f"relationships: {r['from']} -> {r['to']}",
                                                         "reason": f"unknown element {missing!r}"})
                continue
            if frm == to:
                self.product["declared_skipped"].append({"entry": f"relationships: {r['from']} -> {r['to']}",
                                                         "reason": "self reference"})
                continue
            existing = [x for x in self.out.relationships.values() if x.from_id == frm and x.to_id == to]
            if existing and (r.get("what") or r.get("how")):
                # declared facts take precedence (FR-012): the label is declared, the link stays extracted
                for x in existing:
                    if r.get("what"):
                        x.what = r["what"]
                        x.meta = {**x.meta, "what_declared": True}
                    if r.get("how") and not x.how:
                        x.how = r["how"]
                    x.add_evidence(ev)
                continue
            self.out.relate(Relationship(from_id=frm, to_id=to, rule="declared", what=r.get("what"), how=r.get("how"),
                                         style=r["style"], provenance="declared", evidence=[ev]))

    def resolve_name(self, name: str) -> str | None:
        n = str(name).strip().lower()
        if n in (x.lower() for x in self.models) or any(s.get("repo", "").lower() == n for s in self.product["skipped"]):
            repo = next((r for r in self.models if r.lower() == n), None)
            main = self.primary(repo) if repo else None
            if main is not None:
                return main.id
            hit = next((e.id for e in self.out.elements.values() if e.repo.lower() == n and e.type in
                        ("container", "library")), None)
            if hit:
                return hit
        for e in self.out.elements.values():
            if e.level not in ("container", "context"):
                continue
            if e.name.lower() == n or n in (a.lower() for a in e.meta.get("aliases") or []) or e.id.lower() == n:
                return e.id
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "actor"


_METHOD_ORDER = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "ANY")


def _summarise(paths: list[str]) -> str:
    """``GET /api/orders/{}`` and ``POST /api/orders`` -> ``GET, POST /api/orders``: the methods and the
    longest common path, within the standard's "how" budget."""
    if len(paths) == 1:
        return paths[0]
    methods = sorted({p.split(" ", 1)[0] for p in paths},
                     key=lambda m: _METHOD_ORDER.index(m) if m in _METHOD_ORDER else 99)
    segs = [p.split(" ", 1)[1].strip("/").split("/") for p in paths]
    common: list[str] = []
    for parts in zip(*segs):
        if len(set(parts)) != 1 or parts[0] == "{}":
            break
        common.append(parts[0])
    return f"{', '.join(methods)} /{'/'.join(common)}"


def _http_what(calls: list[Element]) -> str | None:
    """Creates and reads orders: verbs from the methods, the resource from the path (FR-003 rule)."""
    verbs: list[str] = []
    resources: list[str] = []
    for c in calls:
        verb = METHOD_VERB.get(str(c.meta.get("method")))
        segs = [s for s in str(c.meta.get("path") or "").split("/") if s.lower() not in _GENERIC_SEGMENTS]
        if verb and verb not in verbs:
            verbs.append(verb)
        if segs and segs[0] not in resources:
            resources.append(segs[0])
    if not verbs or not resources or len(resources) > 2:
        return None
    order = ["creates", "reads", "updates", "deletes"]
    verbs.sort(key=order.index)
    phrase = verbs[0] if len(verbs) == 1 else ", ".join(verbs[:-1]) + " and " + verbs[-1]
    out = f"{phrase.capitalize()} {' and '.join(resources)}"
    return out if len(out) <= 32 else None


def link(root: Path, own: Model | None = None, *, repo: str | None = None, brain=None,
         siblings: bool = True) -> Model:
    """The product model for the repository at ``root``: its own claims plus its siblings', linked.

    Read-only for every repository involved. ``model.product`` describes the grouping (system name,
    repositories, the ones not mapped or not found, declarations skipped with their reason).
    """
    root = Path(root).resolve()
    repo = repo or root.name
    own = own if own is not None else own_model(root, repo, brain)
    lk = _Linker(root, repo, own)
    lk.gather(siblings)
    lk.combine()
    targets = lk.deploy_refs()
    lk.merge_stores(targets)
    lk.rewrite()
    lk.link_http(targets)
    lk.link_packages()
    lk.apply_declared()
    lk.out.product = lk.product  # type: ignore[attr-defined]
    return lk.out
