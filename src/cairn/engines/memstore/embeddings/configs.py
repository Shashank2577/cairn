from typing import Optional

from pydantic import BaseModel, Field, field_validator


class EmbedderConfig(BaseModel):
    provider: str = Field(
        description="Provider of the embedding model ('local' = on-device ONNX model, no key)",
        default="local",
    )
    config: Optional[dict] = Field(description="Configuration for the specific embedding model", default={})

    @field_validator("config")
    def validate_config(cls, v, values):
        from cairn.engines.memstore.utils.factory import EmbedderFactory

        provider = values.data.get("provider")
        if provider in EmbedderFactory.provider_to_class:
            return v
        raise ValueError(f"Unsupported embedding provider: {provider}")
