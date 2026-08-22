"""Provider-neutral gateway contracts for semantic model calls."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ModelCallPurpose(str, Enum):
    """Why the configured semantic model was called."""

    INTENT_FILL = "intent_fill"
    PLAN_COMPILE = "plan_compile"
    GROUNDING = "grounding"
    SITUATION_ANALYSIS = "situation_analysis"
    ACTION_DECISION = "action_decision"
    POST_ACTION_REANALYSIS = "post_action_reanalysis"
    RECOVERY_REANALYSIS = "recovery_reanalysis"


@dataclass(frozen=True)
class SemanticIdentity:
    """Provider/model identity selected by runtime configuration."""

    provider_id: str
    model_id: str

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or not self.model_id.strip():
            raise ValueError("semantic provider and model identities are required")


@dataclass(frozen=True)
class GatewayResponse:
    """A completed semantic model call's result."""

    content: str
    model: str
    tokens_used: int = 0
    latency_ms: int = 0
    provider: str = ""


class Gateway(ABC):
    """Provider-neutral semantic gateway contract.

    The selected deployment is configuration, not a production code
    constant. CPU perception may produce neutral scene facts, but semantic
    decisions must cross this boundary with an explicit call purpose.
    """

    @property
    def provider_id(self) -> str:
        """Return the provider identity when the adapter exposes it."""
        return ""

    @property
    def model_id(self) -> str:
        """Return the exact provider model identity when known."""
        return ""

    @property
    def identity(self) -> SemanticIdentity | None:
        """Return a complete identity, or ``None`` for an anonymous fake."""
        if not self.provider_id or not self.model_id:
            return None
        return SemanticIdentity(self.provider_id, self.model_id)

    def require_configured_identity(self, expected: SemanticIdentity) -> None:
        """Fail closed unless this gateway matches the selected deployment."""
        if self.identity != expected:
            raise ValueError(
                "semantic gateway identity mismatch: "
                f"expected {expected.provider_id}/{expected.model_id}, "
                f"got {self.provider_id or '?'}" "/" f"{self.model_id or '?'}"
            )

    def require_configured_model(self, expected_model: str) -> None:
        """Fail closed unless this gateway matches the selected model."""
        if not expected_model or self.model_id != expected_model:
            raise ValueError(
                f"semantic gateway must identify as {expected_model!r}, "
                f"got {self.model_id!r}"
            )

    @abstractmethod
    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        """Send a purpose-tagged request and return its response."""
        ...
