from typing import Optional

from pydantic import BaseModel, Field, field_validator


class LlmConfig(BaseModel):
    provider: str = Field(description="Provider of the LLM ('router' = Cairn's model router)", default="router")
    config: Optional[dict] = Field(description="Configuration for the specific LLM", default={})

    @field_validator("config")
    def validate_config(cls, v, values):
        from cairn.engines.memstore.utils.factory import LlmFactory

        provider = values.data.get("provider")
        if provider in LlmFactory.provider_to_class:
            return v
        raise ValueError(f"Unsupported LLM provider: {provider}")
