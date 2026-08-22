"""ControlLoop safety contracts: fresh verification, policy and stale binding."""

import pytest

from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.observation.base import Observer
from hpcu.policy.risk_engine import RiskEngine
from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.schemas.action import (
    Action,
    ActionOp,
    ActionTarget,
    Postcondition,
    PostconditionKind,
    Precondition,
    PreconditionKind,
)
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import SceneDelta
from hpcu.schemas.ui_element import ElementSource, UIElement
from hpcu.trace.recorder import TraceRecorder
from hpcu.verifier.verifier import Verifier


class FakeInjector(InputInjector):
    def __init__(self, *, semantic_success: bool = True):
        super().__init__("test-session")
        self.semantic_success = semantic_success
        self.semantic_calls: list[str] = []
        self.physical_calls: list[str] = []

    async def semantic(self, element, action: str) -> ExecutionResult:
        self.semantic_calls.append(element.id)
        return ExecutionResult(success=self.semantic_success, mode="semantic")

    async def physical(self, point, action: str, text: str | None = None):
        self.physical_calls.append(action)
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(
            semantic_invoke=Capability.SUPPORTED,
            physical_pointer=Capability.SUPPORTED,
        )


class ScriptedObserver(Observer):
    def __init__(self, snapshots: list[tuple[UIElement, ...]]):
        super().__init__("test-session")
        self.snapshots = snapshots
        self.calls = 0
        self.previous: dict[str, UIElement] = {}

    async def observe(self) -> SceneDelta:
        index = min(self.calls, len(self.snapshots) - 1)
        current = {element.id: element for element in self.snapshots[index]}
        base = self.calls
        self.calls += 1
        added = tuple(value for key, value in current.items() if key not in self.previous)
        removed = tuple(key for key in self.previous if key not in current)
        modified = tuple(value for key, value in current.items() if key in self.previous)
        self.previous = current
        return SceneDelta(
            base_version=base,
            new_version=base + 1,
            added=added,
            removed=removed,
            modified=modified,
        )


def _element(element_id: str, text: str, *, role: str = "button") -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=1,
        role=role,
        name=text,
        text=text,
        bbox=BoundingBox(
            space=CoordinateSpace.SCREEN_PHYSICAL_PX,
            x=10,
            y=10,
            width=120,
            height=30,
        ),
        sources=(ElementSource(type="dom", confidence=1.0),),
    )


def _loop(snapshots: list[tuple[UIElement, ...]], injector=None):
    selected = injector or FakeInjector()
    recorder = TraceRecorder()
    loop = ControlLoop(
        observer=ScriptedObserver(snapshots),
        grounder=Grounder(confidence_threshold=0.5, min_margin=0.0),
        executor=Executor(selected),
        verifier=Verifier(),
        risk_engine=RiskEngine(),
        recorder=recorder,
    )
    return loop, selected, recorder


@pytest.mark.unit
async def test_step_verifies_postcondition_on_fresh_scene():
    login = _element("login", "Login")
    done = _element("done", "Signed in", role="status")
    loop, injector, recorder = _loop([(login,), (login, done)])
    action = Action(
        id="login-action",
        op=ActionOp.CLICK,
        target=ActionTarget(element_id="login"),
        postconditions=(
            Postcondition(kind=PostconditionKind.ELEMENT_VISIBLE, target="done"),
        ),
    )

    result = await loop.step({}, action)

    assert result.success is True
    assert result.verified is True
    assert result.pre_scene.version < result.scene.version
    assert result.scene_changed is True
    assert injector.semantic_calls == ["login"]
    assert recorder.model_call_count == 0


@pytest.mark.unit
async def test_step_does_not_verify_against_pre_action_scene():
    login = _element("login", "Login")
    transient = _element("done", "Already visible", role="status")
    loop, _injector, _recorder = _loop([(login, transient), (login,)])
    action = Action(
        id="login-action",
        op=ActionOp.CLICK,
        target=ActionTarget(element_id="login"),
        postconditions=(
            Postcondition(kind=PostconditionKind.ELEMENT_VISIBLE, target="done"),
        ),
    )

    result = await loop.step({}, action)

    assert result.success is False
    assert result.failure_code == FailureCode.POSTCONDITION_UNMET.value
    assert result.pre_scene.get("done") is not None
    assert result.scene.get("done") is None


@pytest.mark.unit
async def test_step_skips_execute_when_policy_requires_approval():
    buy = _element("buy", "Purchase now")
    injector = FakeInjector()
    loop, injector, _recorder = _loop([(buy,)], injector)
    action = Action(
        id="pay",
        op=ActionOp.CLICK,
        target=ActionTarget(element_id="buy"),
    )

    result = await loop.step({}, action)

    assert result.success is False
    assert result.skipped is True
    assert result.failure_code == FailureCode.ACTION_REJECTED_BY_POLICY.value
    assert injector.semantic_calls == []


@pytest.mark.unit
async def test_step_rejects_unmet_precondition_before_input():
    login = _element("login", "Login")
    loop, injector, _recorder = _loop([(login,)])
    action = Action(
        id="login-action",
        op=ActionOp.CLICK,
        target=ActionTarget(element_id="login"),
        preconditions=(
            Precondition(kind=PreconditionKind.ELEMENT_VISIBLE, target="missing"),
        ),
    )

    result = await loop.step({}, action)

    assert result.success is False
    assert result.skipped is True
    assert result.failure_code == FailureCode.PRECONDITION_UNMET.value
    assert injector.semantic_calls == []


@pytest.mark.unit
async def test_step_unresolved_does_not_call_model_or_input():
    login = _element("login", "Login")
    loop, injector, recorder = _loop([(login,)])
    action = Action(id="missing", op=ActionOp.CLICK)

    result = await loop.step({"text": "Not present"}, action)

    assert result.success is False
    assert result.skipped is True
    assert injector.semantic_calls == []
    assert recorder.model_call_count == 0
