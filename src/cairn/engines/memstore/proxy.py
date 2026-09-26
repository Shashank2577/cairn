"""OpenAI-compatible chat completions with memory.

``ChatProxy(...).chat.completions.create(messages=..., project_id=...)`` behaves like an OpenAI chat
completion call, but first retrieves the memories relevant to the conversation and puts them in front
of the model, and afterwards stores what the user said as new memories (in the background). The model
call goes through Cairn's router; ``model`` may name a router tier ("fast", "balanced", "deep").
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from cairn.engines.memstore.llms.router import TIERS, render_messages
from cairn.engines.memstore.main import Memory, format_query_with_memories
from cairn.engines.memstore.prompts import MEMORY_ANSWER_PROMPT
from cairn.engines.memstore.scopes import SCOPE_KEYS

logger = logging.getLogger(__name__)


class ChatProxy:
    def __init__(self, config: Optional[dict] = None, *, memory: Optional[Memory] = None, router: Any = None):
        if memory is None:
            memory = Memory.from_config(config, router=router) if config else Memory(router=router)
        self.memory_client = memory
        self.router = router
        self.chat = Chat(self)


class Chat:
    def __init__(self, proxy: ChatProxy):
        self.completions = Completions(proxy)


class Completions:
    def __init__(self, proxy: ChatProxy):
        self.proxy = proxy
        self.memory_client = proxy.memory_client
        self._pending: List[threading.Thread] = []

    def create(
        self,
        model: Optional[str] = None,
        messages: Optional[List] = None,
        # memory arguments
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        project_id: Optional[str] = None,
        team_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        filters: Optional[dict] = None,
        top_k: Optional[int] = 10,
        # model arguments
        max_tokens: Optional[int] = None,
        stream: Optional[bool] = None,
        **ignored: Any,
    ) -> Dict[str, Any]:
        """Answer the conversation with relevant memories in context; returns an OpenAI-style completion.

        Sampling options the router does not expose (temperature, top_p, tools, ...) are accepted and
        ignored so existing OpenAI client code works unchanged.
        """
        if messages is None:
            messages = []
        scope = {k: v for k, v in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id),
                                   ("project_id", project_id), ("team_id", team_id)) if v}
        if not scope:
            raise ValueError("One of " + ", ".join(SCOPE_KEYS) + " must be provided")
        if stream:
            raise ValueError("streaming responses are not supported by the memory proxy")
        if ignored:
            logger.debug("memory proxy ignores unsupported arguments: %s", sorted(ignored))

        prepared_messages = self._prepare_messages(messages)
        if prepared_messages[-1]["role"] == "user":
            self._async_add_to_memory(messages, scope, metadata)
            relevant_memories = self._fetch_relevant_memories(messages, scope, filters, top_k)
            logger.debug(f"Retrieved {len(relevant_memories.get('results', []))} relevant memories")
            prepared_messages[-1]["content"] = format_query_with_memories(messages[-1]["content"], relevant_memories)

        system, prompt = render_messages(prepared_messages)
        router = self._router()
        kwargs: Dict[str, Any] = {"system": system, "max_tokens": int(max_tokens or 1200)}
        if model in TIERS:
            kwargs["tier"] = model
        content = router.complete("memory_chat", prompt, **kwargs)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model or "cairn",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": max(1, len(system + prompt) // 4), "completion_tokens": max(1, len(content) // 4),
                      "total_tokens": max(1, len(system + prompt) // 4) + max(1, len(content) // 4)},
        }

    def _router(self):
        if self.proxy.router is not None:
            return self.proxy.router
        return self.memory_client.llm.router

    def _prepare_messages(self, messages: List[dict]) -> List[dict]:
        messages = [dict(m) for m in messages]
        if not messages or messages[0]["role"] != "system":
            return [{"role": "system", "content": MEMORY_ANSWER_PROMPT}] + messages
        return messages

    def _async_add_to_memory(self, messages, scope, metadata):
        def add_task():
            logger.debug("Adding to memory asynchronously")
            try:
                self.memory_client.add(messages=messages, metadata=metadata, **scope)
            except Exception as exc:
                logger.warning("background memory add failed: %s", exc)

        thread = threading.Thread(target=add_task, daemon=True)
        thread.start()
        self._pending = [t for t in self._pending if t.is_alive()] + [thread]

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until background memory writes started by ``create`` have finished."""
        for thread in list(self._pending):
            thread.join(timeout)

    def _fetch_relevant_memories(self, messages, scope, filters, top_k):
        # Only the last 6 messages form the query, to keep it focused.
        message_input = [f"{message['role']}: {message['content']}" for message in messages][-6:]
        return self.memory_client.search(
            query="\n".join(message_input),
            filters={**(filters or {}), **scope},
            top_k=top_k or 10,
        )
