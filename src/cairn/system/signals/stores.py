"""Data-store uses and outside-service SDK uses (FR-010), driven by the ``stores`` and ``services`` rules
in ``catalog/clients.yaml``.

A store use is a driver connect or client construction, a typed client call (``JdbcTemplate``), a
repository interface extending a Spring Data base, or a configured connection (Django ``DATABASES``,
Prisma ``datasource``). An import or a declared dependency alone is never a use. SQL verbs and tables
come from literal SQL in the same file (the SQL inside a string is read with a documented regex: it is
not the host language's syntax).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..catalog import Catalog, glob
from .files import RepoFiles
from .match import RepoIndex, imports_library, qualify
from .messaging import Connect, connects, quals
from .syntax import FileFacts, Val

_SQL_STMT = re.compile(
    r"\b(?P<verb>insert\s+(?:or\s+\w+\s+)?into|replace\s+into|update(?=\s+\S+\s+set\b)|delete\s+from|"
    r"select\b[\s\S]*?\bfrom|merge\s+into|upsert\s+into)\s+"
    r"(?P<table>[\"`\[]?[A-Za-z_][\w.]*[\"`\]]?)", re.I)
_SQL_JOIN = re.compile(r"\bjoin\s+([\"`\[]?[A-Za-z_][\w.]*[\"`\]]?)", re.I)
_NOT_TABLES = frozenset({"select", "set", "where", "values", "dual", "information_schema", "pg_catalog", "table",
                         "lateral", "unnest", "generate_series", "only", "a", "an", "the", "and", "or", "this",
                         "that", "it", "them", "each", "all", "any", "one", "which", "what"})
VERB = {"insert": "writes", "update": "writes", "delete": "writes", "merge": "writes", "upsert": "writes",
        "replace": "writes", "select": "reads"}
DJANGO_ENGINES = {"postgresql": "postgresql", "postgresql_psycopg2": "postgresql", "postgis": "postgresql",
                  "mysql": "mysql", "sqlite3": "sqlite", "oracle": "sql"}


@dataclass
class StoreUse:
    file: str
    line: int
    tech: str
    how: str
    client: str
    target: Val | None = None
    driver: str | None = None
    config_keys: list = field(default_factory=list)   # configuration keys that name the store (Spring)
    scheme_hint: str | None = None                    # e.g. Prisma provider


@dataclass
class ServiceUse:
    file: str
    line: int
    service: str


def _families(rule) -> set[str]:
    lang = rule.get("language")
    return {lang} if isinstance(lang, str) else set(lang or [])


def sql_ops(text: str) -> list[tuple[str, str]]:
    """``INSERT INTO votes ...`` -> [("writes", "votes")]; ``SELECT 1`` -> []."""
    out = []
    for m in _SQL_STMT.finditer(text):
        verb = m.group("verb").split()[0].lower()
        table = m.group("table").strip("\"`[]").split(".")[-1]
        if table.lower() in _NOT_TABLES or not re.match(r"[A-Za-z_]", table):
            continue
        out.append((VERB.get(verb, "reads"), table))
        if verb == "select":
            for j in _SQL_JOIN.finditer(text[m.end():m.end() + 400]):
                t = j.group(1).strip("\"`[]").split(".")[-1]
                if t.lower() not in _NOT_TABLES:
                    out.append(("reads", t))
    return out


def extract(facts: dict[str, FileFacts], cat: Catalog, idx: RepoIndex, files: RepoFiles | None = None):
    uses: list[StoreUse] = []
    services: list[ServiceUse] = []
    for ff in facts.values():
        for rule in cat.stores:
            if ff.family not in _families(rule):
                continue
            how = rule.get("how") or cat.tech(rule["tech"]).get("how") or rule["tech"]
            for c in connects(rule, ff, idx, how):
                uses.append(_from_connect(c, rule))
            if rule.get("extends"):
                want = set(rule["extends"])
                for _cls, (bases, line) in ff.bases.items():
                    if any(b.split(".")[-1] in want for b in bases):
                        uses.append(StoreUse(ff.path, line, rule["tech"], how, rule.get("client", ""),
                                             config_keys=list(rule.get("config") or [])))
            if "DATABASES" in (rule.get("config") or []) and "DATABASES" in ff.binds:
                uses += _django(ff, rule, how, files)
        for rule in cat.services:
            if ff.family not in _families(rule):
                continue
            if rule.get("library") and not imports_library(ff, rule["library"]) and ff.family != "javascript":
                continue
            if rule.get("library") and not any(q.startswith(tuple(rule["library"])) for q in quals(ff, idx)):
                continue
            for c in ff.calls:
                q = qualify(ff, c.callee, idx)
                if not glob(q, rule.get("use")):
                    continue
                if rule.get("library") and not any(q == lib or q.startswith(lib + ".") or q.startswith(lib + "(")
                                                   for lib in rule["library"]):
                    continue
                if not _where(rule.get("where"), c):
                    continue
                services.append(ServiceUse(ff.path, c.line, rule["service"]))
    if files is not None:
        _prisma(uses, files)
    for u in uses:
        for rule in cat.stores:
            if rule.get("client") == u.client and rule.get("config") and not u.config_keys:
                u.config_keys = [k for k in rule["config"] if "." in k]
    return uses, services


def _from_connect(c: Connect, rule) -> StoreUse:
    return StoreUse(c.file, c.line, rule["tech"], c.how, c.client, c.target, c.driver,
                    config_keys=[k for k in rule.get("config") or [] if "." in k])


def _where(cond, c) -> bool:
    if not cond:
        return True
    for sel, allowed in cond.items():
        if sel.startswith("arg") and sel[3:].isdigit():
            i = int(sel[3:])
            v = c.args[i] if i < len(c.args) else None
            if v is None or v.kind != "str" or v.text not in allowed:
                return False
    return True


def _django(ff: FileFacts, rule, how, files: RepoFiles | None) -> list[StoreUse]:
    out = []
    text = files.read(ff.path) if files is not None else None
    at = text.find("DATABASES") if text else -1
    line = text.count("\n", 0, at) + 1 if text and at >= 0 else 1
    for v in ff.binds.get("DATABASES", []):
        if v.kind != "obj":
            continue
        for db in v.fields.values():
            if db.kind != "obj":
                continue
            eng = db.fields.get("ENGINE")
            if eng is None or eng.kind != "str":
                continue
            tech = DJANGO_ENGINES.get(eng.text.rsplit(".", 1)[-1], "sql")
            out.append(StoreUse(ff.path, line, tech, how, rule.get("client", ""), db.fields.get("HOST")))
    return out


def _prisma(uses: list[StoreUse], files: RepoFiles) -> None:
    """A PrismaClient use takes its database from ``schema.prisma``'s datasource provider."""
    if not any(u.client == "prisma" for u in uses):
        return
    provider = None
    for rel in files.paths:
        if PurePosixPath(rel).name == "schema.prisma":
            m = re.search(r'datasource\s+\w+\s*\{[^}]*provider\s*=\s*"(\w+)"', files.read(rel) or "", re.S)
            if m:
                provider = m.group(1)
                break
    for u in uses:
        if u.client == "prisma" and provider:
            u.scheme_hint = provider
