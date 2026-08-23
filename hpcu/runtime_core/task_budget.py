"""Task-global semantic budget accounting and gateway enforcement."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
    RetryableGateway,
    bind_retry_attempt_hook,
    walk_gateway_chain,
)
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.failure_codes import FailureCode

_PLANNING_PURPOSES = frozenset(
    {ModelCallPurpose.INTENT_FILL, ModelCallPurpose.PLAN_COMPILE}
)
_RECOVERY_PURPOSES = frozenset(
    {
        ModelCallPurpose.POST_ACTION_REANALYSIS,
        ModelCallPurpose.RECOVERY_REANALYSIS,
    }
)


class ModelBudgetExceeded(RuntimeError):
    failure_code = FailureCode.MODEL_BUDGET_EXHAUSTED.value


@dataclass
class TaskBudgetLedger:
    spec: TaskBudgetSpec
    calls_by_purpose: dict[ModelCallPurpose, int] = field(default_factory=dict)
    tokens_used: int = 0
    latency_ms: int = 0

    @property
    def model_calls(self) -> int:
        return sum(self.calls_by_purpose.values())

    @property
    def remaining_calls(self) -> int:
        return max(0, self.spec.max_model_calls - self.model_calls)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.spec.max_model_tokens - self.tokens_used)

    def calls_for(self, purposes: frozenset[ModelCallPurpose]) -> int:
        return sum(self.calls_by_purpose.get(item, 0) for item in purposes)

    def before_call(
        self,
        purpose: ModelCallPurpose,
        *,
        pending_latency_ms: int = 0,
    ) -> None:
        if not isinstance(purpose, ModelCallPurpose):
            purpose = ModelCallPurpose(purpose)
        if self.model_calls >= self.spec.max_model_calls:
            raise ModelBudgetExceeded("task model-call budget exhausted")
        if self.tokens_used >= self.spec.max_model_tokens:
            raise ModelBudgetExceeded("task model-token budget exhausted")
        projected_latency = self.latency_ms + max(0, int(pending_latency_ms))
        if projected_latency >= self.spec.max_model_latency_ms:
            raise ModelBudgetExceeded("task model-latency budget exhausted")

        reserve = self.spec.recovery_call_reserve
        if purpose not in _RECOVERY_PURPOSES and self.remaining_calls <= reserve:
            raise ModelBudgetExceeded("task recovery model-call reserve reached")

        planning_limit = self.spec.planning_call_ceiling
        if (
            planning_limit is not None
            and purpose in _PLANNING_PURPOSES
            and self.calls_for(_PLANNING_PURPOSES) >= planning_limit
        ):
            raise ModelBudgetExceeded("task planning model-call ceiling reached")
        self.calls_by_purpose[purpose] = self.calls_by_purpose.get(purpose, 0) + 1

    def record(
        self,
        response: GatewayResponse,
        *,
        observed_latency_ms: int | None = None,
    ) -> None:
        self.tokens_used += max(0, int(response.tokens_used))
        observed = (
            max(0, int(observed_latency_ms))
            if observed_latency_ms is not None
            else 0
        )
        reported = max(0, int(response.latency_ms))
        self.latency_ms += max(observed, reported)
        if self.tokens_used > self.spec.max_model_tokens:
            raise ModelBudgetExceeded("model response exceeded task token budget")
        if self.latency_ms > self.spec.max_model_latency_ms:
            raise ModelBudgetExceeded("model response exceeded task latency budget")

    def record_elapsed(self, elapsed_ms: int) -> None:
        """Account local elapsed time for a provider call that failed."""
        self.latency_ms += max(0, int(elapsed_ms))
        if self.latency_ms > self.spec.max_model_latency_ms:
            raise ModelBudgetExceeded("model attempt exceeded task latency budget")


def _contains_retryable_gateway(gateway: Gateway) -> bool:
    return any(
        isinstance(layer, RetryableGateway)
        for layer in walk_gateway_chain(gateway)
    )


class BudgetedGateway(Gateway):
    """One accounting boundary for plan, grounding, and recovery calls."""

    def __init__(
        self,
        inner: Gateway,
        ledger: TaskBudgetLedger,
        *,
        monotonic: Callable[[], float] | None = None,
    ):
        self._inner = inner
        self.ledger = ledger
        self._monotonic = monotonic if monotonic is not None else time.monotonic

    @property
    def provider_id(self) -> str:
        return self._inner.provider_id

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def inner_gateway(self) -> Gateway:
        return self._inner

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        retryable = _contains_retryable_gateway(self._inner)
        if not retryable:
            self.ledger.before_call(purpose)
        remaining = self.ledger.remaining_tokens
        effective_max = remaining if max_tokens is None else min(max_tokens, remaining)
        if effective_max <= 0:
            raise ModelBudgetExceeded("task model-token budget exhausted")

        started = self._monotonic()

        def before_retry_attempt(attempt_purpose: ModelCallPurpose) -> None:
            self.ledger.before_call(
                attempt_purpose,
                pending_latency_ms=self._elapsed_ms(started),
            )

        try:
            if retryable:
                with bind_retry_attempt_hook(before_retry_attempt):
                    response = self._inner.call(
                        prompt,
                        system_prompt,
                        effective_max,
                        purpose=purpose,
                    )
            else:
                response = self._inner.call(
                    prompt,
                    system_prompt,
                    effective_max,
                    purpose=purpose,
                )
        except Exception:
            self.ledger.record_elapsed(self._elapsed_ms(started))
            raise
        self.ledger.record(
            response,
            observed_latency_ms=self._elapsed_ms(started),
        )
        return response

    def _elapsed_ms(self, started: float) -> int:
        return max(0, int((self._monotonic() - started) * 1000))
