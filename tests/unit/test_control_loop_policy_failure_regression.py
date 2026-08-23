"""Regression tests for fail-closed policy infrastructure failures."""

import pytest

from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.observation.base import Observer
from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.capability import Capability
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import SceneDelta
from hpcu.schemas.ui_element import ElementSource, UIElement
from hpcu.verifier.verifier import Verifier

pytestmark = pytest.mark.unit


class _Observer(Observer):
    def __init__(self) -> None:
        super().__init__("session")

    async def observe(self) -> SceneDelta:
        element = UIElement(
            id="button",
            scene_version=1,
            role="button",
            text="Login",
            sources=(ElementSource(type="dom", confidence=1.0),),
        )
        return SceneDelta(base_version=0, new_version=1, added=(element,))


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
        self.calls += 1
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(semantic_invoke=Capability.SUPPORTED)


class _ExplodingRiskEngine:
    def allow(self, action, scene):
        del action, scene
        raise RuntimeError("risk allow failure")

    def assess(self, action, scene):
        del action, scene
        raise RuntimeError("risk assess failure")


class _ExplodingApprovalGate:
    def request_approval(self, action, assessment, reason):
        del action, assessment, reason
        raise RuntimeError("approval backend failure")


@pytest.mark.asyncio
async def test_policy_infrastructure_failure_never_reaches_input():
    injector = _Injector()
    loop = ControlLoop(
        observer=_Observer(),
        grounder=Grounder(confidence_threshold=0.88, min_margin=0.0),
        executor=Executor(injector),
        verifier=Verifier(),
        risk_engine=_ExplodingRiskEngine(),
        approval_gate=_ExplodingApprovalGate(),
    )

    result = await loop.step(
        {"text": "Login", "role": "button"},
        Action(id="click", op=ActionOp.CLICK),
    )

    assert result.success is False
    assert result.skipped is True
    assert result.action_attempted is False
    assert result.failure_code == FailureCode.ACTION_REJECTED_BY_POLICY.value
    assert injector.calls == 0
