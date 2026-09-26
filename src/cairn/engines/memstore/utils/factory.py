import importlib
import inspect
from typing import Dict, Optional, Union

from cairn.engines.memstore.configs.embeddings.base import BaseEmbedderConfig
from cairn.engines.memstore.configs.llms.base import BaseLlmConfig, RouterLlmConfig
from cairn.engines.memstore.configs.rerankers.base import BaseRerankerConfig
from cairn.engines.memstore.configs.rerankers.huggingface import HuggingFaceRerankerConfig
from cairn.engines.memstore.configs.rerankers.llm import LLMRerankerConfig
from cairn.engines.memstore.configs.rerankers.sentence_transformer import (
    SentenceTransformerRerankerConfig,
)

_PKG = "cairn.engines.memstore"


def load_class(class_type):
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class LlmFactory:
    """
    Factory for creating LLM instances with appropriate configurations.

    Every generation goes through Cairn's model router ("router"). Other implementations of
    ``LLMBase`` can be added with ``register_provider``.
    """

    # Provider mappings with their config classes
    provider_to_class = {
        "router": (f"{_PKG}.llms.router.RouterLLM", RouterLlmConfig),
    }

    @classmethod
    def create(cls, provider_name: str, config: Optional[Union[BaseLlmConfig, Dict]] = None, **kwargs):
        """
        Create an LLM instance with the appropriate configuration.

        Args:
            provider_name (str): The provider name (e.g., 'router')
            config: Configuration object or dict. If None, will create default config
            **kwargs: Additional configuration parameters

        Returns:
            Configured LLM instance

        Raises:
            ValueError: If provider is not supported
        """
        if provider_name not in cls.provider_to_class:
            raise ValueError(f"Unsupported Llm provider: {provider_name}")

        class_type, config_class = cls.provider_to_class[provider_name]
        llm_class = load_class(class_type)

        # Handle configuration
        if config is None:
            config = config_class(**kwargs)
        elif isinstance(config, dict):
            config = {**config, **kwargs}
            params = inspect.signature(config_class).parameters
            accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in params.values())
            if not accepts_kwargs:
                config = {k: v for k, v in config.items() if k in params}
            config = config_class(**config)
        elif isinstance(config, BaseLlmConfig) and not isinstance(config, config_class):
            config_dict = {
                "model": config.model,
                "temperature": config.temperature,
                "api_key": config.api_key,
                "max_tokens": config.max_tokens,
                "top_p": config.top_p,
                "top_k": config.top_k,
                "enable_vision": config.enable_vision,
                "vision_details": config.vision_details,
                "reasoning_effort": config.reasoning_effort,
                "is_reasoning_model": config.is_reasoning_model,
            }
            config_dict.update(kwargs)
            config = config_class(**config_dict)

        return llm_class(config)

    @classmethod
    def register_provider(cls, name: str, class_path: str, config_class=None):
        """
        Register a new provider.

        Args:
            name (str): Provider name
            class_path (str): Full path to LLM class
            config_class: Configuration class for the provider (defaults to BaseLlmConfig)
        """
        if config_class is None:
            config_class = BaseLlmConfig
        cls.provider_to_class[name] = (class_path, config_class)

    @classmethod
    def get_supported_providers(cls) -> list:
        """
        Get list of supported providers.

        Returns:
            list: List of supported provider names
        """
        return list(cls.provider_to_class.keys())


class EmbedderFactory:
    provider_to_class = {
        "local": f"{_PKG}.embeddings.local.LocalEmbedding",
        # the same on-device model under its runtime's name, for configs written that way
        "fastembed": f"{_PKG}.embeddings.local.LocalEmbedding",
    }

    @classmethod
    def create(cls, provider_name, config, vector_config: Optional[dict] = None):
        class_type = cls.provider_to_class.get(provider_name)
        if class_type:
            embedder_instance = load_class(class_type)
            base_config = BaseEmbedderConfig(**(config or {}))
            return embedder_instance(base_config)
        raise ValueError(f"Unsupported Embedder provider: {provider_name}")

    @classmethod
    def register_provider(cls, name: str, class_path: str):
        cls.provider_to_class[name] = class_path


