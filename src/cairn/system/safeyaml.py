"""One safe YAML reader for everything the system model reads (catalog rules, system.yaml additions,
hand-made diagrams). SafeLoader only (no Python object tags), bounded size, nesting and aliases."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .limits import MAX_DIAGRAM_BYTES, MAX_YAML_ALIASES, MAX_YAML_DEPTH, MAX_YAML_NODES


class UnsafeYAML(ValueError):
    """The document breaks a safety limit (size, depth, aliases) or is not valid YAML."""


class _Loader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self._aliases = 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            self._aliases += 1
            if self._aliases > MAX_YAML_ALIASES:
                raise UnsafeYAML(f"more than {MAX_YAML_ALIASES} aliases")
        return super().compose_node(parent, index)


def _check_shape(doc: Any) -> None:
    """Depth and expanded size, computed once per shared node: an alias-heavy document is measured by
    what it would expand to (the billion-laughs case) without ever expanding it."""
    sizes: dict[int, int] = {}

    def size(x: Any, d: int) -> int:
        if d > MAX_YAML_DEPTH:
            raise UnsafeYAML(f"nested deeper than {MAX_YAML_DEPTH}")
        if not isinstance(x, (dict, list)):
            return 1
        key = id(x)
        if key in sizes:
            return sizes[key]
        kids = x.values() if isinstance(x, dict) else x
        n = 1
        for v in kids:
            n += size(v, d + 1)
            if n > MAX_YAML_NODES:
                raise UnsafeYAML(f"expands to more than {MAX_YAML_NODES} nodes")
        sizes[key] = n
        return n

    size(doc, 0)


def loads(text: str | bytes, *, max_bytes: int = MAX_DIAGRAM_BYTES) -> Any:
    data = text.encode("utf-8", "replace") if isinstance(text, str) else text
    if len(data) > max_bytes:
        raise UnsafeYAML(f"larger than {max_bytes} bytes")
    try:
        loader = _Loader(data.decode("utf-8", "replace"))
        try:
            doc = loader.get_single_data()
        finally:
            loader.dispose()
    except UnsafeYAML:
        raise
    except RecursionError as exc:
        raise UnsafeYAML("nested too deeply") from exc
    except yaml.YAMLError as exc:
        raise UnsafeYAML(f"not valid YAML: {str(exc).splitlines()[0][:160]}") from exc
    _check_shape(doc)
    return doc


def load_file(path: Path, *, max_bytes: int = MAX_DIAGRAM_BYTES) -> Any:
    path = Path(path)
    size = path.stat().st_size
    if size > max_bytes:
        raise UnsafeYAML(f"{path.name} is larger than {max_bytes} bytes")
    with open(path, "rb") as fh:
        return loads(fh.read(max_bytes + 1), max_bytes=max_bytes)
