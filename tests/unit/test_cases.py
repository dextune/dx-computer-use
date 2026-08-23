"""CaseSpec data and the thin CaseRunner adapter contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hpcu.cases.runner import CaseRunner
from hpcu.cases.specs import CaseSpec, load_cases
from hpcu.cases.stats import CaseStats, aggregate
from hpcu.compiler.targeting_compiler import TargetingCompilationError
from hpcu.runtime_core.product_runtime import UnresolvedGoalError
from hpcu.runtime_core.task_runtime import TaskRunResult, TaskStatus
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.capability import Capability
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.strategy import CapabilitySnapshot
from hpcu.schemas.surface import ExecutionMode, SurfaceKind

pytestmark = pytest.mark.unit


def _capability() -> CapabilitySnapshot:
    return CapabilitySnapshot(
        active_surface=SurfaceKind.BROWSER,
        execution_mode=ExecutionMode.SCREEN_STRICT,
        capture=Capability.SUPPORTED,
        structure=Capability.SUPPORTED,
        semantic_input=Capability.SUPPORTED,
        physical_input=Capability.SUPPORTED,
        ocr=Capability.SUPPORTED,
    )


class _Runtime:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def _command_result(*, status: TaskStatus = TaskStatus.VERIFIED_SUCCESS):
    failure = (
        None
        if status is TaskStatus.VERIFIED_SUCCESS
        else FailureCode.UNKNOWN.value
    )
    task = TaskRunResult(
        status=status,
        node_id="verify",
        steps=3,
        failure_code=failure,
        final_scene_version=7,
        final_frame_id="frame-7",
        terminal_evidence_ids=(
            ("result",) if status is TaskStatus.VERIFIED_SUCCESS else ()
        ),
        completed_node_ids=("navigate", "verify"),
        plan_hash="plan-hash",
    )
    plan = SimpleNamespace(
        nodes={
            "navigate": SimpleNamespace(
                action=Action(id="navigate", op=ActionOp.NAVIGATE)
            ),
            "verify": SimpleNamespace(
                action=Action(id="verify", op=ActionOp.ASSERT)
            ),
        }
    )
    return SimpleNamespace(
        task=task,
        plan=plan,
        model_calls=1,
        model_tokens=12,
        model_calls_by_purpose=(("plan_compile", 1),),
        provider_id="minimax",
        model_id="MiniMax-M3",
    )


def test_default_case_file_is_data_only_and_loads():
    cases = load_cases()
    assert len(cases) == 10
    assert len({case.id for case in cases}) == len(cases)
    assert all(case.goal for case in cases)
    assert all(case.max_attempts > 0 for case in cases)
    assert all(case.max_model_calls >= 0 for case in cases)


def test_case_spec_rejects_invalid_attempt_and_model_budgets():
    with pytest.raises(ValueError, match="max_attempts"):
        CaseSpec(
            id="bad-attempts",
            goal="search",
            start_url="https://example.com",
            max_attempts=0,
        )
    with pytest.raises(ValueError, match="model-call"):
        CaseSpec(
            id="bad-model",
            goal="search",
            start_url="https://example.com",
            max_model_calls=-1,
        )


@pytest.mark.asyncio
async def test_case_runner_delegates_once_and_maps_verified_terminal_stats():
    runtime = _Runtime(_command_result())
    runner = CaseRunner(runtime, _capability(), monotonic=lambda: 1.0)
    spec = CaseSpec(
        id="adapter",
        goal="구글에서 노트북을 검색해줘",
        start_url="https://www.google.com/",
        max_attempts=4,
        max_model_calls=2,
    )

    stats = await runner.run_case(spec)

    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.instruction == spec.goal
    assert request.task_budget.max_model_calls == 2
    assert request.max_steps == 32
    assert request.context_metadata["start_url"] == spec.start_url
    assert stats.success is True
    assert stats.runner_success is True
    assert stats.verified_success is True
    assert stats.outcome == TaskStatus.VERIFIED_SUCCESS.value
    assert stats.action_count == 1
    assert stats.model_call_count == 1
    assert stats.compile_call_count == 1
    assert stats.evidence_element_ids == ["result"]
    assert stats.final_scene_version == 7
    assert stats.final_frame_id == "frame-7"


@pytest.mark.asyncio
async def test_case_runner_maps_handoff_without_claiming_success():
    runtime = _Runtime(_command_result(status=TaskStatus.HUMAN_HANDOFF))
    stats = await CaseRunner(runtime, _capability()).run_case(
        CaseSpec(
            id="handoff",
            goal="폼을 제출해줘",
            start_url="https://example.com",
        )
    )

    assert stats.success is False
    assert stats.verified_success is False
    assert stats.handoff_required is True
    assert stats.outcome == TaskStatus.HUMAN_HANDOFF.value
    assert stats.failure_code == FailureCode.UNKNOWN.value


@pytest.mark.asyncio
async def test_case_runner_fails_closed_before_runtime_terminal_on_unresolved_goal():
    runtime = _Runtime(error=UnresolvedGoalError("intent"))
    stats = await CaseRunner(runtime, _capability()).run_case(
        CaseSpec(
            id="unresolved",
            goal="이 작업을 처리해줘",
            start_url="https://example.com",
        )
    )

    assert stats.success is False
    assert stats.failure_code == FailureCode.DECISION_REQUIRED.value
    assert stats.failure == "unresolved_goal"


@pytest.mark.asyncio
async def test_case_runner_maps_targeting_failure_without_executing_actions():
    runtime = _Runtime(
        error=TargetingCompilationError(
            "schema_error:no_unique_targeting_object",
            FailureCode.MODEL_SCHEMA_INVALID,
        )
    )
    stats = await CaseRunner(runtime, _capability()).run_case(
        CaseSpec(
            id="bad-plan",
            goal="노트북을 검색해줘",
            start_url="https://example.com",
        )
    )

    assert len(runtime.requests) == 1
    assert stats.success is False
    assert stats.runner_success is False
    assert stats.verified_success is False
    assert stats.failure_code == FailureCode.MODEL_SCHEMA_INVALID.value
    assert stats.failure == "schema_error:no_unique_targeting_object"
    assert stats.action_count == 0


def test_aggregate_uses_verified_case_success_only():
    success = CaseStats(
        case_id="success",
        success=True,
        verified_success=True,
        model_call_count=1,
    )
    failed = CaseStats(
        case_id="failed",
        success=False,
        runner_success=True,
        verified_success=False,
        model_call_count=2,
    )

    payload = aggregate([success, failed])

    assert payload["totals"]["cases"] == 2
    assert payload["totals"]["successes"] == 1
    assert payload["totals"]["model_call_count"] == 3
