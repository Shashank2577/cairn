"""The diagram standard as code: description schema, checker, layout, SVG, evidence table, Mermaid.

Pipeline: ``schema.loads`` -> ``check.check`` -> ``layout.layout`` -> ``svg.render`` (and ``alt.evidence_table``).
Mermaid in and out live in ``mermaid_in`` and ``mermaid_out``. Everything reads its colours, type sizes,
budgets and dashes from ``tokens.json`` (this folder) and makes no model calls.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

TOKENS_PATH = Path(__file__).with_name("tokens.json")


@lru_cache(maxsize=1)
def _tokens() -> dict:
    with open(TOKENS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def load_tokens() -> dict:
    """The canonical token file as a dict. Treat it as read-only (it is cached and shared)."""
    return _tokens()
