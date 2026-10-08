"""Assemble one repository's system model from deterministic signals (FR-001 to FR-011), and store it.

``build_repo`` is pure and read-only: it scans the repository's files (bounded by ``limits``), reads
deploy descriptions, manifests, configuration names and the parse trees of the files that can matter,
and returns a ``Model``. It makes no model calls and no network access (FR-005, SC-003).

Containers are deploy units, not repositories (FR-006): a compose service built from this repository,
a Dockerfile, a Kubernetes workload, a Procfile process or a package entry point. A monorepo yields
several; a repository with none yields one container marked "entry points not found", or a library
when it only provides a package. Code-level elements carry the facts that linking needs across
repositories: routes, outbound HTTP calls and deploy references (compose services this repository
builds from a sibling's directory). Linking itself happens at read time (``link.py``).

``sync_step`` builds and saves the model for the repository a ``Cairn`` serves; sync runs it after the
map, history, specs and sessions layers, and a failure there never breaks sync.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import catalog as catalog_mod
from .limits import MAX_ELEMENTS, MAX_RELATIONSHIPS
from .model import Element, Evidence, Model, Relationship
from .signals import config as config_mod
from .signals import deploy as deploy_mod
from .signals import entry as entry_mod
from .signals import files as files_mod
from .signals import http as http_mod
from .signals import messaging as messaging_mod
from .signals import routes as routes_mod
from .signals import stores as stores_mod
from .signals import syntax
from .signals.match import RepoIndex, Wrappers, host_of, imports_library, qualify

LANG_DISPLAY = {"python": "Python", "javascript": "Node.js", "java": "Java", "go": "Go", "csharp": ".NET",
                "shell": "Shell"}
WEB_DIRS = ("templates", "static", "public", "views", "wwwroot", "assets")
WEB_DEPS = frozenset({"react", "react-dom", "vue", "svelte", "@angular/core", "next", "nuxt", "socket.io",
                      "ejs", "pug", "handlebars", "express-handlebars", "spring-boot-starter-thymeleaf", "jinja2"})
WEB_CALLS = frozenset({"render_template", "render", "sendFile", "static", "StaticFiles", "send_from_directory",
                       "TemplateResponse", "render_to_response"})
WEB_WORDS = re.compile(r"\b(web ?app|front[- ]?end|storefront|website|web ui|user interface|dashboard)\b", re.I)
VERB_WHAT = {"publish": "Publishes", "subscribe": "Subscribes to", "push": "Queues", "pop": "Consumes"}
CONSUMER_OPS = frozenset({"subscribe", "pop"})
MINIFIED_LINE = 2000
GENERIC_NAMES = frozenset({"get", "set", "update", "post", "put", "delete", "send", "run", "call", "query", "execute",
                           "request", "fetch", "open", "close", "read", "write", "save", "load", "main", "handle",
                           "process", "create", "start", "stop", "init", "setup", "connect", "emit"})


WHAT_CHARS, HOW_CHARS = 32, 36   # label budgets of the diagram standard (tokens.json budget)


def fit(prefix: str, items: list[str], limit: int = WHAT_CHARS, noun: str = "items") -> str:
    """``prefix`` and as many ``items`` as fit in ``limit`` characters, then ``(+N)``: labels stay inside the
    standard's budget without dropping their meaning; when not even one fits, the count of ``noun``."""
    if items and len(f"{prefix} {items[0]}" + (f" (+{len(items) - 1})" if len(items) > 1 else "")) > limit:
        return f"{prefix} {len(items)} {noun}" if len(items) > 1 else f"{prefix} {items[0]}"[: limit - 1] + "…"
    out = f"{prefix} {items[0]}" if items else prefix
    shown = 1 if items else 0
    for it in items[1:]:
        rest = len(items) - shown - 1
        cand = f"{out}, {it}"
        if len(cand) + (len(f" (+{rest})") if rest else 0) > limit:
            break
        out, shown = cand, shown + 1
    if shown < len(items):
        out = f"{out} (+{len(items) - shown})"
    return out if len(out) <= limit else out[: limit - 1] + "…"


def host_key(host: str) -> str:
    """A one-way key for a host's first DNS label, so siblings can be matched without storing the host."""
    label = host.strip().lower().split(".")[0].split(":")[0]
    return hashlib.sha1(f"host:{label}".encode("utf-8")).hexdigest()[:16]


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._\-]+", "-", str(text).strip()).strip("-").lower()
    return s or "unit"


def _ancestor(root: str, path: str) -> bool:
    return root == "" or path == root or path.startswith(root + "/")


# ------------------------------------------------------------------------------------------------ parse
def parse_repo(files: files_mod.RepoFiles, cat: catalog_mod.Catalog) -> dict[str, syntax.FileFacts]:
    """Parse the code files whose text mentions a catalog trigger; then the files that call a helper
    which passes its parameters on to a call (wrapper callers), so helpers resolve across files."""
    facts: dict[str, syntax.FileFacts] = {}
    texts: dict[str, str] = {}
    convention = _file_route_globs(cat)
    for rel in files.paths:
        lang = syntax.lang_of(rel)
        if lang is None or files_mod.is_test_path(rel) or ".min." in rel:
            continue
        text = files.read(rel)
        if not text or _minified(text):
            continue
        if lang == "bash" or cat.triggers(syntax.FAMILY[lang]).search(text) or _entry_like(lang, rel, text) or any(
                routes_mod.route_glob(rel, g) for g in convention):
            facts[rel] = syntax.parse(rel, text, lang)
        else:
            texts[rel] = text
    # callers of generic names (``update``, ``__init__``) are everywhere: they are resolved among the files
    # already parsed, but do not pull every other file into the parse
    names = {n for n in _wrapper_names(facts, cat) if not n.startswith("__") and n not in GENERIC_NAMES}
    if names and texts:
        rx = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in sorted(names)) + r")\s*\(")
        for rel, text in texts.items():
            if rx.search(text):
                facts[rel] = syntax.parse(rel, text)
    return dict(sorted(facts.items()))


