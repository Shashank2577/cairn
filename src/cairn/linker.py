"""Linker: resolves the same thing across layers into one registry.

Deterministic rules (see data-model.md). Map symbols and files become entities; memories and facts
are linked to the files and symbols they mention (INFERRED, confidence 0.7).
"""
from __future__ import annotations

import re
from pathlib import Path

from .engines.mapper import MapIndex, key
from .project import Project
from .store import Brain

GENERIC = frozenset("""self this that data value values result results item items list dict type types name names
string object error errors config client server request response default params param args kwargs test tests
main init index model models util utils helper helpers base core common app user users file files path paths
node nodes edge edges event events state status count total time date info message messages handler handlers
""".split())
MAX_SYMBOLS = 150_000


def ingest_map(project: Project, brain: Brain, idx: MapIndex) -> dict:
    """Mirror map files/symbols/rationale into the read model when the map changed."""
    if not len(idx):
        return {"symbols": 0, "note": "map empty"}
    if brain.get_kv("map.mtime") == str(idx.mtime):
        return {"symbols": 0, "note": "unchanged"}
    brain.drop_source("map", kinds=("file", "symbol", "rationale"))
    ents, links = [], []
    for path, ids in idx.by_file.items():
        ents.append((f"file:{path}", "file", path, path, {"symbols": len(ids), "ext": Path(path).suffix}, "map",
                     Path(path).stem))
    n = 0
    for nid, node in idx.nodes.items():
        ftype = node.get("file_type")
        f = node.get("source_file")
        if ftype == "rationale":
            ents.append((f"rationale:{nid}", "rationale", node.get("label", "")[:200], f,
                         {"loc": node.get("source_location")}, "map", node.get("label", "")))
            continue
        if ftype not in ("code", "document", "concept") or n >= MAX_SYMBOLS:
            continue
        label = idx.label(nid)
        if f and label in (Path(f).name, f):  # file-level node is represented by file:<path>
            continue
        n += 1
        ents.append((f"symbol:{nid}", "symbol", label, f,
                     {"loc": node.get("source_location"), "area": node.get("community_name"), "type": ftype,
                      "degree": idx.degree(nid)}, "map", f or ""))
        if f:
            links.append((f"symbol:{nid}", f"file:{f}", "defined_in", "EXTRACTED", 1.0, "map"))
    brain.put_entities(ents)
    brain.link(links)
    brain.set_kv("map.mtime", str(idx.mtime))
    return {"symbols": n, "files": len(idx.by_file)}


def mention_index(idx: MapIndex) -> dict[str, list[str]]:
    """Labels specific enough to link free text to code (≥4 chars, not generic, ≤3 homonyms)."""
    out: dict[str, list[str]] = {}
    for k, ids in idx.by_label.items():
        if len(k) >= 4 and k not in GENERIC and len(ids) <= 3 and re.fullmatch(r"[a-z_][\w]*", k):
            out[k] = ids
    return out


_TOKEN = re.compile(r"[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*|[\w./-]+\.[A-Za-z0-9]{1,6}")


def link_text(brain: Brain, idx: MapIndex, entity_id: str, text: str, source: str,
              mentions: dict[str, list[str]] | None = None) -> int:
    """Link an entity (memory/fact) to files/symbols it mentions."""
    mentions = mentions if mentions is not None else mention_index(idx)
    rows = []
    for tok in set(_TOKEN.findall(text)):
        if "/" in tok or (Path(tok).suffix and tok in idx.by_file):
            if tok.lstrip("./") in idx.by_file:
                rows.append((entity_id, f"file:{tok.lstrip('./')}", "mentions", "INFERRED", 0.8, source))
            continue
        for part in {tok, tok.split(".")[-1]}:
            k = key(part)
            if k in mentions:
                rows.append((entity_id, f"label:{k}", "mentions", "INFERRED", 0.7, source))
                for nid in mentions[k]:
                    f = idx.file_of(nid)
                    if f:
                        rows.append((entity_id, f"file:{f}", "mentions", "INFERRED", 0.7, source))
    if rows:
        brain.link(rows)
    return len(rows)


def relink_memories(brain: Brain, idx: MapIndex) -> int:
    mentions = mention_index(idx)
    brain.drop_source("memory-links")
    n = 0
    for m in brain.memories():
        n += link_text(brain, idx, f"memory:{m['id']}", m["text"], "memory-links", mentions)
    for f in brain.entities("fact", limit=20_000):
        n += link_text(brain, idx, f["id"], f["name"], "memory-links", mentions)
    return n
