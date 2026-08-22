"""CPU command interpretation, strategy, PlanIR and task-budget contracts."""

import pytest

from hpcu.gateway.budget import ModelCallBudgetError, TaskBudget
from hpcu.gateway.gateway import ModelCallPurpose
from hpcu.planning import GoalInterpreter, PlanCompiler, StrategyPlanner
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.evidence import EvidenceCondition, EvidenceContract, EvidenceKind
from hpcu.schemas.planning import (
    CapabilitySnapshot,
    GoalEnvelope,
    GoalIntent,
    PlanIR,
    PlanNode,
    StrategyPlan,
)


@pytest.mark.unit
def test_interpreter_extracts_url_intent_and_numeric_constraint():
    goal = GoalInterpreter().interpret(
        "https://example.com 에서 200만원 이하 노트북을 검색해줘",
        goal_id="g1",
    )
    assert goal.explicit_url == "https://example.com"
    assert goal.intent is GoalIntent.SEARCH
    assert goal.entities["host"] == "example.com"
    assert any(item.operator == "lte" for item in goal.constraints)


@pytest.mark.unit
def test_interpreter_preserves_unknown_as_semantic_slot():
    goal = GoalInterpreter().interpret("정리 좀 해줘", goal_id="g2")
    assert goal.intent is GoalIntent.UNKNOWN
    assert "intent" in goal.ambiguity_slots


@pytest.mark.unit
def test_interpreter_only_fills_declared_unresolved_slot():
    interpreter = GoalInterpreter()
    goal = interpreter.interpret("정리 좀 해줘", goal_id="g3")
    filled = interpreter.merge_semantic_slots(goal, intent=GoalIntent.EDIT)
    assert filled.intent is GoalIntent.EDIT
    assert "intent" not in filled.ambiguity_slots
    with pytest.raises(ValueError):
        interpreter.merge_semantic_slots(filled, intent=GoalIntent.READ)


@pytest.mark.unit
def test_strategy_prefers_qualified_workflow():
    goal = GoalInterpreter().interpret("현재 목록을 읽어줘", goal_id="g4")
    capabilities = CapabilitySnapshot(
        surface="browser",
        structure=True,
        semantic_input=True,
        workflow_ids=("read-list-v2",),
    )
    selected = StrategyPlanner().select(goal, capabilities)
    assert selected.entry_kind == "workflow"
    assert selected.workflow_id == "read-list-v2"
    assert selected.expected_model_calls == 0


@pytest.mark.unit
def test_read_only_strategy_does_not_require_input_capability():
    goal = GoalInterpreter().interpret("화면 내용을 읽어줘", goal_id="g5")
    capabilities = CapabilitySnapshot(
        surface="remote",
        physical_input=False,
        keyboard_input=False,
    )
    selected = StrategyPlanner().select(goal, capabilities)
    assert selected.entry_kind == "existing_screen"


@pytest.mark.unit
def test_terminal_plan_node_requires_explicit_evidence():
    with pytest.raises(ValueError, match="terminal PlanNode"):
        PlanNode(
            id="read",
            action=Action(id="read", op=ActionOp.READ),
            terminal=True,
        )


@pytest.mark.unit
def test_plan_ir_rejects_missing_edge():
    goal = GoalEnvelope(id="g", raw_instruction="읽어줘", intent=GoalIntent.READ)
    strategy = StrategyPlan(
        id="s",
        goal_id="g",
        execution_mode=goal.execution_mode,
        surface="browser",
    )
    evidence = EvidenceContract(
        all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="result"),)
    )
    first = PlanNode(
        id="first",
        action=Action(id="first", op=ActionOp.READ),
        success_next="missing",
    )
    terminal = PlanNode(
        id="terminal",
        action=Action(id="terminal", op=ActionOp.READ),
        evidence=evidence,
        terminal=True,
        allow_preexisting_success=True,
    )
    with pytest.raises(ValueError, match="missing edge"):
        PlanIR(
            id="p",
            goal=goal,
            strategy=strategy,
            entry_node_id="first",
            nodes=(first, terminal),
        )


@pytest.mark.unit
def test_plan_compiler_refuses_unresolved_goal():
    goal = GoalInterpreter().interpret("무언가 해줘", goal_id="g6")
    capabilities = CapabilitySnapshot(surface="browser")
    strategy = StrategyPlanner().select(goal, capabilities)
    with pytest.raises(ValueError, match="unresolved goal slots"):
        PlanCompiler().compile(goal, strategy)


@pytest.mark.unit
def test_plan_compiler_requires_explicit_action_and_evidence():
    goal = GoalInterpreter().interpret("화면 내용을 읽어줘", goal_id="g7")
    capabilities = CapabilitySnapshot(surface="browser")
    strategy = StrategyPlanner().select(goal, capabilities)
    with pytest.raises(ValueError, match="explicit Action"):
        PlanCompiler().compile(goal, strategy)


@pytest.mark.unit
def test_plan_compiler_builds_explicit_single_action_plan():
    goal = GoalInterpreter().interpret("화면 내용을 읽어줘", goal_id="g8")
    strategy = StrategyPlanner().select(
        goal,
        CapabilitySnapshot(surface="browser"),
    )
    action = Action(id="read-result", op=ActionOp.READ)
    evidence = EvidenceContract(
        all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="result"),)
    )
    plan = PlanCompiler().compile_single_action(
        goal,
        strategy,
        action=action,
        evidence=evidence,
        allow_preexisting_success=True,
        require_scene_change=False,
    )
    assert plan.entry_node_id == "read-result"
    assert plan.node("read-result").evidence is evidence


@pytest.mark.unit
def test_task_budget_is_shared_across_purposes():
    budget = TaskBudget(max_model_calls=2, max_model_tokens=100)
    budget.acquire(ModelCallPurpose.PLAN_COMPILE)
    budget.record_tokens(20)
    budget.acquire(ModelCallPurpose.ACTION_DECISION)
    budget.record_tokens(10)
    assert budget.model_calls_remaining == 0
    snapshot = budget.snapshot()
    assert snapshot.model_calls_used == 2
    assert dict(snapshot.purpose_call_counts) == {
        "action_decision": 1,
        "plan_compile": 1,
    }
    with pytest.raises(ModelCallBudgetError):
        budget.acquire(ModelCallPurpose.RECOVERY_REANALYSIS)


@pytest.mark.unit
def test_task_budget_rejects_response_token_overrun():
    budget = TaskBudget(max_model_calls=1, max_model_tokens=10)
    budget.acquire(ModelCallPurpose.PLAN_COMPILE)
    with pytest.raises(ModelCallBudgetError):
        budget.record_tokens(11)
