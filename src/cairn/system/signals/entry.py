"""Manifests and entry points: what each package is called, what it depends on, and whether it runs.

Entry points: ``[project.scripts]`` / Poetry scripts, ``package.json`` ``scripts.start`` and ``bin``, a
Go ``package main`` with ``func main``, a Java class with ``static main`` (Spring Boot application), a .NET
project that builds an executable, a Python package's ``__main__.py``. Dependencies are names only.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .files import RepoFiles, is_test_path

MANIFESTS = ("pyproject.toml", "package.json", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
             "requirements.txt", "requirements.in", "Pipfile")


@dataclass
class Manifest:
    file: str
    dir: str
    kind: str                       # python | javascript | go | java | csharp
    name: str | None = None
    deps: list = field(default_factory=list)
    entries: list = field(default_factory=list)     # (label, line)
    scripts: list = field(default_factory=list)     # names of the commands it installs (console scripts, bin)
    provides: bool = False          # declares an installable package
    web: bool = False               # .NET web SDK, Spring Boot web starter
    description: str | None = None


def _dir(rel: str) -> str:
    d = str(PurePosixPath(rel).parent)
    return "" if d == "." else d


def _find_line(text: str, needle: str) -> int | None:
    i = text.find(needle)
    return text.count("\n", 0, i) + 1 if i >= 0 else None


def _pep508(spec: str) -> str:
    return re.split(r"[\s<>=!~;\[\(@]", str(spec).strip(), maxsplit=1)[0]


def scan(files: RepoFiles) -> list[Manifest]:
    out: list[Manifest] = []
    for rel in files.paths:
        name = PurePosixPath(rel).name
        if is_test_path(rel):
            continue
        if name not in MANIFESTS and not name.endswith(".csproj"):
            continue
        text = files.read(rel)
        if not text:
            continue
        try:
            m = _parse(rel, name, text)
        except Exception:  # noqa: BLE001 — a malformed manifest declares nothing
            m = None
        if m is not None:
            out.append(m)
    return out


def _parse(rel: str, name: str, text: str) -> Manifest | None:
    d = _dir(rel)
    if name == "pyproject.toml":
        data = tomllib.loads(text)
        proj = data.get("project") if isinstance(data.get("project"), dict) else {}
        poetry = ((data.get("tool") or {}).get("poetry") or {}) if isinstance(data.get("tool"), dict) else {}
        m = Manifest(file=rel, dir=d, kind="python", name=proj.get("name") or poetry.get("name"),
                     description=proj.get("description") or poetry.get("description"))
        m.deps = [_pep508(s) for s in proj.get("dependencies") or [] if isinstance(s, str)]
        m.deps += [str(k) for k in (poetry.get("dependencies") or {}) if str(k).lower() != "python"]
        for group in ("scripts", "gui-scripts"):
            if proj.get(group):
                m.entries.append((f"[project.{group}]", _find_line(text, f"[project.{group}]")))
                m.scripts += [str(k) for k in proj[group]] if isinstance(proj[group], dict) else []
        if isinstance(poetry.get("scripts"), dict):
            m.scripts += [str(k) for k in poetry["scripts"]]
        if poetry.get("scripts"):
            m.entries.append(("[tool.poetry.scripts]", _find_line(text, "[tool.poetry.scripts]")))
        m.provides = bool(m.name)
        return m
    if name in ("requirements.txt", "requirements.in"):
        deps = [_pep508(x) for x in text.splitlines() if x.strip() and not x.strip().startswith(("#", "-"))]
        return Manifest(file=rel, dir=d, kind="python", deps=[x for x in deps if x])
    if name == "Pipfile":
        data = tomllib.loads(text)
        return Manifest(file=rel, dir=d, kind="python", deps=[str(k) for k in (data.get("packages") or {})])
    if name == "package.json":
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        m = Manifest(file=rel, dir=d, kind="javascript", name=data.get("name") if isinstance(data.get("name"), str)
                     else None, description=data.get("description") if isinstance(data.get("description"), str)
                     else None)
        for key in ("dependencies", "peerDependencies", "optionalDependencies"):
            if isinstance(data.get(key), dict):
                m.deps += [str(k) for k in data[key]]
        scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
        if scripts.get("start"):
            m.entries.append(("scripts.start", _find_line(text, '"start"')))
        if data.get("bin"):
            m.entries.append(("bin", _find_line(text, '"bin"')))
            b = data["bin"]
            m.scripts += [str(k) for k in b] if isinstance(b, dict) else ([m.name.split("/")[-1]] if m.name else [])
        m.provides = bool(m.name) and not data.get("private")
        return m
    if name == "go.mod":
        mod = re.search(r"^module\s+(\S+)", text, re.M)
        deps = re.findall(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+v[\w.\-+]+", text, re.M)
        return Manifest(file=rel, dir=d, kind="go", name=mod.group(1) if mod else None, deps=deps,
                        provides=bool(mod))
    if name == "pom.xml":
        if "<!ENTITY" in text or "<!DOCTYPE" in text:
            return None  # no entity expansion from untrusted XML
        import xml.etree.ElementTree as ET
        root = ET.fromstring(re.sub(r'\sxmlns="[^"]*"', "", text, count=1))
        aid, gid = root.findtext("artifactId"), root.findtext("groupId") or root.findtext("parent/groupId")
        m = Manifest(file=rel, dir=d, kind="java", name=f"{gid}:{aid}" if gid and aid else aid,
                     description=root.findtext("description"))
        for dep in root.findall(".//dependencies/dependency"):
            da, dg = dep.findtext("artifactId"), dep.findtext("groupId")
            if da:
                m.deps += [da] + ([f"{dg}:{da}"] if dg else [])
        if "spring-boot-maven-plugin" in text:
            m.entries.append(("spring-boot-maven-plugin", _find_line(text, "spring-boot-maven-plugin")))
        m.web = any(x in m.deps for x in ("spring-boot-starter-web", "spring-boot-starter-webflux"))
        m.provides = (root.findtext("packaging") or "jar") in ("jar", "war")
        return m
    if name in ("build.gradle", "build.gradle.kts"):
        # Gradle scripts are programs; the dependency notation is read with a documented regex fallback.
        deps = re.findall(r"""(?:implementation|api|compile|runtimeOnly)\s*\(?\s*["']([\w.\-]+):([\w.\-]+)""", text)
        m = Manifest(file=rel, dir=d, kind="java", deps=[a for _, a in deps] + [f"{g}:{a}" for g, a in deps])
        if re.search(r"org\.springframework\.boot|\bapplication\b", text):
            m.entries.append(("gradle application", _find_line(text, "org.springframework.boot")
                              or _find_line(text, "application")))
        m.web = any(x in m.deps for x in ("spring-boot-starter-web", "spring-boot-starter-webflux"))
        g = re.search(r"""^\s*group\s*=\s*["']([\w.\-]+)["']""", text, re.M)
        m.name = g.group(1) if g else None
        m.provides = bool(g)
        return m
    if name.endswith(".csproj"):
        if "<!ENTITY" in text or "<!DOCTYPE" in text:
            return None
        m = Manifest(file=rel, dir=d, kind="csharp", name=PurePosixPath(rel).stem)
        m.deps = re.findall(r'<PackageReference\s+Include="([^"]+)"', text)
        m.web = "Microsoft.NET.Sdk.Web" in text
        if m.web or re.search(r"<OutputType>\s*(Exe|WinExe)\s*</OutputType>", text):
            m.entries.append(("csproj executable", _find_line(text, "<OutputType>") or _find_line(text, "Sdk=")))
        m.provides = not m.entries
        return m
    return None


def norm_dep(name: str) -> str:
    """Dependency names compare case-insensitively with ``-``, ``_`` and ``.`` treated alike."""
    return re.sub(r"[-_.]+", "-", str(name).strip().lower())
