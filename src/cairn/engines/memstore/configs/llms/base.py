from abc import ABC
from typing import Any, Dict, Optional, Union


class BaseLlmConfig(ABC):
    """
    Base configuration for LLMs with only common parameters.

    Model selection, credentials and transport belong to Cairn's model router; this class carries the
    generation parameters the memory engine asks for.
    """

    def __init__(
        self,
        model: Optional[Union[str, Dict]] = None,
        temperature: float = 0.1,
        api_key: Optional[str] = None,
        max_tokens: int = 2000,
        top_p: float = 0.1,
        top_k: int = 1,
        enable_vision: bool = False,
        vision_details: Optional[str] = "auto",
        reasoning_effort: Optional[str] = None,
        is_reasoning_model: Optional[bool] = None,
    ):
        """
        Initialize a base configuration class instance for the LLM.

        Args:
            model: A router tier ("fast", "balanced", "deep", "frontier") to force for every call.
                Defaults to None (the router picks the tier from the task).
            temperature: Sampling temperature hint. Defaults to 0.1
            api_key: Unused by the router adapter (credentials come from the router). Defaults to None
            max_tokens: Maximum number of tokens to generate in the response. Defaults to 2000
            top_p: Nucleus sampling hint. Defaults to 0.1
            top_k: Top-k sampling hint. Defaults to 1
            enable_vision: Describe image message parts before extraction. Defaults to False
            vision_details: Level of detail for vision processing ("low", "high", "auto"). Defaults to "auto"
            reasoning_effort: Effort hint for reasoning models. Defaults to None
            is_reasoning_model: Explicit reasoning-model override. Defaults to None
        """
        self.model = model
        self.temperature = temperature
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.top_k = top_k
        self.enable_vision = enable_vision
        self.vision_details = vision_details
        self.reasoning_effort = reasoning_effort
        self.is_reasoning_model = is_reasoning_model


class RouterLlmConfig(BaseLlmConfig):
    """Configuration for the router-backed LLM."""

    def __init__(
        self,
        router: Any = None,
        task: str = "memory",
        tier: Optional[str] = None,
        json_retries: int = 2,
        budget: Any = None,
        **kwargs,
    ):
        """
        Args:
            router: A ``cairn.router.Router`` (or any object with ``available`` and
                ``complete(task, prompt, system=, max_tokens=, tier=)``). When None, one is built for the
                repository discovered from the working directory on first use.
            task: Router task name used for tiering and the cost ledger. Defaults to "memory".
            tier: Force a router tier for every call. Defaults to None.
            json_retries: Extra attempts when structured (JSON) output fails to parse. Defaults to 2.
            budget: A ``cairn.router.Budget`` charged by every call. Defaults to None.
        """
        super().__init__(**kwargs)
        self.router = router
        self.task = task
        self.tier = tier or (self.model if isinstance(self.model, str) else None)
        self.json_retries = max(0, int(json_retries))
        self.budget = budget
