import os
from typing import Dict, Optional

from pydantic import BaseModel, Field, model_validator


class VectorStoreConfig(BaseModel):
    provider: str = Field(
        description="Provider of the vector store (e.g., 'faiss', 'qdrant', 'pgvector')",
        default="faiss",
    )
    config: Optional[Dict] = Field(description="Configuration for the specific vector store", default=None)

    # faiss is local and always available; the others load their client library on first use.
    _provider_configs: Dict[str, str] = {
        "faiss": "FAISSConfig",
        "qdrant": "QdrantConfig",
        "chroma": "ChromaDbConfig",
        "pgvector": "PGVectorConfig",
        "mongodb": "MongoDBConfig",
        "milvus": "MilvusDBConfig",
        "cassandra": "CassandraConfig",
        "redis": "RedisDBConfig",
        "valkey": "ValkeyConfig",
        "elasticsearch": "ElasticsearchConfig",
        "opensearch": "OpenSearchConfig",
        "supabase": "SupabaseConfig",
        "weaviate": "WeaviateConfig",
        "langchain": "LangchainConfig",
        "oracledb": "OracleAIVectorSearchConfig",
    }

    @model_validator(mode="after")
    def validate_and_create_config(self) -> "VectorStoreConfig":
        provider = self.provider
        config = self.config

        if provider not in self._provider_configs:
            raise ValueError(f"Unsupported vector store provider: {provider}")

        module = __import__(
            f"cairn.engines.memstore.configs.vector_stores.{provider}",
            fromlist=[self._provider_configs[provider]],
        )
        config_class = getattr(module, self._provider_configs[provider])

        if config is None:
            config = {}

        if not isinstance(config, dict):
            if not isinstance(config, config_class):
                raise ValueError(f"Invalid config type for provider {provider}")
            return self

        # file-based stores default to the engine directory
        if "path" not in config and "path" in config_class.__annotations__:
            from cairn.engines.memstore.configs.base import engine_dir

            config["path"] = os.path.join(engine_dir(), provider)

        self.config = config_class(**config)
        return self
