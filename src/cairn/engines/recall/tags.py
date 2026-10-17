"""Privacy and protocol tag handling (standard library only).

Anything a user wraps in ``<private>...</private>`` never reaches the store. Cairn's own injected
context (``<cairn-context>`` and the AGENTS.md memory block), system reminders and persisted tool output
are stripped too, so memory is never built out of memory.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("cairn.recall")

TAG_NAMES = (
    "private",
    "cairn-context",
    "system_instruction",
    "system-instruction",
    "persisted-output",
    "system-reminder",
)
STRIP_RE = re.compile(r"<(" + "|".join(re.escape(t) for t in TAG_NAMES) + r")\b[^>]*>[\s\S]*?</\1>")
# the memory block Cairn keeps in AGENTS.md (comment-delimited, so every agent's instruction loader keeps it)
MEMORY_BLOCK_RE = re.compile(r"<!-- cairn:memory:begin -->[\s\S]*?<!-- cairn:memory:end -->")
SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>")
MAX_TAG_COUNT = 100

PROTOCOL_ONLY_TAGS = ("task-notification",)
_PROTOCOL_RE = re.compile(
    r"^\s*<(" + "|".join(PROTOCOL_ONLY_TAGS) + r")\b[^>]*>(?:(?!<\1\b|</\1\b)[\s\S])*</\1>\s*$")
MAX_PROTOCOL_PAYLOAD_CHARS = 256 * 1024

MAX_STORED_PROMPT_CHARS = 4000


def strip_tags(text: str) -> tuple[str, dict[str, int]]:
    """Remove every tagged block; returns (stripped text, count per tag)."""
    counts = {name: 0 for name in TAG_NAMES}

    def drop(m: re.Match) -> str:
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
        return ""

    stripped = MEMORY_BLOCK_RE.sub("", STRIP_RE.sub(drop, text or ""))
    total = sum(counts.values())
    if total > MAX_TAG_COUNT:
        log.warning("tag count exceeds limit: %s (max %s, %s chars)", total, MAX_TAG_COUNT, len(text or ""))
    return stripped.strip(), counts


def strip_memory_tags(text: str) -> str:
    return strip_tags(text)[0]


def is_internal_protocol_payload(text: str) -> bool:
    """A prompt that is only an agent-protocol envelope (e.g. a task notification) is not user work."""
    if not text or len(text) > MAX_PROTOCOL_PAYLOAD_CHARS:
        return False
    return bool(_PROTOCOL_RE.match(text))


def normalize_stored_prompt_text(prompt: str) -> str:
    """The prompt as stored: private blocks removed, bounded in size."""
    raw = (prompt or "").strip()
    stripped = strip_memory_tags(prompt or "").strip()
    preferred = stripped or raw
    if len(preferred) <= MAX_STORED_PROMPT_CHARS:
        return preferred
    return preferred[: MAX_STORED_PROMPT_CHARS - 1] + "…"


# High-precision credential shapes. Each row: (id, prefilter keywords, pattern). A row is only tried when
# one of its keywords occurs in the text (cheap substring test), so ordinary text costs almost nothing.
# Values are masked whole.
_B = r"(?<![A-Za-z0-9_])"
SECRET_PATTERNS: tuple[tuple[str, tuple[str, ...], re.Pattern], ...] = tuple(
    (pid, kws, re.compile(rx)) for pid, kws, rx in (
        ("private-key", ("-----BEGIN",),
         r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|\Z)"),
        ("url-credentials", ("://",), r"(?<=://)[^\s/:@'\"<>]+:[^\s/@'\"<>]+(?=@[^\s/'\"<>]+)"),
        ("github-token", ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"),
         _B + r"(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,255})"),
        ("gitlab-token", ("glpat-",), _B + r"glpat-[A-Za-z0-9_-]{20,}"),
        ("aws-access-key-id", ("AKIA", "ASIA", "ABIA", "ACCA"), _B + r"(?:AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}(?![A-Za-z0-9])"),
        ("jwt", ("eyJ",), _B + r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        ("slack-token", ("xox", "xapp-"), _B + r"(?:xox[abposr]-[A-Za-z0-9-]{10,}|xapp-\d-[A-Za-z0-9-]{10,})"),
        ("stripe-key", ("sk_live_", "rk_live_", "sk_test_", "rk_test_"), _B + r"[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),
        ("anthropic-key", ("sk-ant-",), _B + r"sk-ant-[A-Za-z0-9_-]{20,}"),
        ("openai-key", ("sk-",), _B + r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}"),
        ("npm-token", ("npm_",), _B + r"npm_[A-Za-z0-9]{36}(?![A-Za-z0-9])"),
        ("pypi-token", ("pypi-",), _B + r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20,}"),
        ("sendgrid-key", ("SG.",), _B + r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),
        ("twilio-key", ("SK", "AC"), _B + r"(?:SK|AC)[0-9a-f]{32}(?![A-Za-z0-9])"),
        ("google-api-key", ("AIza",), _B + r"AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"),
        ("azure-storage-key", ("AccountKey=",), r"(?<=AccountKey=)[A-Za-z0-9+/]{60,}={0,2}"),
    ))

# Values that look like credentials are masked in anything recorded from tool input.
SECRET_RE = re.compile(
    r"(?i)\b([\w-]*(?:token|secret|password|passwd|api[_-]?key)[\w-]*)(\s*[=:]\s*)(['\"]?)[^\s'\"]+\3"
    r"|\b(bearer)\s+[\w.~+/-]+=*|(--[\w-]*(?:token|secret|password|api-key)[\w-]*)\s+\S+")


def _mask_pairs(m: re.Match) -> str:
    if m.group(4):
        return f"{m.group(4)} ***"
    if m.group(5):
        return f"{m.group(5)} ***"
    return f"{m.group(1)}{m.group(2)}***"


def redact_hits(text: str) -> list[str]:
    """Ids of every pattern that matches ``text`` (never the matched value)."""
    text = text or ""
    hits = [pid for pid, kws, rx in SECRET_PATTERNS if any(k in text for k in kws) and rx.search(text)]
    if SECRET_RE.search(text):
        hits.append("key-value")
    return hits


def redact_shapes(text: str) -> str:
    """Mask only the high-precision token shapes (for prose, where ``name: value`` is ordinary wording)."""
    text = text or ""
    for _pid, kws, rx in SECRET_PATTERNS:
        if any(k in text for k in kws):
            text = rx.sub("***", text)
    return text


def redact(text: str) -> str:
    return SECRET_RE.sub(_mask_pairs, redact_shapes(text))


def redact_value(value, _depth: int = 0, _fn=None):
    """Redact every string inside a JSON-shaped value (dicts, lists), leaving its structure intact."""
    fn = _fn or redact
    if _depth > 12:
        return value
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [redact_value(v, _depth + 1, fn) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(v, _depth + 1, fn) for k, v in value.items()}
    return value
