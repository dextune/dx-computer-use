"""Regression tests for immutable safety fields in PlanIR identity."""

from dataclasses import replace

import pytest

from hpcu.schemas.action import Action, ActionOp, ActionTarget, RetryPolicy
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.plan import GroundingHints, PlanIR, PlanNode, PlanningContext
from hpcu.schemas.surface import SurfaceKind

pytestmark = pytest.mark.unit


def _plan(blocked_tokens=()) -> PlanIR:
    goal = GoalEnvelope(
        raw_instruction="inspect",
        intent=IntentKind.SEARCH,
        terminal_state="done",
        model_call_budget=0,
    )
    node = PlanNode(
        id="checkpoint",
        action=Action(id="checkpoint", op=ActionOp.CHECKPOINT),
        surface=SurfaceKind.BROWSER,
    )
    return PlanIR(
        goal=goal,
        strategy_id="screen",
        entry_node_id=node.id,
        nodes={node.id: node},
        task_budget=TaskBudgetSpec(max_model_calls=0),
        blocked_tokens=blocked_tokens,
    )


def _replace_node(plan: PlanIR, node: PlanNode) -> PlanIR:
    return replace(plan, nodes={node.id: node})


def test_blocked_tokens_change_plan_hash():
    first = _plan(("captcha",))
    second = replace(first, blocked_tokens=("login",))

    assert first.plan_hash != second.plan_hash


def test_blocked_tokens_are_canonicalized_to_immutable_tuples():
    source = [" captcha "]
    context = PlanningContext(blocked_tokens=source)
    plan = _plan(source)

    source[0] = "mutated"

    assert context.blocked_tokens == ("captcha",)
    assert plan.blocked_tokens == ("captcha",)


def test_blocked_tokens_reject_non_strings():
    with pytest.raises(ValueError, match="non-empty strings"):
        PlanningContext(blocked_tokens=(123,))

    with pytest.raises(ValueError, match="non-empty strings"):
        _plan((123,))


def test_action_timeout_and_retry_change_plan_hash():
    first = _plan()
    node = first.nodes["checkpoint"]
    changed_action = replace(
        node.action,
        timeout_ms=node.action.timeout_ms + 1,
        retry=RetryPolicy(max_attempts=7, alternate_modes=("semantic",)),
    )
    second = _replace_node(first, replace(node, action=changed_action))

    assert first.plan_hash != second.plan_hash


def test_action_target_changes_plan_hash():
    first = _plan()
    node = first.nodes["checkpoint"]
    changed_action = replace(
        node.action,
        target=ActionTarget(element_id="different", locator="css=#different"),
    )
    second = _replace_node(first, replace(node, action=changed_action))

    assert first.plan_hash != second.plan_hash


def test_grounding_hints_change_plan_hash():
    first = _plan()
    node = first.nodes["checkpoint"]
    second = _replace_node(
        first,
        replace(
            node,
            grounding_hints=GroundingHints(
                tokens=("ready",),
                ignore_tokens=("ad",),
                source="plan_context",
            ),
        ),
    )

    assert first.plan_hash != second.plan_hash


def test_task_budget_changes_plan_hash():
    first = _plan()
    second = replace(
        first,
        task_budget=replace(
            first.task_budget,
            max_model_tokens=first.task_budget.max_model_tokens + 1,
        ),
    )

    assert first.plan_hash != second.plan_hash


def test_unresolved_slots_change_plan_hash():
    first = _plan()
    second = replace(
        first,
        goal=replace(first.goal, ambiguity_slots=("missing_target",)),
    )

    assert first.plan_hash != second.plan_hash
