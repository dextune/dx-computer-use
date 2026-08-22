"""Deep contracts for PlanIR compilation, patching, evidence, and terminal commit."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hpcu.grounder.grounder import GroundingCandidate, GroundingResult
from hpcu.input.injector import ExecutionResult
from hpcu.planning.plan_compiler import PlanCompiler
from hpcu.recovery.loop_breaker import RecoveryAction
from hpcu.runtime_core.control_loop import StepResult
from hpcu.runtime_core.task_budget import TaskBudgetLedger
from hpcu.runtime_core.task_runtime import TaskRuntime, TaskStatus
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.coordinates import CoordinateSpace
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import GoalEntity, GoalEnvelope, IntentKind, Reversibility
from hpcu.schemas.plan import (
    PlanIR,
    PlanningContext,
    PlanNode,
    PlanPatch,
    TargetQuerySpec,
)
from hpcu.schemas.scene import FrameHandle, Scene
from hpcu.schemas.strategy import StrategyPlan
from hpcu.schemas.surface import ExecutionMode, SurfaceKind
from hpcu.schemas.trace import TraceEventType
from hpcu.schemas.ui_element import ElementState, UIElement
from hpcu.trace.recorder import TraceRecorder

pytestmark = pytest.mark.unit

_EVIDENCE = {
    IntentKind.NAVIGATE: ("surface_identity",),
    IntentKind.SEARCH: ("query_echo", "result_candidate"),
    IntentKind.SELECT: ("selected_state",),
    IntentKind.COMPARE: ("candidate_a", "candidate_b"),
    IntentKind.EDIT: ("old_value", "edited_value"),
    IntentKind.SUBMIT: ("submission_confirmation",),
}


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
        evidence_requirements=_EVIDENCE[intent],
        reversibility=(
            Reversibility.IRREVERSIBLE
            if intent is IntentKind.SUBMIT
            else Reversibility.REVERSIBLE
        ),
        model_call_budget=0,
    )


def _frame(version: int) -> FrameHandle:
    return FrameHandle(
        shm_id=f"frame-{version}",
        width=100,
        height=100,
        stride=400,
        pixel_format="BGRA",
        timestamp_ns=version,
        space=CoordinateSpace.SCREEN_PHYSICAL_PX,
        source="test",
    )


def _element(
    element_id: str,
    text: str,
    version: int,
    *,
    role: str = "text",
    selected: bool = False,
) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=version,
        role=role,
        name=text,
        text=text,
        state=ElementState(visible=True, enabled=True, selected=selected),
        fingerprint=f"{element_id}:{text}",
    )


def _grounding(element_id: str) -> GroundingResult:
    return GroundingResult(
        element_id=element_id,
        confidence=1.0,
        candidates=(
            GroundingCandidate(element_id=element_id, confidence=1.0),
        ),
    )


def _step(
    version: int,
    primary_id: str,
    primary_text: str,
    *,
    primary_role: str = "text",
    selected: bool = False,
    verification_id: str | None = None,
    verification_text: str = "",
    success: bool = True,
    failure_code: str | None = None,
    execution: ExecutionResult | None = None,
    recovery_action: RecoveryAction | None = None,
) -> StepResult:
    elements = {
        primary_id: _element(
            primary_id,
            primary_text,
            version,
            role=primary_role,
            selected=selected,
        )
    }
    verification = None
    if verification_id is not None:
        elements[verification_id] = _element(
            verification_id,
            verification_text,
            version,
        )
        verification = _grounding(verification_id)
    scene = Scene(version=version, elements=elements, frame=_frame(version))
    return StepResult(
        success=success,
        scene=scene,
        failure_code=failure_code,
        grounding=_grounding(primary_id),
        verification_grounding=verification,
        execution=execution,
        recovery_action=recovery_action,
        pre_scene_version=version,
        post_scene_version=version,
    )


class _ScriptedLoop:
    def __init__(self, script: dict[str, list[StepResult]] | None = None) -> None:
        self.script = {key: list(values) for key, values in (script or {}).items()}
        self.calls: list[str] = []
        self.recorder = TraceRecorder()
        self.scene = Scene(version=0)

    async def step(self, query, action, *, verification_query=None):
        del query, verification_query
        self.calls.append(action.id)
        queued = self.script.get(action.id, [])
        if queued:
            result = queued.pop(0)
        else:
            version = len(self.calls)
            result = _step(version, action.id, action.id)
        self.scene = result.scene
        return result


def _search_script(*, same_result: bool = False) -> dict[str, list[StepResult]]:
    result_id = "search-field" if same_result else "result-card"
    return {
        "focus-search": [
            _step(1, "search-field", "", primary_role="textbox")
        ],
        "type-query": [
            _step(2, "search-field", "laptop", primary_role="textbox")
        ],
        "submit-query": [
            _step(
                3,
                "search-field",
                "laptop",
                primary_role="textbox",
                verification_id=result_id,
                verification_text="laptop result",
            )
        ],
        "verify-result": [
            _step(4, result_id, "laptop result")
        ],
    }


@pytest.mark.parametrize(
    ("intent", "expected_ops", "evidence_nodes"),
    [
        (
            IntentKind.NAVIGATE,
            (
                ActionOp.ASSERT,
                ActionOp.FOCUS_WINDOW,
                ActionOp.NAVIGATE,
                ActionOp.ASSERT,
            ),
            {"verify-destination": ("surface_identity",)},
        ),
        (
            IntentKind.SEARCH,
            (
                ActionOp.CLICK,
                ActionOp.REPLACE_TEXT,
                ActionOp.HOTKEY,
                ActionOp.ASSERT,
            ),
            {
                "type-query": ("query_echo",),
                "verify-result": ("result_candidate",),
            },
        ),
        (
            IntentKind.SELECT,
            (
                ActionOp.ASSERT,
                ActionOp.READ,
                ActionOp.SELECT,
                ActionOp.ASSERT,
            ),
            {"verify-selection": ("selected_state",)},
        ),
        (
            IntentKind.COMPARE,
            (
                ActionOp.READ,
                ActionOp.ASSERT,
                ActionOp.READ,
                ActionOp.ASSERT,
            ),
            {
                "verify-candidate-a": ("candidate_a",),
                "verify-comparison": ("candidate_b",),
            },
        ),
        (
            IntentKind.EDIT,
            (
                ActionOp.ASSERT,
                ActionOp.READ,
                ActionOp.REPLACE_TEXT,
                ActionOp.ASSERT,
            ),
            {
                "read-current-value": ("old_value",),
                "verify-edit": ("edited_value",),
            },
        ),
        (
            IntentKind.SUBMIT,
            (
                ActionOp.ASSERT,
                ActionOp.ASSERT,
                ActionOp.CLICK,
                ActionOp.ASSERT,
            ),
            {"verify-submission": ("submission_confirmation",)},
        ),
    ],
)
def test_compile_covers_core_intents_and_assigns_evidence_once(
    intent: IntentKind,
    expected_ops: tuple[ActionOp, ...],
    evidence_nodes: dict[str, tuple[str, ...]],
):
    compiler = PlanCompiler()
    first = compiler.compile(_goal(intent), _strategy(), _context())
    second = compiler.compile(_goal(intent), _strategy(), _context())

    assert first.plan_hash == second.plan_hash
    assert len(first.nodes) == 4
    assert tuple(node.action.op for node in first.nodes.values()) == expected_ops
    assert {
        node_id: node.evidence_requirements
        for node_id, node in first.nodes.items()
        if node.evidence_requirements
    } == evidence_nodes
    assigned = tuple(
        requirement
        for node in first.nodes.values()
        for requirement in node.evidence_requirements
    )
    assert assigned == _EVIDENCE[intent]
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


def test_patch_cannot_replace_or_resume_at_verified_node():
    plan = PlanCompiler().compile(
        _goal(IntentKind.SEARCH),
        _strategy(),
        _context(),
    )
    replace_completed = PlanPatch(
        parent_plan_hash=plan.plan_hash,
        replaced_node_ids=("focus-search",),
        nodes={"focus-search": plan.nodes["focus-search"]},
        resume_node_id="type-query",
        reason=FailureCode.POSTCONDITION_UNMET,
    )
    with pytest.raises(ValueError, match="completed"):
        plan.apply_patch(
            replace_completed,
            completed_node_ids=("focus-search",),
        )

    resume_completed = PlanPatch(
        parent_plan_hash=plan.plan_hash,
        replaced_node_ids=("type-query",),
        nodes={"type-query": plan.nodes["type-query"]},
        resume_node_id="focus-search",
        reason=FailureCode.POSTCONDITION_UNMET,
    )
    with pytest.raises(ValueError, match="resume"):
        plan.apply_patch(
            resume_completed,
            completed_node_ids=("focus-search",),
        )


def test_patch_cannot_add_back_edge_to_verified_node():
    plan = PlanCompiler().compile(
        _goal(IntentKind.SEARCH),
        _strategy(),
        _context(),
    )
    replacement = replace(
        plan.nodes["type-query"],
        success_edge="focus-search",
    )
    patch = PlanPatch(
        parent_plan_hash=plan.plan_hash,
        replaced_node_ids=("type-query",),
        nodes={"type-query": replacement},
        resume_node_id="type-query",
        reason=FailureCode.POSTCONDITION_UNMET,
    )
    with pytest.raises(ValueError, match="edge"):
        plan.apply_patch(patch, completed_node_ids=("focus-search",))


@pytest.mark.asyncio
async def test_task_runtime_applies_one_bounded_local_patch_and_commits_once():
    plan = PlanCompiler().compile(
        _goal(IntentKind.SEARCH),
        _strategy(),
        _context(),
    )
    script = _search_script()
    script["type-query"] = [
        _step(
            2,
            "search-field",
            "",
            primary_role="textbox",
            success=False,
            failure_code=FailureCode.POSTCONDITION_UNMET.value,
        )
    ]
    script["type-query-repaired"] = [
        _step(3, "search-field", "laptop", primary_role="textbox")
    ]
    script["submit-query"] = [
        _step(
            4,
            "search-field",
            "laptop",
            primary_role="textbox",
            verification_id="result-card",
            verification_text="laptop result",
        )
    ]
    script["verify-result"] = [
        _step(5, "result-card", "laptop result")
    ]
    loop = _ScriptedLoop(script)
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

    result = await TaskRuntime(
        loop,
        local_repairer=repair,
        max_local_repairs_per_node=1,
    ).run(plan)

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
    assert result.terminal_evidence_ids == ("search-field", "result-card")
    assert [binding.requirement for binding in result.evidence_bindings] == [
        "query_echo",
        "result_candidate",
    ]
    assert all(binding.frame_id for binding in result.evidence_bindings)
    assert len(result.patch_lineage) == 1
    completions = [
        item
        for item in loop.recorder.records()
        if item.event_type is TraceEventType.COMPLETION
    ]
    assert len(completions) == 1


@pytest.mark.asyncio
async def test_search_rejects_same_element_as_query_and_result_evidence():
    plan = PlanCompiler().compile(
        _goal(IntentKind.SEARCH),
        _strategy(),
        _context(),
    )
    result = await TaskRuntime(_ScriptedLoop(_search_script(same_result=True))).run(
        plan
    )

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == FailureCode.VERIFICATION_FAILED.value
    assert result.terminal_evidence_ids == ()


@pytest.mark.asyncio
async def test_compare_rejects_one_candidate_reused_twice():
    plan = PlanCompiler().compile(
        _goal(IntentKind.COMPARE),
        _strategy(),
        _context(),
    )
    script = {
        "read-candidate-a": [_step(1, "candidate-a", "price 100")],
        "verify-candidate-a": [_step(2, "candidate-a", "price 100")],
        "read-candidate-b": [_step(3, "candidate-a", "price 200")],
        "verify-comparison": [_step(4, "candidate-a", "price 200")],
    }

    result = await TaskRuntime(_ScriptedLoop(script)).run(plan)

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == FailureCode.VERIFICATION_FAILED.value


@pytest.mark.asyncio
async def test_edit_rejects_unchanged_old_and_new_value():
    plan = PlanCompiler().compile(
        _goal(IntentKind.EDIT),
        _strategy(),
        _context(),
    )
    script = {
        "inspect-edit-target": [_step(1, "name-field", "renamed")],
        "read-current-value": [_step(2, "name-field", "renamed")],
        "replace-value": [_step(3, "name-field", "renamed")],
        "verify-edit": [_step(4, "name-field", "renamed")],
    }

    result = await TaskRuntime(_ScriptedLoop(script)).run(plan)

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == FailureCode.VERIFICATION_FAILED.value


@pytest.mark.asyncio
async def test_select_requires_observed_selected_state():
    plan = PlanCompiler().compile(
        _goal(IntentKind.SELECT),
        _strategy(),
        _context(),
    )
    script = {
        "inspect-candidate": [_step(1, "item", "item")],
        "read-candidate": [_step(2, "item", "item")],
        "select-candidate": [_step(3, "item", "item")],
        "verify-selection": [
            _step(4, "item", "item", selected=False)
        ],
    }

    result = await TaskRuntime(_ScriptedLoop(script)).run(plan)

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == FailureCode.VERIFICATION_FAILED.value


def _single_failure_plan(*, irreversible: bool = False) -> PlanIR:
    intent = IntentKind.SUBMIT if irreversible else IntentKind.NAVIGATE
    goal = _goal(intent)
    node = PlanNode(
        id="only",
        action=Action(id="only", op=ActionOp.CLICK),
        surface=SurfaceKind.BROWSER,
        evidence_requirements=goal.evidence_requirements,
        irreversible=irreversible,
    )
    return PlanIR(
        goal=goal,
        strategy_id="single",
        entry_node_id="only",
        nodes={"only": node},
        task_budget=TaskBudgetSpec(max_model_calls=0),
    )


@pytest.mark.asyncio
async def test_policy_rejection_never_enters_repair():
    plan = _single_failure_plan()
    loop = _ScriptedLoop(
        {
            "only": [
                _step(
                    1,
                    "target",
                    "target",
                    success=False,
                    failure_code=FailureCode.ACTION_REJECTED_BY_POLICY.value,
                )
            ]
        }
    )
    repair_calls = 0

    def repair(_request, _plan):
        nonlocal repair_calls
        repair_calls += 1
        return None

    result = await TaskRuntime(loop, local_repairer=repair).run(plan)

    assert result.status is TaskStatus.HUMAN_HANDOFF
    assert repair_calls == 0
    assert loop.calls == ["only"]


@pytest.mark.asyncio
async def test_loop_halt_never_enters_repair():
    plan = _single_failure_plan()
    loop = _ScriptedLoop(
        {
            "only": [
                _step(
                    1,
                    "target",
                    "target",
                    success=False,
                    failure_code=FailureCode.LOOP_DETECTED.value,
                    recovery_action=RecoveryAction.HALT,
                )
            ]
        }
    )
    repair_calls = 0

    def repair(_request, _plan):
        nonlocal repair_calls
        repair_calls += 1
        return None

    result = await TaskRuntime(loop, local_repairer=repair).run(plan)

    assert result.status is TaskStatus.FAILED
    assert result.failure_code == FailureCode.LOOP_DETECTED.value
    assert repair_calls == 0
    assert loop.calls == ["only"]


@pytest.mark.asyncio
async def test_irreversible_effect_with_failed_verification_is_not_reexecuted():
    plan = _single_failure_plan(irreversible=True)
    loop = _ScriptedLoop(
        {
            "only": [
                _step(
                    1,
                    "submit",
                    "submitted",
                    success=False,
                    failure_code=FailureCode.POSTCONDITION_UNMET.value,
                    execution=ExecutionResult(success=True, mode="semantic"),
                )
            ]
        }
    )
    repair_calls = 0

    def repair(_request, _plan):
        nonlocal repair_calls
        repair_calls += 1
        return None

    result = await TaskRuntime(loop, local_repairer=repair).run(plan)

    assert result.status is TaskStatus.HUMAN_HANDOFF
    assert repair_calls == 0
    assert loop.calls == ["only"]


@pytest.mark.asyncio
async def test_six_node_local_path_uses_zero_model_calls():
    goal = _goal(IntentKind.NAVIGATE)
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
            evidence_requirements=(
                goal.evidence_requirements if next_id is None else ()
            ),
        )
    plan = PlanIR(
        goal=goal,
        strategy_id="local-only",
        entry_node_id="local-0",
        nodes=nodes,
        task_budget=budget,
    )
    ledger = TaskBudgetLedger(budget)
    loop = _ScriptedLoop()

    result = await TaskRuntime(loop, ledger).run(plan)

    assert result.success is True
    assert result.steps == 6
    assert ledger.model_calls == 0
    assert loop.calls == [f"local-{index}" for index in range(6)]
