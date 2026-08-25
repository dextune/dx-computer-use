"""Plan identity contracts for OS-discovered application candidates."""

from dataclasses import replace

import pytest

from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.plan import PlanIR, PlanNode, PlanPatch
from hpcu.schemas.surface import SurfaceKind

pytestmark = pytest.mark.unit


def _plan(candidate_id: str | None) -> PlanIR:
    action = Action(
        id="launch",
        op=ActionOp.LAUNCH_APPLICATION,
        value="browser",
        application_candidate_id=candidate_id,
    )
    node = PlanNode(
        id="launch",
        action=action,
        surface=SurfaceKind.BROWSER,
    )
    return PlanIR(
        goal=GoalEnvelope(
            raw_instruction="브라우저를 열어줘",
            intent=IntentKind.NAVIGATE,
            terminal_state="browser visible",
            evidence_requirements=("surface_identity",),
        ),
        strategy_id="screen",
        entry_node_id=node.id,
        nodes={node.id: node},
        task_budget=TaskBudgetSpec(
            max_model_calls=1,
            max_model_tokens=256,
            max_model_latency_ms=1000,
            planning_call_ceiling=1,
        ),
    )


def test_application_candidate_changes_plan_hash():
    unbound = _plan(None)
    first = _plan("first.desktop")
    second = _plan("second.desktop")

    assert unbound.plan_hash != first.plan_hash
    assert first.plan_hash != second.plan_hash


def test_application_candidate_changes_patch_hash():
    parent = _plan(None)
    original = parent.nodes["launch"]
    first = replace(
        original,
        action=replace(
            original.action,
            application_candidate_id="first.desktop",
        ),
    )
    second = replace(
        original,
        action=replace(
            original.action,
            application_candidate_id="second.desktop",
        ),
    )

    first_patch = PlanPatch(
        parent_plan_hash=parent.plan_hash,
        replaced_node_ids=("launch",),
        nodes={"launch": first},
        resume_node_id="launch",
        reason=FailureCode.DECISION_REQUIRED,
    )
    second_patch = PlanPatch(
        parent_plan_hash=parent.plan_hash,
        replaced_node_ids=("launch",),
        nodes={"launch": second},
        resume_node_id="launch",
        reason=FailureCode.DECISION_REQUIRED,
    )

    assert first_patch.patch_hash != second_patch.patch_hash
