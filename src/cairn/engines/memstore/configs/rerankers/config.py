from typing import Optional

from pydantic import BaseModel, Field


class RerankerConfig(BaseModel):
    """Configuration for rerankers."""

    provider: str = Field(
        description="Reranker provider ('llm_reranker', 'sentence_transformer', 'huggingface')", default="llm_reranker"
    )
    config: Optional[dict] = Field(description="Provider-specific reranker configuration", default=None)

    model_config = {"extra": "forbid"}
