"""Tests for effective task budgets and purpose-aware retries."""

import pytest

from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
    RetryableGateway,
)
from hpcu.runtime_config import effective_task_budget, semantic_retry_attempts
from hpcu.runtime_core.task_budget import ModelBudgetExceeded, TaskBudgetLedger
from hpcu.schemas.budget import TaskBudgetSpec

pytestmark = pytest.mark.unit


class ConnectError(Exception):
    """Transient error name recognized by RetryableGateway."""


class _FlakyGateway(Gateway):
    def __init__(self, failures: int) -> None:
        self.failures = failures
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
        return GatewayResponse(content="{}", model="fake")


def test_effective_budget_comes_from_deployment_config():
    budget = effective_task_budget(
        {
            "tier_budget": {
                "max_model_calls_per_task": 5,
                "max_model_tokens_per_task": 900,
                "max_model_latency_ms_per_task": 7000,
                "planning_call_ceiling": 2,
                "recovery_call_reserve": 1,
            }
        }
    )

    assert budget == TaskBudgetSpec(
        max_model_calls=5,
        max_model_tokens=900,
        max_model_latency_ms=7000,
        planning_call_ceiling=2,
        recovery_call_reserve=1,
    )


def test_explicit_budget_override_wins_without_merging():
    override = TaskBudgetSpec(max_model_calls=1)
    assert effective_task_budget({}, override) is override


def test_planning_ceiling_and_recovery_reserve_are_enforced():
    ledger = TaskBudgetLedger(
        TaskBudgetSpec(
            max_model_calls=3,
            planning_call_ceiling=2,
            recovery_call_reserve=1,
        )
    )
    ledger.before_call(ModelCallPurpose.INTENT_FILL)
    ledger.before_call(ModelCallPurpose.PLAN_COMPILE)

    with pytest.raises(ModelBudgetExceeded, match="reserve|planning"):
        ledger.before_call(ModelCallPurpose.ACTION_DECISION)
    ledger.before_call(ModelCallPurpose.RECOVERY_REANALYSIS)
    assert ledger.model_calls == 3


def test_planning_ceiling_counts_real_attempts():
    ledger = TaskBudgetLedger(
        TaskBudgetSpec(
            max_model_calls=4,
            planning_call_ceiling=1,
        )
    )
    ledger.before_call(ModelCallPurpose.PLAN_COMPILE)
    with pytest.raises(ModelBudgetExceeded, match="planning"):
        ledger.before_call(ModelCallPurpose.PLAN_COMPILE)


def test_retry_policy_maps_each_purpose_independently():
    policy = semantic_retry_attempts(
        {
            "semantic": {
                "request_limits": {
                    "intent_fill_retry_attempts": 0,
                    "plan_compile_retry_attempts": 1,
                    "grounding_retry_attempts": 2,
                    "action_decision_retry_attempts": 3,
                    "reanalysis_retry_attempts": 4,
                }
            }
        }
    )

    assert policy[ModelCallPurpose.INTENT_FILL] == 0
    assert policy[ModelCallPurpose.PLAN_COMPILE] == 1
    assert policy[ModelCallPurpose.GROUNDING] == 2
    assert policy[ModelCallPurpose.ACTION_DECISION] == 3
    assert policy[ModelCallPurpose.RECOVERY_REANALYSIS] == 4


def test_retryable_gateway_uses_purpose_specific_limit():
    inner = _FlakyGateway(failures=2)
    gateway = RetryableGateway(
        inner,
        max_retries=5,
        max_retries_by_purpose={ModelCallPurpose.PLAN_COMPILE: 1},
        base_delay_ms=0,
        max_delay_ms=0,
    )

    with pytest.raises(ConnectError):
        gateway.call("plan", purpose=ModelCallPurpose.PLAN_COMPILE)
    assert inner.attempts == 2