def _file_route_globs(cat) -> list[str]:
    """Files whose path alone makes them routes (Next.js handlers, Django ``urls.py``): always parsed."""
    return [g for fw in cat.frameworks for r in fw.get("routes") or [] if r.get("kind") == "file-route"
            for g in r.get("files") or []]


def _entry_like(lang: str, rel: str, text: str) -> bool:
    """A program entry point: Go ``package main``, a Java or C# ``main``, a Python ``__main__.py``."""
    if lang == "go":
        return "package main" in text
    if lang == "java":
        return "static void main" in text
    if lang == "csharp":
        return " Main(" in text
    return lang == "python" and rel.endswith("__main__.py")


def _minified(text: str) -> bool:
    lines = text.count("\n") + 1
    return len(text) / lines > 400 or max((len(x) for x in text[:200_000].splitlines()), default=0) > MINIFIED_LINE * 5


def _wrapper_names(facts, cat) -> set[str]:
    """First-party functions that pass a parameter straight into a call on a catalog library (a client
    typed by its import, or a global such as ``fetch``): their callers may hold the literal topic or URL."""
    libs: set[str] = set()
    for section in (cat.http, cat.messaging, cat.stores, cat.services):
        for rule in section:
            libs.update(str(x) for x in rule.get("library") or [])
    libs_t = tuple(sorted(libs))
    out: set[str] = set()
    for ff in facts.values():
        for c in ff.calls:
            if not c.func or len(c.func) < 4:
                continue
            params = ff.funcs.get(c.func, ([],))[0]
            if not any(a.kind == "name" and a.text in params for a in c.args):
                continue
            q = qualify(ff, c.callee)
            if q.startswith(libs_t) or q in ("fetch", "axios"):
                out.add(c.func)
    return out


# ------------------------------------------------------------------------------------------------ units
@dataclass
class Unit:
    key: str
    name: str
    root: str
    primary: bool = True
    aliases: set = field(default_factory=set)
    evidence: list = field(default_factory=list)
    deploys: list = field(default_factory=list)
    dockerfile: deploy_mod.Dockerfile | None = None
    image_only: bool = False
    image: str | None = None
    job: bool = False
    ports: list = field(default_factory=list)
    env_names: dict = field(default_factory=dict)     # name -> Evidence
    env_values: dict = field(default_factory=dict)    # transient
    command: str | None = None
    manifests: list = field(default_factory=list)
    entries: list = field(default_factory=list)
    replicas: int | None = None

    @property
    def names(self) -> set[str]:
        return {self.name.lower(), *(a.lower() for a in self.aliases)}


@dataclass
class StoreInfo:
    key: str
    tech: str
    aliases: set = field(default_factory=set)
    evidence: list = field(default_factory=list)
    from_deploy: bool = False
    host_keys: set = field(default_factory=set)
    configs: set = field(default_factory=set)
    messaging: bool = False


