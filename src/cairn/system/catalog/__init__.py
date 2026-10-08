"""The extraction catalog: frameworks, clients, drivers, SDKs, images and sensitive names, as data.

Bundled rules live next to this file (``frameworks.yaml``, ``clients.yaml``, ``infrastructure.yaml``); a
project adds its own in ``.cairn/catalog/*.yaml`` with the same top-level keys. Every file is read with
the safe YAML reader; an entry that is malformed is skipped and reported in ``Catalog.skipped`` with the
reason, never fatal. Matching semantics are documented at the top of each bundled file.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..safeyaml import UnsafeYAML, load_file

HERE = Path(__file__).parent
BUNDLED = ("frameworks.yaml", "clients.yaml", "infrastructure.yaml")
LANGUAGES = frozenset({"python", "javascript", "java", "go", "csharp", "shell"})
ROUTE_KINDS = frozenset({"decorator", "annotation", "call", "file-route", "config"})
OPS = frozenset({"publish", "subscribe", "push", "pop"})
LIST_KEYS = ("frameworks", "http", "messaging", "stores", "services")
MAP_KEYS = ("techs", "services_info", "images", "schemes", "drivers", "config_keys")
ENV_TRIGGERS = {"python": ("environ", "getenv"), "javascript": ("process.env", "import.meta.env"),
                "go": ("Getenv", "LookupEnv"), "java": ("getenv", "@Value"), "csharp": ("GetEnvironmentVariable",)}
MARKERS = {"javascript": ("fetch(",), "java": ("Mapping", "Listener", "FeignClient", "Repository", "RestTemplate",
                                               "WebClient", "DriverManager"),
           "python": ("urlpatterns", "DATABASES"), "go": ("net/http",)}


def _import_word(lib: str, family: str) -> str:
    """How a file of ``family`` mentions library ``lib`` when it imports it."""
    if family == "go" or lib.startswith("@") or "/" not in lib:
        return lib
    return lib.split("/")[0]


@dataclass
class Catalog:
    frameworks: list = field(default_factory=list)
    http: list = field(default_factory=list)
    messaging: list = field(default_factory=list)
    stores: list = field(default_factory=list)
    services: list = field(default_factory=list)
    techs: dict = field(default_factory=dict)
    services_info: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)
    schemes: dict = field(default_factory=dict)
    drivers: dict = field(default_factory=dict)
    config_keys: dict = field(default_factory=dict)
    sensitive_words: set = field(default_factory=set)
    vendors: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)
    _triggers: dict | None = None

    # -- lookups
    def tech(self, key: str | None) -> dict:
        return self.techs.get(key or "", {"name": key or "unknown", "type": "data-store", "how": key or "driver"})

    def image_tech(self, image: str | None) -> str | None:
        """``docker.io/library/postgres:16-alpine`` -> ``postgresql``; unknown images -> None."""
        if not image:
            return None
        ref = str(image).strip().split("@", 1)[0]
        last = ref.rsplit("/", 1)[-1]
        if ":" in last:
            ref = ref[: len(ref) - len(last)] + last.split(":", 1)[0]
        ref = ref.lower()
        for pre in ("docker.io/library/", "docker.io/", "index.docker.io/library/", "library/"):
            if ref.startswith(pre):
                ref = ref[len(pre):]
        if ref in self.images:
            return self.images[ref]
        tail = ref.split("/")
        for i in range(1, len(tail)):
            cand = "/".join(tail[i:])
            if cand in self.images:
                return self.images[cand]
        return None

    def scheme_tech(self, scheme: str | None) -> str | None:
        if not scheme:
            return None
        s = scheme.lower()
        if s.startswith("jdbc:"):
            s = s[5:]
        return self.schemes.get(s.split("+", 1)[0])

    def sensitive_kind(self, name: str) -> str | None:
        """``SENDGRID_API_KEY`` -> "a SendGrid credential"; a name that is not sensitive -> None."""
        words = [w.lower() for w in re.split(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])", str(name)) if w]
        if not words or not any(w in self.sensitive_words for w in words):
            return None
        vendor = next((self.vendors[w] for w in words if w in self.vendors), None)
        return f"a {vendor} credential" if vendor else "a credential"

    def triggers(self, family: str) -> re.Pattern:
        """Words whose presence makes a file of this language worth parsing: the libraries and frameworks
        its rules name (as the language spells an import of them), framework markers and env reads."""
        if self._triggers is None:
            self._triggers = {}
        rx = self._triggers.get(family)
        if rx is None:
            words: set[str] = set(ENV_TRIGGERS.get(family, ()))
            words |= set(MARKERS.get(family, ()))
            for section in (self.frameworks, self.http, self.messaging, self.stores, self.services):
                for rule in section:
                    langs = rule.get("language")
                    if family not in ([langs] if isinstance(langs, str) else list(langs or [])):
                        continue
                    for lib in list(rule.get("library") or []) + list(rule.get("detect") or []):
                        words.add(_import_word(str(lib), family))
                    words.update(str(c) for c in (rule.get("commands") or {}))
            words = {w for w in words if len(w) >= 2}
            rx = self._triggers[family] = re.compile(
                "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) or r"(?!x)x")
        return rx


def load(project_root: Path | None = None) -> Catalog:
    """The bundled catalog plus the project's ``.cairn/catalog/*.yaml`` additions (project rules first)."""
    cat = Catalog()
    extra = []
    if project_root is not None:
        d = Path(project_root) / ".cairn" / "catalog"
        if d.is_dir():
            extra = sorted(p for p in d.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file())
    for path in [*extra, *(HERE / n for n in BUNDLED)]:
        _merge(cat, path, bundled=path.parent == HERE)
    return cat


