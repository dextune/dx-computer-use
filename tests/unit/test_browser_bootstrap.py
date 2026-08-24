"""Unit contracts for typed browser lifecycle bootstrap."""

import pytest

from hpcu.cases.planning import CasePlanCompiler
from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.lifecycle.launcher import (
    ApplicationLaunchCapabilities,
    ApplicationLauncher,
    ApplicationLaunchResult,
)
from hpcu.observation.base import Observer
from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.schemas.action import Action, ActionOp, Postcondition, PostconditionKind
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import ScreenPoint
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import GoalEntity, GoalEnvelope, IntentKind
from hpcu.schemas.plan import PlanningContext, TargetQuerySpec
from hpcu.schemas.scene import SceneDelta
from hpcu.schemas.strategy import StrategyPlan
from hpcu.schemas.surface import ExecutionMode, SurfaceKind
from hpcu.schemas.ui_element import ElementSource, UIElement
from hpcu.verifier.verifier import Verifier

pytestmark = pytest.mark.unit


class _NoInput(InputInjector):
    def __init__(self) -> None:
        super().__init__("session")
        self.calls = 0

    async def semantic(self, element, action: str) -> ExecutionResult:
        del element, action
        self.calls += 1
        return ExecutionResult(success=False, mode="semantic")

    async def physical(
        self,
        point: ScreenPoint,
        action: str,
        text: str | None = None,
    ) -> ExecutionResult:
        del point, action, text
        self.calls += 1
        return ExecutionResult(success=False, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities()


class _Launcher(ApplicationLauncher):
    def __init__(self, *, evidence_element_id: str | None = "x11:42") -> None:
        super().__init__("session")
        self.evidence_element_id = evidence_element_id
        self.calls: list[str] = []

    async def launch(
        self,
        application: str,
        *,
        timeout_ms: int,
        poll_interval_ms: int,
    ) -> ApplicationLaunchResult:
        del timeout_ms, poll_interval_ms
        self.calls.append(application)
        return ApplicationLaunchResult(
            success=True,
            application=application,
            evidence_element_id=self.evidence_element_id,
        )

    def capabilities(self) -> ApplicationLaunchCapabilities:
        return ApplicationLaunchCapabilities(
            launch=Capability.SUPPORTED,
            discovery=Capability.SUPPORTED,
        )


class _LaunchObserver(Observer):
    def __init__(self) -> None:
        super().__init__("session")
        self.calls = 0

    async def observe(self) -> SceneDelta:
        self.calls += 1
        if self.calls == 1:
            return SceneDelta(base_version=0, new_version=1)
        window = UIElement(
            id="x11:42",
            scene_version=self.calls,
            role="window",
            name="Browser",
            sources=(ElementSource(type="atspi", ref="42"),),
        )
        if self.calls == 2:
            return SceneDelta(
                base_version=1,
                new_version=2,
                added=(window,),
            )
        return SceneDelta(
            base_version=self.calls - 1,
            new_version=self.calls,
            modified=(window,),
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _launch_action() -> Action:
    return Action(
        id="launch-browser",
        op=ActionOp.LAUNCH_APPLICATION,
        value="browser",
        postconditions=(
            Postcondition(
                kind=PostconditionKind.ELEMENT_VISIBLE,
                target="$execution",
            ),
        ),
    )


def _search_goal() -> GoalEnvelope:
    return GoalEnvelope(
        raw_instruction="노트북을 검색해줘",
        intent=IntentKind.SEARCH,
        terminal_state="search results visible",
        entities=(GoalEntity(kind="search_query", value="노트북"),),
        evidence_requirements=("query_echo", "result_candidate"),
    )


def _strategy() -> StrategyPlan:
    return StrategyPlan(
        id="screen",
        surface=SurfaceKind.BROWSER,
        execution_mode=ExecutionMode.SCREEN_STRICT,
        route="screen",
        expected_steps=4,
        evidence_strength=0.8,
        expected_reliability=0.8,
        expected_latency_ms=10.0,
    )


def _planning_context(*, launch: bool) -> PlanningContext:
    allowed = set(ActionOp)
    if not launch:
        allowed.remove(ActionOp.LAUNCH_APPLICATION)
    return PlanningContext(
        target_queries={
            "surface": TargetQuerySpec(role="window"),
            "entry_ready": TargetQuerySpec(text="results"),
            "search_field": TargetQuerySpec(text="search", role="textbox"),
            "search_result": TargetQuerySpec(text="노트북"),
        },
        values={"entry_url": "https://example.com"},
        allowed_ops=frozenset(allowed),
    )


@pytest.mark.asyncio
async def test_empty_desktop_launches_without_grounding_or_input():
    launcher = _Launcher()
    injector = _NoInput()
    clock = _Clock()
    loop = ControlLoop(
        observer=_LaunchObserver(),
        grounder=Grounder(confidence_threshold=0.88, min_margin=0.0),
        executor=Executor(
            injector,
            application_launcher=launcher,
            poll_interval_ms=1,
        ),
        verifier=Verifier(),
        config={
            "performance": {
                "settle_poll_interval_ms": 1,
                "settle_stable_polls": 2,
            }
        },
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    result = await loop.step(None, _launch_action())

    assert result.success is True
    assert result.action_attempted is True
    assert result.execution is not None
    assert result.execution.evidence_element_id == "x11:42"
    assert result.scene.get("x11:42") is not None
    assert launcher.calls == ["browser"]
    assert injector.calls == 0


@pytest.mark.asyncio
async def test_launch_success_without_scene_evidence_fails_closed():
    launcher = _Launcher(evidence_element_id=None)
    loop = ControlLoop(
        observer=_LaunchObserver(),
        grounder=Grounder(confidence_threshold=0.88, min_margin=0.0),
        executor=Executor(_NoInput(), application_launcher=launcher),
        verifier=Verifier(),
    )

    result = await loop.step(None, _launch_action())

    assert result.success is False
    assert result.failure_code == FailureCode.APPLICATION_NOT_OBSERVED.value
    assert result.action_attempted is True


@pytest.mark.asyncio
async def test_executor_reports_missing_launcher_explicitly():
    executor = Executor(_NoInput())
    prepared = executor.prepare(_launch_action(), None)

    result = await executor.execute(prepared)

    assert result.success is False
    assert result.failure_code == FailureCode.APPLICATION_LAUNCH_UNSUPPORTED.value


def test_case_plan_launches_browser_before_entry_navigation():
    plan = CasePlanCompiler().compile(
        _search_goal(),
        _strategy(),
        _planning_context(launch=True),
    )

    assert plan.entry_node_id == "launch-case-browser"
    launch = plan.nodes["launch-case-browser"]
    assert launch.action.op is ActionOp.LAUNCH_APPLICATION
    assert launch.action.value == "browser"
    assert launch.success_edge == "enter-case-url"
    assert plan.nodes["enter-case-url"].success_edge == "verify-case-entry"
    assert plan.nodes["verify-case-entry"].success_edge == "focus-search"


def test_case_plan_preserves_navigation_when_launch_capability_missing():
    plan = CasePlanCompiler().compile(
        _search_goal(),
        _strategy(),
        _planning_context(launch=False),
    )

    assert plan.entry_node_id == "enter-case-url"
    assert "launch-case-browser" not in plan.nodes
