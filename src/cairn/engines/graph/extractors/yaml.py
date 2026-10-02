"""Yaml extractor — .yaml/.yml are classified as documents, but unlike .md they had no
AST extractor, so the code-only map build dropped them entirely: a repo's CI pipelines,
policies and role-pack configs were invisible to the graph. This extractor mints a file
node per yaml file (so search, the per-language overview and co-change always see it)
plus a bounded two-level view of its top-level mapping keys (CI job ids, policy names).
Only key NAMES are indexed, never values, so no secret material can reach the graph."""
from __future__ import annotations

import re
from pathlib import Path

from cairn.engines.graph.extractors.base import _file_stem, _make_id, _read_text

_YAML_MAX_BYTES = 1_048_576   # 1 MiB — a yaml corpus item bigger than this is a data dump, not config
_MAX_PAIRS = 200              # total key nodes per file
_MAX_DEPTH = 2                # top-level keys plus one nested level (e.g. jobs -> job ids)

# Fallback for installs without a yaml grammar: a top-level `key:` at column 0.
# Indented keys are skipped so only the file's primary sections surface.
_TOP_LEVEL_KEY_RE = re.compile(r"^([^\s#\"'][^:]*?):(?:\s|$)", re.MULTILINE)


def _key_text(node, source: bytes) -> str | None:
    """The plain text of a mapping key, quotes stripped; None when it carries no name."""
    if node is None:
        return None
    s = _read_text(node, source).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1].strip()
    return s or None


def _value_is_mapping(node) -> bool:
    """True when a pair's value node is a block/flow mapping (worth one more level)."""
    if node is None:
        return False
    if node.type == "block_node":
        for c in node.children:
            if c.type in ("block_mapping", "flow_mapping"):
                return True
    return node.type == "flow_mapping"


def _walk_mapping(mapping, source: bytes, str_path: str, stem: str, file_nid: str,
                  nodes: list, edges: list, seen: set, prefix: tuple[str, ...],
                  depth: int, budget: list) -> None:
    for pair in mapping.children:
        if pair.type != "block_mapping_pair" or budget[0] <= 0:
            continue
        key = _key_text(pair.child_by_field_name("key"), source)
        if not key:
            continue  # a nameless key would collapse ids onto the file node (#1899 shape)
        budget[0] -= 1
        nid = _make_id(stem, *prefix, key)
        if not nid or nid in seen:
            continue
        seen.add(nid)
        line = pair.start_point[0] + 1
        nodes.append({"id": nid, "label": key, "file_type": "document",
                      "source_file": str_path, "source_location": f"L{line}"})
        edges.append({"source": file_nid if depth == 0 else _make_id(stem, *prefix),
                      "target": nid, "relation": "contains", "confidence": "EXTRACTED",
                      "source_file": str_path, "source_location": f"L{line}", "weight": 1.0})
        if depth + 1 < _MAX_DEPTH and _value_is_mapping(pair.child_by_field_name("value")):
            value = pair.child_by_field_name("value")
            inner = next((c for c in value.children if c.type in ("block_mapping", "flow_mapping")), None)
            if inner is not None:
                _walk_mapping(inner, source, str_path, stem, file_nid, nodes, edges, seen,
                              prefix + (key,), depth + 1, budget)


def _parser():
    """A yaml tree-sitter Parser, or None when no grammar is installed.

    The standalone ``tree-sitter-yaml`` wheel is preferred (its ``language()``
    returns a raw capsule that must be wrapped in ``tree_sitter.Language``, as
    json_config does for json); ``tree-sitter-language-pack`` — an optional
    extra that also carries the yaml grammar — is the fallback."""
    try:
        from tree_sitter import Language, Parser
    except ImportError:
        return None
    grammar = None
    try:
        import tree_sitter_yaml
        grammar = Language(tree_sitter_yaml.language())
    except Exception:  # noqa: BLE001 — absent or incompatible grammar: try the pack
        grammar = None
    if grammar is not None:
        return Parser(grammar)
    try:
        from tree_sitter_language_pack import get_language
        return Parser(get_language("yaml"))
    except Exception:  # noqa: BLE001 — no yaml grammar installed
        return None


def extract_yaml(path: Path) -> dict:
    """File node + bounded top-level structure for a .yaml/.yml file.

    Full deep extraction is deliberately avoided: arbitrary yaml is often bulk data
    (k8s manifests, lockfiles, fixtures) and walking it end to end swamps the graph
    the same way data JSON did (#1224). Two levels of top-level keys keep the useful
    names (a workflow's `jobs` and their ids, a policy's sections) without the flood.
    """
    try:
        with path.open("rb") as f:
            source = f.read(_YAML_MAX_BYTES + 1)
        if len(source) > _YAML_MAX_BYTES:
            return {"nodes": [], "edges": [], "error": "yaml file too large to index"}
    except OSError as exc:
        return {"nodes": [], "edges": [], "error": str(exc)}

    stem = _file_stem(path)
    str_path = str(path)
    file_nid = _make_id(str(path))
    nodes: list[dict] = [{"id": file_nid, "label": path.name, "file_type": "document",
                          "source_file": str_path, "source_location": "L1"}]
    edges: list[dict] = []
    seen: set[str] = set()

    parsed = False
    parser = _parser()
    if parser is not None:
        try:
            tree = parser.parse(source)
            for document in tree.root_node.children:
                if document.type != "document":
                    continue
                for child in document.children:
                    if child.type == "block_node":
                        for mapping in child.children:
                            if mapping.type in ("block_mapping", "flow_mapping"):
                                parsed = True
                                _walk_mapping(mapping, source, str_path, stem, file_nid,
                                              nodes, edges, seen, (), 0, [_MAX_PAIRS])
        except Exception:  # noqa: BLE001 — a broken grammar must not take the file out of the map
            parsed = False

    if not parsed:
        # No yaml grammar installed (it ships with tree-sitter-language-pack, an
        # optional extra): degrade to a bare file node plus column-0 keys so the
        # file is still visible to search and the overview on minimal installs.
        nodes = [nodes[0]]
        edges = []
        seen = set()
        for m in _TOP_LEVEL_KEY_RE.finditer(source.decode("utf-8", errors="replace")):
            key = m.group(1).strip()
            nid = _make_id(stem, key)
            if not nid or nid in seen:
                continue
            seen.add(nid)
            line = source[:m.start()].count(b"\n") + 1
            nodes.append({"id": nid, "label": key, "file_type": "document",
                          "source_file": str_path, "source_location": f"L{line}"})
            edges.append({"source": file_nid, "target": nid, "relation": "contains",
                          "confidence": "EXTRACTED", "source_file": str_path,
                          "source_location": f"L{line}", "weight": 1.0})
            if len(nodes) > _MAX_PAIRS:
                break

    return {"nodes": nodes, "edges": edges}
