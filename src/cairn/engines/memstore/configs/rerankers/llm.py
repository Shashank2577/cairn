from typing import Any, Dict, Optional

from pydantic import Field

from cairn.engines.memstore.configs.rerankers.base import BaseRerankerConfig


class LLMRerankerConfig(BaseRerankerConfig):
    """
    Configuration for LLM-based reranker.
    
    Attributes:
        model (str): Router tier to force for scoring calls (e.g. "fast"). Defaults to None (task tiering).
        api_key (str): Unused with the router provider.
        provider (str): LLM provider. Defaults to "router".
        top_k (int): Number of top documents to return after reranking.
        temperature (float): Temperature for LLM generation. Defaults to 0.0 for deterministic scoring.
        max_tokens (int): Maximum tokens for LLM response. Defaults to 100.
        scoring_prompt (str): Custom prompt template for scoring documents.
    """
    
    model: Optional[str] = Field(
        default=None,
        description="Router tier to force for scoring calls"
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API key for the LLM provider"
    )
    provider: str = Field(
        default="router",
        description="LLM provider ('router' = Cairn's model router)"
    )
    top_k: Optional[int] = Field(
        default=None,
        description="Number of top documents to return after reranking"
    )
    temperature: float = Field(
        default=0.0,
        description="Temperature for LLM generation"
    )
    max_tokens: int = Field(
        default=100,
        description="Maximum tokens for LLM response"
    )
    scoring_prompt: Optional[str] = Field(
        default=None,
        description="Custom prompt template for scoring documents"
    )
    llm: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Nested LLM configuration with 'provider' and 'config' keys. "
        "Overrides top-level provider/model/api_key when provided.",
    )
