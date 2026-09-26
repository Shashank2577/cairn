"""Local cross-encoder reranking with ``BAAI/bge-reranker-v2-m3`` via sentence-transformers
(optional: ``pip install sentence-transformers``). Runs on the local machine; no model call."""

import asyncio
from typing import Any

from cairn.engines.temporal.cross_encoder.client import CrossEncoderClient


class BGERerankerClient(CrossEncoderClient):
    def __init__(self, model_name: str = 'BAAI/bge-reranker-v2-m3'):
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            raise ImportError(
                'sentence-transformers is required for BGERerankerClient. '
                'Install it with: pip install sentence-transformers'
            ) from None
        self.model: Any = CrossEncoder(model_name)

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []

        input_pairs = [[query, passage] for passage in passages]

        # Run the synchronous predict method in an executor
        loop = asyncio.get_running_loop()
        scores = await loop.run_in_executor(None, self.model.predict, input_pairs)

        ranked_passages = sorted(
            [(passage, float(score)) for passage, score in zip(passages, scores, strict=False)],
            key=lambda x: x[1],
            reverse=True,
        )

        return ranked_passages
