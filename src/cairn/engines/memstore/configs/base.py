import os
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from cairn.engines.memstore.configs.rerankers.config import RerankerConfig
from cairn.engines.memstore.embeddings.configs import EmbedderConfig
from cairn.engines.memstore.llms.configs import LlmConfig
from cairn.engines.memstore.vector_stores.configs import VectorStoreConfig


def engine_dir() -> str:
    """Default home of engine state when no project directory is given ($CAIRN_HOME/memstore)."""
    home = os.environ.get("CAIRN_HOME") or os.path.join(os.path.expanduser("~"), ".cairn")
    return os.path.join(home, "memstore")


class MemoryItem(BaseModel):
    id: str = Field(..., description="The unique identifier for the text data")
    memory: str = Field(..., description="The memory deduced from the text data")
    hash: Optional[str] = Field(None, description="The hash of the memory")
    # The metadata value can be anything and not just string.
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata for the text data")
    score: Optional[float] = Field(None, description="The score associated with the text data")
    created_at: Optional[str] = Field(None, description="The timestamp when the memory was created")
    updated_at: Optional[str] = Field(None, description="The timestamp when the memory was updated")


class GraphStoreConfig(BaseModel):
    """Relationship memory. Cairn keeps one fact graph (the temporal engine); this section only says
    whether memory writes/reads should also flow through it."""

    provider: Literal["temporal"] = Field("temporal", description="Graph backend (the temporal fact graph)")
    enabled: bool = Field(True, description="Send added memories to the graph and return related facts")
    config: Optional[Dict[str, Any]] = Field(default=None, description="Extra options passed to the graph")
    custom_prompt: Optional[str] = Field(default=None, description="Extra extraction guidance for the graph")
    threshold: float = Field(0.7, description="Minimum relevance for graph facts returned with results")


class MemoryConfig(BaseModel):
    vector_store: VectorStoreConfig = Field(
        description="Configuration for the vector store",
        default_factory=VectorStoreConfig,
    )
    llm: LlmConfig = Field(
        description="Configuration for the language model",
        default_factory=LlmConfig,
    )
    embedder: EmbedderConfig = Field(
        description="Configuration for the embedding model",
        default_factory=EmbedderConfig,
    )
    history_db_path: str = Field(
        description="Path to the history database",
        default_factory=lambda: os.path.join(engine_dir(), "history.db"),
    )
    reranker: Optional[RerankerConfig] = Field(
        description="Configuration for the reranker",
        default=None,
    )
    graph_store: Optional[GraphStoreConfig] = Field(
        description="Relationship memory through the temporal fact graph",
        default=None,
    )
    version: str = Field(
        description="The version of the API",
        default="v1.1",
    )
    add_mode: Literal["reconcile", "additive"] = Field(
        description=(
            "How add() writes inferred memories. 'reconcile' extracts facts, then decides ADD / UPDATE / "
            "DELETE / NONE against similar existing memories. 'additive' extracts linked ADD-only memories "
            "in one call and relies on hash de-duplication."
        ),
        default="reconcile",
    )
    custom_instructions: Optional[str] = Field(
        description="Custom instructions for fact extraction",
        default=None,
    )
    custom_fact_extraction_prompt: Optional[str] = Field(
        description="Replaces the fact extraction system prompt (reconcile mode)",
        default=None,
    )
    custom_update_memory_prompt: Optional[str] = Field(
        description="Replaces the ADD/UPDATE/DELETE/NONE decision prompt (reconcile mode)",
        default=None,
    )
    entity_linking: bool = Field(
        description="Extract entities from memories and boost search results that share them",
        default=True,
    )
    reconcile_top_k: int = Field(
        description="How many similar memories each new fact is compared against",
        default=5,
    )

    @field_validator("reconcile_top_k")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("reconcile_top_k must be >= 1")
        return v

