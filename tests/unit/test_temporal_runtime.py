"""Temporal and recovery semantics that must remain inside ControlLoop."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.input.keys import ENTER, FOCUS_LOCATION
from hpcu.observation.base import Observer
from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.schemas.action import (
    Action,
    ActionOp,
    ActionTarget,
    Postcondition,
    PostconditionKind,
)
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace, ScreenPoint
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene, SceneDelta
from hpcu.schemas.ui_element import ElementState, UIElement
from hpcu.verifier.verifier import Verifier

pytestmark = pytest.mark.unit

_SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


class _Injector(InputInjector):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__("session")
        self.fail = fail
        self.semantic_calls: list[str] = []
        self.physical_calls: list[tuple[str, str | None]] = []

    async def semantic(self, element, action):
        self.semantic_calls.append(action)
        return ExecutionResult(
            success=not self.fail,
            mode="semantic",
            failure_code=(
                FailureCode.INPUT_SEMANTIC_UNSUPPORTED.value if self.fail else None
            ),
        )

    async def physical(self, point, action, text=None):
        del point
        self.physical_calls.append((action, text))
        return ExecutionResult(
            success=not self.fail,
            mode="physical",
            failure_code=(
                FailureCode.INPUT_PHYSICAL_UNSUPPORTED.value if self.fail else None
            ),
        )

    def capabilities(self):
        return InputCapabilities(
            semantic_invoke=Capability.SUPPORTED,
            physical_pointer=Capability.SUPPORTED,
            physical_keyboard=Capability.SUPPORTED,
        )


class _StateObserver(Observer):
    def __init__(self, states: list[bool]) -> None:
        super().__init__("session")
        self.states = states
        self.calls = 0

    async def observe(self) -> SceneDelta:
        index = min(self.calls, len(self.states) - 1)
        self.calls += 1
        version = self.calls
        element = _element(visible=self.states[index], version=version)
        return SceneDelta(
            base_version=version - 1,
            new_version=version,
            added=(element,) if version == 1 else (),
            modified=() if version == 1 else (element,),
        )


def _element(*, visible: bool = True, version: int = 1) -> UIElement:
    return UIElement(
        id="status",
        scene_version=version,
        role="button",
        name="Ready",
        text="Ready",
        state=ElementState(visible=visible, enabled=True),
        bbox=BoundingBox(_SPACE, 0, 0, 20, 20),
        fingerprint="status",
    )


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_wait_until_observes_fresh_scenes_and_requires_stability():
    observer = _StateObserver([False, True, True])
    loop = ControlLoop(
        observer,
        Grounder(confidence_threshold=0.5, min_margin=0.0),
        Executor(_Injector()),
        Verifier(),
        config={
            "performance": {
                "settle_poll_interval_ms": 1,
                "settle_stable_polls": 2,
            }
        },
        sleep=_no_sleep,
    )
    action = Action(
        id="wait-ready",
        op=ActionOp.WAIT_UNTIL,
        target=ActionTarget(element_id="status"),
        postconditions=(
            Postcondition(
                kind=PostconditionKind.ELEMENT_VISIBLE,
                target="$target",
            ),
        ),
        timeout_ms=100,
    )

    result = await loop.step(None, action)

    assert result.success is True
    assert result.pre_scene_version == 1
    assert result.post_scene_version == 3
    assert observer.calls == 3


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.06
        return self.value


@pytest.mark.asyncio
async def test_wait_until_returns_timeout_failure_code():
    observer = _StateObserver([False])
    loop = ControlLoop(
        observer,
        Grounder(confidence_threshold=0.5, min_margin=0.0),
        Executor(_Injector()),
        Verifier(),
        config={
            "performance": {
                "settle_poll_interval_ms": 1,
                "settle_stable_polls": 1,
            }
        },
        sleep=_no_sleep,
        monotonic=_Clock(),
    )
    action = Action(
        id="wait-ready",
        op=ActionOp.WAIT_UNTIL,
        target=ActionTarget(element_id="status"),
        postconditions=(
            Postcondition(
                kind=PostconditionKind.ELEMENT_VISIBLE,
                target="$target",
            ),
        ),
        timeout_ms=100,
    )

    result = await loop.step(None, action)

    assert result.success is False
    assert result.failure_code == FailureCode.ACTION_TIMEOUT.value
    assert observer.calls >= 2


class _RecordingLoopBreaker:
    window_size = 4

    def __init__(self) -> None:
        self.history_sizes: list[tuple[int, int]] = []

    def detect(self, actions, scenes):
        self.history_sizes.append((len(actions), len(scenes)))
        return None

    def recover(self, detection, scene, action):
        raise AssertionError("recover must not run without a detection")


@pytest.mark.asyncio
async def test_control_loop_feeds_real_history_to_loop_breaker():
    observer = _StateObserver([True, True])
    breaker = _RecordingLoopBreaker()
    loop = ControlLoop(
        observer,
        Grounder(confidence_threshold=0.5, min_margin=0.0),
        Executor(_Injector(fail=True)),
        Verifier(),
        loop_breaker=breaker,
    )
    action = Action(id="click", op=ActionOp.CLICK)

    first = await loop.step({"text": "Ready", "role": "button"}, action)
    second = await loop.step({"text": "Ready", "role": "button"}, action)

    assert first.success is False
    assert second.success is False
    assert breaker.history_sizes == [(1, 1), (2, 2)]
    assert len(loop.action_history) == 2
    assert len(loop.scene_history) == 2


@pytest.mark.asyncio
async def test_navigate_executes_one_portable_keyboard_sequence():
    injector = _Injector()
    executor = Executor(injector)
    element = _element()
    scene = Scene(version=1, elements={element.id: element})
    action = Action(
        id="navigate",
        op=ActionOp.NAVIGATE,
        value="https://example.com",
    )
    prepared = executor.prepare(
        action,
        element.id,
        element=element,
        physical_point=ScreenPoint(_SPACE, 10, 10),
        source_scene=scene,
    )

    result = await executor.execute(prepared, current_scene=scene)

    assert result.success is True
    assert injector.semantic_calls == ["focus"]
    assert injector.physical_calls == [
        ("key", FOCUS_LOCATION),
        ("replace_text", "https://example.com"),
        ("key", ENTER),
    ]
