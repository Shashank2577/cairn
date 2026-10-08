"""Deploy descriptions: Dockerfiles, compose files (including swarm stacks), Kubernetes workloads and
Services, Procfiles.

Only names are kept: service and workload names, images, ports, environment variable NAMES, a short
start command. Environment VALUES are read into ``env_values`` for one purpose only (resolving a URL's
host to another deploy unit, or a store's scheme) and the build drops them after resolution; nothing in
this module writes them anywhere. Files under test, example and documentation trees are ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..safeyaml import UnsafeYAML, loads
from .files import RepoFiles, is_test_path

COMPOSE = re.compile(r"^(?:docker-)?compose(?:[.\-][\w.\-]+)?\.ya?ml$|^docker-stack(?:[.\-][\w.\-]+)?\.ya?ml$", re.I)
DOCKERFILE = re.compile(r"^(?:Dockerfile|Containerfile)(?:\.[\w.\-]+)?$|^[\w.\-]+\.Dockerfile$", re.I)
WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod", "ReplicaSet", "DeploymentConfig"}
MAX_DOCS = 200


@dataclass
class Unit:
    """One deploy-description entry (a compose service, a Kubernetes workload, a Procfile process)."""
    source: str                     # compose | k8s | procfile
    file: str
    entry: str                      # "services.api", "Deployment/vote", "web"
    name: str
    line: int | None = None
    build: str | None = None        # context directory relative to the repository root (may leave it: "../x")
    dockerfile: str | None = None
    image: str | None = None
    ports: list = field(default_factory=list)
    env_names: list = field(default_factory=list)
    env_values: dict = field(default_factory=dict)   # transient, never stored
    depends_on: list = field(default_factory=list)
    command: str | None = None
    job: bool = False
    replicas: int | None = None
    aliases: list = field(default_factory=list)


@dataclass
class Dockerfile:
    file: str
    dir: str
    base: str | None = None
    expose: list = field(default_factory=list)      # (port, line)
    cmd: tuple | None = None                        # (short command, line)
    env_names: list = field(default_factory=list)
    from_line: int | None = None


@dataclass
class Deploy:
    units: list = field(default_factory=list)
    dockerfiles: list = field(default_factory=list)
    problems: list = field(default_factory=list)    # (file, reason)


def scan(files: RepoFiles) -> Deploy:
    out = Deploy()
    for rel in files.paths:
        name = PurePosixPath(rel).name
        if is_test_path(rel):
            continue
        if DOCKERFILE.match(name):
            df = dockerfile(rel, files.read(rel))
            if df is not None:
                out.dockerfiles.append(df)
        elif COMPOSE.match(name):
            _compose(rel, files.read(rel), out)
        elif name == "Procfile":
            _procfile(rel, files.read(rel), out)
        elif name.endswith((".yaml", ".yml")):
            text = files.read(rel)
            if text and "apiVersion" in text and "kind" in text:
                _k8s(rel, text, out)
    _k8s_service_aliases(out)
    return out


# ------------------------------------------------------------------------------------------- Dockerfile
def _logical_lines(text: str):
    buf, start = "", 0
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not buf and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if not buf:
            start = i
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        yield start, buf.strip()
        buf = ""
    if buf:
        yield start, buf.strip()


def dockerfile(rel: str, text: str | None) -> Dockerfile | None:
    if text is None:
        return None
    d = str(PurePosixPath(rel).parent)
    df = Dockerfile(file=rel, dir="" if d == "." else d)
    stage_cmd = stage_expose = None
    for ln, line in _logical_lines(text):
        word, _, rest = line.partition(" ")
        word = word.upper()
        if word == "FROM":
            toks = [t for t in rest.split() if not t.startswith("--")]
            df.base = toks[0] if toks else None
            df.from_line = ln
            stage_cmd, stage_expose = None, []
            df.expose = []
            df.cmd = None
        elif word == "EXPOSE":
            stage_expose = [(p.split("/")[0], ln) for p in rest.split() if re.fullmatch(r"\d+(?:/\w+)?", p)]
            df.expose.extend(stage_expose)
        elif word in ("CMD", "ENTRYPOINT"):
            stage_cmd = (short_command(rest), ln)
            if word == "ENTRYPOINT" or df.cmd is None:
                df.cmd = stage_cmd
        elif word == "ENV":
            df.env_names.extend(_env_names(rest))
    return df


def _env_names(rest: str) -> list[str]:
    if "=" in rest:
        return [m.group(1) for m in re.finditer(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=", rest)]
    first = rest.split()[:1]
    return [first[0]] if first and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", first[0]) else []


def short_command(rest) -> str | None:
    """The executable and its first plain argument (``gunicorn app:app``); flags and anything that could
    carry a value (``--password=x``, ``-p secret``) are dropped."""
    toks: list[str] = []
    if isinstance(rest, list):
        toks = [str(t) for t in rest]
    else:
        s = str(rest).strip()
        if s.startswith("["):
            toks = re.findall(r'"((?:[^"\\]|\\.)*)"', s)
        else:
            toks = s.split()
    out: list[str] = []
    for t in toks:
        if t.startswith("-") or "=" in t or "$" in t or len(t) > 60:
            break
        out.append(t)
        if len(out) == 2:
            break
    return " ".join(out) or None


# ------------------------------------------------------------------------------------------- compose
def _line_of(text: str, key: str, after: int = 0) -> int | None:
    rx = re.compile(r"^\s*['\"]?" + re.escape(key) + r"['\"]?\s*:", re.M)
    m = rx.search(text, after)
    return text.count("\n", 0, m.start()) + 1 if m else None


def _compose(rel: str, text: str | None, out: Deploy) -> None:
    if not text:
        return
    try:
        doc = loads(text)
    except UnsafeYAML as exc:
        out.problems.append((rel, str(exc)[:120]))
        return
    services = doc.get("services") if isinstance(doc, dict) else None
    if not isinstance(services, dict):
        return
    base = str(PurePosixPath(rel).parent)
    base = "" if base == "." else base
    svc_at = text.find("services:")
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        u = Unit(source="compose", file=rel, entry=f"services.{name}", name=str(name),
                 line=_line_of(text, str(name), max(svc_at, 0)))
        b = svc.get("build")
        if isinstance(b, str):
            u.build = _join(base, b)
        elif isinstance(b, dict):
            u.build = _join(base, str(b.get("context") or "."))
            if isinstance(b.get("dockerfile"), str):
                u.dockerfile = _join(u.build, b["dockerfile"])
        if isinstance(svc.get("image"), str):
            u.image = svc["image"]
        for p in svc.get("ports") or []:
            if isinstance(p, dict) and p.get("target"):
                u.ports.append(str(p["target"]))
            elif isinstance(p, (str, int)):
                u.ports.append(str(p).split(":")[-1].split("/")[0])
        env = svc.get("environment")
        if isinstance(env, dict):
            for k, v in env.items():
                u.env_names.append(str(k))
                if isinstance(v, (str, int)):
                    u.env_values[str(k)] = str(v)
        elif isinstance(env, list):
            for item in env:
                k, _, v = str(item).partition("=")
                u.env_names.append(k)
                if v:
                    u.env_values[k] = v
        dep = svc.get("depends_on")
        u.depends_on = [str(x) for x in (dep if isinstance(dep, list) else (dep or {}))] if isinstance(
            dep, (list, dict)) else []
        cmd = svc.get("entrypoint") or svc.get("command")
        if cmd:
            u.command = short_command(cmd)
        restart = str(svc.get("restart") or "")
        u.job = restart.strip("'\"") in ("no", "on-failure") and bool(svc.get("profiles"))
        dep_ = svc.get("deploy")
        if isinstance(dep_, dict) and isinstance(dep_.get("replicas"), int):
            u.replicas = dep_["replicas"]
        out.units.append(u)


def _join(base: str, rel: str) -> str:
    parts: list[str] = [p for p in base.split("/") if p]
    ups = 0
    for p in str(rel).replace("\\", "/").split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if parts:
                parts.pop()
            else:
                ups += 1
            continue
        parts.append(p)
    return "/".join([".."] * ups + parts)


# ------------------------------------------------------------------------------------------- Procfile
def _procfile(rel: str, text: str | None, out: Deploy) -> None:
    base = str(PurePosixPath(rel).parent)
    for i, line in enumerate((text or "").splitlines(), 1):
        m = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.+)$", line.strip())
        if m:
            out.units.append(Unit(source="procfile", file=rel, entry=m.group(1), name=m.group(1), line=i,
                                  build="" if base == "." else base, command=short_command(m.group(2))))


# ------------------------------------------------------------------------------------------- Kubernetes
def _docs(text: str):
    chunk: list[str] = []
    start = 1
    for i, line in enumerate(text.splitlines(), 1):
        if re.match(r"^---\s*$", line):
            if chunk:
                yield start, "\n".join(chunk)
            chunk, start = [], i + 1
        else:
            chunk.append(line)
    if chunk:
        yield start, "\n".join(chunk)


def _k8s(rel: str, text: str, out: Deploy) -> None:
    if "{{" in text:
        out.problems.append((rel, "templated (Helm); not read"))
        return
    for n, (start, chunk) in enumerate(_docs(text)):
        if n >= MAX_DOCS:
            break
        try:
            doc = loads(chunk)
        except UnsafeYAML as exc:
            out.problems.append((rel, str(exc)[:120]))
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("kind"), str):
            continue
        kind = doc["kind"]
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        name = str(meta.get("name") or "")
        if not name:
            continue
        line = (_line_of(chunk, "name") or 1) + start - 1
        if kind == "Service":
            spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
            sel = spec.get("selector") if isinstance(spec.get("selector"), dict) else {}
            out.units.append(Unit(source="k8s-service", file=rel, entry=f"Service/{name}", name=name, line=line,
                                  aliases=[f"{k}={v}" for k, v in sel.items()]))
            continue
        if kind not in WORKLOADS:
            continue
        spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
        if kind == "CronJob":
            spec = ((spec.get("jobTemplate") or {}).get("spec") or {}) if isinstance(spec.get("jobTemplate"), dict) else {}
        tpl = spec.get("template") if isinstance(spec.get("template"), dict) and kind != "Pod" else doc
        tspec = tpl.get("spec") if isinstance(tpl.get("spec"), dict) else {}
        labels = ((tpl.get("metadata") or {}).get("labels") or {}) if isinstance(tpl.get("metadata"), dict) else {}
        containers = [c for c in tspec.get("containers") or [] if isinstance(c, dict)]
        u = Unit(source="k8s", file=rel, entry=f"{kind}/{name}", name=name, line=line,
                 job=kind in ("Job", "CronJob"),
                 replicas=spec.get("replicas") if isinstance(spec.get("replicas"), int) else None,
                 aliases=[f"{k}={v}" for k, v in labels.items()] if isinstance(labels, dict) else [])
        for c in containers[:1]:
            u.image = str(c.get("image") or "") or None
            for p in c.get("ports") or []:
                if isinstance(p, dict) and p.get("containerPort"):
                    u.ports.append(str(p["containerPort"]))
            for e in c.get("env") or []:
                if isinstance(e, dict) and e.get("name"):
                    u.env_names.append(str(e["name"]))
                    if isinstance(e.get("value"), (str, int)):
                        u.env_values[str(e["name"])] = str(e["value"])
            cmd = c.get("command") or c.get("args")
            if cmd:
                u.command = short_command(cmd)
        out.units.append(u)


def _k8s_service_aliases(out: Deploy) -> None:
    """A Kubernetes Service name is a DNS host for the workloads it selects: record it as their alias."""
    workloads = [u for u in out.units if u.source == "k8s"]
    for svc in [u for u in out.units if u.source == "k8s-service"]:
        sel = set(svc.aliases)
        for w in workloads:
            if sel and sel <= set(w.aliases) and svc.name != w.name and svc.name not in w.aliases:
                w.aliases.append(svc.name)
    out.units = [u for u in out.units if u.source != "k8s-service"]
    for u in out.units:
        u.aliases = [a for a in u.aliases if "=" not in a]
