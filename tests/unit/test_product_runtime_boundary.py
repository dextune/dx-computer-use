"""The product composition root owns semantic identity and task budget."""

import pytest

from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
    RetryableGateway,
)
from hpcu.runtime_core.product_runtime import CommandRuntime
from hpcu.runtime_core.task_budget import (
    BudgetedGateway,
    ModelBudgetExceeded,
    TaskBudgetLedger,
)
from hpcu.schemas.budget import TaskBudgetSpec

pytestmark = pytest.mark.unit


class ConnectError(Exception):
    """Transient error name recognized by RetryableGateway."""


class _Gateway(Gateway):
    def __init__(self, provider: str = "fake", model: str = "fake-model") -> None:
        self._provider = provider
        self._model = model

    @property
    def provider_id(self) -> str:
        return self._provider

    @property
    def model_id(self) -> str:
        return self._model

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        del prompt, system_prompt, max_tokens, purpose
        return GatewayResponse(
            content="{}",
            model=self.model_id,
            provider=self.provider_id,
        )


class _FlakyGateway(_Gateway):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectError("transient")
        return super().call(
            prompt,
            system_prompt,
            max_tokens,
            purpose=purpose,
        )


def _config(*, provider: str = "fake", model: str = "fake-model") -> dict:
    return {
        "semantic": {
            "default_provider": provider,
            "default_model": model,
            "request_limits": {
                "action_decision_retry_attempts": 2,
                "retry_base_delay_ms": 1,
                "retry_max_delay_ms": 2,
            },
        },
        "recovery": {
            "max_local_repairs_per_node": 1,
            "max_replans_per_task": 1,
        },
    }


def test_command_runtime_rejects_provider_or_model_drift():
    with pytest.raises(ValueError, match="provider mismatch"):
        CommandRuntime(
            lambda: object(),
            provider_gateway=_Gateway(provider="other"),
            config=_config(),
        )
    with pytest.raises(ValueError, match="model mismatch"):
        CommandRuntime(
            lambda: object(),
            provider_gateway=_Gateway(model="other-model"),
            config=_config(),
        )


def test_command_runtime_rejects_foreign_budget_wrapper():
    budget = TaskBudgetSpec(max_model_calls=1)
    wrapped = BudgetedGateway(
        _Gateway(),
        TaskBudgetLedger(budget),
    )

    with pytest.raises(ValueError, match="pre-existing task budget"):
        CommandRuntime(
            lambda: object(),
            provider_gateway=wrapped,
            config=_config(),
        )


def test_command_runtime_preserves_existing_retry_boundary():
    retrying = RetryableGateway(
        _Gateway(),
        max_retries=0,
        base_delay_ms=1,
        max_delay_ms=1,
    )
    runtime = CommandRuntime(
        lambda: object(),
        provider_gateway=retrying,
        config=_config(),
    )
    ledger = TaskBudgetLedger(TaskBudgetSpec(max_model_calls=1))

    gateway = runtime._semantic_gateway(ledger)

    assert isinstance(gateway, BudgetedGateway)
    assert gateway._inner is retrying


def test_command_runtime_auto_retry_cannot_bypass_task_budget():
    inner = _FlakyGateway()
    runtime = CommandRuntime(
        lambda: object(),
        provider_gateway=inner,
        config=_config(),
    )
    ledger = TaskBudgetLedger(
        TaskBudgetSpec(
            max_model_calls=1,
            max_model_tokens=100,
            max_model_latency_ms=1000,
        )
    )
    gateway = runtime._semantic_gateway(ledger)

    with pytest.raises(ModelBudgetExceeded, match="model-call"):
        gateway.call("compile", purpose=ModelCallPurpose.PLAN_COMPILE)

    assert inner.attempts == 1
    assert ledger.model_calls == 1
