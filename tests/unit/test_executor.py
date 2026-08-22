"""Unit tests for the Executor.

Fake InputInjector implementations only — no real OS input is touched.
"""

import asyncio

import pytest

from hpcu.executor.executor import Executor, PreparedAction, is_valid_command
from hpcu.input.injector import (
    ExecutionResult,
    InputCapabilities,
    InputInjector,
)
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import CoordinateSpace, ScreenPoint
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.ui_element import UIElement


class FakeInputInjector(InputInjector):
    """In-memory injector that records semantic/physical calls."""

    def __init__(self, semantic_success: bool = True):
        super().__init__("test-session")
        self._semantic_success = semantic_success
        self.semantic_calls: list[tuple[str, str]] = []
        self.physical_calls: list[tuple[float, float, str, str | None]] = []

    async def semantic(self, element: UIElement, action: str) -> ExecutionResult:
        self.semantic_calls.append((element.id, action))
        if self._semantic_success:
            return ExecutionResult(success=True, mode="semantic")
        return ExecutionResult(
            success=False,
            mode="semantic",
            failure_code=FailureCode.INPUT_SEMANTIC_UNSUPPORTED.value,
        )

    async def physical(
        self, point: ScreenPoint, action: str, text: str | None = None
    ) -> ExecutionResult:
        self.physical_calls.append((point.x, point.y, action, text))
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(
            semantic_invoke=Capability.SUPPORTED,
            physical_pointer=Capability.SUPPORTED,
        )


def make_action(op: ActionOp = ActionOp.CLICK) -> Action:
    return Action(id="a1", op=op)


def make_element() -> UIElement:
    return UIElement(id="btn_login", scene_version=1, role="button", name="로그인")


def make_point() -> ScreenPoint:
    return ScreenPoint(
        space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=100, y=200
    )


@pytest.mark.unit
def test_prepare_maps_op_to_command():
    # Given an Executor and a click Action — prepare never calls the injector
    executor = Executor(FakeInputInjector())
    action = make_action(ActionOp.CLICK)

    # When prepare resolves the action
    prepared = executor.prepare(action, "btn_login")

    # Then the command reflects the op and the id is preserved
    assert isinstance(prepared, PreparedAction)
    assert prepared.command == "click"
    assert prepared.element_id == "btn_login"
    assert prepared.action is action
    assert prepared.element is None


@pytest.mark.unit
def test_prepare_passes_resolved_target_through():
    # Given a prepared target element and point
    executor = Executor(FakeInputInjector())
    element = make_element()
    point = make_point()

    # When prepare is called with a resolved element and point
    prepared = executor.prepare(
        make_action(ActionOp.CLICK), "btn_login", element=element, physical_point=point
    )

    # Then the resolved target is carried on the PreparedAction
    assert prepared.element is element
    assert prepared.physical_point is point


@pytest.mark.unit
def test_prepare_unmapped_op_falls_back_to_op_value():
    # Given an Executor and an op with no explicit command mapping
    executor = Executor(FakeInputInjector())

    # When preparing a wait_until action
    prepared = executor.prepare(make_action(ActionOp.WAIT_UNTIL), None)

    # Then the command falls back to the op value
    assert prepared.command == "wait_until"


@pytest.mark.unit
async def test_execute_delegates_to_semantic_when_element_present():
    # Given a fake injector whose semantic path succeeds, and a prepared action with an element
    injector = FakeInputInjector(semantic_success=True)
    executor = Executor(injector)
    prepared = executor.prepare(
        make_action(ActionOp.CLICK), "btn_login", element=make_element()
    )

    # When the action is executed
    result = await executor.execute(prepared)

    # Then semantic was used and succeeded, physical was not touched
    assert result.success is True
    assert result.mode == "semantic"
    assert injector.semantic_calls == [("btn_login", "click")]
    assert injector.physical_calls == []


@pytest.mark.unit
async def test_execute_falls_back_to_physical_when_semantic_fails():
    # Given a fake injector whose semantic path fails, with both element and point present
    injector = FakeInputInjector(semantic_success=False)
    executor = Executor(injector)
    prepared = executor.prepare(
        make_action(ActionOp.CLICK),
        "btn_login",
        element=make_element(),
        physical_point=make_point(),
    )

    # When the action is executed
    result = await executor.execute(prepared)

    # Then semantic was attempted first, and physical was the fallback success
    assert len(injector.semantic_calls) == 1
    assert result.success is True
    assert result.mode == "physical"
    assert injector.physical_calls == [(100.0, 200.0, "click", None)]


