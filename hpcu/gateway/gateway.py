"""Provider-neutral gateway contracts for semantic model calls."""

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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


RetryAttemptHook = Callable[[ModelCallPurpose], None]
_RETRY_ATTEMPT_HOOK: ContextVar[RetryAttemptHook | None] = ContextVar(
    "hpcu_retry_attempt_hook",
    default=None,
)


@contextmanager
def bind_retry_attempt_hook(hook: RetryAttemptHook) -> Iterator[None]:
    """Bind a task-local hook invoked before every provider attempt."""
    token = _RETRY_ATTEMPT_HOOK.set(hook)
    try:
        yield
    finally:
        _RETRY_ATTEMPT_HOOK.reset(token)


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


# Transient error types to retry
_TRANSIENT_HTTP_STATUS = frozenset({429, 502, 503, 504})


class RetryableGateway(Gateway):
    """Transient-error retry wrapper around any Gateway.

    Retries on network errors (ReadTimeout, ConnectError, RemoteProtocolError)
    and HTTP 429/502/503/504 with exponential backoff + jitter.
    Does NOT retry on 4xx (except 429) or schema errors.
    """

    def __init__(
        self,
        inner: Gateway,
        *,
        max_retries: int = 2,
        base_delay_ms: int = 500,
        max_delay_ms: int = 8000,
    ):
        self._inner = inner
        self._max_retries = max_retries
        self._base_delay_ms = base_delay_ms
        self._max_delay_ms = max_delay_ms

    @property
    def provider_id(self) -> str:
        return self._inner.provider_id

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    def __getattr__(self, name: str):
        """Delegate attribute access to the inner gateway.

        This allows CountingGateway-specific attributes (call_count,
        error_count, tokens, ai_calls, require_configured_identity, etc.)
        to be accessed transparently through the retry wrapper.
        """
        # Avoid infinite recursion: __getattr__ is only called when the
        # attribute is not found through normal lookup.
        return getattr(self._inner, name)

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        last_error: Exception | None = None
        attempt_hook = _RETRY_ATTEMPT_HOOK.get()
        for attempt in range(self._max_retries + 1):
            if attempt_hook is not None:
                attempt_hook(purpose)
            try:
                return self._inner.call(
                    prompt, system_prompt, max_tokens, purpose=purpose
                )
            except Exception as exc:
                last_error = exc
                if attempt == self._max_retries:
                    raise
                if not self._is_transient(exc):
                    raise
                delay_ms = min(
                    self._base_delay_ms * (2 ** attempt)
                    + random.randint(0, self._base_delay_ms),
                    self._max_delay_ms,
                )
                time.sleep(delay_ms / 1000.0)
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """Return True when the error is likely transient and worth retrying."""
        name = type(exc).__name__
        # httpx errors
        if name in (
            "ReadTimeout",
            "ConnectError",
            "RemoteProtocolError",
            "ConnectTimeout",
            "ReadError",
            "WriteError",
        ):
            return True
        # HTTP status errors (httpx.HTTPStatusError)
        if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
            return exc.response.status_code in _TRANSIENT_HTTP_STATUS
        return False