def _merge(cat: Catalog, path: Path, *, bundled: bool) -> None:
    try:
        doc = load_file(path)
    except (UnsafeYAML, OSError) as exc:
        cat.skipped.append({"file": path.name, "entry": None, "reason": str(exc)[:160]})
        return
    if not isinstance(doc, dict):
        cat.skipped.append({"file": path.name, "entry": None, "reason": "not a mapping"})
        return
    for key in MAP_KEYS:
        src_key = "services" if key == "services_info" and isinstance(doc.get("services"), dict) else key
        block = doc.get(src_key)
        if isinstance(block, dict):
            for k, v in block.items():
                getattr(cat, key).setdefault(str(k).lower() if key in ("images", "schemes", "drivers") else str(k), v)
    for key in LIST_KEYS:
        block = doc.get(key)
        if key == "services" and isinstance(block, dict):
            continue
        if block is None:
            continue
        if not isinstance(block, list):
            cat.skipped.append({"file": path.name, "entry": key, "reason": "expected a list"})
            continue
        for i, rule in enumerate(block):
            reason = _invalid(key, rule, cat)
            if reason:
                cat.skipped.append({"file": path.name, "entry": f"{key}[{i}]", "reason": reason})
            else:
                getattr(cat, key).append(rule)
    sens = doc.get("sensitive")
    if isinstance(sens, dict):
        cat.sensitive_words.update(str(w).lower() for w in sens.get("words") or [])
        for k, v in (sens.get("vendors") or {}).items():
            cat.vendors.setdefault(str(k).lower(), str(v))


def _langs(rule) -> list[str]:
    lang = rule.get("language")
    return [lang] if isinstance(lang, str) else list(lang or [])


def _invalid(key: str, rule, cat: Catalog) -> str | None:
    if not isinstance(rule, dict):
        return "not a mapping"
    langs = _langs(rule)
    if not langs or any(x not in LANGUAGES for x in langs):
        return f"language must be one of {sorted(LANGUAGES)}"
    if key == "frameworks":
        if not isinstance(rule.get("framework"), str):
            return "framework name missing"
        routes = rule.get("routes")
        if not isinstance(routes, list) or not routes:
            return "routes missing"
        for r in routes:
            if not isinstance(r, dict) or r.get("kind") not in ROUTE_KINDS:
                return f"route kind must be one of {sorted(ROUTE_KINDS)}"
    elif key == "http":
        if not (isinstance(rule.get("match"), list) or isinstance(rule.get("commands"), dict)):
            return "match patterns missing"
    elif key == "messaging":
        ops = rule.get("ops")
        if not isinstance(ops, list) or not ops:
            return "ops missing"
        for op in ops:
            if not isinstance(op, dict) or op.get("op") not in OPS or not (op.get("match") or op.get("loose")):
                return f"each op needs op (one of {sorted(OPS)}) and match or loose"
        if not isinstance(rule.get("tech"), str):
            return "tech missing"
    elif key == "stores":
        if not isinstance(rule.get("tech"), str):
            return "tech missing"
        if not (rule.get("connect") or rule.get("extends") or rule.get("config")):
            return "a store needs connect, extends or config patterns (a use site)"
    elif key == "services":
        if not isinstance(rule.get("service"), str) or not isinstance(rule.get("use"), list):
            return "service and use patterns required"
    return None


_GLOBS: dict[tuple, re.Pattern] = {}


def compiled(patterns) -> re.Pattern:
    """One compiled regular expression for a list of glob patterns (cached; patterns are catalog data)."""
    key = tuple(patterns or ())
    rx = _GLOBS.get(key)
    if rx is None:
        rx = _GLOBS[key] = re.compile("|".join(f"(?:{fnmatch.translate(p)})" for p in key) or r"(?!x)x")
    return rx


def glob(qual: str, patterns) -> bool:
    return bool(patterns) and compiled(patterns).match(qual) is not None


def expand(patterns, methods) -> list[tuple[str, str | None]]:
    """``"x.{method}"`` with methods {get: GET} -> [("x.get", "get")]; patterns without it -> (p, None)."""
    out = []
    for p in patterns or ():
        if "{method}" in p:
            for m in methods or {}:
                out.append((p.replace("{method}", str(m)), str(m)))
        else:
            out.append((p, None))
    return out
