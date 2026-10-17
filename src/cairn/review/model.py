"""The model stage's one entry point so far: what may leave the machine.

Nothing here calls a model yet. `sanitize` is the single gate every string must pass before it can reach a
model or a posted comment; it reuses Cairn's own masking so improvements to the masking patterns apply here too.
"""
from __future__ import annotations

from ..engines.recall.tags import redact_value, strip_tags


def sanitize(value):
    """Mask credentials and remove ``<private>`` blocks from a string or a JSON-shaped value."""
    if isinstance(value, str):
        return redact_value(strip_tags(value)[0])
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    return value
