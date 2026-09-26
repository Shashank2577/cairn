"""Model-judged reranking through the router.

Each passage is judged for relevance to the query by the model (the ``temporal.rerank`` task, a
fast tier), in batches, and returned with a 0..1 relevance score. Use it when rerank quality
matters more than latency; ``LocalRerankerClient`` is the default. Falls back to the local
reranker when no model is available or a call fails, so search never breaks.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from ..helpers import semaphore_gather
from ..prompts.models import Message
from .client import CrossEncoderClient
from .local import LocalRerankerClient

logger = logging.getLogger(__name__)

BATCH = 20


class PassageRelevance(BaseModel):
    id: int = Field(..., description='The id of the passage')
    relevant: bool = Field(..., description='True if the passage is relevant to the query')
    score: float = Field(
        ..., description='Relevance of the passage to the query from 0.0 (unrelated) to 1.0'
    )


class RelevanceJudgements(BaseModel):
    judgements: list[PassageRelevance]


class RouterRerankerClient(CrossEncoderClient):
    def __init__(self, llm_client: Any, fallback: CrossEncoderClient | None = None):
        """``llm_client`` is a ``RouterLLMClient`` (structured output with validation/retry)."""
        self.llm_client = llm_client
        self.fallback = fallback or LocalRerankerClient()

    async def _judge(self, query: str, batch: list[tuple[int, str]]) -> dict[int, float]:
        passages = '\n'.join(f'<PASSAGE id="{i}">\n{p}\n</PASSAGE>' for i, p in batch)
        messages = [
            Message(
                role='system',
                content='You are an expert tasked with determining whether each passage is '
                'relevant to the query.',
            ),
            Message(
                role='user',
                content=f"""
<QUERY>
{query}
</QUERY>
{passages}

For every passage, decide whether it is relevant to QUERY and give a relevance score between
0.0 and 1.0. Return one judgement per passage id.
""",
            ),
        ]
        response = await self.llm_client.generate_response(
            messages, response_model=RelevanceJudgements, prompt_name='rerank', max_tokens=2048
        )
        judged = RelevanceJudgements(**response).judgements
        ids = {i for i, _ in batch}
        out: dict[int, float] = {}
        for j in judged:
            if j.id in ids:
                score = min(1.0, max(0.0, float(j.score)))
                out[j.id] = score if j.relevant else min(score, 0.49)
        return out

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []
        if not getattr(self.llm_client, 'available', True):
            return await self.fallback.rank(query, passages)
        indexed = list(enumerate(passages))
        batches = [indexed[i : i + BATCH] for i in range(0, len(indexed), BATCH)]
        try:
            results = await semaphore_gather(*[self._judge(query, b) for b in batches])
        except Exception as exc:
            logger.warning('model rerank failed (%s); using local reranker', exc)
            return await self.fallback.rank(query, passages)
        scores: dict[int, float] = {}
        for r in results:
            scores.update(r)
        ranked = [(p, scores.get(i, 0.0)) for i, p in indexed]
        ranked.sort(key=lambda x: -x[1])
        return ranked
