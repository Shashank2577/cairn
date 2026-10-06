"""Repository discovery, paths and configuration.

Everything Cairn knows about a repository lives in ``<repo>/.cairn/``. Only ``config.toml`` is
meant to be committed; the rest is local, rebuildable state.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = """\
# Cairn project configuration. Every key is optional; delete what you don't change.

[context]
budget = 1800                    # default token budget for agent-facing context packs
ambient = true                   # a short Cairn context nugget on code prompts (Claude Code, ≤500 tokens); false to turn off

[models]
provider = "auto"                # auto | anthropic | openai | claude-code  (auto: API key, else your Claude Code login)
fast = "claude-haiku-4-5-20251001"   # classification, summaries at volume
balanced = "claude-sonnet-5"         # extraction, linking tie-breaks
deep = "claude-opus-5-5"             # why / impact / drift synthesis, ask
frontier = "claude-fable-5-1"        # opt-in whole-system reviews only
# base_url = "http://localhost:11434/v1"

[deep]
enabled = "auto"                 # auto = on when a model key is present
budget_tokens = 150000           # hard ceiling per deep sync
wall_seconds = 300               # stop the deep tier after this many seconds; remaining days fill in on later syncs
graph_url = ""                   # falkor://host:6379 or bolt://host:7687 ; empty = embedded store

[memory]
add_mode = "reconcile"
wall_seconds = 300               # stop memory seeding after this many seconds; remaining seeds continue next sync           # reconcile: merge/update/retire similar memories with a model | additive
seed = true                      # learn decisions, clarifications, conventions and gotchas from the repo on sync
rerank = false                   # rerank recall results with a model
graph = false                    # also write memories into the timeline fact graph
instructions = ""                # extra guidance for what counts as a memory
# vector_store = { provider = "qdrant", config = { url = "http://team-server:6333" } }   # shared store for a team

[temporal]
backend = ""                     # empty: embedded (.cairn/temporal/) unless `url` names a server; or kuzu | neo4j | falkordb
url = ""                         # bolt://host:7687 | falkor://host:6379 ; empty = embedded
group_id = ""                    # namespace for facts (default: the project id)
reranker = "local"               # local | model | bge
update_communities = false       # refresh fact communities on every ingest (costs model calls)
entity_types = []                # extra entity types to extract, e.g. ["Component", "Decision", "Library"]
extraction_instructions = ""     # extra guidance for fact extraction

[history]
max_commits = 3000

[recall]
worker_spawn = true              # write observations and summaries after each agent turn (uses your model provider)
mode = "code"                    # what observations focus on (cairn sessions modes)
observe_batch = 5                # tool events per model call
context_observations = 50        # observations injected at session start

[sessions]
capture = true                   # record agent prompts, files read/changed and commands in .cairn/ (local only)
"""

DEFAULTS: dict[str, Any] = tomllib.loads(DEFAULT_CONFIG)

# Settings that decide where data and credentials go, or which shared namespace a project reads. A server
# that serves other people's repositories never takes them from the repository: anyone who can push could
# otherwise send the server's model keys to their own endpoint or read another project's shared stores. On
# such a server they come from the operator ($CAIRN_HOME/server.toml sections, or CAIRN_* environment variables).
OPERATOR_KEYS = frozenset({
    "models.provider", "models.base_url", "temporal.url", "temporal.backend", "temporal.user", "temporal.database",
    "temporal.search_host", "temporal.group_id", "deep.graph_url", "memory.vector_store", "team.server",
    "team.project", "team.token_env", "team.id"})
OPERATOR_ENV = {"models.provider": "CAIRN_MODELS_PROVIDER", "models.base_url": "CAIRN_MODELS_BASE_URL",
                "temporal.url": "CAIRN_TEMPORAL_URL", "temporal.backend": "CAIRN_TEMPORAL_BACKEND"}


def operator_cfg(dotted: str) -> Any:
    """An operator-level value for one of OPERATOR_KEYS, or None."""
    env = OPERATOR_ENV.get(dotted)
    if env and os.environ.get(env):
        return os.environ[env]
    home = Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn")
    try:
        data = tomllib.loads((home / "server.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    section, key = dotted.split(".", 1)
    value = (data.get(section) or {}).get(key)
    return value


# Everything in .cairn/ is local, rebuildable state except what the team shares through git.
GITIGNORE = "*\n!config.toml\n!.gitignore\n!workflow/\n!workflow/**\n"


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def find_root(start: Path | None = None) -> Path | None:
    """Return the git work-tree root containing ``start`` (or None)."""
    here = (start or Path.cwd()).resolve()
    for p in (here, *here.parents):
        if (p / ".git").exists():
            return p
    return None


def _toml_literal(dotted: str, value: Any) -> str:
    """A TOML value for `value` (JSON string escapes are valid TOML basic-string escapes)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return "[" + ", ".join(json.dumps(v) for v in value) + "]"
    raise ValueError(f"{dotted}: can't store {type(value).__name__} values")


