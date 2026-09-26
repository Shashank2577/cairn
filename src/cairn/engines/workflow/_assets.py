"""Bundle path resolution and version lookup for the Cairn workflow engine.

Stdlib-only; zero internal imports so it sits at the base of the dependency
graph without risk of circular imports.

Everything the workflow needs (page templates, command templates, scripts,
bundled extensions/presets/workflows/bundles and the catalogs that list them)
ships inside the package under ``assets/``. Nothing is ever downloaded to set
up or refresh a project.
"""
from __future__ import annotations

import io
import os
import re
from email.message import Message
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.response import addinfourl

# Version of the workflow engine API. Extension, preset, workflow and bundle
# manifests declare ``requires.workflow_version`` constraints against it, and
# it is recorded in ``.cairn/workflow/init-options.json``.
WORKFLOW_ENGINE_VERSION = "1.0.12"

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# ``builtin://<name>`` catalog URLs served from the packaged assets.
BUILTIN_SCHEME = "builtin"
_BUILTIN_CATALOGS: dict[str, str] = {
    "extensions": "extensions/catalog.json",
    "presets": "presets/catalog.json",
    "workflows": "workflows/catalog.json",
    "steps": "workflows/step-catalog.json",
    "integrations": "integrations/catalog.json",
    "default": "bundles/catalog.json",  # first-party bundle catalog
}


def _locate_core_pack() -> Path | None:
    """Return the filesystem path to the bundled assets directory, or None."""
    return ASSETS_DIR if ASSETS_DIR.is_dir() else None


def _repo_root() -> Path:
    """Return the root that source-checkout fallbacks resolve against.

    The engine always runs from its packaged assets, so the fallback root is
    the assets directory itself (lookups such as ``<root>/extensions/<id>``
    resolve to the same bundled files).
    """
    return ASSETS_DIR


def _locate_bundled_extension(extension_id: str) -> Path | None:
    """Return the path to a bundled extension, or None."""
    if not re.match(r'^[a-z0-9-]+$', extension_id):
        return None
    candidate = ASSETS_DIR / "extensions" / extension_id
    if (candidate / "extension.yml").is_file():
        return candidate
    return None


def _locate_bundled_workflow(workflow_id: str) -> Path | None:
    """Return the path to a bundled workflow directory, or None."""
    if not re.match(r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$', workflow_id):
        return None
    candidate = ASSETS_DIR / "workflows" / workflow_id
    if (candidate / "workflow.yml").is_file():
        return candidate
    return None


def _locate_bundled_preset(preset_id: str) -> Path | None:
    """Return the path to a bundled preset, or None."""
    if not re.match(r'^[a-z0-9-]+$', preset_id):
        return None
    candidate = ASSETS_DIR / "presets" / preset_id
    if (candidate / "preset.yml").is_file():
        return candidate
    return None


def get_workflow_version() -> str:
    """Return the workflow engine version used for compatibility checks."""
    return WORKFLOW_ENGINE_VERSION


def get_cairn_version() -> str:
    """Return the installed Cairn version (display only)."""
    try:
        from cairn import __version__

        return __version__
    except Exception:  # pragma: no cover - cairn is always importable in practice
        return "unknown"


def user_config_dir() -> Path:
    """User-scope workflow configuration: ``$CAIRN_HOME/workflow`` (default ``~/.cairn/workflow``).

    Holds user-level catalog stacks (``*-catalogs.yml``) and ``auth.json``.
    """
    home = os.environ.get("CAIRN_HOME", "").strip()
    base = Path(home).expanduser() if home else Path.home() / ".cairn"
    return base / "workflow"


# ---- builtin:// URLs --------------------------------------------------------------------------
def is_builtin_url(url: str) -> bool:
    try:
        return urlparse(url).scheme.lower() == BUILTIN_SCHEME
    except (TypeError, ValueError):
        return False


def builtin_asset_path(url: str) -> Path:
    """Map a ``builtin://`` URL to the packaged file it names.

    ``builtin://<catalog>`` names one of the packaged catalogs;
    ``builtin://<dir>/<path>`` names a file under ``assets/<dir>/``.
    Raises ``FileNotFoundError`` when the URL does not name a packaged file.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != BUILTIN_SCHEME or not parsed.netloc:
        raise FileNotFoundError(f"Not a built-in asset URL: {url}")
    rel_path = parsed.path.lstrip("/")
    if not rel_path:
        rel = _BUILTIN_CATALOGS.get(parsed.netloc)
        if rel is None:
            raise FileNotFoundError(f"Unknown built-in catalog: {url}")
    else:
        parts = PurePosixPath(rel_path).parts
        if any(p in ("..", "") for p in parts):
            raise FileNotFoundError(f"Invalid built-in asset path: {url}")
        rel = f"{parsed.netloc}/{rel_path}"
    path = (ASSETS_DIR / rel).resolve()
    try:
        path.relative_to(ASSETS_DIR.resolve())
    except ValueError:
        raise FileNotFoundError(f"Invalid built-in asset path: {url}") from None
    if not path.is_file():
        raise FileNotFoundError(f"Built-in asset not found: {url}")
    return path


def open_builtin_url(url: str):
    """Return a urllib-style response for a ``builtin://`` URL (no network)."""
    import urllib.error

    try:
        data = builtin_asset_path(url).read_bytes()
    except FileNotFoundError as exc:
        headers = Message()
        raise urllib.error.HTTPError(url, 404, str(exc), headers, io.BytesIO(b""))
    headers = Message()
    headers["Content-Length"] = str(len(data))
    return addinfourl(io.BytesIO(data), headers, url, code=200)


def normalize_legacy_requires(data):
    """Map a legacy host-version constraint onto ``requires.workflow_version``.

    Extension, preset, integration, workflow and bundle manifests written for the
    engine this one descends from name the host-version constraint differently. When
    ``requires`` has no ``workflow_version`` but exactly one other ``*_version`` key,
    that key is the host constraint and is renamed in place. Returns *data*.
    """
    if isinstance(data, dict):
        requires = data.get("requires")
        if isinstance(requires, dict) and "workflow_version" not in requires:
            keys = [k for k in requires if isinstance(k, str) and k.endswith("_version")]
            if len(keys) == 1:
                requires["workflow_version"] = requires.pop(keys[0])
    return data
