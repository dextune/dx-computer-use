"""Async semantic compiler and slot-filler regression tests."""

import asyncio

import pytest

from hpcu.compiler.targeting_compiler import (
    TargetingCompilationError,
    TargetingCompiler,
)
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.planning.goal_interpreter import GoalInterpreter
from hpcu.planning.semantic_interrupt import SemanticSlotFiller
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import IntentKind

pytestmark = pytest.mark.unit


class _Gateway(Gateway):
    def __init__(self, response: str) -> None:
        self.response = response
        self.purposes: list[ModelCallPurpose] = []

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        del prompt, system_prompt, max_tokens
        self.purposes.append(purpose)
        return GatewayResponse(content=self.response, model="fake")


class _SlowAsyncGateway(_Gateway):
    async def acall(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        del prompt, system_prompt, max_tokens, purpose
        await asyncio.sleep(1)
        return GatewayResponse(content="{}", model="fake")


def _targeting_json() -> str:
    return (
        '{"ready_any":["ready"],"success_any":["done"],'
        '"forbid_any":[],"pick_query":"target","pick_required":true,'
        '"dismiss_any":[],"blocked_any":[],"ignore_any":[]}'
    )


@pytest.mark.asyncio
async def test_targeting_compiler_async_uses_plan_purpose():
    gateway = _Gateway(_targeting_json())
    pack = await TargetingCompiler(
        gateway,
        config={"performance": {"model_call_timeout_ms": 100}},
    ).compile_async("goal", "대상을 선택해줘")

    assert pack.pick_query == "target"
    assert gateway.purposes == [ModelCallPurpose.PLAN_COMPILE]


@pytest.mark.asyncio
async def test_targeting_async_timeout_fails_closed_with_typed_code():
    compiler = TargetingCompiler(
        _SlowAsyncGateway(_targeting_json()),
        config={"performance": {"model_call_timeout_ms": 1}},
    )

    with pytest.raises(TargetingCompilationError) as captured:
        await compiler.compile_async("goal", "대상을 선택해줘")

    assert captured.value.failure_code == FailureCode.MODEL_TIMEOUT.value


@pytest.mark.asyncio
async def test_slot_filler_async_returns_only_unresolved_values():
    goal = GoalInterpreter().interpret("이 작업을 처리해줘")
    assert goal.ambiguity_slots == ("intent",)
    filler = SemanticSlotFiller(
        _Gateway('{"intent":"navigate"}'),
        timeout_ms=100,
    )

    values = await filler.fill(goal)

    assert values == {"intent": IntentKind.NAVIGATE.value}
