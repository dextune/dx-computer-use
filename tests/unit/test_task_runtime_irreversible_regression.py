"""Regression tests for irreversible side-effect terminal priority."""

import pytest

from hpcu.input.injector import ExecutionResult
from hpcu.recovery.loop_breaker import RecoveryAction
from hpcu.runtime_core.control_loop import StepResult
from hpcu.runtime_core.task_runtime import TaskRuntime, TaskStatus
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.plan import PlanIR, PlanNode
from hpcu.schemas.scene import Scene
from hpcu.schemas.surface import SurfaceKind

pytestmark = pytest.mark.unit


class _ControlLoop:
    async def step(self, query, action, *, verification_query=None):
        del query, action, verification_query
        return StepResult(
            success=False,
            scene=Scene(version=1),
            failure_code=FailureCode.POSTCONDITION_UNMET.value,
            execution=ExecutionResult(success=True, mode="semantic"),
            recovery_action=RecoveryAction.HALT,
            pre_scene_version=1,
            post_scene_version=1,
        )


def _plan() -> PlanIR:
    goal = GoalEnvelope(
        raw_instruction="submit",
        intent=IntentKind.SUBMIT,
        terminal_state="submitted",
        model_call_budget=0,
    )
    node = PlanNode(
        id="submit",
        action=Action(id="submit", op=ActionOp.CLICK),
        surface=SurfaceKind.BROWSER,
        irreversible=True,
    )
    return PlanIR(
        goal=goal,
        strategy_id="screen",
        entry_node_id=node.id,
        nodes={node.id: node},
        task_budget=TaskBudgetSpec(max_model_calls=0),
    )


@pytest.mark.asyncio
async def test_irreversible_success_with_uncertain_verification_beats_loop_halt():
    result = await TaskRuntime(_ControlLoop()).run(_plan())

    assert result.status is TaskStatus.HUMAN_HANDOFF
    assert result.failure_code == FailureCode.POSTCONDITION_UNMET.value
