"""The engine's one model client: every structured call goes through ``cairn.router.Router``.

The router picks the provider (API key, OpenAI-compatible endpoint or the signed-in Claude Code
CLI), the model tier, prompt caching and the cost ledger. This adapter does what the engine needs
on top: map each prompt to a router task, run the (synchronous) router call off the event loop,
parse/validate the JSON answer against the pydantic response model (retrying with the validation
error when the model gets the shape wrong), and enforce the episode's token ``Budget`` on EVERY
call — ``budget.check`` before the call, ``budget.charge`` of the measured prompt + completion
after it. The charge is deliberate double-entry with the ledger, not against the router's budget
accounting: the router only sees provider-reported input+output, which excludes the cached /
re-sent prompt volume that dominates a fan-out episode's real burn, so the client charges what it
actually sent and received and ``BudgetExceeded`` stops an episode mid-flight.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import typing

from pydantic import BaseModel, ValidationError

from ..prompts.models import Message
from .client import LLMClient, get_extraction_language_instruction
from .config import LLMConfig, ModelSize
from .errors import EmptyResponseError, RateLimitError, RefusalError

logger = logging.getLogger(__name__)

# Router task per prompt. Task names are what the ledger shows; ``TASK_TIERS`` below is the tier
# each task should run on (the router's own TASK_TIER table wins when it lists the task).
TASK_FOR_PROMPT: dict[str, str] = {
    'extract_nodes.extract_message': 'temporal.extract',
    'extract_nodes.extract_text': 'temporal.extract',
    'extract_nodes.extract_json': 'temporal.extract',
    'extract_edges.edge': 'temporal.extract',
    'extract_nodes_and_edges.extract_message': 'temporal.extract',
    'dedupe_nodes.nodes': 'temporal.dedupe',
    'dedupe_edges.resolve_edge': 'temporal.resolve',
    'extract_nodes.extract_attributes': 'temporal.attributes',
    'extract_edges.extract_attributes': 'temporal.attributes',
    'extract_edges.extract_timestamps': 'temporal.timestamps',
    'extract_edges.extract_timestamps_batch': 'temporal.timestamps',
    'extract_nodes.extract_summaries_batch': 'temporal.summarize',
    'extract_nodes.extract_entity_summaries_from_episodes': 'temporal.summarize',
    'summarize_nodes.summarize_pair': 'temporal.community',
    'summarize_nodes.summary_description': 'temporal.community',
    'summarize_sagas.summarize_saga': 'temporal.saga',
    'rerank': 'temporal.rerank',
}

TASK_TIERS: dict[str, str] = {
    'temporal.extract': 'balanced',
    'temporal.dedupe': 'balanced',
    'temporal.resolve': 'fast',
    'temporal.attributes': 'fast',
    'temporal.timestamps': 'fast',
    'temporal.summarize': 'fast',
    'temporal.community': 'fast',
    'temporal.saga': 'fast',
    'temporal.rerank': 'fast',
}


def task_for_prompt(prompt_name: str | None, model_size: ModelSize = ModelSize.medium) -> str:
    if prompt_name and prompt_name in TASK_FOR_PROMPT:
        return TASK_FOR_PROMPT[prompt_name]
    return 'temporal.extract' if model_size == ModelSize.medium else 'temporal.summarize'


_FENCE = re.compile(r'^```[a-zA-Z0-9_-]*[ \t]*\r?\n?|\r?\n?```[ \t]*$')


def parse_json_object(text: str) -> dict[str, typing.Any]:
    """Pull one JSON object out of a model answer (code fences and surrounding prose allowed)."""
    stripped = (text or '').strip()
    if not stripped:
        raise EmptyResponseError('model returned an empty response')
    if stripped.startswith('```'):
        stripped = _FENCE.sub('', stripped).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find('{'), stripped.rfind('}')
        if start < 0 or end <= start:
            raise
        data = json.loads(stripped[start : end + 1])
    if isinstance(data, list):
        # A bare list is accepted when the schema has exactly one list field (models sometimes
        # answer with just the array); callers wrap it via ``_wrap_list``.
        return {'__list__': data}
    if not isinstance(data, dict):
        raise json.JSONDecodeError('expected a JSON object', stripped, 0)
    return data


def _wrap_list(data: dict[str, typing.Any], response_model: type[BaseModel] | None) -> dict:
    if '__list__' not in data:
        return data
    if response_model is not None:
        list_fields = [
            name
            for name, field in response_model.model_fields.items()
            if typing.get_origin(field.annotation) is list
        ]
        if len(list_fields) == 1:
            return {list_fields[0]: data['__list__']}
    raise json.JSONDecodeError('expected a JSON object, got a list', str(data['__list__'])[:80], 0)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, RateLimitError | TimeoutError | asyncio.TimeoutError):
        return True
    name = type(exc).__name__.lower()
    if 'ratelimit' in name or 'timeout' in name or 'overloaded' in name:
        return True
    status = getattr(getattr(exc, 'response', None), 'status_code', None) or getattr(
        exc, 'status_code', None
    )
    return isinstance(status, int) and (status == 429 or 500 <= status < 600)


class RouterLLMClient(LLMClient):
    """``LLMClient`` over ``cairn.router.Router`` (async via a worker thread)."""

    def __init__(
        self,
        router: typing.Any,
        *,
        budget: typing.Any = None,
        config: LLMConfig | None = None,
        cache: bool = False,
        cache_dir: str | None = None,
        max_attempts: int = 3,
        max_concurrency: int = 4,
    ):
        if config is None:
            config = LLMConfig(max_tokens=8192, temperature=0)
        super().__init__(config, cache, cache_dir)
        self.router = router
        # Shared episode Budget (cairn.router.Budget): checked before and charged after every
        # call in ``_generate_response``; BudgetExceeded stops the episode mid-flight.
        self.budget = budget
        self.max_attempts = max(1, max_attempts)
        # The router is synchronous (and the CLI provider spawns a process per call); bound
        # how many run at once regardless of how wide the engine fans out.
        self.max_concurrency = max(1, max_concurrency)
        self._slots: dict[int, asyncio.Semaphore] = {}

    @property
    def available(self) -> bool:
        return bool(getattr(self.router, 'available', False))

    def _slot(self) -> asyncio.Semaphore:
        loop_id = id(asyncio.get_running_loop())
        sem = self._slots.get(loop_id)
        if sem is None:
            self._slots = {loop_id: asyncio.Semaphore(self.max_concurrency)}
            sem = self._slots[loop_id]
        return sem

    def _get_provider_type(self) -> str:
        return str(getattr(self.router, 'provider', None) or 'router')

    def _tier_override(self, task: str) -> str | None:
        try:
            from cairn.router import TASK_TIER
        except Exception:  # pragma: no cover - router always importable inside cairn
            TASK_TIER = {}
        return None if task in TASK_TIER else TASK_TIERS.get(task)

    def _complete(self, task: str, system: str, prompt: str, max_tokens: int) -> str:
        # No ``budget`` kwarg here: this client checks and charges the Budget itself (see
        # ``_generate_response``), so the router's coarser input+output charge can't double-charge.
        kwargs: dict[str, typing.Any] = {'system': system, 'max_tokens': max_tokens}
        tier = self._tier_override(task)
        if tier is not None:
            kwargs['tier'] = tier
        try:
            return self.router.complete(task, prompt, **kwargs)
        except TypeError:
            # Routers (or test doubles) without the optional keywords.
            return self.router.complete(task, prompt, system=system, max_tokens=max_tokens)

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 8192,
        model_size: ModelSize = ModelSize.medium,
        prompt_name: str | None = None,
    ) -> dict[str, typing.Any]:
        system = '\n\n'.join(m.content for m in messages if m.role == 'system')
        prompt = '\n\n'.join(m.content for m in messages if m.role != 'system')
        task = task_for_prompt(prompt_name, model_size)
        input_tokens = len(system + prompt) // 4
        if self.budget is not None:
            # Per-call enforcement inside an episode: fail before spending when the estimate
            # (prompt + the max_tokens ceiling, mirroring the router's own formula) no longer
            # fits. BudgetExceeded is not transient, so neither retry loop re-runs it.
            self.budget.check(input_tokens + max_tokens)
        async with self._slot():
            text = await asyncio.to_thread(self._complete, task, system, prompt, max_tokens)
        output_tokens = len(text or '') // 4
        self.token_tracker.record(prompt_name, input_tokens, output_tokens)
        if self.budget is not None:
            # Charge what the call actually moved (measured prompt + completion; the re-sent
            # prompt volume is the burn the router's input+output charge can't see).
            self.budget.charge(input_tokens + output_tokens)
        return parse_json_object(text)

    async def generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        model_size: ModelSize = ModelSize.medium,
        group_id: str | None = None,
        prompt_name: str | None = None,
        *,
        attribute_extraction: bool = False,
    ) -> dict[str, typing.Any]:
        if not self.available:
            raise RuntimeError('the temporal graph needs a model (configure models.provider)')
        if max_tokens is None:
            max_tokens = self.max_tokens
        messages = [Message(role=m.role, content=m.content) for m in messages]
        self._apply_attribute_extraction_preamble(messages, attribute_extraction)
        if response_model is not None:
            schema = json.dumps(response_model.model_json_schema())
            messages[-1].content += (
                '\n\nRespond with ONLY a JSON object (no prose, no code fences) in the following '
                f'format:\n\n{schema}'
            )
        messages[0].content += get_extraction_language_instruction(group_id)
        for message in messages:
            message.content = self._clean_input(message.content)

        with self.tracer.start_span('llm.generate') as span:
            span.add_attributes(
                {
                    'llm.provider': self._get_provider_type(),
                    'model.size': model_size.value,
                    'max_tokens': max_tokens,
                    'prompt.name': prompt_name or '',
                    'cache.enabled': self.cache_enabled,
                }
            )
            cache_key = None
            if self.cache_enabled and self.cache_dir is not None:
                cache_key = self._get_cache_key(messages)
                cached = self.cache_dir.get(cache_key)
                if cached is not None:
                    span.add_attributes({'cache.hit': True})
                    return cached

            last_error: Exception | None = None
            for attempt in range(1, self.max_attempts + 1):
                try:
                    raw = await self._generate_response(
                        messages, response_model, max_tokens, model_size, prompt_name
                    )
                    raw = _wrap_list(raw, response_model)
                    result = self._validate(raw, response_model, attribute_extraction)
                    if cache_key is not None:
                        self.cache_dir.set(cache_key, result)  # type: ignore[union-attr]
                    return result
                except RefusalError:
                    raise
                except (json.JSONDecodeError, ValidationError, EmptyResponseError) as e:
                    last_error = e
                    if attempt >= self.max_attempts:
                        break
                    hint = (
                        f'The previous response was not a valid '
                        f'{response_model.__name__ if response_model else "JSON"} object '
                        f'({type(e).__name__}: {str(e)[:400]}). Reply again with only the corrected '
                        'JSON object.'
                    )
                    messages = [*messages, Message(role='user', content=hint)]
                    logger.warning('retrying %s after invalid output (attempt %d)', prompt_name, attempt)
                except Exception as e:
                    last_error = e
                    if attempt >= self.max_attempts or not _is_transient(e):
                        break
                    delay = min(30.0, (2**attempt) + random.random())
                    logger.warning('retrying %s after %s (%.1fs)', prompt_name, type(e).__name__, delay)
                    await asyncio.sleep(delay)
            span.set_status('error', str(last_error))
            if last_error is not None:
                span.record_exception(last_error)
                raise last_error
            raise RuntimeError('model call failed')

    @staticmethod
    def _validate(
        data: dict[str, typing.Any],
        response_model: type[BaseModel] | None,
        attribute_extraction: bool,
    ) -> dict[str, typing.Any]:
        if response_model is None:
            return data
        instance = response_model.model_validate(data)
        if attribute_extraction:
            # Keep only what the model actually returned so callers can tell "omitted" from
            # "default" (node attributes are overlay-merged with prior values).
            return {k: v for k, v in data.items() if k in response_model.model_fields}
        return instance.model_dump()
