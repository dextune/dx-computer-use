"""Regression tests for provider-attempt accounting under retries."""

import pytest

from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
    RetryableGateway,
)
from hpcu.runtime_core.task_budget import (
    BudgetedGateway,
    ModelBudgetExceeded,
    TaskBudgetLedger,
)
from hpcu.schemas.budget import TaskBudgetSpec

pytestmark = pytest.mark.unit


class ConnectError(Exception):
    """Transient error name recognized by RetryableGateway."""


class _Clock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class _FlakyGateway(Gateway):
    def __init__(
        self,
        *,
        failures: int,
        response_latency_ms: int = 0,
    ) -> None:
        self.failures = failures
        self.response_latency_ms = response_latency_ms
        self.attempts = 0

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        del prompt, system_prompt, max_tokens, purpose
        self.attempts += 1
        if self.attempts <= self.failures:
            raise ConnectError("transient")
        return GatewayResponse(
            content="{}",
            model="fake-model",
            provider="fake",
            tokens_used=7,
            latency_ms=self.response_latency_ms,
        )


class _TransparentGateway(Gateway):
    """Represent a logical-call telemetry wrapper around transport retries."""

    def __init__(self, inner: Gateway) -> None:
        self._inner = inner

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        return self._inner.call(
            prompt,
            system_prompt,
            max_tokens,
            purpose=purpose,
        )


def _ledger(*, calls: int, latency_ms: int = 1000) -> TaskBudgetLedger:
    return TaskBudgetLedger(
        TaskBudgetSpec(
            max_model_calls=calls,
            max_model_tokens=100,
            max_model_latency_ms=latency_ms,
        )
    )


def test_retry_cannot_exceed_task_model_call_budget():
    # Given
    inner = _FlakyGateway(failures=1)
    retrying = RetryableGateway(
        inner,
        max_retries=2,
        base_delay_ms=0,
        max_delay_ms=0,
    )
    ledger = _ledger(calls=1)
    gateway = BudgetedGateway(
        retrying,
        ledger,
        monotonic=_Clock(10.0, 10.1),
    )

    # When / Then
    with pytest.raises(ModelBudgetExceeded, match="model-call"):
        gateway.call("compile", purpose=ModelCallPurpose.PLAN_COMPILE)
    assert inner.attempts == 1
    assert ledger.model_calls == 1
    assert ledger.calls_by_purpose[ModelCallPurpose.PLAN_COMPILE] == 1
    assert ledger.latency_ms == 99


def test_retry_attempts_are_counted_through_logical_wrapper():
    # Given
    inner = _FlakyGateway(failures=1, response_latency_ms=5)
    retrying = RetryableGateway(
        inner,
        max_retries=2,
        base_delay_ms=0,
        max_delay_ms=0,
    )
    ledger = _ledger(calls=2)
    gateway = BudgetedGateway(
        _TransparentGateway(retrying),
        ledger,
        monotonic=_Clock(20.0, 20.2),
    )

    # When
    response = gateway.call("compile", purpose=ModelCallPurpose.PLAN_COMPILE)

    # Then
    assert response.content == "{}"
    assert inner.attempts == 2
    assert ledger.model_calls == 2
    assert ledger.calls_by_purpose[ModelCallPurpose.PLAN_COMPILE] == 2
    assert ledger.tokens_used == 7
    assert ledger.latency_ms == 199


def test_failed_provider_call_accounts_local_elapsed_time():
    # Given
    inner = _FlakyGateway(failures=1)
    ledger = _ledger(calls=1)
    gateway = BudgetedGateway(
        inner,
        ledger,
        monotonic=_Clock(30.0, 30.25),
    )

    # When / Then
    with pytest.raises(ConnectError):
        gateway.call("decide", purpose=ModelCallPurpose.ACTION_DECISION)
    assert ledger.model_calls == 1
    assert ledger.latency_ms == 250


def test_reported_latency_is_used_when_greater_than_observed():
    # Given
    inner = _FlakyGateway(failures=0, response_latency_ms=400)
    ledger = _ledger(calls=1)
    gateway = BudgetedGateway(
        inner,
        ledger,
        monotonic=_Clock(40.0, 40.01),
    )

    # When
    gateway.call("fill", purpose=ModelCallPurpose.INTENT_FILL)

    # Then
    assert ledger.model_calls == 1
    assert ledger.latency_ms == 400


def test_retry_attempt_hook_does_not_leak_after_budgeted_call():
    # Given
    inner = _FlakyGateway(failures=0)
    retrying = RetryableGateway(
        inner,
        max_retries=0,
        base_delay_ms=0,
        max_delay_ms=0,
    )
    ledger = _ledger(calls=1)
    gateway = BudgetedGateway(
        retrying,
        ledger,
        monotonic=_Clock(50.0, 50.01),
    )

    # When
    gateway.call("first", purpose=ModelCallPurpose.PLAN_COMPILE)
    retrying.call("outside", purpose=ModelCallPurpose.ACTION_DECISION)

    # Then
    assert inner.attempts == 2
    assert ledger.model_calls == 1