def _trailing_comment(line: str, width: int) -> str:
    """The comment after a `key = value` line, found where the text before `#` is a complete TOML value."""
    body = line.split("=", 1)[1] if "=" in line else ""
    for i, ch in enumerate(body):
        if ch == "#":
            try:
                tomllib.loads("v = " + body[:i])
            except tomllib.TOMLDecodeError:
                continue
            return " " * max(1, 33 - width) + body[i:].rstrip()
    return ""


@dataclass
class Project:
    root: Path
    config: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    # False when a server serves this repository to other people: OPERATOR_KEYS then come from the operator.
    trusted: bool = True
    # The namespace in shared stores; a server sets it to the platform's project id (folder names can collide).
    scope_id: str | None = None

    # ---- construction -------------------------------------------------------------------------
    @classmethod
    def discover(cls, start: Path | None = None, *, allow_non_git: bool = True) -> Project | None:
        root = find_root(start)
        if root is None and allow_non_git:
            env_root = os.environ.get("CAIRN_ROOT")
            root = Path(env_root).resolve() if env_root else (start or Path.cwd()).resolve()
        if root is None:
            return None
        proj = cls(root=root)
        proj.reload()
        return proj

    def reload(self) -> None:
        cfg = dict(DEFAULTS)
        for path in (self.config_path, self.local_config_path):  # committed settings, then local overrides
            if path.exists():
                try:
                    cfg = _merge(cfg, tomllib.loads(path.read_text(encoding="utf-8")))
                except tomllib.TOMLDecodeError:
                    pass  # a broken config never blocks the tool; `cairn doctor` reports it
        self.config = cfg

    # ---- paths --------------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.root.name

    @property
    def id(self) -> str:
        """Stable project id used to namespace shared stores."""
        if self.scope_id:
            return self.scope_id
        return "".join(c if c.isalnum() else "-" for c in self.name.lower()).strip("-") or "project"

    @property
    def dir(self) -> Path:
        return self.root / ".cairn"

    @property
    def config_path(self) -> Path:
        return self.dir / "config.toml"

    @property
    def db_path(self) -> Path:
        return self.dir / "brain.db"

    @property
    def map_dir(self) -> Path:
        return self.dir / "graph"

    @property
    def map_json(self) -> Path:
        return self.map_dir / "graph.json"

    @property
    def initialized(self) -> bool:
        return self.db_path.exists()

    @cached_property
    def is_git(self) -> bool:
        return (self.root / ".git").exists()

    def rel(self, path: str | Path) -> str:
        p = Path(path)
        try:
            return p.resolve().relative_to(self.root).as_posix() if p.is_absolute() else p.as_posix()
        except ValueError:
            return p.as_posix()

    # ---- helpers -------------------------------------------------------------------------------
    def ensure_dir(self) -> None:
        self.dir.mkdir(exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
        gi = self.dir / ".gitignore"
        text = gi.read_text(encoding="utf-8") if gi.exists() else ""
        missing = [ln for ln in GITIGNORE.splitlines() if ln not in text.splitlines()]
        if missing:  # new repos get the full file; older ones gain the lines added since
            gi.write_text((text.rstrip("\n") + "\n" if text.strip() else "") + "\n".join(missing) + "\n", encoding="utf-8")

    def remove_legacy_artifacts(self) -> list[str]:
        """Earlier versions wrote the code map to a folder (and an ignore file) at the repository root. The map now
        lives in `.cairn/graph/`; remove the old generated copies if this repository is set up with Cairn."""
        removed = []
        if not self.dir.is_dir():
            return removed
        import shutil
        for name in ("graph" + "ify-out", "." + "graph" + "ifyignore"):  # old names, spelled so searches stay clean
            p = self.root / name
            if p.is_dir() and (p / "graph.json").exists():
                shutil.rmtree(p, ignore_errors=True)
                removed.append(name)
            elif p.is_file() and "# cairn:" in p.read_text(errors="replace", encoding="utf-8"):
                p.unlink()
                removed.append(name)
        return removed

    def git(self, *args: str, timeout: int = 60) -> str:
        """Run git in the repo; return stdout ('' on failure)."""
        try:
            res = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                                 text=True, timeout=timeout, errors="replace", encoding="utf-8")
            return res.stdout if res.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _default(self, dotted: str, default: Any) -> Any:
        cur: Any = DEFAULTS
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    @property
    def local_config_path(self) -> Path:
        """Untracked overrides (not committed): what a server or a person sets for this checkout only."""
        return self.dir / "config.local.toml"

    def set_cfg(self, dotted: str, value: Any, *, local: bool = False) -> None:
        """Set one `section.key` in config.toml (or config.local.toml with `local`), keeping every other line and
        comment. The new file is parsed before it is written: a value that can't be stored raises ValueError
        and nothing changes."""
        section, key = dotted.split(".", 1)
        literal = _toml_literal(dotted, value)
        path = self.local_config_path if local else self.config_path
        self.ensure_dir()
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        header = re.compile(r"^\s*\[\s*" + re.escape(section) + r"\s*\]\s*(#.*)?$")
        any_header = re.compile(r"^\s*\[\s*[A-Za-z0-9_.\-\"' ]+\s*\]\s*(#.*)?$")
        key_line = re.compile(r"^\s*(" + re.escape(key) + r"|\"" + re.escape(key) + r"\")\s*=")
        start = next((i for i, ln in enumerate(lines) if header.match(ln)), None)
        if start is None:
            lines += (["", f"[{section}]"] if lines else [f"[{section}]"]) + [f"{key} = {literal}"]
        else:
            end = next((i for i in range(start + 1, len(lines)) if any_header.match(lines[i])), len(lines))
            for i in range(start + 1, end):
                if key_line.match(lines[i]):
                    lines[i] = f"{key} = {literal}" + _trailing_comment(lines[i], len(key) + len(literal) + 3)
                    break
            else:
                at = end
                while at > start + 1 and not lines[at - 1].strip():  # keep the blank line before the next section
                    at -= 1
                lines.insert(at, f"{key} = {literal}")
        text = "\n".join(lines) + "\n"
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{dotted}: the config would not be valid TOML ({exc})") from exc
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        self.reload()

    def cfg(self, dotted: str, default: Any = None) -> Any:
        if not self.trusted:
            if dotted in OPERATOR_KEYS:
                value = operator_cfg(dotted)
                if value is not None:
                    return value
                return self._default(dotted, default)
            if "." not in dotted and any(k.startswith(dotted + ".") for k in OPERATOR_KEYS):
                section = dict(self.config.get(dotted) or {})
                for k in OPERATOR_KEYS:
                    if k.startswith(dotted + "."):
                        name = k.split(".", 1)[1]
                        value = operator_cfg(k)
                        if value is not None:
                            section[name] = value
                        else:
                            section.pop(name, None)
                            base = (DEFAULTS.get(dotted) or {})
                            if name in base:
                                section[name] = base[name]
                return section
        cur: Any = self.config
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def agent_project(start: Path | None = None) -> Project | None:
    """The set-up project an agent working in ``start`` reads from: this checkout's, or, in a linked git
    worktree without a store of its own, the main checkout's (same repository, same memory; a worktree's
    agent wiring and store are not committed, so a new worktree has neither). None where Cairn is not set
    up, and never the home folder (``~/.cairn`` is Cairn's own store, not a project)."""
    here = (start or Path.cwd()).resolve()
    proj = Project.discover(here)
    root = proj.root if proj else here
    try:
        if root.resolve() == Path.home().resolve():
            return None
    except OSError:
        return None
    if proj is not None and proj.db_path.exists():
        return proj
    from .engines.recall.projects import detect_worktree
    wt = detect_worktree(root)
    if wt.is_worktree and wt.parent_repo_path:
        parent = Project.discover(Path(wt.parent_repo_path))
        if parent is not None and parent.db_path.exists():
            return parent
    return None
