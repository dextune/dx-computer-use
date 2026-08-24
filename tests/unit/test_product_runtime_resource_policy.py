"""Composition tests for effective budgets and retry policy injection."""

import pytest

from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
    RetryableGateway,
    walk_gateway_chain,
)
from hpcu.runtime_core.product_runtime import CommandRequest, CommandRuntime
from hpcu.runtime_core.task_budget import TaskBudgetLedger
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.capability import Capability
from hpcu.schemas.strategy import CapabilitySnapshot
from hpcu.schemas.surface import ExecutionMode, SurfaceKind

pytestmark = pytest.mark.unit


class _Gateway(Gateway):
    @property
    def provider_id(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return "fake-model"

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        del prompt, system_prompt, max_tokens, purpose
        return GatewayResponse(content="{}", model=self.model_id)


def _config() -> dict:
    return {
        "semantic": {
            "default_provider": "fake",
            "default_model": "fake-model",
            "request_limits": {
                "intent_fill_retry_attempts": 0,
                "plan_compile_retry_attempts": 1,
                "application_selection_retry_attempts": 5,
                "grounding_retry_attempts": 2,
                "action_decision_retry_attempts": 3,
                "reanalysis_retry_attempts": 4,
                "retry_base_delay_ms": 0,
                "retry_max_delay_ms": 0,
            },
        },
        "tier_budget": {
            "max_model_calls_per_task": 3,
            "max_model_tokens_per_task": 100,
            "max_model_latency_ms_per_task": 1000,
            "planning_call_ceiling": 2,
            "recovery_call_reserve": 1,
        },
    }


def _capability() -> CapabilitySnapshot:
    return CapabilitySnapshot(
        active_surface=SurfaceKind.BROWSER,
        execution_mode=ExecutionMode.SCREEN_STRICT,
        capture=Capability.SUPPORTED,
        structure=Capability.SUPPORTED,
        semantic_input=Capability.SUPPORTED,
        physical_input=Capability.SUPPORTED,
    )


def test_command_request_defers_default_budget_to_composition_root():
    request = CommandRequest("브라우저 열어줘", _capability())
    assert request.task_budget is None


def test_existing_retry_wrapper_receives_purpose_policy():
    retrying = RetryableGateway(
        _Gateway(),
        max_retries=9,
        base_delay_ms=0,
        max_delay_ms=0,
    )
    runtime = CommandRuntime(
        lambda: object(),
        provider_gateway=retrying,
        config=_config(),
    )
    gateway = runtime._semantic_gateway(
        TaskBudgetLedger(TaskBudgetSpec(max_model_calls=10))
    )

    layers = walk_gateway_chain(gateway)
    retry_layer = next(
        layer for layer in layers if isinstance(layer, RetryableGateway)
    )
    assert retry_layer is retrying
    assert retry_layer.retry_limit(ModelCallPurpose.INTENT_FILL) == 0
    assert retry_layer.retry_limit(ModelCallPurpose.PLAN_COMPILE) == 1
    assert retry_layer.retry_limit(ModelCallPurpose.APPLICATION_SELECTION) == 5
    assert retry_layer.retry_limit(ModelCallPurpose.GROUNDING) == 2
    assert retry_layer.retry_limit(ModelCallPurpose.ACTION_DECISION) == 3
    assert retry_layer.retry_limit(ModelCallPurpose.RECOVERY_REANALYSIS) == 4


def test_explicit_task_budget_is_preserved_by_request():
    budget = TaskBudgetSpec(max_model_calls=1)
    request = CommandRequest(
        "브라우저 열어줘",
        _capability(),
        task_budget=budget,
    )
    assert request.task_budget is budget
