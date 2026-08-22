"""Gateway ABC — the single contract any text-LLM backend implements."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayResponse:
    """A completed model call's result."""

    content: str
    model: str
    tokens_used: int = 0
    latency_ms: int = 0


class Gateway(ABC):
    """Abstract text-LLM client.

    Concrete adapters (MiniMax, OpenAI, ...) implement `call` and are
    constructor-injected into the ModelRouter.
    """

    @abstractmethod
    def call(self, prompt: str, system_prompt: str = "") -> GatewayResponse:
        """Send a prompt and return a schema-stripped GatewayResponse."""
        ...