@pytest.mark.unit
async def test_execute_uses_physical_when_only_point_present():
    # Given a prepared action with only a physical point (no element)
    injector = FakeInputInjector(semantic_success=True)
    executor = Executor(injector)
    prepared = executor.prepare(
        make_action(ActionOp.CLICK), None, physical_point=make_point()
    )

    # When the action is executed
    result = await executor.execute(prepared)

    # Then physical injection is used directly (no element to target semantically)
    assert result.success is True
    assert result.mode == "physical"
    assert injector.semantic_calls == []
    assert injector.physical_calls == [(100.0, 200.0, "click", None)]


@pytest.mark.unit
async def test_execute_reports_unsupported_when_no_target():
    # Given a prepared action with neither element nor point
    injector = FakeInputInjector(semantic_success=True)
    executor = Executor(injector)
    prepared = executor.prepare(make_action(ActionOp.CLICK), None)
    assert prepared.element is None
    assert prepared.physical_point is None

    # When the action is executed
    result = await executor.execute(prepared)

    # Then an honest unsupported result is returned, no injector call happens
    assert result.success is False
    assert result.failure_code == FailureCode.INPUT_PHYSICAL_UNSUPPORTED.value
    assert injector.semantic_calls == []
    assert injector.physical_calls == []


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, dt: float) -> None:
        self.t += dt
        await asyncio.sleep(0)


def _executor_with_clock() -> tuple[Executor, _FakeClock]:
    clock = _FakeClock()
    executor = Executor(
        FakeInputInjector(),
        poll_interval_ms=5,
        required_stable_polls=2,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    return executor, clock


@pytest.mark.unit
async def test_wait_until_returns_true_when_predicate_holds():
    executor, _clock = _executor_with_clock()
    settled = await executor.wait_until(lambda: True, timeout_ms=100)
    assert settled is True


@pytest.mark.unit
async def test_wait_until_returns_false_after_timeout():
    executor, clock = _executor_with_clock()
    settled = await executor.wait_until(lambda: False, timeout_ms=30)
    assert settled is False
    assert clock.t >= 0.03


@pytest.mark.unit
async def test_wait_until_returns_true_after_condition_becomes_true():
    executor, _clock = _executor_with_clock()
    counter = {"turns": 0}

    def predicate() -> bool:
        counter["turns"] += 1
        return counter["turns"] >= 3

    settled = await executor.wait_until(predicate, timeout_ms=2000)
    assert settled is True
    assert counter["turns"] >= 3


@pytest.mark.unit
async def test_settle_detector_requires_consecutive_true_polls():
    executor, _clock = _executor_with_clock()
    settled = await executor.settle_detector(lambda: True, timeout_ms=100)
    assert settled is True


@pytest.mark.unit
async def test_wait_until_yields_to_event_loop():
    executor, _clock = _executor_with_clock()
    sibling_done = {"done": False}

    async def sibling() -> None:
        sibling_done["done"] = True

    wait_task = asyncio.create_task(
        executor.wait_until(lambda: False, timeout_ms=50)
    )
    sibling_task = asyncio.create_task(sibling())
    await wait_task
    await sibling_task
    assert sibling_done["done"] is True


@pytest.mark.unit
async def test_inject_physical_type_passes_text():
    injector = FakeInputInjector()
    executor = Executor(injector)
    result = await executor.inject_physical(make_point(), "type", text="https://example.com")
    assert result.success is True
    assert injector.physical_calls == [(100.0, 200.0, "type", "https://example.com")]
    assert injector.semantic_calls == []


@pytest.mark.unit
def test_is_valid_command_accepts_snake_case_token():
    # Given common injector command tokens
    # When validated against the command pattern
    # Then well-formed tokens pass and malformed ones fail
    assert is_valid_command("click") is True
    assert is_valid_command("double_click") is True
    assert is_valid_command("Click") is False
    assert is_valid_command("click me") is False
