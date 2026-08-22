"""Deep contracts for PlanIR compilation, patching, and terminal commit."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hpcu.planning.plan_compiler import PlanCompiler
from hpcu.runtime_core.control_loop import StepResult
from hpcu.runtime_core.task_budget import TaskBudgetLedger
from hpcu.runtime_core.task_runtime import TaskRuntime, TaskStatus
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import GoalEntity, GoalEnvelope, IntentKind, Reversibility
from hpcu.schemas.plan import (
    PlanIR,
    PlanningContext,
    PlanNode,
    PlanPatch,
    TargetQuerySpec,
)
from hpcu.schemas.scene import Scene
from hpcu.schemas.strategy import StrategyPlan
from hpcu.schemas.surface import ExecutionMode, SurfaceKind
from hpcu.schemas.trace import TraceEventType
from hpcu.trace.recorder import TraceRecorder

pytestmark = pytest.mark.unit


def _strategy() -> StrategyPlan:
    return StrategyPlan(
        id="screen",
        surface=SurfaceKind.BROWSER,
        execution_mode=ExecutionMode.SCREEN_STRICT,
        route="screen_action",
        expected_steps=4,
        evidence_strength=0.9,
        expected_reliability=0.9,
        expected_latency_ms=10,
    )


def _context(*, allowed_ops: frozenset[ActionOp] | None = None) -> PlanningContext:
    queries = {
        "surface": TargetQuerySpec(role="window"),
        "destination": TargetQuerySpec(text="Example"),
        "search_field": TargetQuerySpec(text="Search", role="textbox"),
        "search_result": TargetQuerySpec(text="laptop"),
        "candidate": TargetQuerySpec(text="first item"),
        "selected_state": TargetQuerySpec(text="selected item"),
        "candidate_a": TargetQuerySpec(text="item A"),
        "candidate_b": TargetQuerySpec(text="item B"),
        "editable_target": TargetQuerySpec(text="Name", role="textbox"),
        "edited_state": TargetQuerySpec(text="renamed"),
        "form": TargetQuerySpec(role="form"),
        "submit_control": TargetQuerySpec(text="Submit", role="button"),
        "submission_confirmation": TargetQuerySpec(text="Submitted"),
        "tool_result": TargetQuerySpec(text="done"),
    }
    return PlanningContext(
        target_queries=queries,
        values={
            "url": "https://example.com",
            "replacement_value": "renamed",
        },
        allowed_ops=allowed_ops or frozenset(ActionOp),
    )


def _goal(intent: IntentKind) -> GoalEnvelope:
    entities: tuple[GoalEntity, ...] = ()
    if intent is IntentKind.SEARCH:
        entities = (GoalEntity("search_query", "laptop"),)
    elif intent is IntentKind.NAVIGATE:
        entities = (GoalEntity("url", "https://example.com"),)
    return GoalEnvelope(
        raw_instruction=f"run {intent.value}",
        intent=intent,
        terminal_state=f"{intent.value} complete",
        entities=entities,
        reversibility=(
            Reversibility.IRREVERSIBLE
            if intent is IntentKind.SUBMIT
            else Reversibility.REVERSIBLE
        ),
        model_call_budget=0,
    )


@pytest.mark.parametrize(
    ("intent", "expected_ops"),
    [
        (
            IntentKind.NAVIGATE,
            (
                ActionOp.ASSERT,
                ActionOp.FOCUS_WINDOW,
                ActionOp.NAVIGATE,
                ActionOp.ASSERT,
            ),
        ),
        (
            IntentKind.SEARCH,
            (
                ActionOp.CLICK,
                ActionOp.REPLACE_TEXT,
                ActionOp.HOTKEY,
                ActionOp.ASSERT,
            ),
        ),
        (
            IntentKind.SELECT,
            (
                ActionOp.ASSERT,
                ActionOp.READ,
                ActionOp.SELECT,
                ActionOp.ASSERT,
            ),
        ),
        (
            IntentKind.COMPARE,
            (
                ActionOp.READ,
                ActionOp.ASSERT,
                ActionOp.READ,
                ActionOp.ASSERT,
            ),
        ),
        (
            IntentKind.EDIT,
            (
                ActionOp.ASSERT,
                ActionOp.READ,
                ActionOp.REPLACE_TEXT,
                ActionOp.ASSERT,
            ),
        ),
        (
            IntentKind.SUBMIT,
            (
                ActionOp.ASSERT,
                ActionOp.ASSERT,
                ActionOp.CLICK,
                ActionOp.ASSERT,
            ),
        ),
    ],
)
def test_compile_covers_all_core_intents_deterministically(
    intent: IntentKind,
    expected_ops: tuple[ActionOp, ...],
):
    compiler = PlanCompiler()
    first = compiler.compile(_goal(intent), _strategy(), _context())
    second = compiler.compile(_goal(intent), _strategy(), _context())

    assert first.plan_hash == second.plan_hash
    assert len(first.nodes) == 4
    assert tuple(node.action.op for node in first.nodes.values()) == expected_ops
    assert first.task_budget.max_model_calls == 0
    if intent is IntentKind.SUBMIT:
        assert first.nodes["submit-form"].irreversible is True


def test_compile_rejects_operation_missing_from_capability():
    context = _context(allowed_ops=frozenset({ActionOp.ASSERT}))
    with pytest.raises(ValueError, match="cannot execute"):
        PlanCompiler().compile(_goal(IntentKind.SEARCH), _strategy(), context)


def test_patch_preserves_completed_prefix_and_records_lineage():
    plan = PlanCompiler().compile(
        _goal(IntentKind.SEARCH),
        _strategy(),
        _context(),
    )
    original = plan.nodes["type-query"]
    replacement = replace(
        original,
        action=replace(original.action, id="type-query-repaired"),
    )
    patch = PlanPatch(
        parent_plan_hash=plan.plan_hash,
        replaced_node_ids=("type-query",),
        nodes={"type-query": replacement},
        resume_node_id="type-query",
        reason=FailureCode.POSTCONDITION_UNMET,
    )

    patched = plan.apply_patch(
        patch,
        completed_node_ids=("focus-search",),
    )

    assert patched.entry_node_id == plan.entry_node_id
    assert patched.nodes["focus-search"] == plan.nodes["focus-search"]
    assert patched.nodes["type-query"].action.id == "type-query-repaired"
    assert patched.patch_lineage == (patch.patch_hash,)


def test_patch_cannot_replace_verified_completed_node():
    plan = PlanCompiler().compile(
        _goal(IntentKind.SEARCH),
        _strategy(),
        _context(),
    )
    patch = PlanPatch(
        parent_plan_hash=plan.plan_hash,
        replaced_node_ids=("focus-search",),
        nodes={"focus-search": plan.nodes["focus-search"]},
        resume_node_id="focus-search",
        reason=FailureCode.POSTCONDITION_UNMET,
    )
    with pytest.raises(ValueError, match="completed"):
        plan.apply_patch(patch, completed_node_ids=("focus-search",))


class _SequenceLoop:
    def __init__(self, failures: dict[str, int] | None = None) -> None:
        self.failures = dict(failures or {})
        self.calls: list[str] = []
        self.recorder = TraceRecorder()
        self.scene = Scene(version=0)

    async def step(self, query, action, *, verification_query=None):
        del query, verification_query
        self.calls.append(action.id)
        self.scene = Scene(version=len(self.calls))
        remaining = self.failures.get(action.id, 0)
        if remaining:
            self.failures[action.id] = remaining - 1
            return StepResult(
                success=False,
                scene=self.scene,
                failure_code=FailureCode.POSTCONDITION_UNMET.value,
            )
        return StepResult(success=True, scene=self.scene)


@pytest.mark.asyncio
async def test_task_runtime_applies_one_bounded_local_patch():
    plan = PlanCompiler().compile(
        _goal(IntentKind.SEARCH),
        _strategy(),
        _context(),
    )
    loop = _SequenceLoop({"type-query": 1})
    repair_calls = 0

    def repair(request, current_plan):
        nonlocal repair_calls
        repair_calls += 1
        assert request.failed_node_id == "type-query"
        assert request.completed_nodes == ("focus-search",)
        original = current_plan.nodes["type-query"]
        replacement = replace(
            original,
            action=replace(original.action, id="type-query-repaired"),
        )
        return PlanPatch(
            parent_plan_hash=current_plan.plan_hash,
            replaced_node_ids=("type-query",),
            nodes={"type-query": replacement},
            resume_node_id="type-query",
            reason=request.reason,
        )

    runtime = TaskRuntime(
        loop,
        local_repairer=repair,
        max_local_repairs_per_node=1,
    )
    result = await runtime.run(plan)

    assert result.status is TaskStatus.VERIFIED_SUCCESS
    assert repair_calls == 1
    assert loop.calls == [
        "focus-search",
        "type-query",
        "type-query-repaired",
        "submit-query",
        "verify-result",
    ]
    assert result.completed_node_ids == (
        "focus-search",
        "type-query",
        "submit-query",
        "verify-result",
    )
    assert len(result.patch_lineage) == 1
    completions = [
        item
        for item in loop.recorder.records()
        if item.event_type is TraceEventType.COMPLETION
    ]
    assert len(completions) == 1


@pytest.mark.asyncio
async def test_six_node_local_path_uses_zero_model_calls():
    goal = _goal(IntentKind.COMPARE)
    budget = TaskBudgetSpec(max_model_calls=0)
    nodes: dict[str, PlanNode] = {}
    for index in range(6):
        node_id = f"local-{index}"
        next_id = f"local-{index + 1}" if index < 5 else None
        nodes[node_id] = PlanNode(
            id=node_id,
            action=Action(id=node_id, op=ActionOp.ASSERT),
            surface=SurfaceKind.BROWSER,
            success_edge=next_id,
        )
    plan = PlanIR(
        goal=goal,
        strategy_id="local-only",
        entry_node_id="local-0",
        nodes=nodes,
        task_budget=budget,
    )
    ledger = TaskBudgetLedger(budget)
    loop = _SequenceLoop()

    result = await TaskRuntime(loop, ledger).run(plan)

    assert result.success is True
    assert result.steps == 6
    assert ledger.model_calls == 0
    assert loop.calls == [f"local-{index}" for index in range(6)]
