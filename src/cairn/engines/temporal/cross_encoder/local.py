"""Local rerankers (no model call, no key).

``LocalRerankerClient`` scores passages with a small ONNX cross-encoder through fastembed when the
package and model are available. Otherwise — or when ``CAIRN_RERANKER=fusion`` /
``CAIRN_EMBEDDER=hash`` asks for an offline setup — it fuses two local rankings with reciprocal
rank fusion: BM25 over the candidate passages and cosine similarity of local embeddings.
``LexicalRerankerClient`` is the BM25 half on its own (fully dependency-free).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import threading
from collections import Counter
from typing import Any

import numpy as np

from .client import CrossEncoderClient

logger = logging.getLogger(__name__)

DEFAULT_CROSS_ENCODER = 'Xenova/ms-marco-MiniLM-L-6-v2'
RRF_K = 60

_WORD = re.compile(r'[a-z0-9]+')
_CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')
_STOP = frozenset(
    'a an and are as at be by for from has have in is it its of on or that the this to was were '
    'will with what when where who which why how does did do'.split()
)


_SUFFIXES = ('ingly', 'edly', 'ing', 'ers', 'ies', 'ied', 'ed', 'es', 'er', 'ly', 's')


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _tokens(text: str) -> list[str]:
    words = _WORD.findall(_CAMEL.sub(' ', text or '').replace('_', ' ').lower())
    return [_stem(w) for w in words if w not in _STOP]


def bm25_scores(query: str, passages: list[str], k1: float = 1.2, b: float = 0.75) -> list[float]:
    """BM25 of ``query`` against each passage, with IDF computed over the passages themselves."""
    docs = [_tokens(p) for p in passages]
    q = _tokens(query)
    if not docs or not q:
        return [0.0] * len(passages)
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / n or 1.0
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(d))
    scores = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            freq = tf[term]
            s += idf * freq * (k1 + 1) / (freq + k1 * (1 - b + b * len(d) / avgdl))
        scores.append(s)
    return scores


def rrf_fuse(rankings: list[list[int]], n: int, k: int = RRF_K) -> list[float]:
    """Reciprocal rank fusion of several orderings (lists of passage indices, best first)."""
    fused = [0.0] * n
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            fused[idx] += 1.0 / (k + rank + 1)
    return fused


class LexicalRerankerClient(CrossEncoderClient):
    """BM25 reranking over the candidate passages (deterministic, dependency-free)."""

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        scores = bm25_scores(query, passages)
        return sorted(zip(passages, scores, strict=True), key=lambda x: -x[1])


_model_lock = threading.Lock()
_models: dict[str, Any] = {}
_failed: set[str] = set()


def _mode() -> str:
    mode = os.environ.get('CAIRN_RERANKER', 'auto').strip().lower()
    if mode not in ('auto', 'fastembed', 'fusion'):
        mode = 'auto'
    if mode == 'auto' and os.environ.get('CAIRN_EMBEDDER', '').strip().lower() == 'hash':
        return 'fusion'  # an offline/hash setup never tries to download a model
    return mode


def _load_cross_encoder(model_name: str) -> Any | None:
    if model_name in _failed:
        return None
    with _model_lock:
        if model_name in _models:
            return _models[model_name]
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            from cairn.engines.vectors import model_cache_dir

            cache = model_cache_dir()
            cache.mkdir(parents=True, exist_ok=True)
            _models[model_name] = TextCrossEncoder(model_name, cache_dir=str(cache))
            return _models[model_name]
        except Exception as exc:
            if _mode() == 'fastembed':
                raise
            _failed.add(model_name)
            logger.info('local cross-encoder unavailable (%s); using rank fusion', exc)
            return None


class LocalRerankerClient(CrossEncoderClient):
    """Cross-encoder reranking run locally, falling back to BM25 + embedding rank fusion."""

    def __init__(self, embedder: Any = None, model_name: str = DEFAULT_CROSS_ENCODER):
        self.embedder = embedder
        self.model_name = model_name

    @property
    def backend(self) -> str:
        if _mode() != 'fusion' and self.model_name not in _failed:
            return f'cross-encoder:{self.model_name}'
        return 'fusion'

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []
        if _mode() != 'fusion':
            model = await asyncio.to_thread(_load_cross_encoder, self.model_name)
            if model is not None:
                scores = await asyncio.to_thread(lambda: list(model.rerank(query, passages)))
                # Map logits to (0, 1) so ``reranker_min_score`` has a stable meaning.
                probs = [1.0 / (1.0 + math.exp(-float(s))) for s in scores]
                return sorted(zip(passages, probs, strict=True), key=lambda x: -x[1])
        return await self._fusion(query, passages)

    async def _fusion(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        n = len(passages)
        lexical = bm25_scores(query, passages)
        rankings = [sorted(range(n), key=lambda i: -lexical[i])]
        embedder = self.embedder
        if embedder is None:
            from ..embedder.local import LocalEmbedder

            embedder = self.embedder = LocalEmbedder()
        try:
            vectors = await embedder.create_batch([query, *passages])
            q = np.asarray(vectors[0], dtype=np.float32)
            m = np.asarray(vectors[1:], dtype=np.float32)
            norms = np.linalg.norm(m, axis=1) * (np.linalg.norm(q) or 1.0)
            cos = (m @ q) / np.where(norms == 0, 1.0, norms)
            rankings.append(sorted(range(n), key=lambda i: -float(cos[i])))
        except Exception as exc:  # embeddings are an enrichment here
            logger.debug('embedding half of rank fusion skipped: %s', exc)
        fused = rrf_fuse(rankings, n)
        return sorted(zip(passages, fused, strict=True), key=lambda x: -x[1])
