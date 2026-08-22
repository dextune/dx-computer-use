"""Regression tests for command→plan runtime remediation."""

import pytest

from hpcu.capture.frame_store import FrameStore
from hpcu.executor.executor import Executor
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.planning.goal_interpreter import GoalInterpreter
from hpcu.planning.plan_compiler import PlanCompiler
from hpcu.planning.semantic_interrupt import SemanticSlotFiller
from hpcu.planning.strategy_planner import StrategyPlanner
from hpcu.runtime_core.control_loop import StepResult
from hpcu.runtime_core.task_budget import (
    BudgetedGateway,
    ModelBudgetExceeded,
    TaskBudgetLedger,
)
from hpcu.runtime_core.task_runtime import TaskRuntime
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace, ScreenPoint
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import IntentKind
from hpcu.schemas.plan import TargetQuerySpec
from hpcu.schemas.scene import Scene
from hpcu.schemas.strategy import CapabilitySnapshot
from hpcu.schemas.surface import ExecutionMode, SurfaceKind
from hpcu.schemas.ui_element import ElementSource, UIElement

pytestmark = pytest.mark.unit


class _Injector(InputInjector):
    def __init__(self) -> None:
        super().__init__("session")
        self.semantic_calls = 0
        self.physical_calls = 0

    async def semantic(self, element, action):
        self.semantic_calls += 1
        return ExecutionResult(success=True, mode="semantic")

    async def physical(self, point, action, text=None):
        self.physical_calls += 1
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self):
        return InputCapabilities(
            semantic_invoke=Capability.SUPPORTED,
            physical_pointer=Capability.SUPPORTED,
        )


class _Gateway(Gateway):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_id(self):
        return "fake"

    @property
    def model_id(self):
        return "fake-model"

    def call(
        self,
        prompt,
        system_prompt="",
        max_tokens=None,
        *,
        purpose=ModelCallPurpose.SITUATION_ANALYSIS,
    ):
        self.calls += 1
        return GatewayResponse(
            content="{}",
            model=self.model_id,
            provider=self.provider_id,
            tokens_used=7,
            latency_ms=11,
        )


def _screen_capability() -> CapabilitySnapshot:
    return CapabilitySnapshot(
        active_surface=SurfaceKind.BROWSER,
        execution_mode=ExecutionMode.SCREEN_STRICT,
        capture=Capability.SUPPORTED,
        structure=Capability.SUPPORTED,
        semantic_input=Capability.SUPPORTED,
        physical_input=Capability.SUPPORTED,
    )


def test_goal_interpreter_keeps_simple_search_model_free():
    goal = GoalInterpreter().interpret("구글에서 노트북을 검색해줘")
    assert goal.intent is IntentKind.SEARCH
    assert goal.entity_values("search_query") == ("노트북",)
    assert goal.ambiguity_slots == ()
    assert goal.model_call_budget == 2


@pytest.mark.parametrize(
    ("instruction", "intent"),
    [
        ("https://example.com 열어줘", IntentKind.NAVIGATE),
        ("브라우저에서 헤드폰을 찾아줘", IntentKind.SEARCH),
        ("목록에서 첫 항목을 선택해줘", IntentKind.SELECT),
        ("A와 B를 비교해줘", IntentKind.COMPARE),
        ("파일 이름을 수정해줘", IntentKind.EDIT),
        ("폼을 제출해줘", IntentKind.SUBMIT),
    ],
)
def test_goal_interpreter_covers_core_intents(instruction, intent):
    assert GoalInterpreter().interpret(instruction).intent is intent


def test_semantic_fill_cannot_relax_protected_fields():
    with pytest.raises(ValueError, match="protected"):
        GoalInterpreter().interpret(
            "이 화면에서 처리해줘",
            semantic_fill=lambda _goal: {"risk_class": "low"},
        )


def test_semantic_slot_filler_only_returns_unresolved_fields():
    class _SlotGateway(_Gateway):
        def call(
            self,
            prompt,
            system_prompt="",
            max_tokens=None,
            *,
            purpose=ModelCallPurpose.SITUATION_ANALYSIS,
        ):
            assert purpose is ModelCallPurpose.INTENT_FILL
            self.calls += 1
            return GatewayResponse(
                content='{ "intent": "navigate" }',
                model=self.model_id,
                provider=self.provider_id,
                tokens_used=5,
                latency_ms=3,
            )

    inner = _SlotGateway()
    ledger = TaskBudgetLedger(
        TaskBudgetSpec(
            max_model_calls=1, max_model_tokens=10, max_model_latency_ms=50
        )
    )
    filler = SemanticSlotFiller(BudgetedGateway(inner, ledger))
    goal = GoalInterpreter().interpret(
        "이 작업을 처리해줘", semantic_fill=filler, model_call_budget=1
    )
    assert goal.intent is IntentKind.NAVIGATE
    assert goal.ambiguity_slots == ()
    assert ledger.model_calls == 1


def test_semantic_slot_filler_rejects_protected_key():
    class _BadGateway(_Gateway):
        def call(
            self,
            prompt,
            system_prompt="",
            max_tokens=None,
            *,
            purpose=ModelCallPurpose.SITUATION_ANALYSIS,
        ):
            return GatewayResponse(
                content='{ "risk_class": "low" }',
                model=self.model_id,
                provider=self.provider_id,
            )

    filler = SemanticSlotFiller(_BadGateway())
    with pytest.raises(ValueError, match="protected"):
        GoalInterpreter().interpret("이 작업을 처리해줘", semantic_fill=filler)


