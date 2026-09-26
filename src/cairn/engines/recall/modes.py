"""Observation vocabularies ("modes"): observation types, concepts and the observer's prompt text.

A mode is a JSON file. ``code`` is the default; ``code--ja`` is ``code`` deep-merged with the
Japanese override; user modes live in ``$CAIRN_HOME/modes`` (or ``CAIRN_RECALL_MODES_DIR``) and win
over the built-in ones. Standard library only.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("cairn.recall")

BUILTIN_DIR = Path(__file__).resolve().parent / "modes"
MODE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:--[a-z0-9]+(?:-[a-z0-9]+)*)?$")
DEFAULT_MODE = "code"


def mode_dirs() -> list[Path]:
    dirs = []
    env = os.environ.get("CAIRN_RECALL_MODES_DIR")
    if env:
        dirs.append(Path(env))
    home = Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn")
    dirs += [home / "modes", BUILTIN_DIR]
    return list(dict.fromkeys(dirs))


def _deep_merge(base: Any, over: Any) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _deep_merge(base.get(k), v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
        return out
    return over


def _load_file(mode_id: str) -> dict:
    if not MODE_ID.match(mode_id):
        raise ValueError(f"Invalid mode ID: {mode_id}")
    for d in mode_dirs():
        p = d / f"{mode_id}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Mode file not found: {mode_id}.json")


class Mode:
    def __init__(self, mode_id: str, config: dict):
        self.id = mode_id
        self.config = config

    @property
    def name(self) -> str:
        return self.config.get("name", self.id)

    @property
    def observation_types(self) -> list[dict]:
        return list(self.config.get("observation_types") or [])

    @property
    def observation_concepts(self) -> list[dict]:
        return list(self.config.get("observation_concepts") or [])

    @property
    def prompts(self) -> dict:
        return dict(self.config.get("prompts") or {})

    def type_ids(self) -> list[str]:
        return [t["id"] for t in self.observation_types]

    def concept_ids(self) -> list[str]:
        return [c["id"] for c in self.observation_concepts]

    def type_icon(self, type_id: str) -> str:
        return next((t.get("emoji") or "\U0001F4DD" for t in self.observation_types if t["id"] == type_id), "\U0001F4DD")

    def work_emoji(self, type_id: str) -> str:
        return next((t.get("work_emoji") or "\U0001F4DD" for t in self.observation_types if t["id"] == type_id),
                    "\U0001F4DD")

    def label(self) -> str:
        return f"{self.name} ({self.id})"


_cache: dict[str, Mode] = {}


def load_mode(mode_id: str | None = None) -> Mode:
    """Load a mode (with one level of ``parent--override`` inheritance), falling back to ``code``."""
    mode_id = (mode_id or DEFAULT_MODE).strip() or DEFAULT_MODE
    if mode_id in _cache:
        return _cache[mode_id]
    parts = mode_id.split("--")
    if len(parts) > 2:
        log.warning("invalid mode inheritance %s, using %s", mode_id, DEFAULT_MODE)
        return load_mode(DEFAULT_MODE)
    if len(parts) == 1:
        try:
            mode = Mode(mode_id, _load_file(mode_id))
        except (OSError, ValueError) as exc:
            if mode_id == DEFAULT_MODE:
                raise RuntimeError("the built-in code mode is missing") from exc
            log.warning("mode %s unavailable (%s), falling back to %s", mode_id, exc, DEFAULT_MODE)
            return load_mode(DEFAULT_MODE)
    else:
        parent = load_mode(parts[0])
        try:
            override = _load_file(mode_id)
        except (OSError, ValueError) as exc:
            log.warning("mode override %s unavailable (%s), using %s", mode_id, exc, parent.id)
            return parent
        mode = Mode(mode_id, _deep_merge(parent.config, override))
    _cache[mode_id] = mode
    return mode


def available_modes() -> list[dict]:
    seen: dict[str, dict] = {}
    for d in mode_dirs():
        for p in sorted(d.glob("*.json")) if d.is_dir() else []:
            if p.stem in seen:
                continue
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            seen[p.stem] = {"id": p.stem, "name": cfg.get("name", p.stem), "description": cfg.get("description", ""),
                            "builtin": d == BUILTIN_DIR}
    return list(seen.values())


def clear_cache() -> None:
    _cache.clear()