class Builder:
    def __init__(self, root: Path, repo: str, commit: str, cat: catalog_mod.Catalog):
        self.root, self.repo, self.commit, self.cat = Path(root), repo, commit, cat
        self.stats: Counter = Counter()

    # -- evidence
    def ev(self, kind: str, file: str | None = None, line: int | None = None, entry: str | None = None) -> Evidence:
        return Evidence(repo=self.repo, kind=kind, file=file, line=line, entry=entry, commit=self.commit)

    def run(self) -> Model:
        t0 = time.time()
        self.files = files_mod.scan(self.root)
        self.deploy = deploy_mod.scan(self.files)
        self.manifests = entry_mod.scan(self.files)
        self.values = config_mod.scan(self.files)
        self.facts = parse_repo(self.files, self.cat)
        self.idx = RepoIndex(self.facts)
        self.wrappers = Wrappers(self.facts)
        self.units: list[Unit] = []
        self.refs: list[deploy_mod.Unit] = []
        self.stores: dict[str, StoreInfo] = {}
        self.assemble_units()
        self.model = Model()
        self.emit()
        self.stats["files"] = len(self.files.paths)
        self.stats["parsed"] = len(self.facts)
        for k, v in self.files.skipped.items():
            self.stats["skipped: " + k] += v
        self.model.stats = dict(self.stats, seconds=round(time.time() - t0, 3))  # type: ignore[attr-defined]
        return self.model

    # -- units ------------------------------------------------------------------------------------------
    def find(self, name: str) -> Unit | None:
        n = name.lower()
        return next((u for u in self.units if n in u.names), None)

    def at_root(self, root: str) -> Unit | None:
        return next((u for u in self.units if u.root == root and u.primary), None)

    def new_unit(self, name: str, root: str, *, primary: bool = True) -> Unit:
        display = self.repo if root == "" and primary and not self.at_root("") else name
        key = slug(display)
        taken = {u.key for u in self.units}
        base, i = key, 2
        while key in taken:
            key, i = f"{base}-{i}", i + 1
        u = Unit(key=key, name=display, root=root, primary=primary)
        if name and name != display:
            u.aliases.add(name)
        self.units.append(u)
        return u

    def owner(self, path: str) -> Unit | None:
        best = None
        for u in self.units:
            if u.primary and not u.image_only and _ancestor(u.root, path):
                if best is None or len(u.root) > len(best.root):
                    best = u
        if best is None:
            runnable = [u for u in self.units if u.primary and not u.image_only]
            if len(runnable) == 1:
                best = runnable[0]
        return best

    def assemble_units(self) -> None:
        cat = self.cat
        # 1. compose services and Procfile processes built from this repository
        for d in self.deploy.units:
            if d.source not in ("compose", "procfile") or d.build is None:
                continue
            if d.build.startswith(".."):
                self.refs.append(d)
                continue
            u = next((x for x in self.units if x.root == d.build and d.name.lower() in x.names), None)
            if u is None:
                same_root = self.at_root(d.build)
                u = self.new_unit(d.name, d.build, primary=same_root is None)
            self._attach_deploy(u, d)
        # 2. Dockerfiles
        for df in self.deploy.dockerfiles:
            u = next((x for x in self.units for d in x.deploys if getattr(d, "dockerfile", None) == df.file), None)
            u = u or self.at_root(df.dir)
            if u is None:
                u = self.new_unit(PurePosixPath(df.dir).name or self.repo, df.dir)
            if u.dockerfile is None:
                u.dockerfile = df
            if df.from_line:
                u.evidence.append(self.ev("dockerfile", df.file, df.from_line, f"FROM {df.base}" if df.base else None))
            for port, line in df.expose[:2]:
                u.ports.append(port)
                u.evidence.append(self.ev("dockerfile", df.file, line, f"EXPOSE {port}"))
            if df.cmd and df.cmd[0]:
                u.command = u.command or df.cmd[0]
                u.evidence.append(self.ev("dockerfile", df.file, df.cmd[1], f"CMD {df.cmd[0]}"))
            for name in df.env_names:
                u.env_names.setdefault(name, self.ev("config", df.file, df.from_line, "ENV"))
        # 3. image-only services and Kubernetes workloads
        for d in self.deploy.units:
            if not ((d.source == "compose" and d.build is None) or d.source == "k8s"):
                continue
            tech = cat.image_tech(d.image)
            u = self.find(d.name) or next((self.find(a) for a in d.aliases if self.find(a)), None)
            if u is None and d.image and tech is None:
                u = self._by_image(d.image)
            if tech is not None and (u is None or u.image_only):
                self._store_from_deploy(d, tech)
                continue
            if u is None:
                u = self.new_unit(d.name, f"<image:{d.name}>", primary=True)
                u.image_only, u.image = True, d.image
            self._attach_deploy(u, d)
        # 4. entry points
        for m in self.manifests:
            if not m.entries:
                continue
            u = self._deepest(m.dir) or self._deploys_package(m)
            if u is None:
                u = self.new_unit(m.name or PurePosixPath(m.dir).name or self.repo, m.dir)
            u.manifests.append(m)
            for label, line in m.entries:
                u.entries.append(label)
                u.evidence.append(self.ev("manifest", m.file, line, label))
        for path, ff in self.facts.items():
            if ff.family == "python":
                if PurePosixPath(path).name != "__main__.py":
                    continue
            elif not ff.is_main:
                continue
            if ff.family == "java" and not any(d.name == "SpringBootApplication" for d in ff.decos) \
                    and self._deepest(PurePosixPath(path).parent.as_posix()) is None and self.units:
                continue
            d = str(PurePosixPath(path).parent)
            d = "" if d == "." else d
            u = self._deepest(d)
            if u is None:
                root = d if ff.family == "go" else self._manifest_dir(d)
                u = self.new_unit(PurePosixPath(root).name or self.repo, root)
            line = ff.funcs.get("main", ff.funcs.get("Main", ([], 1)))[1]
            u.entries.append("main")
            u.evidence.append(self.ev("entry", path, line, None))
        # manifests (dependencies) per unit
        for u in self.units:
            if u.image_only:
                continue
            for m in self.manifests:
                if m in u.manifests:
                    continue
                inside = _ancestor(u.root, m.dir) and self._deepest(m.dir) is u
                above = _ancestor(m.dir, u.root) and m.dir != u.root
                if inside or above:
                    u.manifests.append(m)

    def _deploys_package(self, m: entry_mod.Manifest) -> Unit | None:
        """A deploy unit with no code of its own whose start command is one of this package's commands
        (``ENTRYPOINT ["cairn", "serve"]`` for a package installing ``cairn``) deploys that package: its
        code root becomes the package's directory."""
        if not m.scripts:
            return None
        for u in self.units:
            exe = (u.command or "").split(" ")[0].rsplit("/", 1)[-1]
            if u.image_only or exe not in m.scripts:
                continue
            if any(syntax.lang_of(p) and _ancestor(u.root, p) for p in self.files.paths if u.root):
                continue
            u.root = m.dir
            return u
        return None

    def _deepest(self, d: str) -> Unit | None:
        best = None
        for u in self.units:
            if not u.image_only and _ancestor(u.root, d) and (best is None or len(u.root) > len(best.root)):
                best = u
        return best

    def _manifest_dir(self, d: str) -> str:
        dirs = sorted({m.dir for m in self.manifests if _ancestor(m.dir, d)}, key=len, reverse=True)
        return dirs[0] if dirs else d

    def _by_image(self, image: str) -> Unit | None:
        base = image.rsplit("/", 1)[-1].split(":", 1)[0].lower()
        for u in self.units:
            for n in u.names:
                if base == n or base.endswith(("_" + n, "-" + n)):
                    return u
        return None

    def _attach_deploy(self, u: Unit, d: deploy_mod.Unit) -> None:
        u.deploys.append(d)
        u.aliases.update(a for a in (d.name, *d.aliases) if a != u.name)
        what = {"compose": f"services.{d.name}", "procfile": f"Procfile {d.name}"}.get(d.source, d.entry)
        detail = f" build {d.build or '.'}" if d.build is not None and d.source == "compose" else (
            f" image {d.image}" if d.image else "")
        u.evidence.append(self.ev("deploy", d.file, d.line, what + detail))
        u.ports.extend(d.ports)
        for n in d.env_names:
            u.env_names.setdefault(n, self.ev("config", d.file, d.line, f"{what} environment"))
        for k, v in d.env_values.items():
            u.env_values.setdefault(k, v)
        u.command = u.command or d.command
        u.job = u.job or d.job
        u.replicas = u.replicas or d.replicas

    def _store_from_deploy(self, d: deploy_mod.Unit, tech: str) -> None:
        key = slug(d.name)
        s = self.stores.get(key)
        if s is None:
            s = self.stores[key] = StoreInfo(key=key, tech=tech, from_deploy=True)
        s.aliases.add(d.name)
        s.aliases.update(d.aliases)
        what = f"services.{d.name}" if d.source == "compose" else d.entry
        s.evidence.append(self.ev("image", d.file, d.line, f"{what} image {d.image}"))

    # -- dependencies, kinds, labels --------------------------------------------------------------------
    def deps(self, u: Unit) -> set[str]:
        return {entry_mod.norm_dep(x) for m in u.manifests for x in m.deps}

    def deps_for(self, path: str) -> set[str]:
        u = self.owner(path)
        return self.deps(u) if u is not None else set()

    # -- resolution ---------------------------------------------------------------------------------------
    def value(self, u: Unit | None, name: str) -> str | None:
        if u is not None and name in u.env_values:
            return u.env_values[name]
        return self.values.lookup(u.root if u is not None and not u.image_only else "", name)

    def resolve_host(self, host: str | None) -> tuple[str | None, str | None]:
        """(local element id, host key) for a host: a deploy unit or store of this repository, else a key."""
        if not host:
            return None, None
        label = host.strip().lower().split(".")[0]
        if label in http_mod.LOCAL_HOSTS or host.lower() in http_mod.LOCAL_HOSTS:
            return None, None
        for u in self.units:
            if label in u.names:
                return self.uid(u), None
        for s in self.stores.values():
            if label in {a.lower() for a in s.aliases} or label == s.key:
                return self.sid(s), None
        return None, host_key(label)

    def target_of(self, u: Unit | None, val) -> tuple[str | None, str | None, str | None]:
        """(scheme, host, config name) for a connect target value (literal or configuration)."""
        if val is None:
            return None, None, None
        v = val
        if v.kind == "str":
            scheme, host = host_of(v.text)
            return scheme, host, None
        if v.kind in ("env", "config"):
            scheme, host = host_of(self.value(u, v.text))
            return scheme, host, v.text
        if v.kind == "tmpl":
            lits = "".join(p[1] for p in v.parts if p[0] == "lit")
            scheme, host = host_of(lits) if "://" in lits else (None, None)
            cfg = next((p[1] for p in v.parts if p[0] in ("env", "config")), None)
            return scheme, host, cfg
        if v.kind == "obj":
            for k in ("connectionString", "url", "uri", "host", "Addr"):
                if k in v.fields:
                    return self.target_of(u, v.fields[k])
        return None, None, None

    def store_for(self, u: Unit | None, tech: str, target, *, messaging: bool = False,
                  scheme_hint: str | None = None, config_keys=()) -> StoreInfo:
        scheme, host, cfg = self.target_of(u, target)
        for k in config_keys or ():
            val = self.value(u, k)
            if val:
                s2, h2 = host_of(val)
                scheme, host = scheme or s2, host or h2
                cfg = cfg or k
        if tech == "sql":
            tech = self.cat.scheme_tech(scheme or scheme_hint) or tech
        label = host.strip().lower().split(".")[0] if host else None
        if label and label not in http_mod.LOCAL_HOSTS:
            for s in self.stores.values():
                if label in {a.lower() for a in s.aliases}:
                    s.messaging |= messaging
                    return s
        same = [s for s in self.stores.values() if s.tech == tech]
        if len(same) == 1:
            same[0].messaging |= messaging
            if cfg:
                same[0].configs.add(cfg)
            return same[0]
        key = slug(tech) if not same else slug(f"{tech}-{label or cfg or len(same)}")
        s = self.stores.get(key)
        if s is None:
            s = self.stores[key] = StoreInfo(key=key, tech=tech)
        if label and label not in http_mod.LOCAL_HOSTS:
            s.host_keys.add(host_key(label))
        if cfg:
            s.configs.add(cfg)
        s.messaging |= messaging
        return s

    # -- ids ----------------------------------------------------------------------------------------------
    def uid(self, u: Unit) -> str:
        return f"{self.repo}:container:{u.key}"

    def stype(self, s: StoreInfo) -> str:
        t = self.cat.tech(s.tech)
        if s.messaging and (t.get("type") == "channel" or t.get("channel_when_messaging")):
            return "channel"
        return t.get("type") or "data-store"

    def sid(self, s: StoreInfo) -> str:
        """The store's element id; ``final`` is False while uses are still being collected (the type of a
        Redis instance, cache or channel, is only known once every use is)."""
        if not getattr(self, "final", False):
            return f"\0store:{s.key}"
        return f"{self.repo}:{self.stype(s)}:{s.key}"

    # -- emit ---------------------------------------------------------------------------------------------
    def emit(self) -> None:
        cat = self.cat
        self.final = False
        if not self.units and self._no_units():
            return
        routes = routes_mod.extract(self.facts, cat, self.idx, self.deps_for)
        calls = http_mod.extract(self.facts, cat, self.idx, self.wrappers)
        ops, conns = messaging_mod.extract(self.facts, cat, self.idx, self.wrappers)
        uses, services = stores_mod.extract(self.facts, cat, self.idx, self.files)
        by_unit: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for kind, items in (("route", routes), ("call", calls), ("op", ops), ("conn", conns), ("use", uses),
                            ("service", services)):
            for it in items:
                u = self.owner(it.file)
                if u is None:
                    self.stats[f"unattributed {kind}s"] += 1
                    continue
                by_unit[u.key][kind].append(it)
        # stores used by messaging ops and connects (types are final once every use is known)
        rels: list[Relationship] = []
        for u in self.units:
            sig = by_unit.get(u.key, {})
            rels += self._unit_messaging(u, sig)
            rels += self._unit_stores(u, sig)
            rels += self._unit_services(u, sig)
        self.final = True
        placeholder = {f"\0store:{s.key}": self.sid(s) for s in self.stores.values()}
        for r in rels:
            r.to_id = placeholder.get(r.to_id, r.to_id)
        for u in sorted(self.units, key=lambda x: (x.root, x.name)):
            self.model.add(self._container(u, by_unit.get(u.key, {})))
        for s in sorted(self.stores.values(), key=lambda x: x.key):
            self.model.add(self._store(s))
        for r in rels:
            if len(self.model.relationships) < MAX_RELATIONSHIPS:
                self.model.relate(r)
        for u in self.units:
            sig = by_unit.get(u.key, {})
            self._code_elements(u, sig)
        self._deploy_refs()

    def _no_units(self) -> bool:
        """No deploy unit or entry point: a library when the repository provides a package (True: nothing
        else to emit), else one container marked "entry points not found"."""
        provided = [m for m in self.manifests if m.provides]
        langs = Counter(syntax.FAMILY.get(syntax.lang_of(p) or "", "") for p in self.files.paths
                        if syntax.lang_of(p) and not files_mod.is_test_path(p))
        lang = next((lang for lang, _ in langs.most_common() if lang), None)
        if provided:
            m = provided[0]
            name = m.name or self.repo
            pkgs = sorted({entry_mod.norm_dep(x.name) for x in provided if x.name} | self._python_packages())
            el = Element(id=f"{self.repo}:library:{slug(name)}", type="library", name=name, repo=self.repo,
                         tech=f"{LANG_DISPLAY.get(m.kind, m.kind.title())} package",
                         desc=m.description or "Shared code used at build time", built_at_commit=self.commit,
                         evidence=[self.ev("manifest", m.file, 1, f"package {name}")],
                         meta={"packages": pkgs, "root": m.dir})
            self.model.add(el)
            self.stats["library"] = 1
            return True
        u = self.new_unit(self.repo, "")
        u.entries.append("none")
        u.evidence.append(self.ev("manifest", None, None, f"{self.repo}: no deploy file or entry point found"))
        self._fallback_lang = lang
        return False

    def _python_packages(self) -> set[str]:
        out = set()
        for p in self.files.paths:
            parts = PurePosixPath(p).parts
            if parts[-1] == "__init__.py" and (len(parts) == 2 or (len(parts) == 3 and parts[0] == "src")):
                out.add(entry_mod.norm_dep(parts[-2]))
        return out

    def _language(self, u: Unit) -> str | None:
        counts: Counter = Counter()
        for p in self.files.paths:
            lang = syntax.lang_of(p)
            if lang and not files_mod.is_test_path(p) and self.owner(p) is u:
                counts[syntax.FAMILY[lang]] += 1
        cmd = (u.command or "").split(" ")[0]
        if cmd.endswith(".sh") and counts.get("shell"):
            return "shell"
        if u.dockerfile and u.dockerfile.base:
            base = u.dockerfile.base.lower()
            for key, fam in (("dotnet", "csharp"), ("node", "javascript"), ("python", "python"), ("golang", "go"),
                             ("openjdk", "java"), ("temurin", "java"), ("maven", "java"), ("gradle", "java")):
                if key in base and counts.get(fam):
                    return fam
        if counts:
            return counts.most_common(1)[0][0]
        return getattr(self, "_fallback_lang", None)

    def _container(self, u: Unit, sig) -> Element:
        routes, ops = sig.get("route", []), sig.get("op", [])
        frameworks = sorted({r.framework for r in routes})
        lang = self._language(u)
        tech_parts = [LANG_DISPLAY.get(lang, lang.title()) if lang else None, *frameworks]
        if u.image_only and u.image:
            tech_parts = [u.image.rsplit("/", 1)[-1].split(":", 1)[0]]
        consumers = [o for o in ops if o.op in CONSUMER_OPS]
        kind = self._kind(u, routes, consumers, sig)
        desc = self._desc(u, routes, consumers, ops)
        provenance = "inferred" if u.image_only or "none" in u.entries else "extracted"
        evidence = list(dict.fromkeys(u.evidence))
        if not evidence:
            evidence = [self.ev("manifest", None, None, f"{self.repo}: no deploy file or entry point")]
        meta = {"root": u.root if not u.image_only else None, "aliases": sorted(u.aliases),
                "kind_inferred": True, "ports": sorted(set(u.ports))[:8],
                "deps": self._deps_meta(u), "config": self._config_meta(u, sig),
                "frameworks": frameworks}
        if u.replicas:
            meta["count"] = u.replicas
        if "none" in u.entries:
            meta["entry_points_not_found"] = True
        if not u.primary:
            meta["shares_code_with"] = self.uid(self.at_root(u.root)) if self.at_root(u.root) else None
        return Element(id=self.uid(u), type="container", name=u.name, repo=self.repo, kind=kind,
                       tech=" · ".join(x for x in tech_parts if x) or "unknown", desc=desc, provenance=provenance,
                       evidence=evidence, built_at_commit=self.commit, meta=meta)

    def _kind(self, u: Unit, routes, consumers, sig) -> str | None:
        if "none" in u.entries:
            return None
        if u.job or any(d.job for d in u.deploys):
            return "job"
        if routes:
            return "web-app" if self._web(u, sig) else "service"
        if consumers:
            return "worker"
        if (u.command or "").endswith(".sh"):
            return "job"
        if any(e.startswith(("[project.scripts]", "[tool.poetry", "bin")) for e in u.entries):
            return "cli"
        return "service" if u.ports else ("worker" if sig.get("op") else None)

    def _web(self, u: Unit, sig) -> bool:
        for p in self.files.paths:
            parts = PurePosixPath(p).parts
            if self.owner(p) is u and any(d in WEB_DIRS for d in parts[:-1]) and p.endswith(
                    (".html", ".htm", ".css", ".ejs", ".hbs", ".pug", ".jinja", ".j2", ".vue", ".jsx", ".tsx")):
                return True
        if self.deps(u) & WEB_DEPS:
            return True
        for path, ff in self.facts.items():
            if self.owner(path) is u and any(c.name in WEB_CALLS for c in ff.calls):
                return True
        return any(m.description and WEB_WORDS.search(m.description) for m in u.manifests)

    def _desc(self, u: Unit, routes, consumers, ops) -> str | None:
        for m in u.manifests:
            if m.description and m.dir == u.root:
                return m.description
        if routes:
            rs = sorted({f"{r.method} {r.path}" for r in routes})
            return "Serves " + ", ".join(rs[:2]) + (f" (+{len(rs) - 2})" if len(rs) > 2 else "")
        if consumers:
            keys = sorted({o.key for o in consumers if o.key})
            return ("Consumes " + ", ".join(keys[:3])) if keys else "Consumes messages"
        if u.image_only and u.image:
            return f"Runs image {u.image.rsplit('/', 1)[-1]}"
        if "none" in u.entries:
            return "entry points not found"
        if u.command:
            return f"Runs {u.command}"
        if u.entries:
            return f"Started by {u.entries[0]}"
        return f"Deploy unit {u.name}"

    def _deps_meta(self, u: Unit) -> dict:
        """dependency (normalised) -> evidence: the first import in the unit's code, else the manifest."""
        out: dict[str, dict] = {}
        for m in u.manifests:
            for d in m.deps:
                out.setdefault(entry_mod.norm_dep(d), {"file": m.file, "entry": f"dependency {d}"})
                if len(out) >= 400:
                    break
        for path, ff in self.facts.items():
            if self.owner(path) is not u:
                continue
            for origin in ff.imports.values():
                token = _import_root(origin, ff.family)
                key = entry_mod.norm_dep(token)
                if token and key in out and "line" not in out[key]:
                    text = self.files.read(path) or ""
                    m = re.search(r"\b" + re.escape(token) + r"\b", text)
                    if m:
                        out[key] = {"file": path, "line": text.count("\n", 0, m.start()) + 1}
        return out

    def _config_meta(self, u: Unit, sig) -> list:
        """Configuration names this unit reads or is given; sensitive names as their kind only."""
        seen: dict[str, dict] = {}
        for path, ff in self.facts.items():
            if self.owner(path) is not u:
                continue
            for line, name in sorted(ff.env_reads):
                d = config_mod.describe(name, self.cat)
                k = d.get("name") or d["kind"]
                seen.setdefault(k, {**d, "at": []})
                if len(seen[k]["at"]) < 3:
                    seen[k]["at"].append(f"{path}:{line}")
        for name, ev in sorted(u.env_names.items()):
            d = config_mod.describe(name, self.cat)
            k = d.get("name") or d["kind"]
            seen.setdefault(k, {**d, "at": []})
            if len(seen[k]["at"]) < 3:
                seen[k]["at"].append(ev.ref().split("/", 1)[-1] if ev.file else ev.ref())
        return [seen[k] for k in sorted(seen)][:200]

    # -- relationships per unit ------------------------------------------------------------------------
    def _unit_messaging(self, u: Unit, sig) -> list[Relationship]:
        rels = []
        conns_by_tech: dict[str, list] = defaultdict(list)
        for c in sig.get("conn", []):
            conns_by_tech[c.tech].append(c)
        groups: dict[tuple, list] = defaultdict(list)
        for o in sig.get("op", []):
            target = conns_by_tech[o.tech][0].target if conns_by_tech.get(o.tech) else None
            s = self.store_for(u, o.tech, target, messaging=True)
            groups[(s.key, o.op)].append(o)
        used = {k[0] for k in groups}
        for (skey, op), items in sorted(groups.items()):
            s = self.stores[skey]
            keys = sorted({o.key for o in items if o.key})
            cfgs = sorted({config_mod.describe(o.key_config, self.cat).get("name") or "a configured name"
                           for o in items if o.key_config})
            what = fit(VERB_WHAT[op], keys, noun="topics" if op in ("publish", "subscribe") else "queues") \
                if keys else None
            how = items[0].how + (f" · {', '.join(cfgs[:2])}" if cfgs and not keys else "")
            evs = []
            for o in items:
                evs.append(self.ev(o.op, o.file, o.line))
                for f, ln in o.via[:2]:
                    evs.append(self.ev("call", f, ln))
            s.evidence.extend(evs[:2])
            rule = "queue-key" if op in ("push", "pop") else "pubsub-topic"
            rels.append(Relationship(from_id=self.uid(u), to_id=self.sid(s), rule=rule, how=how, what=what,
                                     style="async", repo=self.repo, evidence=list(dict.fromkeys(evs)),
                                     built_at_commit=self.commit, key=op,
                                     meta={"op": op, "keys": keys, "consumer": op in CONSUMER_OPS,
                                           "rank_reverse": op in CONSUMER_OPS}))
        for tech, conns in conns_by_tech.items():
            s = self.store_for(u, tech, conns[0].target)
            if s.key in used:
                continue
            evs = [self.ev("driver", c.file, c.line) for c in conns[:5]]
            s.evidence.extend(evs[:2])
            rels.append(Relationship(from_id=self.uid(u), to_id=self.sid(s), rule="store-use", how=conns[0].how,
                                     style="sync", repo=self.repo, built_at_commit=self.commit, evidence=evs))
        return rels

    def _unit_stores(self, u: Unit, sig) -> list[Relationship]:
        rels = []
        uses = sig.get("use", [])
        if not uses:
            return rels
        by_store: dict[str, list] = defaultdict(list)
        for use in uses:
            tech = self.cat.drivers.get(str(use.driver or "").lower()) or use.tech
            s = self.store_for(u, tech, use.target, scheme_hint=use.scheme_hint, config_keys=use.config_keys)
            by_store[s.key].append(use)
        sql_stores = [k for k in by_store if self.cat.tech(self.stores[k].tech).get("how") == "SQL"]
        libs = {r.get("client"): r.get("library") or [] for r in self.cat.stores}
        sql: dict[str, list] = defaultdict(list)
        for path, ff in self.facts.items():
            if self.owner(path) is not u or not ff.sql:
                continue
            # literal SQL belongs to the store used in the same file, else to the stores whose driver the
            # file imports (a repository module beside the connect call); never to an unrelated store
            here = [k for k in sql_stores if any(x.file == path for x in by_store[k])]
            targets = here or [k for k in sql_stores if any(
                imports_library(ff, libs.get(x.client)) for x in by_store[k] if libs.get(x.client))]
            for line, text in ff.sql:
                for verb, table in stores_mod.sql_ops(text):
                    for k in targets:
                        sql[k].append((verb, table, path, line))
        for key, items in sorted(by_store.items()):
            s = self.stores[key]
            ops = sql.get(key, [])
            verbs = sorted({v for v, *_ in ops}, key=lambda v: 0 if v == "reads" else 1)
            tables = sorted({t for _, t, *_ in ops})
            what = fit(" and ".join(verbs).capitalize(), tables, noun="tables") if verbs and tables else None
            evs = [self.ev("driver", x.file, x.line) for x in items[:5]]
            s.evidence.extend(evs[:2])
            seen_t: set = set()
            for verb, table, path, line in ops:
                if (verb, table) not in seen_t:
                    seen_t.add((verb, table))
                    evs.append(self.ev("query", path, line))
            rels.append(Relationship(from_id=self.uid(u), to_id=self.sid(s), rule="store-use", how=items[0].how,
                                     what=what, style="sync", repo=self.repo, built_at_commit=self.commit,
                                     evidence=list(dict.fromkeys(evs)), meta={"tables": tables[:20]}))
        return rels

    def _unit_services(self, u: Unit, sig) -> list[Relationship]:
        rels = []
        by: dict[str, list] = defaultdict(list)
        for s in sig.get("service", []):
            by[s.service].append(s)
        for key, items in sorted(by.items()):
            info = self.cat.services_info.get(key, {"name": key})
            etype = info.get("type") or "external-system"
            eid = f"{self.repo}:{'store' if etype == 'data-store' else 'external'}:{slug(key)}"
            el = Element(id=eid, type=etype, name=info.get("name") or key, repo=self.repo, tech=info.get("name"),
                         desc=info.get("desc"), built_at_commit=self.commit,
                         evidence=[self.ev("sdk", x.file, x.line) for x in items[:3]],
                         meta={"merge": f"service:{key}", "external": True})
            self.model.add(el)
            rels.append(Relationship(from_id=self.uid(u), to_id=eid, rule="catalog-sdk", how=info.get("how"),
                                     what=info.get("what"), style="sync", repo=self.repo, built_at_commit=self.commit,
                                     evidence=[self.ev("sdk", x.file, x.line) for x in items[:5]]))
        return rels

    def _store(self, s: StoreInfo) -> Element:
        t = self.cat.tech(s.tech)
        etype = self.stype(s)
        same = [x for x in self.stores.values() if x.tech == s.tech]
        name = t.get("name") or s.tech
        if len(same) > 1:
            name = f"{name} ({sorted(s.aliases)[0] if s.aliases else s.key})"
        evidence = list(dict.fromkeys(s.evidence)) or []
        meta = {"merge": f"tech:{s.tech}", "tech_key": s.tech, "aliases": sorted(s.aliases),
                "instance": sorted(s.aliases)[0] if s.aliases else None, "host_keys": sorted(s.host_keys),
                "configs": sorted(config_mod.describe(c, self.cat).get("name") or "a credential" for c in s.configs),
                "external": bool(t.get("external"))}
        return Element(id=self.sid(s), type=etype, name=name, repo=self.repo, tech=t.get("name"),
                       desc={"channel": "Carries messages", "data-store": "Stores data"}.get(etype),
                       built_at_commit=self.commit, evidence=evidence, meta=meta)

    def _code_elements(self, u: Unit, sig) -> None:
        uid = self.uid(u)
        for r in sig.get("route", []):
            if len(self.model.elements) >= MAX_ELEMENTS:
                self.stats["elements truncated"] += 1
                return
            self.model.add(Element(id=f"{self.repo}:route:{u.key}:{r.method} {r.path}", type="code", level="code",
                                   name=f"{r.method} {r.path}", repo=self.repo, kind="route", parent=uid,
                                   built_at_commit=self.commit, evidence=[self.ev("route", r.file, r.line)],
                                   meta={"method": r.method, "path": r.path, "framework": r.framework,
                                         "handler": r.handler}))
        for c in sig.get("call", []):
            if len(self.model.elements) >= MAX_ELEMENTS:
                self.stats["elements truncated"] += 1
                return
            target, hkey = self.resolve_host(c.host)
            base = c.base
            if base and target is None:
                scheme, host = host_of(self.value(u, base))
                t2, h2 = self.resolve_host(host)
                target, hkey = target or t2, hkey or h2
            base_d = config_mod.describe(base, self.cat) if base else {}
            meta = {"method": c.method, "path": c.path, "base": base_d.get("name"), "base_kind": base_d.get("kind"),
                    "target": target if target != uid else None, "self_target": target == uid,
                    "host_key": hkey, "client": c.client}
            evs = [self.ev("call", c.file, c.line)] + [self.ev("call", f, ln) for f, ln in c.via[:3]]
            self.model.add(Element(id=f"{self.repo}:http-call:{u.key}:{c.file}:{c.line}:{c.method}", type="code",
                                   level="code", name=f"{c.method} {c.path or '(dynamic URL)'}", repo=self.repo,
                                   kind="http-call", parent=uid, built_at_commit=self.commit, evidence=evs,
                                   meta=meta))

    def _deploy_refs(self) -> None:
        """Compose services this repository builds from another directory (a sibling repository): record
        where they point and which configuration names resolve to which element, never the values."""
        for d in self.refs:
            targets, hkeys = {}, {}
            for name, value in d.env_values.items():
                if self.cat.sensitive_kind(name):
                    continue
                _, host = host_of(value)
                t, h = self.resolve_host(host)
                if t:
                    targets[name] = t
                elif h:
                    hkeys[name] = h
            self.model.add(Element(id=f"{self.repo}:deploy-ref:{slug(d.name)}", type="code", level="code",
                                   name=d.name, repo=self.repo, kind="deploy-ref", built_at_commit=self.commit,
                                   evidence=[self.ev("deploy", d.file, d.line, f"services.{d.name} build {d.build}")],
                                   meta={"service": d.name, "build": d.build, "config_targets": targets,
                                         "config_host_keys": hkeys,
                                         "config": [config_mod.describe(n, self.cat) for n in sorted(d.env_names)]}))


