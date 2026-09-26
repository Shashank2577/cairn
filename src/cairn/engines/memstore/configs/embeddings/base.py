from abc import ABC
from typing import Optional


class BaseEmbedderConfig(ABC):
    """
    Config for Embeddings.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        embedding_dims: Optional[int] = None,
        model_kwargs: Optional[dict] = None,
        memory_add_embedding_type: Optional[str] = None,
        memory_update_embedding_type: Optional[str] = None,
        memory_search_embedding_type: Optional[str] = None,
    ):
        """
        Initializes a configuration class instance for the Embeddings.

        :param model: Embedding model to use; the local embedder accepts only its own model, defaults to None
        :type model: Optional[str], optional
        :param api_key: Unused by the local embedder, defaults to None
        :type api_key: Optional[str], optional
        :param embedding_dims: The number of dimensions in the embedding, defaults to None
        :type embedding_dims: Optional[int], optional
        :param model_kwargs: Extra keyword arguments for the embedding model, defaults a dict inside init
        :type model_kwargs: Optional[Dict[str, Any]], defaults a dict inside init
        :param memory_add_embedding_type: The type of embedding to use for the add memory action, defaults to None
        :type memory_add_embedding_type: Optional[str], optional
        :param memory_update_embedding_type: The type of embedding to use for the update memory action, defaults to None
        :type memory_update_embedding_type: Optional[str], optional
        :param memory_search_embedding_type: The type of embedding to use for the search memory action, defaults to None
        :type memory_search_embedding_type: Optional[str], optional
        """

        self.model = model
        self.api_key = api_key
        self.embedding_dims = embedding_dims
        self.model_kwargs = model_kwargs or {}
        self.memory_add_embedding_type = memory_add_embedding_type
        self.memory_update_embedding_type = memory_update_embedding_type
        self.memory_search_embedding_type = memory_search_embedding_type
