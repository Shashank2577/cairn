"""The observer's prompts: the first (init) prompt of a session, the continuation prompt of later
prompts, one prompt per captured tool event, and the progress-summary prompt. The wording comes from
the active mode, so a mode can change the vocabulary and language of every record.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .modes import Mode

SUMMARY_MODE_MARKER = "MODE SWITCH: PROGRESS SUMMARY"

# Per-field budget for <parameters>/<outcome>. Oversized fields keep a head (60%) and a tail (30%)
# with an explicit elision marker so the observer never invents what was cut.
OBS_PROMPT_FIELD_MAX_CHARS = 16_000
_HEAD_RATIO = 0.6
_TAIL_RATIO = 0.3
_MAX_SANITIZE_DEPTH = 12
_ELIDED = "image data withheld from the observer"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def observation_skeleton(mode: Mode) -> str:
    p = mode.prompts
    types = " | ".join(mode.type_ids())
    return f"""{p.get('output_format_header', '')}

<observation>
  <type>[ {types} ]</type>
  <!--
    {p.get('type_guidance', '')}
  -->
  <title>{p.get('xml_title_placeholder', '')}</title>
  <subtitle>{p.get('xml_subtitle_placeholder', '')}</subtitle>
  <facts>
    <fact>{p.get('xml_fact_placeholder', '')}</fact>
    <fact>{p.get('xml_fact_placeholder', '')}</fact>
    <fact>{p.get('xml_fact_placeholder', '')}</fact>
  </facts>
  <!--
    {p.get('field_guidance', '')}
  -->
  <narrative>{p.get('xml_narrative_placeholder', '')}</narrative>
  <concepts>
    <concept>{p.get('xml_concept_placeholder', '')}</concept>
    <concept>{p.get('xml_concept_placeholder', '')}</concept>
  </concepts>
  <!--
    {p.get('concept_guidance', '')}
  -->
  <files_read>
    <file>{p.get('xml_file_placeholder', '')}</file>
    <file>{p.get('xml_file_placeholder', '')}</file>
  </files_read>
  <files_modified>
    <file>{p.get('xml_file_placeholder', '')}</file>
    <file>{p.get('xml_file_placeholder', '')}</file>
  </files_modified>
</observation>
{p.get('format_examples', '')}

{p.get('footer', '')}"""


def wrap_prior_context(prior: str) -> str:
    """Brief a generation that starts partway through a session with what was already recorded."""
    trimmed = (prior or "").strip()
    if not trimmed:
        return ""
    return f"""
<session_start_context>
{trimmed}
</session_start_context>

The context above is what you have already recorded for this work. Continue from
there: do not re-record it, and do not treat its absence from the conversation
above as meaning the work did not happen."""


def build_init_prompt(project: str, session_id: str, user_prompt: str, mode: Mode, prior_context: str = "") -> str:
    p = mode.prompts
    return f"""{p.get('system_identity', '')}
{wrap_prior_context(prior_context)}

<observed_from_primary_session>
  <user_request>{user_prompt}</user_request>
  <requested_at>{_today()}</requested_at>
</observed_from_primary_session>

{p.get('observer_role', '')}

{p.get('spatial_awareness', '')}

{p.get('recording_focus', '')}

{p.get('skip_guidance', '')}

{observation_skeleton(mode)}

{p.get('header_memory_start', '')}"""


def build_continuation_prompt(user_prompt: str, prompt_number: int, session_id: str, mode: Mode,
                              prior_context: str = "") -> str:
    p = mode.prompts
    return f"""{p.get('continuation_greeting', '')}
{wrap_prior_context(prior_context)}

<observed_from_primary_session>
  <user_request>{user_prompt}</user_request>
  <requested_at>{_today()}</requested_at>
</observed_from_primary_session>

{p.get('system_identity', '')}

{p.get('observer_role', '')}

{p.get('spatial_awareness', '')}

{p.get('recording_focus', '')}

{p.get('skip_guidance', '')}

{p.get('continuation_instruction', '')}

{observation_skeleton(mode)}

