"""Repository discovery, paths and configuration.

Everything Cairn knows about a repository lives in ``<repo>/.cairn/``. Only ``config.toml`` is
meant to be committed; the rest is local, rebuildable state.
"""
from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = """\
# Cairn project configuration. Every key is optional; delete what you don't change.

[server]
port = 4747                      # first free port from here is used

[context]
budget = 1800                    # default token budget for agent-facing context packs

[models]
provider = "anthropic"           # anthropic | openai  (openai = any OpenAI-compatible endpoint)
fast = "claude-haiku-4-5-20251001"   # classification, summaries at volume
balanced = "claude-sonnet-5"         # extraction, linking tie-breaks
deep = "claude-opus-5-5"             # why / impact / drift synthesis, ask
frontier = "claude-fable-5-1"        # opt-in whole-system reviews only
# base_url = "http://localhost:11434/v1"

[deep]
enabled = "auto"                 # auto = on when a model key is present
budget_tokens = 150000           # hard ceiling per deep sync
graph_url = ""                   # falkor://host:6379 or bolt://host:7687 ; empty = embedded store

[history]
max_commits = 3000

[sessions]
project = ""                     # session-capture project name if it differs from the folder name
"""

DEFAULTS: dict[str, Any] = tomllib.loads(DEFAULT_CONFIG)


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


@dataclass
class Project:
    root: Path
    config: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))

    # ---- construction -------------------------------------------------------------------------
    @classmethod
    def discover(cls, start: Path | None = None, *, allow_non_git: bool = True) -> "Project | None":
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
        if self.config_path.exists():
            try:
                cfg = _merge(cfg, tomllib.loads(self.config_path.read_text()))
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
        return self.root / "graphify-out"

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
            self.config_path.write_text(DEFAULT_CONFIG)
        gi = self.dir / ".gitignore"
        if not gi.exists():
            gi.write_text("*\n!config.toml\n!.gitignore\n")

    def git(self, *args: str, timeout: int = 60) -> str:
        """Run git in the repo; return stdout ('' on failure)."""
        try:
            res = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                                 text=True, timeout=timeout, errors="replace")
            return res.stdout if res.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def cfg(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.config
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur
