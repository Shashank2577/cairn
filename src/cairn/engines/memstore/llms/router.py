"""The memory engine's one LLM: a thin adapter over Cairn's model router.

The router owns providers, credentials, tiers, prompt caching, budgets and the cost ledger. This
adapter turns chat-style message lists into a (system, prompt) pair, and adds what the router does
not do by itself: structured JSON output (parsed, optionally validated against a pydantic model,
retried on failure) and tool calls emulated through JSON.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from cairn.engines.memstore.configs.llms.base import BaseLlmConfig, RouterLlmConfig
from cairn.engines.memstore.exceptions import LLMError
from cairn.engines.memstore.llms.base import LLMBase
from cairn.engines.memstore.utils.messages import extract_json, remove_code_blocks

logger = logging.getLogger(__name__)

TIERS = ("fast", "balanced", "deep", "frontier")

JSON_INSTRUCTION = (
    "Respond with exactly one valid JSON object and nothing else: no prose before or after it, "
    "no markdown code fences."
)


def _content_text(content: Any) -> str:
    """Flatten message content (a string or a list of typed parts) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    parts: List[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            if part.get("type") == "text" or "text" in part:
                parts.append(str(part.get("text", "")))
            elif part.get("type") == "image_url":
                url = part.get("image_url")
                url = url.get("url") if isinstance(url, dict) else url
                parts.append(f"[image: {url}]")
    return "\n".join(p for p in parts if p)


def render_messages(messages: List[Dict[str, Any]]) -> Tuple[str, str]:
    """Split chat messages into (system, prompt) for a single router turn.

    System messages become the system prompt. A lone user message is sent as-is; a longer exchange
    is rendered as a role-labelled transcript so the model sees the whole conversation.
    """
    system_parts: List[str] = []
    turns: List[Tuple[str, str]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        text = _content_text(msg.get("content"))
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            role = f"tool result ({msg.get('name') or msg.get('tool_call_id') or 'tool'})"
        elif msg.get("name"):
            role = f"{role} ({msg['name']})"
        turns.append((role, text))
    if len(turns) == 1 and turns[0][0] == "user":
        prompt = turns[0][1]
    else:
        prompt = "\n\n".join(f"{role}: {text}" for role, text in turns)
    return "\n\n".join(system_parts), prompt


def parse_json_object(text: str) -> Any:
    """Parse a model reply as JSON, tolerating code fences, think-blocks and surrounding prose."""
    cleaned = remove_code_blocks(text or "")
    if not cleaned.strip():
        raise ValueError("empty reply")
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        return json.loads(extract_json(cleaned), strict=False)


class RouterLLM(LLMBase):
    def __init__(self, config: Optional[Union[BaseLlmConfig, RouterLlmConfig, Dict]] = None):
        if config is None:
            config = RouterLlmConfig()
        elif isinstance(config, dict):
            config = RouterLlmConfig(**config)
        elif not isinstance(config, RouterLlmConfig):
            base = {k: getattr(config, k) for k in (
                "model", "temperature", "api_key", "max_tokens", "top_p", "top_k", "enable_vision",
                "vision_details", "reasoning_effort", "is_reasoning_model")}
            config = RouterLlmConfig(**base)
        super().__init__(config)
        self._router = config.router

    # ---- router ----------------------------------------------------------------------------------
    @property
    def router(self):
        if self._router is None:
            from cairn.project import Project
            from cairn.router import Router

            project = Project.discover()
            if project is None:
                raise LLMError("no repository found for the model router", error_code="LLM_001")
            self._router = Router(project, None)
        return self._router

    @property
    def available(self) -> bool:
        try:
            return bool(self.router.available)
        except Exception:
            return False

    def _tier(self) -> Optional[str]:
        tier = getattr(self.config, "tier", None)
        return tier if tier in TIERS else None

    def _complete(self, system: str, prompt: str, *, task: Optional[str], max_tokens: Optional[int]) -> str:
        if not self.available:
            raise LLMError("no model is available (configure models.provider or an API key)", error_code="LLM_002")
        kwargs: Dict[str, Any] = {"system": system, "max_tokens": int(max_tokens or self.config.max_tokens)}
        tier = self._tier()
        if tier:
            kwargs["tier"] = tier
        budget = getattr(self.config, "budget", None)
        if budget is not None:
            kwargs["budget"] = budget
        try:
            return self.router.complete(task or self.config.task, prompt, **kwargs) or ""
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"model call failed: {exc}", error_code="LLM_003") from exc

    # ---- public API ------------------------------------------------------------------------------
    def generate_response(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict]] = None,
        tool_choice: str = "auto",
        response_format: Optional[Dict[str, Any]] = None,
        *,
        task: Optional[str] = None,
        max_tokens: Optional[int] = None,
        schema: Any = None,
        **kwargs,
    ):
        """Generate a response.

        ``response_format={"type": "json_object"}`` (or ``"json_schema"``) and ``schema`` (a pydantic
        model class) return a JSON string that is guaranteed to parse (and validate), retrying the model
        on malformed output; after the retries the last raw reply is returned so callers can decide.
        With ``tools`` the reply is ``{"content": str, "tool_calls": [{"name", "arguments"}]}``.
        """
        system, prompt = render_messages(messages)
        if tools:
            return self._generate_with_tools(system, prompt, tools, tool_choice, task=task, max_tokens=max_tokens)
        fmt = (response_format or {}).get("type") if isinstance(response_format, dict) else None
        if fmt not in ("json_object", "json_schema") and schema is None:
            return self._complete(system, prompt, task=task, max_tokens=max_tokens)
        json_schema = None
        if fmt == "json_schema":
            json_schema = (response_format.get("json_schema") or {}).get("schema")
        elif schema is not None and hasattr(schema, "model_json_schema"):
            json_schema = schema.model_json_schema()
        instruction = JSON_INSTRUCTION
        if json_schema:
            instruction += "\nThe object must match this JSON schema:\n" + json.dumps(json_schema)
        return self._json_call(f"{system}\n\n{instruction}".strip(), prompt, schema=schema, task=task,
                               max_tokens=max_tokens)

    def generate_structured(self, messages: List[Dict[str, Any]], schema: Any, **kwargs):
        """Return a validated instance of the pydantic ``schema`` (raises LLMError when impossible)."""
        raw = self.generate_response(messages, schema=schema, **kwargs)
        try:
            return schema.model_validate(parse_json_object(raw))
        except Exception as exc:
            raise LLMError(f"structured output did not validate: {exc}", error_code="LLM_004") from exc

    # ---- structured output -----------------------------------------------------------------------
    def _json_call(self, system: str, prompt: str, *, schema: Any, task: Optional[str],
                   max_tokens: Optional[int]) -> str:
        attempt_prompt = prompt
        raw = ""
        for attempt in range(self.config.json_retries + 1):
            raw = self._complete(system, attempt_prompt, task=task, max_tokens=max_tokens)
            try:
                obj = parse_json_object(raw)
                if schema is not None:
                    obj = schema.model_validate(obj).model_dump()
                return json.dumps(obj, ensure_ascii=False)
            except Exception as exc:
                logger.info("structured reply rejected (attempt %d): %s", attempt + 1, str(exc)[:200])
                attempt_prompt = (
                    f"{prompt}\n\n---\nYour previous reply could not be used ({str(exc)[:200]}). "
                    f"Previous reply:\n{raw[:2000]}\n---\n{JSON_INSTRUCTION}"
                )
        logger.warning("model did not return valid JSON after %d attempts", self.config.json_retries + 1)
        return raw

    def _generate_with_tools(self, system: str, prompt: str, tools: List[Dict], tool_choice: Any, *,
                             task: Optional[str], max_tokens: Optional[int]) -> Dict[str, Any]:
        specs = []
        names = set()
        for tool in tools:
            fn = tool.get("function", tool) if isinstance(tool, dict) else {}
            if not fn.get("name"):
                continue
            names.add(fn["name"])
            specs.append({"name": fn["name"], "description": fn.get("description", ""),
                          "parameters": fn.get("parameters", {})})
        rule = "Call tools when they help."
        if tool_choice == "required":
            rule = "You must call at least one tool."
        elif tool_choice == "none":
            rule = "Do not call any tool."
        elif isinstance(tool_choice, dict):
            forced = (tool_choice.get("function") or {}).get("name")
            if forced:
                rule = f"You must call the tool '{forced}'."
        instruction = (
            "You can call these tools:\n" + json.dumps(specs, ensure_ascii=False) + f"\n{rule}\n"
            'Reply with a JSON object {"content": "<text or empty>", "tool_calls": '
            '[{"name": "<tool name>", "arguments": {<arguments matching the tool parameters>}}]}. '
            + JSON_INSTRUCTION
        )
        raw = self._json_call(f"{system}\n\n{instruction}".strip(), prompt, schema=None, task=task,
                              max_tokens=max_tokens)
        try:
            obj = parse_json_object(raw)
        except Exception:
            return {"content": raw, "tool_calls": []}
        calls = []
        for call in obj.get("tool_calls") or []:
            if not isinstance(call, dict) or call.get("name") not in names:
                continue
            args = call.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append({"name": call["name"], "arguments": args})
        return {"content": obj.get("content") or "", "tool_calls": calls}
