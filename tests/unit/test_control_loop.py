"""ControlLoop.step — fresh-scene verification on deterministic fakes."""

from dataclasses import replace

import pytest

from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder, GroundingCandidate, GroundingResult
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
)
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import SceneDelta
from hpcu.schemas.ui_element import ElementSource, ElementState, UIElement
from hpcu.trace.recorder import TraceRecorder
from hpcu.verifier.verifier import Verifier


class FakeInjector(InputInjector):
    def __init__(self, session_id: str = "test-session"):
        super().__init__(session_id)
        self.semantic_calls: list[str] = []

    async def semantic(self, element, action: str) -> ExecutionResult:
        self.semantic_calls.append(element.id)
        return ExecutionResult(success=True, mode="semantic")

    async def physical(
        self, point, action: str, text: str | None = None
    ) -> ExecutionResult:
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(semantic_invoke=Capability.SUPPORTED)


class FakeObserver(Observer):
    def __init__(
        self,
        before: tuple[UIElement, ...],
        after: tuple[UIElement, ...] | None = None,
    ):
        super().__init__("test-session")
        self._before = before
        self._after = after if after is not None else before
        self._observed = 0

    async def observe(self) -> SceneDelta:
        self._observed += 1
        if self._observed == 1:
            return SceneDelta(base_version=0, new_version=1, added=self._before)
        refreshed = tuple(replace(element, scene_version=2) for element in self._after)
        return SceneDelta(base_version=1, new_version=2, modified=refreshed)


class ScriptedGrounder:
    def __init__(self, *results: GroundingResult):
        self._results = list(results)

    def resolve(self, query, scene) -> GroundingResult:
        del query, scene
        return self._results.pop(0)


def _grounding(
    element_id: str | None,
    confidence: float,
    *,
    failure_code: FailureCode | None = None,
    candidate_ids: tuple[str, ...] = (),
) -> GroundingResult:
    return GroundingResult(
        element_id=element_id,
        confidence=confidence,
        candidates=tuple(
            GroundingCandidate(candidate_id, confidence)
            for candidate_id in candidate_ids
        ),
        failure_code=failure_code,
    )


def _login_button(*, selected: bool = False) -> UIElement:
    return UIElement(
        id="login",
        scene_version=1,
        role="button",
        name="로그인",
        text="로그인",
        state=ElementState(selected=selected),
        bbox=BoundingBox(
            space=CoordinateSpace.SCREEN_PHYSICAL_PX,
            x=10,
            y=10,
            width=80,
            height=30,
        ),
        sources=(ElementSource(type="dom", confidence=1.0),),
        fingerprint="login-button",
    )


def _buy_button() -> UIElement:
    return replace(_login_button(), id="buy", name="Purchase now", text="Purchase now")


def _loop(
    before: tuple[UIElement, ...],
    after: tuple[UIElement, ...] | None = None,
    injector: FakeInjector | None = None,
    grounder=None,
):
    inj = injector or FakeInjector()
    recorder = TraceRecorder()
    loop = ControlLoop(
        observer=FakeObserver(before, after),
        grounder=grounder or Grounder(confidence_threshold=0.5, min_margin=0.0),
        executor=Executor(inj),
        verifier=Verifier(),
        risk_engine=RiskEngine(),
        recorder=recorder,
    )
    return loop, inj, recorder


@pytest.mark.unit
async def test_step_requires_fresh_post_action_transition():
    loop, injector, recorder = _loop(
        (_login_button(selected=False),),
        (_login_button(selected=True),),
    )
    action = Action(
        id="s1",
        op=ActionOp.CLICK,
        postconditions=(
            Postcondition(
                kind=PostconditionKind.STATE_MATCHES,
                target="$target",
                value=1,
            ),
        ),
    )
    result = await loop.step({"text": "로그인", "role": "button"}, action)
    assert result.success is True
    assert result.pre_scene_version == 1
    assert result.post_scene_version == 2
    assert injector.semantic_calls == ["login"]
    assert recorder.model_call_count == 0


@pytest.mark.unit
async def test_noop_scene_cannot_prove_click_effect():
    loop, injector, _recorder = _loop((_login_button(selected=False),))
    action = Action(
        id="s1",
        op=ActionOp.CLICK,
        postconditions=(
            Postcondition(kind=PostconditionKind.ELEMENT_VISIBLE, target="$target"),
        ),
    )
    result = await loop.step({"text": "로그인", "role": "button"}, action)
    assert result.success is False
    assert result.failure_code == FailureCode.POSTCONDITION_UNMET.value
    assert result.post_scene_version == 2
    assert injector.semantic_calls == ["login"]


@pytest.mark.unit
async def test_step_skips_execute_when_policy_high():
    injector = FakeInjector()
    loop, injector, _recorder = _loop((_buy_button(),), injector=injector)
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
    assert result.failure_code == FailureCode.GROUNDING_CONFIDENCE_LOW.value
    assert injector.semantic_calls == []
    assert recorder.model_call_count == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    "failure_code",
    [
        FailureCode.GROUNDING_CONFIDENCE_LOW,
        FailureCode.GROUNDING_AMBIGUOUS,
    ],
)
async def test_step_preserves_cpu_grounding_failure_reason(failure_code):
    unresolved = _grounding(
        None,
        0.7,
        failure_code=failure_code,
        candidate_ids=("login", "other"),
    )
    loop, injector, recorder = _loop(
        (_login_button(),),
        grounder=ScriptedGrounder(unresolved),
    )

    result = await loop.step({"text": "로그인"}, Action(id="s1", op=ActionOp.CLICK))

    assert result.success is False
    assert result.skipped is True
    assert result.failure_code == failure_code.value
    assert injector.semantic_calls == []
    assert recorder.model_call_count == 0


@pytest.mark.unit
async def test_verification_grounding_preserves_cpu_failure_reason():
    resolved = _grounding(
        "login",
        1.0,
        candidate_ids=("login",),
    )
    unresolved = _grounding(
        None,
        0.6,
        failure_code=FailureCode.GROUNDING_CONFIDENCE_LOW,
        candidate_ids=("login",),
    )
    loop, injector, recorder = _loop(
        (_login_button(),),
        (_login_button(selected=True),),
        grounder=ScriptedGrounder(resolved, unresolved),
    )
    action = Action(
        id="s1",
        op=ActionOp.CLICK,
        postconditions=(
            Postcondition(
                kind=PostconditionKind.ELEMENT_VISIBLE,
                target="$verify",
            ),
        ),
    )

    result = await loop.step(
        {"text": "로그인"},
        action,
        verification_query={"text": "완료"},
    )

    assert result.success is False
    assert result.failure_code == FailureCode.GROUNDING_CONFIDENCE_LOW.value
    assert injector.semantic_calls == ["login"]
    assert recorder.model_call_count == 0