{p.get('header_memory_continued', '')}"""


# ---- tool payload sanitising -----------------------------------------------------------------------
def _is_data_url(url: str) -> bool:
    return url[:5].lower() == "data:"


def _elide(source: dict, data_key: str = "data") -> dict:
    out: dict[str, Any] = {"elided": _ELIDED}
    if isinstance(source.get("media_type"), str):
        out["media_type"] = source["media_type"]
    if isinstance(source.get(data_key), str):
        out["bytes"] = len(source[data_key])
    return out


def strip_image_payloads(value: Any, depth: int = 0) -> Any:
    """Replace inlined base64 images with a short marker (content blocks of any provider shape)."""
    if depth > _MAX_SANITIZE_DEPTH or value is None or not isinstance(value, (dict, list)):
        return value
    if isinstance(value, list):
        mapped = [strip_image_payloads(v, depth + 1) for v in value]
        return mapped if any(a is not b for a, b in zip(mapped, value)) else value
    rec = value
    src = rec.get("source")
    if rec.get("type") == "image" and isinstance(src, dict):
        if isinstance(src.get("elided"), str):
            return value
        url = src.get("url")
        if isinstance(url, str) and not _is_data_url(url):
            return value
        return {"type": "image", "source": _elide(src)}
    f = rec.get("file")
    if rec.get("type") == "image" and isinstance(f, dict):
        if isinstance(f.get("elided"), str):
            return value
        if isinstance(f.get("base64"), str):
            return {"type": "image", "file": _elide(f, "base64")}
    iu = rec.get("image_url")
    if rec.get("type") == "image_url" and isinstance(iu, dict):
        if isinstance(iu.get("elided"), str):
            return value
        url = iu.get("url")
        if isinstance(url, str) and _is_data_url(url):
            return {"type": "image_url", "image_url": {"elided": _ELIDED, "bytes": len(url)}}
        return value
    out, changed = {}, False
    for k, v in rec.items():
        nv = strip_image_payloads(v, depth + 1)
        changed = changed or nv is not v
        out[k] = nv
    return out if changed else value


def strip_image_payloads_from_field(value: Any) -> Any:
    """Fields arrive as JSON text; parse, strip, and hand back the original text when nothing changed."""
    if not isinstance(value, str):
        return strip_image_payloads(value)
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    stripped = strip_image_payloads(parsed)
    return value if stripped is parsed else stripped


def _js_stringify(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def truncate_observation_field(value: Any, max_chars: int = OBS_PROMPT_FIELD_MAX_CHARS) -> str:
    raw = _js_stringify(value) if value is not None else "null"
    if len(raw) <= max_chars:
        return raw
    head = raw[: max(0, int(max_chars * _HEAD_RATIO))]
    tail_n = max(0, int(max_chars * _TAIL_RATIO))
    tail = raw[-tail_n:] if tail_n else ""
    elided = max(0, len(raw) - len(head) - len(tail))
    return (f'{head}\n... <elided chars="{elided}" original_size_chars="{len(raw)}" reason="oversize" /> ...\n'
            f"{tail}")


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def build_observation_prompt(tool_name: str, tool_input: Any, tool_output: Any, created_at_epoch_ms: int,
                             cwd: str | None = None, max_chars: int = OBS_PROMPT_FIELD_MAX_CHARS) -> str:
    ti = _maybe_json(tool_input)
    to = _maybe_json(tool_output)
    when = datetime.fromtimestamp(created_at_epoch_ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")
    when = when.replace("+00:00", "Z")
    wd = f"\n  <working_directory>{cwd}</working_directory>" if cwd else ""
    return f"""<observed_from_primary_session>
  <what_happened>{tool_name}</what_happened>
  <occurred_at>{when}</occurred_at>{wd}
  <parameters>{truncate_observation_field(strip_image_payloads_from_field(ti), max_chars)}</parameters>
  <outcome>{truncate_observation_field(strip_image_payloads_from_field(to), max_chars)}</outcome>
</observed_from_primary_session>

If a <parameters> or <outcome> block above contains an "<elided chars=... />" marker, that field was truncated to fit the observer's context window. Describe only what you can see in the kept portion and do not infer details about the elided range.

Return either one or more <observation>...</observation> blocks, or <skip_summary reason="noise" /> if this tool use should be skipped.
Concrete debugging findings from logs, queue state, database rows, session routing, or code-path inspection count as durable discoveries and should be recorded.
Never reply with prose such as "Skipping", "No substantive tool executions", or any explanation outside XML. Non-XML text is discarded."""


def build_summary_prompt(last_assistant_message: str, mode: Mode) -> str:
    p = mode.prompts
    return f"""--- {SUMMARY_MODE_MARKER} ---
⚠️ CRITICAL TAG REQUIREMENT — READ CAREFULLY:
• You MUST wrap your ENTIRE response in <summary>...</summary> tags.
• Do NOT use <observation> tags. <observation> output will be DISCARDED and cause a system error.
• The ONLY accepted root tag is <summary>. Any other root tag is a protocol violation.

{p.get('header_summary_checkpoint', '')}
{p.get('summary_instruction', '')}

{p.get('summary_context_label', '')}
{last_assistant_message or ''}

{p.get('summary_format_instruction', '')}
<summary>
  <request>{p.get('xml_summary_request_placeholder', '')}</request>
  <investigated>{p.get('xml_summary_investigated_placeholder', '')}</investigated>
  <learned>{p.get('xml_summary_learned_placeholder', '')}</learned>
  <completed>{p.get('xml_summary_completed_placeholder', '')}</completed>
  <next_steps>{p.get('xml_summary_next_steps_placeholder', '')}</next_steps>
  <notes>{p.get('xml_summary_notes_placeholder', '')}</notes>
</summary>

REMINDER: Your response MUST use <summary> as the root tag, NOT <observation>.
{p.get('summary_footer', '')}"""


# ---- oversized-field condensing (#3800 in the original design) -------------------------------------
FIELD_OPTIMIZE_TARGET_RATIO = 0.8


def build_field_compression_prompt(text: str, budget_chars: int) -> str:
    return f"""Condense the tool payload below to under {budget_chars} characters.

It is going into an observation record, so preserve everything that carries
signal: file paths, identifiers, commands, counts, error text, status codes, and
any concrete values a later reader would need. Drop repetition, boilerplate and
filler. Keep the original ordering.

Reply with the condensed payload only — no preamble, no commentary, no code
fences.

<payload>
{text}
</payload>"""
