"""Configuration NAMES read by code and set by deploy and config files (FR-011, FR-029b).

Names are what the model stores. Names the catalog marks sensitive (keys, tokens, passwords) are stored
only as their kind ("a SendGrid credential"). Values are read here for one purpose: to resolve where a
configured URL, host or connection string points (another deploy unit, a store's scheme). ``Values`` is
an in-memory lookup used during the build and never written anywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..catalog import Catalog
from ..safeyaml import UnsafeYAML, loads
from .files import RepoFiles, is_test_path
from .match import host_of

ENV_FILES = (".env", ".env.example", ".env.sample", ".env.template", ".env.defaults", ".env.production")
_PROPS = re.compile(r"^application(?:-[\w.]+)?\.(?:properties|ya?ml)$")


def describe(name: str, cat: Catalog) -> dict:
    """``{"name": "API_BASE_URL"}`` or, for a sensitive name, ``{"kind": "a SendGrid credential"}``."""
    kind = cat.sensitive_kind(name)
    return {"kind": kind} if kind else {"name": name}


@dataclass
class Values:
    """Configuration values by directory, for resolution only (never stored)."""
    by_dir: dict = field(default_factory=dict)       # dir -> {name: value}
    names_at: dict = field(default_factory=dict)     # dir -> {name: (file, line)}

    def lookup(self, root: str, name: str) -> str | None:
        """The value of ``name`` in a config file at ``root`` or below it (nearest first)."""
        for d in sorted(self.by_dir, key=lambda x: (x.count("/"), x)):
            if root and d != root and not d.startswith(root + "/"):
                continue
            v = self.by_dir[d].get(name)
            if v is not None:
                return v
        return None


def scan(files: RepoFiles) -> Values:
    vals = Values()
    for rel in files.paths:
        name = PurePosixPath(rel).name
        if is_test_path(rel):
            continue
        if name in ENV_FILES:
            _dotenv(rel, files.read(rel), vals)
        elif _PROPS.match(name):
            text = files.read(rel)
            if text is None:
                continue
            if name.endswith(".properties"):
                _properties(rel, text, vals)
            else:
                _yaml_props(rel, text, vals)
    return vals


def _dir_of(rel: str) -> str:
    parts = list(PurePosixPath(rel).parent.parts)
    # Spring resources live under src/main/resources: attribute them to the module root.
    for i in range(len(parts) - 2):
        if parts[i:i + 3] == ["src", "main", "resources"]:
            parts = parts[:i]
            break
    d = "/".join(parts)
    return "" if d == "." else d


def _put(vals: Values, rel: str, key: str, value: str | None, line: int | None) -> None:
    d = _dir_of(rel)
    vals.names_at.setdefault(d, {}).setdefault(key, (rel, line))
    if value is not None and "${" not in value:
        vals.by_dir.setdefault(d, {}).setdefault(key, value)


def _dotenv(rel: str, text: str | None, vals: Values) -> None:
    for i, line in enumerate((text or "").splitlines(), 1):
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.*)$", line)
        if m:
            _put(vals, rel, m.group(1), m.group(2).strip().strip("'\""), i)


def _properties(rel: str, text: str, vals: Values) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith(("#", "!")):
            continue
        m = re.match(r"^([^=:\s]+)\s*[=:]\s*(.*)$", s)
        if m:
            _put(vals, rel, m.group(1), m.group(2).strip(), i)


def _yaml_props(rel: str, text: str, vals: Values) -> None:
    for chunk in re.split(r"(?m)^---\s*$", text):
        try:
            doc = loads(chunk)
        except UnsafeYAML:
            continue
        if not isinstance(doc, dict):
            continue
        for key, value in _flatten(doc):
            leaf = key.rsplit(".", 1)[-1]
            line = None
            m = re.search(r"(?m)^\s*" + re.escape(leaf) + r"\s*:", text)
            if m:
                line = text.count("\n", 0, m.start()) + 1
            _put(vals, rel, key, None if isinstance(value, (dict, list)) else (None if value is None else str(value)),
                 line)


def _flatten(d: dict, prefix: str = "", depth: int = 0):
    if depth > 12:
        return
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            yield from _flatten(v, key, depth + 1)
        else:
            yield key, v


def resolve(value: str | None) -> tuple[str | None, str | None]:
    """(scheme, host) of a configured value; the value itself is not kept."""
    return host_of(value)
