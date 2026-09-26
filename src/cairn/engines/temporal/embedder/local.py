"""Local embeddings for the temporal graph: no key, no network after the first model download.

Both clients sit on Cairn's shared embedding helper (``cairn.engines.vectors``). ``LocalEmbedder``
uses whatever that helper resolved (``BAAI/bge-small-en-v1.5`` via fastembed, or its hashing
embedder when the model cannot load or ``CAIRN_EMBEDDER=hash``). ``HashingEmbedder`` always uses the
deterministic hashing embedder (tests, fully offline setups).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable

from cairn.engines import vectors

from .client import EMBEDDING_DIM, EmbedderClient


def _as_text(input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]) -> str:
    if isinstance(input_data, str):
        return input_data
    items = list(input_data)  # type: ignore[arg-type]
    if items and all(isinstance(x, str) for x in items):
        return ' '.join(items)  # type: ignore[arg-type]
    return ' '.join(str(x) for x in items)


def hash_vector(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """The shared deterministic hashing embedding of ``text`` (unit length)."""
    return [float(x) for x in vectors.hash_embed(text, dim)]


class HashingEmbedder(EmbedderClient):
    """Deterministic lexical embedder (384-d, unit length), independent of the local model."""

    embedder_id = vectors.HASH_ID

    def __init__(self, dim: int = EMBEDDING_DIM):
        self.dim = dim

    async def create(self, input_data) -> list[float]:
        return hash_vector(_as_text(input_data), self.dim)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [hash_vector(t, self.dim) for t in input_data_list]


class LocalEmbedder(EmbedderClient):
    """Embeddings from ``cairn.engines.vectors.embed`` (run off the event loop)."""

    def __init__(
        self,
        embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
        embedder_id_fn: Callable[[], str] | None = None,
    ):
        self._embed = embed_fn or vectors.embed
        self._id_fn = embedder_id_fn or (vectors.embedder_id if embed_fn is None else None)

    @property
    def embedder_id(self) -> str:
        """Identity of the embedding space (stored vectors are re-embedded when it changes)."""
        return self._id_fn() if self._id_fn else 'custom'

    def _run(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._embed(texts)]

    async def create(self, input_data) -> list[float]:
        return (await asyncio.to_thread(self._run, [_as_text(input_data)]))[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        if not input_data_list:
            return []
        return await asyncio.to_thread(self._run, [_as_text(t) for t in input_data_list])
