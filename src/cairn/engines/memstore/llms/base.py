import asyncio
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

from cairn.engines.memstore.configs.llms.base import BaseLlmConfig


class LLMBase(ABC):
    """
    Base class for all LLM providers.
    Handles common functionality and delegates provider-specific logic to subclasses.
    """

    def __init__(self, config: Optional[Union[BaseLlmConfig, Dict]] = None):
        """Initialize a base LLM class

        :param config: LLM configuration option class or dict, defaults to None
        :type config: Optional[Union[BaseLlmConfig, Dict]], optional
        """
        if config is None:
            self.config = BaseLlmConfig()
        elif isinstance(config, dict):
            # Handle dict-based configuration (backward compatibility)
            self.config = BaseLlmConfig(**config)
        else:
            self.config = config

        # Validate configuration
        self._validate_config()

    def _validate_config(self):
        """
        Validate the configuration.
        Override in subclasses to add provider-specific validation.
        """
        if not hasattr(self.config, "model"):
            raise ValueError("Configuration must have a 'model' attribute")

    @property
    def available(self) -> bool:
        """Whether a model can be called right now. Providers without a notion of availability are."""
        return True

    @abstractmethod
    def generate_response(
        self, messages: List[Dict[str, str]], tools: Optional[List[Dict]] = None, tool_choice: str = "auto", **kwargs
    ):
        """
        Generate a response based on the given messages.

        Args:
            messages (list): List of message dicts containing 'role' and 'content'.
            tools (list, optional): List of tools that the model can call. Defaults to None.
            tool_choice (str, optional): Tool choice method. Defaults to "auto".
            **kwargs: Additional provider-specific parameters (e.g. ``response_format``).

        Returns:
            str or dict: The generated response. With ``tools``, a dict
            ``{"content": str, "tool_calls": [{"name": str, "arguments": dict}]}``.
        """
        pass

    async def agenerate_response(
        self, messages: List[Dict[str, str]], tools: Optional[List[Dict]] = None, tool_choice: str = "auto", **kwargs
    ):
        """Async variant of ``generate_response`` (runs the blocking call in a worker thread)."""
        return await asyncio.to_thread(self.generate_response, messages, tools, tool_choice, **kwargs)
