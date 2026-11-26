"""The model stage's one entry point so far: what may leave the machine.

Nothing here calls a model yet. `sanitize` is the single gate every string must pass before it can reach a
model or a posted comment; it reuses Cairn's own masking so improvements to the masking patterns apply here too.
"""
from __future__ import annotations

from ..engines.recall.tags import redact_value, strip_tags


def sanitize(value):
    """Mask credentials and remove ``<private>`` blocks from a string or a JSON-shaped value (keys included)."""
    if isinstance(value, str):
        return redact_value(strip_tags(value)[0])
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, dict):
        return {sanitize(k) if isinstance(k, str) else k: sanitize(v) for k, v in value.items()}
    return value


def complete(router, task: str, prompt: str, **kw):
    """The only way review code reaches a model: every string argument passes `sanitize` first.

    Nothing else in this package may call ``router.complete`` directly (a test enforces it).
    """
    return router.complete(sanitize(task), sanitize(prompt), **{k: sanitize(v) for k, v in kw.items()})
