"""Local embeddings for the memory engine: an adapter over Cairn's shared ``vectors.embed``."""
from typing import Literal, Optional

from cairn.engines import vectors
from cairn.engines.memstore.configs.embeddings.base import BaseEmbedderConfig
from cairn.engines.memstore.embeddings.base import EmbeddingBase


class LocalEmbedding(EmbeddingBase):
    """384-d on-device embeddings (``BAAI/bge-small-en-v1.5`` with a deterministic offline fallback)."""

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)
        if self.config.model and self.config.model != vectors.MODEL:
            raise ValueError(
                f"The local embedder serves {vectors.MODEL}; '{self.config.model}' is not available locally."
            )
        self.config.model = vectors.MODEL
        if self.config.embedding_dims and self.config.embedding_dims != vectors.DIM:
            raise ValueError(f"The local embedder produces {vectors.DIM}-d vectors, not {self.config.embedding_dims}")
        self.config.embedding_dims = vectors.DIM

    @property
    def space(self) -> str:
        """Identity of the embedding space (changes when the offline fallback is in use)."""
        return vectors.embedder_id()

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text.

        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        return self.embed_batch([text], memory_action)[0]

    def embed_batch(self, texts, memory_action="add"):
        clean = [str(t).replace("\n", " ") for t in texts]
        if memory_action == "search":
            return vectors.embed_query(clean)
        return vectors.embed(clean)
