"""Parse the observer's XML replies and classify replies that are not XML.

``parse_agent_xml`` turns ``<observation>`` blocks (or one ``<summary>``, or ``<skip_summary/>``)
into dicts. The classifiers tell a model that declined (idle/prose, dropped) apart from a provider
refusal that must keep the queued work: quota, authentication, transport failures and context
overflow. Standard library only.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from .modes import Mode

log = logging.getLogger("cairn.recall")

TITLE_MAX_GRAPHEMES = 120
TITLE_TRUNCATE_AT = 117

_FENCE = re.compile(r"^\s*```(?:xml)?\s*\n([\s\S]*?)\n```\s*$", re.IGNORECASE)
_SKIP = re.compile(r'<skip_summary(?:\s+reason="([^"]*)")?\s*/>')
_ROOT = re.compile(r"<(observation|summary)\b", re.IGNORECASE)
_LABEL_TITLE = re.compile(r"^\[\*\*title\*\*:\s*([\s\S]+?)\s*\]$")


def strip_code_fences(text: str) -> str:
    m = _FENCE.match(text)
    return m.group(1) if m else text


def _field(content: str, name: str) -> str | None:
    m = re.search(rf"<{name}>([\s\S]*?)</{name}>", content)
    if not m:
        return None
    v = m.group(1).strip()
    return v or None


def _array(content: str, array: str, element: str) -> list[str]:
    m = re.search(rf"<{array}>([\s\S]*?)</{array}>", content)
    if not m:
        return []
    return [e.strip() for e in re.findall(rf"<{element}>([\s\S]*?)</{element}>", m.group(1)) if e.strip()]


def _unwrap_title(title: str | None) -> str | None:
    if title is None:
        return None
    m = _LABEL_TITLE.match(title)
    if not m:
        return title
    inner = m.group(1).strip()
    return inner or title


def _graphemes(s: str) -> list[str]:
    """Approximate grapheme clusters: a base character plus following combining marks / ZWJ runs."""
    out: list[str] = []
    for ch in s:
        if out and (unicodedata.combining(ch) or ch in "‍️︎" or out[-1].endswith("‍")):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def _unstructured_text(content: str) -> str | None:
    if re.search(r"</?(summary|skip_summary)\b", content, re.IGNORECASE):
        return None
    stripped = re.sub(r"<(type|title|subtitle|narrative|facts|concepts|files_read|files_modified)(?:\s*/>|>[\s\S]*?</\1>)",
                      " ", content)
    stripped = re.sub(r"<[^>]+>", " ", stripped).replace("\r\n", "\n")
    lines = [ln.strip() for ln in stripped.split("\n") if ln.strip()]
    text = "\n".join(lines).strip()
    return text or None


def _fallback_title(text: str) -> tuple[str, str | None]:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    first = lines[0] if lines else text.strip()
    g = _graphemes(first)
    if len(g) > TITLE_MAX_GRAPHEMES:
        title = "".join(g[:TITLE_TRUNCATE_AT]) + "..."
        rest = ["".join(g[TITLE_TRUNCATE_AT:]).strip(), *lines[1:]]
    else:
        title, rest = first, lines[1:]
    narrative = "\n".join(r for r in rest if r).strip()
    return title, narrative or None


def parse_observations(text: str, mode: Mode) -> list[dict]:
    valid = mode.type_ids()
    fallback = valid[0] if valid else "discovery"
    out = []
    for block in re.findall(r"<observation>([\s\S]*?)</observation>", text):
        typ = _field(block, "type")
        title = _unwrap_title(_field(block, "title"))
        subtitle = _field(block, "subtitle")
        narrative = _field(block, "narrative")
        facts = _array(block, "facts", "fact")
        concepts = _array(block, "concepts", "concept")
        files_read = _array(block, "files_read", "file")
        files_modified = _array(block, "files_modified", "file")
        final_type = typ or fallback
        if typ and typ not in valid:
            log.warning("observation type %r is not in mode %s; kept as emitted", typ, mode.id)
        # concepts are matched exactly, so "gotcha: WASM quirk" becomes "gotcha"; the type itself is not a concept
        cleaned = [c.split(":", 1)[0].strip() for c in concepts]
        cleaned = [c for c in cleaned if c and c != final_type]
        if not title and not narrative and not facts and not cleaned:
            salvage = _unstructured_text(block)
            if not salvage:
                continue
            title, narrative = _fallback_title(salvage)
        out.append({"type": final_type, "title": title, "subtitle": subtitle, "facts": facts, "narrative": narrative,
                    "concepts": cleaned, "files_read": files_read, "files_modified": files_modified})
    return out


def parse_summary(text: str) -> dict | None:
    m = re.search(r"<summary>([\s\S]*?)</summary>", text)
    if not m:
        return None
    c = m.group(1)
    s = {k: _field(c, k) for k in ("request", "investigated", "learned", "completed", "next_steps", "notes")}
    if not any(s[k] for k in ("request", "investigated", "learned", "completed", "next_steps")):
        return None  # a <summary> with no sub-tags is a false positive
    return s


def parse_agent_xml(raw: str, mode: Mode) -> dict:
    """``{"valid": True, "observations": [...], "summary": dict|None}`` or ``{"valid": False}``."""
    if not isinstance(raw, str) or not raw.strip():
        return {"valid": False}
    raw = strip_code_fences(raw)
    skip = _SKIP.search(raw)
    if skip:
        return {"valid": True, "observations": [],
                "summary": {"request": None, "investigated": None, "learned": None, "completed": None,
                            "next_steps": None, "notes": None, "skipped": True, "skip_reason": skip.group(1)}}
    root = _ROOT.search(raw)
    if not root:
        return {"valid": False}
    if root.group(1).lower() == "observation":
        obs = parse_observations(raw, mode)
        return {"valid": True, "observations": obs, "summary": None} if obs else {"valid": False}
    summary = parse_summary(raw)
    return {"valid": True, "observations": [], "summary": summary} if summary else {"valid": False}


# ---- non-XML output classification -------------------------------------------------------------------
PREVIEW_LENGTH = 200
_XMLISH = re.compile(r"<(observation|summary)\b|<skip_summary\b", re.IGNORECASE)


def preview_output(raw, max_length: int = PREVIEW_LENGTH) -> str:
    if not isinstance(raw, str):
        return f"(non-string output: {type(raw).__name__})"
    collapsed = " ".join(raw.split())
    if len(collapsed) <= max_length:
        return collapsed
    return f"{collapsed[:max_length]}…(+{len(collapsed) - max_length} chars)"


def classify_observer_output(raw) -> str:
    """xml | idle | prose"""
    if not isinstance(raw, str) or not raw.strip():
        return "idle"
    return "xml" if _XMLISH.search(raw) else "prose"


def _norm(raw: str) -> str:
    return " ".join(raw.lower().split())


def is_quota_limited(raw) -> bool:
    if not isinstance(raw, str) or not raw.strip() or _XMLISH.search(raw):
        return False
    t = _norm(raw)
    pats = [
        r"\byou'?ve (?:hit|reached) your\b.{0,40}\blimit\b",
        r"\bsession limit\b",
        r"\bout of (?:usage )?credits\b",
        r"/usage-credits\b",
        r"\bclaude\b.*\busage\b.*\blimit\b.*\b(reached|exceeded|exhausted|reset|resets|try again)\b",
        r"\b(reached|exceeded|exhausted)\b.*\bclaude\b.*\busage\b.*\blimit\b",
        r"\bweekly\b.*\b(limit|quota)\b.*\b(reached|exceeded|exhausted|reset|resets|try again)\b",
        r"\b(reached|exceeded|exhausted)\b.*\bweekly\b.*\b(limit|quota)\b",
        r"\bsubscription\b.*\b(limit|quota)\b.*\b(reached|exceeded|exhausted|reset|resets|try again)\b",
        r"\b(rate limit|quota)\b.*\b(subscription|weekly|claude usage)\b.*\b(reached|exceeded|exhausted|reset|resets"
        r"|try again)\b",
    ]
    return any(re.search(p, t) for p in pats)


def is_context_overflow(raw) -> bool:
    if not isinstance(raw, str) or not raw.strip() or _XMLISH.search(raw):
        return False
    t = _norm(raw)
    pats = [
        r"\b(?:prompt|input|conversation|request) is too long\b",
        r"\bmaximum context length\b",
        r"\bcontext (?:window|length|limit)\b.{0,30}\b(?:exceeded|too (?:long|large)|overflow)\b",
        r"\bexceeds?\b.{0,30}\bcontext (?:window|length|limit)\b",
        r"\b(?:too many|exceeds the maximum number of) (?:input )?tokens\b",
        r"\breduce the length of the messages\b",
    ]
    return any(re.search(p, t) for p in pats)


def is_auth_failure(raw) -> bool:
    if not isinstance(raw, str) or not raw.strip() or _XMLISH.search(raw):
        return False
    t = _norm(raw)
    pats = [
        r"^not logged in\b\s*[.!]?\s*$",
        r"^not logged in\b\s*[·|:\-–—]\s*(?:please\s+)?run\s+/login\b\s*[.!]?\s*$",
        r"(?:^|[·|]\s*)please run /login\b\s*[.!]?\s*$",
        r"\bfailed to authenticate\b",
        r"\bauthentication (?:failed|failure|error)\b",
        r"\b(?:authentication|auth)\b.{0,20}\b(?:required|expired|invalid|again)\b.{0,20}/login\b",
        r"\b(?:api|http)\s*(?:error\s*)?:?\s*(?:401|403)\b",
        r"\b(?:(?:401|403)\s+(?:unauthorized|forbidden)|status\s*[:=]?\s*(?:401|403)|request failed with\s+(?:401|403))\b",
        r"/login\b.{0,40}\b(?:to\s+authenticate|again|to\s+continue|and\s+retry|reauthenticate|credentials|provider"
        r"|claude)\b",
    ]
    return any(re.search(p, t) for p in pats)


_NETWORK_CONDITION = re.compile(
    r"\b(?:connect|connection|network|socket|dns|proxy|tls|ssl|certificate|unreachable|econnrefused|econnreset"
    r"|etimedout|enotfound|enetunreach|ehostunreach|epipe|econnaborted|eai_again|eproto)\b|\bfetch failed\b"
    r"|\bsocket hang up\b")
_ENVELOPES = {
    "fetch": re.compile(r"^(?:fetch|network|connection)\s*error\b\s*(?::|-|–|—|$)\s*"),
    "api": re.compile(r"^(?:api|http|request)\s*error\b\s*(?::|-|–|—|$)\s*"),
    "bare": re.compile(r"^error\s*:\s*"),
}
_DETAIL_IS_CLAUSE = re.compile(r"\b(?:is|are|was|were|be|been|being|has|have|had)\b")
_CONCRETE_FAILURE = re.compile(
    r"\b(?:econnrefused|econnreset|etimedout|enotfound|enetunreach|ehostunreach|epipe|econnaborted|eai_again|eproto"
    r"|unreachable|refused|timed out)\b|\bfetch failed\b|\bsocket hang up\b|\breset by peer\b")


def _envelope_reports_failure(envelope: re.Pattern, text: str, require_condition: bool) -> bool:
    m = envelope.match(text)
    if not m:
        return False
    detail = text[m.end():].strip()
    if require_condition and not _NETWORK_CONDITION.search(detail):
        return False
    if detail == "" or not _DETAIL_IS_CLAUSE.search(detail):
        return True
    return bool(_CONCRETE_FAILURE.search(detail))


def is_transport_failure(raw) -> bool:
    if not isinstance(raw, str) or not raw.strip() or _XMLISH.search(raw):
        return False
    if is_auth_failure(raw):
        return False
    t = _norm(raw)
    codes = r"(?:econnrefused|econnreset|etimedout|enotfound|enetunreach|ehostunreach|epipe|econnaborted|eai_again|eproto)"
    return (_envelope_reports_failure(_ENVELOPES["fetch"], t, False)
            or _envelope_reports_failure(_ENVELOPES["api"], t, True)
            or _envelope_reports_failure(_ENVELOPES["bare"], t, True)
            or bool(re.match(rf"^(?:connect|getaddrinfo|read|write|socket)\b.*\b{codes}\b", t))
            or bool(re.match(rf"^{codes}\b", t))
            or bool(re.match(r"^(?:fetch failed|socket hang up|connectionrefused)\b", t))
            or bool(re.match(r"^(?:api|http|request)\s*(?:error\s*)?:?\s*5\d{2}\b", t))
            or bool(re.match(r"^request failed with\s+5\d{2}\b", t))
            or bool(re.match(r"^status\s*[:=]?\s*5\d{2}\b", t))
            or bool(re.match(r"^5\d{2}\s+(?:internal server error|bad gateway|service unavailable|gateway timeout)\b",
                             t)))


def is_rejection(raw) -> bool:
    return bool(raw) and (is_context_overflow(raw) or is_quota_limited(raw) or is_auth_failure(raw)
                          or is_transport_failure(raw))


# ---- errors raised by the model layer -----------------------------------------------------------------
def classify_error(exc: BaseException) -> str:
    """setup_required | auth_invalid | rate_limit | quota_exhausted | unrecoverable | transient"""
    msg = str(exc)
    low = msg.lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(exc, FileNotFoundError) or "no model key configured" in low or "executable not found" in low \
            or "enoent" in low or msg.startswith("spawn "):
        return "setup_required"
    if status in (401, 403) or "invalid api key" in low or "api key expired" in low or "api key not valid" in low \
            or is_auth_failure(msg.split("model call failed:", 1)[-1].strip() or msg):
        return "auth_invalid"
    if status == 429 or "rate limit" in low:
        return "rate_limit"
    if "quota exceeded" in low or is_quota_limited(msg.split("model call failed:", 1)[-1].strip() or msg):
        return "quota_exhausted"
    if "prompt is too long" in low or "context window" in low or is_context_overflow(msg):
        return "unrecoverable"
    if status == 400 or "invalid_request_error" in low or "model identifier is invalid" in low:
        return "unrecoverable"
    return "transient"
