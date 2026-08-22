"""ControlLoop.step — one cycle on fakes, zero model calls."""

import pytest

from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.observation.base import Observer
from hpcu.policy.risk_engine import RiskEngine
from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.schemas.action import Action, ActionOp, ActionTarget, Postcondition, PostconditionKind
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import SceneDelta
from hpcu.schemas.ui_element import ElementSource, UIElement
from hpcu.trace.recorder import TraceRecorder
from hpcu.verifier.verifier import Verifier


class FakeInjector(InputInjector):
    def __init__(self, session_id: str = "test-session"):
        super().__init__(session_id)
        self.semantic_calls: list[str] = []

    async def semantic(self, element, action: str) -> ExecutionResult:
        self.semantic_calls.append(element.id)
        return ExecutionResult(success=True, mode="semantic")

    async def physical(self, point, action: str, text: str | None = None) -> ExecutionResult:
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(semantic_invoke=Capability.SUPPORTED)


class FakeObserver(Observer):
    def __init__(self, elements: tuple[UIElement, ...]):
        super().__init__("test-session")
        self._elements = elements
        self._once = False

    async def observe(self) -> SceneDelta:
        if self._once:
            return SceneDelta(base_version=1, new_version=2)
        self._once = True
        return SceneDelta(
            base_version=0,
            new_version=1,
            added=self._elements,
        )


def _login_button() -> UIElement:
    return UIElement(
        id="login",
        scene_version=1,
        role="button",
        name="로그인",
        text="로그인",
        bbox=BoundingBox(
            space=CoordinateSpace.SCREEN_PHYSICAL_PX,
            x=10, y=10, width=80, height=30,
        ),
        sources=(ElementSource(type="dom", confidence=1.0),),
    )


def _buy_button() -> UIElement:
    return UIElement(
        id="buy",
        scene_version=1,
        role="button",
        name="Purchase now",
        text="Purchase now",
        bbox=BoundingBox(
            space=CoordinateSpace.SCREEN_PHYSICAL_PX,
            x=10, y=10, width=80, height=30,
        ),
        sources=(ElementSource(type="dom", confidence=1.0),),
    )


def _loop(elements: tuple[UIElement, ...], injector: FakeInjector | None = None):
    inj = injector or FakeInjector()
    recorder = TraceRecorder()
    loop = ControlLoop(
        observer=FakeObserver(elements),
        grounder=Grounder(confidence_threshold=0.5, min_margin=0.0),
        executor=Executor(inj),
        verifier=Verifier(),
        risk_engine=RiskEngine(),
        recorder=recorder,
    )
    return loop, inj, recorder


@pytest.mark.unit
async def test_step_clicks_without_model_call():
    loop, injector, recorder = _loop((_login_button(),))
    action = Action(
        id="s1",
        op=ActionOp.CLICK,
        postconditions=(Postcondition(kind=PostconditionKind.ELEMENT_VISIBLE, target="login"),),
    )
    result = await loop.step({"text": "로그인", "role": "button"}, action)
    assert result.success is True
    assert injector.semantic_calls == ["login"]
    assert recorder.model_call_count == 0
    assert recorder.seq >= 2
    assert loop.scene.get("login") is not None


@pytest.mark.unit
async def test_step_skips_execute_when_policy_high():
    injector = FakeInjector()
    loop, injector, _recorder = _loop((_buy_button(),), injector)
    action = Action(id="pay", op=ActionOp.CLICK, target=ActionTarget(element_id="buy"))
    result = await loop.step({"text": "Purchase now", "role": "button"}, action)
    assert result.success is False
    assert result.skipped is True
    assert result.failure_code == FailureCode.ACTION_REJECTED_BY_POLICY.value
    assert injector.semantic_calls == []


@pytest.mark.unit
async def test_step_unresolved_does_not_call_model():
    loop, injector, recorder = _loop((_login_button(),))
    action = Action(id="s1", op=ActionOp.CLICK)
    result = await loop.step({"text": "없는버튼"}, action)
    assert result.success is False
    assert result.skipped is True
    assert injector.semantic_calls == []
    assert recorder.model_call_count == 0