"""Model pins left over from an older default.

`cairn init` writes the default model of each tier into `.cairn/config.toml`, so a repository set up before a default
changed keeps the old one for good. Only a value that is *exactly* a superseded default is treated as stale: it is what
init wrote, not a choice. Anything else (another model, a local endpoint, the current default) is never touched.
"""
from __future__ import annotations

import tomllib

from .project import Project

# tier -> defaults `cairn init` used to write, now superseded. Add one when a template default changes.
SUPERSEDED: dict[str, tuple[str, ...]] = {"fast": ("claude-haiku-4-5-20251001", "claude-haiku-4-5")}


def _local_pin(project: Project, tier: str) -> str:
    try:
        text = project.local_config_path.read_text(encoding="utf-8")
        return str((tomllib.loads(text).get("models") or {}).get(tier) or "")
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def stale_pins(project: Project) -> list[dict]:
    """[{tier, pinned, default}] for each tier whose configured model is a superseded default."""
    from .project import DEFAULTS
    out = []
    for tier, old in SUPERSEDED.items():
        pinned = str(project.cfg(f"models.{tier}", "") or "")
        if pinned in old:
            out.append({"tier": tier, "pinned": pinned, "default": DEFAULTS["models"][tier]})
    return out


def upgrade_pins(project: Project) -> list[dict]:
    """Rewrite each stale pin to the current default, in the file that holds it (a local override stays local)."""
    done = []
    for pin in stale_pins(project):
        key = f"models.{pin['tier']}"
        local = _local_pin(project, pin["tier"]) == pin["pinned"]
        project.set_cfg(key, pin["default"], local=local)
        done.append(pin)
    return done
