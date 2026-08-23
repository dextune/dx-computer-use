"""Provider-neutral gateway contracts for semantic model calls."""

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
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
    """Provider-neutral semantic gateway contract."""

    @property
    def provider_id(self) -> str:
        """Return the provider identity when the adapter exposes it."""
        return ""

    @property
    def model_id(self) -> str:
        """Return the exact provider model identity when known."""
        return ""

    @property
    def inner_gateway(self) -> "Gateway | None":
        """Expose a transparent wrapper edge without relying on `_inner`."""
        return None

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


def walk_gateway_chain(gateway: Gateway) -> tuple[Gateway, ...]:
    """Return wrapper layers once each, tolerating legacy transparent wrappers."""
    chain: list[Gateway] = []
    current: object | None = gateway
    seen: set[int] = set()
    while isinstance(current, Gateway) and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        next_gateway = current.inner_gateway
        if next_gateway is None:
            legacy = getattr(current, "_inner", None)
            next_gateway = legacy if isinstance(legacy, Gateway) else None
        current = next_gateway
    return tuple(chain)


_TRANSIENT_HTTP_STATUS = frozenset({429, 502, 503, 504})


class RetryableGateway(Gateway):
    """Retry transient provider failures with purpose-specific ceilings."""

    def __init__(
        self,
        inner: Gateway,
        *,
        max_retries: int = 2,
        max_retries_by_purpose: Mapping[ModelCallPurpose, int] | None = None,
        base_delay_ms: int = 500,
        max_delay_ms: int = 8000,
        sleep: Callable[[float], None] | None = None,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if base_delay_ms < 0 or max_delay_ms < 0:
            raise ValueError("retry delay values must be non-negative")
        self._inner = inner
        self._max_retries = max_retries
        self._max_retries_by_purpose: dict[ModelCallPurpose, int] = {}
        self.configure_retry_policy(max_retries_by_purpose or {})
        self._base_delay_ms = base_delay_ms
        self._max_delay_ms = max_delay_ms
        self._sleep = sleep or time.sleep

    @property
    def provider_id(self) -> str:
        return self._inner.provider_id

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def inner_gateway(self) -> Gateway:
        return self._inner

    def configure_retry_policy(
        self,
        policy: Mapping[ModelCallPurpose, int],
    ) -> None:
        normalized: dict[ModelCallPurpose, int] = {}
        for raw_purpose, raw_limit in policy.items():
            purpose = ModelCallPurpose(raw_purpose)
            limit = int(raw_limit)
            if limit < 0:
                raise ValueError("purpose retry limits must be non-negative")
            normalized[purpose] = limit
        self._max_retries_by_purpose = normalized

    def retry_limit(self, purpose: ModelCallPurpose) -> int:
        return self._max_retries_by_purpose.get(purpose, self._max_retries)

    def __getattr__(self, name: str):
        """Delegate telemetry and adapter-specific attributes transparently."""
        return getattr(self._inner, name)

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        if not isinstance(purpose, ModelCallPurpose):
            purpose = ModelCallPurpose(purpose)
        last_error: Exception | None = None
        attempt_hook = _RETRY_ATTEMPT_HOOK.get()
        retry_limit = self.retry_limit(purpose)
        for attempt in range(retry_limit + 1):
            if attempt_hook is not None:
                attempt_hook(purpose)
            try:
                return self._inner.call(
                    prompt,
                    system_prompt,
                    max_tokens,
                    purpose=purpose,
                )
            except Exception as exc:
                last_error = exc
                if attempt == retry_limit or not self._is_transient(exc):
                    raise
                delay_ms = min(
                    self._base_delay_ms * (2**attempt)
                    + random.randint(0, self._base_delay_ms),
                    self._max_delay_ms,
                )
                self._sleep(delay_ms / 1000.0)
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        name = type(exc).__name__
        if name in (
            "ReadTimeout",
            "ConnectError",
            "RemoteProtocolError",
            "ConnectTimeout",
            "ReadError",
            "WriteError",
        ):
            return True
        if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
            return exc.response.status_code in _TRANSIENT_HTTP_STATUS
        return False
