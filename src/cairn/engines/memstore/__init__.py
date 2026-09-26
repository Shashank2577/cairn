"""Cairn's self-reconciling memory engine ("Memory").

    from cairn.engines.memstore import Memory
    m = Memory.from_config({...}, router=router)
    m.add("We use pnpm, never npm", project_id="shop")
    m.search("package manager", filters={"project_id": "shop"})

See ``main.Memory`` for the full API and ``configs.base.MemoryConfig`` for configuration.
Names are imported on first use, so importing a small helper module (e.g. ``utils.text``) stays cheap.
"""
import importlib

_EXPORTS = {
    "AsyncMemory": "cairn.engines.memstore.main",
    "Memory": "cairn.engines.memstore.main",
    "GraphStoreConfig": "cairn.engines.memstore.configs.base",
    "MemoryConfig": "cairn.engines.memstore.configs.base",
    "MemoryItem": "cairn.engines.memstore.configs.base",
    "MemoryType": "cairn.engines.memstore.configs.enums",
    "SCOPE_FIELD": "cairn.engines.memstore.scopes",
    "SCOPE_KEYS": "cairn.engines.memstore.scopes",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        value = getattr(importlib.import_module(_EXPORTS[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