class VectorStoreFactory:
    provider_to_class = {
        "faiss": f"{_PKG}.vector_stores.faiss.FAISS",
        "qdrant": f"{_PKG}.vector_stores.qdrant.Qdrant",
        "chroma": f"{_PKG}.vector_stores.chroma.ChromaDB",
        "pgvector": f"{_PKG}.vector_stores.pgvector.PGVector",
        "milvus": f"{_PKG}.vector_stores.milvus.MilvusDB",
        "mongodb": f"{_PKG}.vector_stores.mongodb.MongoDB",
        "redis": f"{_PKG}.vector_stores.redis.RedisDB",
        "valkey": f"{_PKG}.vector_stores.valkey.ValkeyDB",
        "elasticsearch": f"{_PKG}.vector_stores.elasticsearch.ElasticsearchDB",
        "opensearch": f"{_PKG}.vector_stores.opensearch.OpenSearchDB",
        "supabase": f"{_PKG}.vector_stores.supabase.Supabase",
        "weaviate": f"{_PKG}.vector_stores.weaviate.Weaviate",
        "langchain": f"{_PKG}.vector_stores.langchain.Langchain",
        "cassandra": f"{_PKG}.vector_stores.cassandra.CassandraDB",
        "oracledb": f"{_PKG}.vector_stores.oracledb.OracleAIVectorSearch",
    }

    @classmethod
    def create(cls, provider_name, config):
        class_type = cls.provider_to_class.get(provider_name)
        if class_type:
            if not isinstance(config, dict):
                config = config.model_dump()
            vector_store_instance = load_class(class_type)
            return vector_store_instance(**config)
        raise ValueError(f"Unsupported VectorStore provider: {provider_name}")

    @classmethod
    def reset(cls, instance):
        instance.reset()
        return instance


class RerankerFactory:
    """
    Factory for creating reranker instances with appropriate configurations.
    Supports provider-specific configs following the same pattern as other factories.
    """

    # Provider mappings with their config classes
    provider_to_class = {
        "llm_reranker": (f"{_PKG}.reranker.llm_reranker.LLMReranker", LLMRerankerConfig),
        "sentence_transformer": (
            f"{_PKG}.reranker.sentence_transformer_reranker.SentenceTransformerReranker",
            SentenceTransformerRerankerConfig,
        ),
        "huggingface": (f"{_PKG}.reranker.huggingface_reranker.HuggingFaceReranker", HuggingFaceRerankerConfig),
    }

    @classmethod
    def create(cls, provider_name: str, config: Optional[Union[BaseRerankerConfig, Dict]] = None,
               llm=None, **kwargs):
        """
        Create a reranker instance based on the provider and configuration.

        Args:
            provider_name: The reranker provider (e.g., 'llm_reranker', 'sentence_transformer')
            config: Configuration object or dictionary
            llm: The engine's LLM, reused by LLM-based rerankers
            **kwargs: Additional configuration parameters

        Returns:
            Reranker instance configured for the specified provider

        Raises:
            ImportError: If the provider class cannot be imported
            ValueError: If the provider is not supported
        """
        if provider_name not in cls.provider_to_class:
            raise ValueError(f"Unsupported reranker provider: {provider_name}")

        class_path, config_class = cls.provider_to_class[provider_name]

        # Handle configuration
        if config is None:
            config = config_class(**kwargs)
        elif isinstance(config, dict):
            config = config_class(**config, **kwargs)
        elif not isinstance(config, BaseRerankerConfig):
            raise ValueError(f"Config must be a {config_class.__name__} instance or dict")

        # Import and create the reranker class
        try:
            reranker_class = load_class(class_path)
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Could not import reranker for provider '{provider_name}': {e}")

        if llm is not None and "llm" in inspect.signature(reranker_class).parameters:
            return reranker_class(config, llm=llm)
        return reranker_class(config)