def _import_root(origin: str, family: str) -> str:
    """The package an import names: ``shop_contracts.orders.Order`` -> ``shop_contracts``,
    ``@acme/ui/price`` -> ``@acme/ui``, ``express`` -> ``express``; relative imports -> ""."""
    if not origin or origin.startswith("."):
        return ""
    if origin.startswith("@"):
        return "/".join(origin.split("/")[:2]).split(".")[0]
    if family == "go":
        return origin
    return re.split(r"[./]", origin, maxsplit=1)[0]


def build_repo(root: Path, repo: str, commit: str = "", *, catalog: catalog_mod.Catalog | None = None) -> Model:
    """The system model of one repository. Pure and read-only; zero model calls; no network."""
    root = Path(root)
    cat = catalog or catalog_mod.load(root)
    return Builder(root, repo, commit, cat).run()


def head_commit(root: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=20, check=True).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def sync_step(cairn) -> dict:
    """Build and store this repository's model (the sync step). Returns counts for the progress line."""
    from .model import save
    project = cairn.project
    commit = head_commit(project.root)
    model = build_repo(project.root, project.name, commit)
    counts = save(cairn.brain, model, project.name, commit)
    stats = getattr(model, "stats", {})
    containers = sum(1 for e in model.elements.values() if e.type == "container")
    return {**counts, "containers": containers, "parsed": stats.get("parsed", 0), "files": stats.get("files", 0),
            "commit": commit[:12]}
