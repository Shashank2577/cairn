"""Browser libraries the graph views use, shipped with the package (offline).

* ``vis-network.min.js`` 9.1.6 — interactive graph view (``export html``)
* ``d3.v7.min.js`` 7.9.0 — collapsible tree view (``tree``)
* ``mermaid.min.js`` 11.17.2 — call-flow / architecture diagrams (``export callflow-html``)

Each file keeps its own licence header. A view is written either self-contained
(the library inlined — works from ``file://`` with no network) or, when an
``asset_base`` URL is given, with ``<script src="{asset_base}{name}">`` so a
server can serve the library once from :func:`asset_path`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ASSET_DIR = Path(__file__).with_name("assets")

ASSETS: dict[str, dict[str, str]] = {
    "vis-network.min.js": {"library": "vis-network", "version": "9.1.6",
                           "licence": "Apache-2.0 OR MIT", "media_type": "text/javascript"},
    "d3.v7.min.js": {"library": "d3", "version": "7.9.0", "licence": "ISC",
                     "media_type": "text/javascript"},
    "mermaid.min.js": {"library": "mermaid", "version": "11.17.2", "licence": "MIT",
                       "media_type": "text/javascript"},
}


def asset_path(name: str) -> Path:
    """Filesystem path of a bundled asset. Raises KeyError for unknown names (so a
    server route can never be walked outside the asset directory)."""
    if name not in ASSETS:
        raise KeyError(name)
    return ASSET_DIR / name


@lru_cache(maxsize=None)
def asset_text(name: str) -> str:
    return asset_path(name).read_text(encoding="utf-8")


def script_tag(name: str, asset_base: str | None = None) -> str:
    """``<script>`` element for a bundled library: inlined when ``asset_base`` is
    None, otherwise referenced as ``{asset_base}{name}``."""
    if asset_base is not None:
        from html import escape
        return f'<script src="{escape(asset_base + name, quote=True)}"></script>'
    # A literal "</script" inside the library would end the element early.
    body = asset_text(name).replace("</script", "<\\/script")
    return f"<script>\n{body}\n</script>"
