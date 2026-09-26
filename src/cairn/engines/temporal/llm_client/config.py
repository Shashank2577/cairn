from enum import Enum

DEFAULT_MAX_TOKENS = 16384
DEFAULT_TEMPERATURE = 1


class ModelSize(Enum):
    """Which tier a prompt needs: ``medium`` maps to the router's balanced tier, ``small`` to fast."""

    small = 'small'
    medium = 'medium'


class LLMConfig:
    """
    Settings shared by the engine's model clients.

    With ``RouterLLMClient`` the provider, key, endpoint and model names come from Cairn's model
    router (``[models]`` in ``.cairn/config.toml``); only ``max_tokens`` and ``temperature`` are
    used. ``model`` / ``small_model`` name local models for ``LocalEntityExtractorClient``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        small_model: str | None = None,
    ):
        """
        Args:
            api_key: Credential for clients that talk to a service directly (unused by the router
                adapter, which takes credentials from the router).
            model: Model used for regular prompts (clients that choose their own model).
            base_url: Endpoint for clients that talk to a service directly.
            temperature: Sampling temperature.
            max_tokens: Default output-token ceiling per call.
            small_model: Model used for simpler prompts (clients that choose their own model).
        """
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.small_model = small_model
        self.temperature = temperature
        self.max_tokens = max_tokens
