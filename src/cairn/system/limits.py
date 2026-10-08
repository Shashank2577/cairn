"""Resource limits for everything the system model reads. One place, so tests and readers agree.

Inputs come from repositories Cairn did not write and from hand-made diagram files, so every reader is
bounded: file size, YAML nesting and alias expansion, element and relationship counts, label length.
"""
from __future__ import annotations

MAX_FILE_BYTES = 1_000_000          # a source, manifest or deploy file larger than this is skipped
MAX_DIAGRAM_BYTES = 2_000_000       # a hand-made YAML or Mermaid diagram file
MAX_YAML_DEPTH = 40                 # nesting depth accepted from any YAML document
MAX_YAML_ALIASES = 100              # alias references per document
MAX_YAML_NODES = 200_000            # nodes after alias expansion (billion-laughs guard)
MAX_ELEMENTS = 5_000                # per repository model
MAX_RELATIONSHIPS = 20_000          # per repository model
MAX_EVIDENCE_PER_CLAIM = 20         # evidence references kept per element or relationship
MAX_LABEL_CHARS = 200               # any name, label or description is cut to this
MAX_FILES_SCANNED = 50_000          # files considered per repository
