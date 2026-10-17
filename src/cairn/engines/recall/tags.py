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
# one of its keywords occurs in the text (cheap, case-insensitive substring test), so ordinary text costs
# almost nothing. A pattern with one capturing group keeps that group (a label) and masks the rest.
_B = r"(?<![A-Za-z0-9_])"
SECRET_PATTERNS: tuple[tuple[str, tuple[str, ...], re.Pattern], ...] = tuple(
    (pid, tuple(k.lower() for k in kws), re.compile(rx)) for pid, kws, rx in (
        ("private-key", ("-----BEGIN",),
         r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|\Z)"),
        # userinfo runs to the LAST '@' before the host, so a password may itself contain '@'
        ("url-credentials", ("://",),
         r"(?<=://)[^\s/:@'\"<>]+:[^\s/'\"<>]*(?=@[^@\s/'\"<>]+(?:[/:?#\s'\"<>]|$))"),
        ("basic-auth", ("authorization",), r"(?i)(authorization\s*[:=]\s*['\"]?(?:basic|digest)\s+)[A-Za-z0-9+/=_-]{8,}"),
        ("aws-secret-key", ("aws",),
         r"(?i)(aws[_-]?secret[_-]?(?:access[_-]?)?key[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
        ("slack-webhook", ("hooks.slack.com",),
         r"https://hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9/_-]{16,}"),
        ("huggingface-token", ("hf_",), _B + r"hf_[A-Za-z0-9]{34,}"),
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
        # a Twilio SID is a two-letter prefix plus exactly 32 lowercase hex digits, never part of a longer word
        ("twilio-key", ("SK", "AC"), _B + r"(?:SK|AC)(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{32}(?![A-Za-z0-9])"),
        ("google-api-key", ("AIza",), _B + r"AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"),
        ("azure-storage-key", ("AccountKey=",), r"(?<=AccountKey=)[A-Za-z0-9+/]{60,}={0,2}"),
    ))

# ``name = value`` / ``"name": "value"`` / ``--flag value`` for credential-looking names. Whether the value is
# masked depends on what it looks like (see _value_is_secret), so prose and code are left alone.
_NAME = r"[\w-]*(?:token|secret|password|passwd|api[_-]?key|private[_-]?key|credential)[\w-]*"
KV_RE = re.compile(
    r"(?i)\b(?P<key>" + _NAME + r")(?P<kq>[\"']?)(?P<sep>\s*[=:]\s*)(?P<vq>[\"']?)(?P<val>[^\s'\"]+)(?P=vq)")
FLAG_RE = re.compile(r"(?i)(?P<flag>--[\w-]*(?:token|secret|password|api-key)[\w-]*)(?P<sep>\s+)(?P<val>[^\s'\"]+)")
BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[\w.~+/-]+=*")
SENSITIVE_KEY_RE = re.compile(
    r"(?i)^(?:authorization|proxy-authorization|" + _NAME + r"|[\w-]*cookie)$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z_]*(?:\.[A-Za-z_][A-Za-z_]*)+$")
_PLACEHOLDER_RE = re.compile(r"^(?:\$|<|\{\{|\*{3}|\[redacted\]|%\w)", re.I)
_NOT_VALUES = {"true", "false", "none", "null", "nil", "undefined", "str", "string", "int", "bool"}


def _value_is_secret(val: str, tight: bool) -> bool:
    """Does ``val`` look like a credential? ``tight`` means ``key=value`` or a quoted value (no prose spacing)."""
    if not val or val.lower() in _NOT_VALUES or val.isdigit() or _PLACEHOLDER_RE.match(val):
        return False
    if "(" in val or ")" in val or _IDENT_RE.match(val):
        return False  # a call or an attribute chain is code, not a value
    if tight:
        return True
    has_digit = any(c.isdigit() for c in val)
    has_symbol = any(not (c.isalnum() or c in "_-") for c in val)
    return len(val) >= 8 and (has_digit or has_symbol) and any(c.isalpha() for c in val)


def _mask_kv(m: re.Match) -> str:
    tight = not any(c.isspace() for c in m.group("sep")) or bool(m.group("vq"))
    if not _value_is_secret(m.group("val"), tight):
        return m.group(0)
    return f"{m.group('key')}{m.group('kq')}{m.group('sep')}{m.group('vq')}***{m.group('vq')}"


def _mask_flag(m: re.Match) -> str:
    if not _value_is_secret(m.group("val"), True):
        return m.group(0)
    return f"{m.group('flag')} ***"


def _apply(rx: re.Pattern, text: str) -> str:
    return rx.sub((lambda m: m.group(1) + "***") if rx.groups else "***", text)


def _candidates(text: str):
    low = text.lower()
    return [(pid, rx) for pid, kws, rx in SECRET_PATTERNS if any(k in low for k in kws)]


def redact_hits(text: str) -> list[str]:
    """Ids of every pattern that matches ``text`` (never the matched value)."""
    text = text or ""
    hits = [pid for pid, rx in _candidates(text) if rx.search(text)]
    if any(_mask_kv(m) != m.group(0) for m in KV_RE.finditer(text)) \
            or any(_mask_flag(m) != m.group(0) for m in FLAG_RE.finditer(text)) or BEARER_RE.search(text):
        hits.append("key-value")
    return hits


def redact_shapes(text: str) -> str:
    """Mask only the high-precision token shapes (for prose, where ``name: value`` is ordinary wording)."""
    text = text or ""
    for _pid, rx in _candidates(text):
        text = _apply(rx, text)
    return text


def redact(text: str) -> str:
    text = KV_RE.sub(_mask_kv, redact_shapes(text))
    text = FLAG_RE.sub(_mask_flag, text)
    return BEARER_RE.sub(lambda m: f"{m.group(1)} ***", text)


def redact_value(value, _depth: int = 0, _fn=None, _key: str | None = None):
    """Redact every string inside a JSON-shaped value (dicts, lists), leaving its structure intact.
    Under a credential-looking dict key (``password``, ``API_KEY``, ``Authorization``...) the whole string
    value is masked. Passing ``_fn=redact_shapes`` is the prose mode: shapes only, keys are not consulted."""
    fn = _fn or redact
    if _depth > 12:
        return value
    if isinstance(value, str):
        if _fn is None and _key and SENSITIVE_KEY_RE.match(_key) and _value_is_secret(value, True):
            return "***"
        return fn(value)
    if isinstance(value, list):
        return [redact_value(v, _depth + 1, _fn, _key) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(v, _depth + 1, _fn, k if isinstance(k, str) else None) for k, v in value.items()}
    return value