def test_strategy_and_plan_are_deterministic():
    goal = GoalInterpreter().interpret("구글에서 노트북을 검색해줘")
    strategy = StrategyPlanner().select(goal, _screen_capability())
    compiler = PlanCompiler()
    kwargs = {
        "search_box": TargetQuerySpec(text="Search", role="textbox"),
        "result_target": TargetQuerySpec(text="노트북"),
    }
    first = compiler.compile_search(goal, strategy, **kwargs)
    second = compiler.compile_search(goal, strategy, **kwargs)
    assert first.plan_hash == second.plan_hash
    assert tuple(first.nodes) == (
        "focus-search",
        "type-query",
        "submit-query",
        "verify-result",
    )
    assert first.nodes["submit-query"].target_query == kwargs["search_box"]
    assert first.nodes["submit-query"].verification_query == kwargs["result_target"]


@pytest.mark.asyncio
async def test_unknown_physical_command_fails_closed_without_input():
    injector = _Injector()
    executor = Executor(injector)
    point = ScreenPoint(CoordinateSpace.SCREEN_PHYSICAL_PX, 1.0, 1.0)
    result = await executor.inject_physical(point, "clik")
    assert result.success is False
    assert result.failure_code == FailureCode.ACTION_UNSUPPORTED.value
    assert injector.semantic_calls == 0
    assert injector.physical_calls == 0


@pytest.mark.asyncio
async def test_stale_prepared_action_never_reaches_injector():
    injector = _Injector()
    executor = Executor(injector)
    bbox = BoundingBox(CoordinateSpace.SCREEN_PHYSICAL_PX, 0, 0, 20, 20)
    element = UIElement(
        id="target",
        scene_version=1,
        role="button",
        name="Open",
        bbox=bbox,
        fingerprint="stable-target",
        sources=(ElementSource(type="dom"),),
    )
    before = Scene(version=1, elements={element.id: element})
    after = Scene(version=2, elements={element.id: element})
    prepared = executor.prepare(
        Action(id="click", op=ActionOp.CLICK),
        element.id,
        element=element,
        physical_point=ScreenPoint(CoordinateSpace.SCREEN_PHYSICAL_PX, 10, 10),
        source_scene=before,
    )
    result = await executor.execute(prepared, current_scene=after)
    assert result.failure_code == FailureCode.STALE_DECISION.value
    assert injector.semantic_calls == 0
    assert injector.physical_calls == 0


def test_task_budget_is_global_across_call_purposes():
    ledger = TaskBudgetLedger(
        TaskBudgetSpec(max_model_calls=2, max_model_tokens=20, max_model_latency_ms=100)
    )
    inner = _Gateway()
    gateway = BudgetedGateway(inner, ledger)
    gateway.call("plan", purpose=ModelCallPurpose.PLAN_COMPILE)
    gateway.call("ground", purpose=ModelCallPurpose.GROUNDING)
    assert ledger.model_calls == 2
    assert ledger.tokens_used == 14
    assert ledger.calls_by_purpose[ModelCallPurpose.PLAN_COMPILE] == 1
    assert ledger.calls_by_purpose[ModelCallPurpose.GROUNDING] == 1
    with pytest.raises(ModelBudgetExceeded):
        gateway.call("again", purpose=ModelCallPurpose.RECOVERY_REANALYSIS)
    assert inner.calls == 2


def test_frame_store_is_bounded_and_evicts_oldest():
    store = FrameStore(max_frames=2)
    store.put("a", b"a")
    store.put("b", b"b")
    store.put("c", b"c")
    assert "a" not in store
    assert store.get("b") == b"b"
    assert store.get("c") == b"c"
    assert len(store) == 2


@pytest.mark.asyncio
async def test_task_runtime_preserves_preused_global_budget_ledger():
    goal = GoalInterpreter().interpret(
        "브라우저에서 노트북을 검색해줘",
        model_call_budget=2,
        latency_budget_ms=1000,
    )
    strategy = StrategyPlanner().select(goal, _screen_capability())
    budget = TaskBudgetSpec(
        max_model_calls=2, max_model_tokens=100, max_model_latency_ms=1000
    )
    plan = PlanCompiler().compile_search(
        goal,
        strategy,
        search_box=TargetQuerySpec(text="Search", role="textbox"),
        result_target=TargetQuerySpec(text="노트북"),
        task_budget=budget,
    )
    ledger = TaskBudgetLedger(budget)
    ledger.before_call(ModelCallPurpose.INTENT_FILL)
    ledger.record(
        GatewayResponse(content="{}", model="fake", tokens_used=3, latency_ms=5)
    )

    class _Loop:
        async def step(self, query, action, *, verification_query=None):
            return StepResult(success=True, scene=Scene(version=1))

    runtime = TaskRuntime(_Loop(), budget_ledger=ledger)
    result = await runtime.run(plan)
    assert result.success is True
    assert runtime.budget_ledger is ledger
    assert runtime.budget_ledger.model_calls == 1
