"""Regression tests for observation failures inside ControlLoop.step."""

import pytest

from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.observation.base import Observer
from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import SceneDelta
from hpcu.schemas.ui_element import ElementSource, UIElement
from hpcu.verifier.verifier import Verifier

pytestmark = pytest.mark.unit


class _Observer(Observer):
    def __init__(self, fail_on: int) -> None:
        super().__init__("session")
        self.fail_on = fail_on
        self.calls = 0

    async def observe(self) -> SceneDelta:
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("injected observation failure")
        element = UIElement(
            id="button",
            scene_version=self.calls,
            role="button",
            text="Login",
            bbox=BoundingBox(
                space=CoordinateSpace.SCREEN_PHYSICAL_PX,
                x=10,
                y=10,
                width=80,
                height=30,
            ),
            sources=(ElementSource(type="dom", confidence=1.0),),
        )
        if self.calls == 1:
            return SceneDelta(base_version=0, new_version=1, added=(element,))
        return SceneDelta(
            base_version=self.calls - 1,
            new_version=self.calls,
            modified=(element,),
        )


class _Injector(InputInjector):
    def __init__(self) -> None:
        super().__init__("session")
        self.calls = 0

    async def semantic(self, element, action: str) -> ExecutionResult:
        del element, action
        self.calls += 1
        return ExecutionResult(success=True, mode="semantic")

    async def physical(self, point, action: str, text: str | None = None):
        del point, action, text
        raise AssertionError("physical fallback should not run")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(semantic_invoke=Capability.SUPPORTED)


def _loop(fail_on: int):
    injector = _Injector()
    return (
        ControlLoop(
            observer=_Observer(fail_on),
            grounder=Grounder(confidence_threshold=0.88, min_margin=0.0),
            executor=Executor(injector),
            verifier=Verifier(),
        ),
        injector,
    )


@pytest.mark.asyncio
async def test_pre_observe_failure_returns_typed_failure_without_input():
    loop, injector = _loop(fail_on=1)

    result = await loop.step(
        {"text": "Login", "role": "button"},
        Action(id="click", op=ActionOp.CLICK),
    )

    assert result.success is False
    assert result.skipped is True
    assert result.failure_code == FailureCode.CAPTURE_BACKEND_UNAVAILABLE.value
    assert result.execution is None
    assert injector.calls == 0


@pytest.mark.asyncio
async def test_post_observe_failure_preserves_successful_execution():
    loop, injector = _loop(fail_on=2)

    result = await loop.step(
        {"text": "Login", "role": "button"},
        Action(id="click", op=ActionOp.CLICK),
    )

    assert result.success is False
    assert result.skipped is False
    assert result.failure_code == FailureCode.CAPTURE_BACKEND_UNAVAILABLE.value
    assert result.execution is not None and result.execution.success is True
    assert result.pre_scene_version == 1
    assert result.post_scene_version is None
    assert injector.calls == 1
