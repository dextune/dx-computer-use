"""Failure-injection tests for no-fallback and challenge-safe execution."""

import pytest

from hpcu.cases.runner import CaseRunner
from hpcu.cases.specs import CaseSpec, EvidenceSpec
from hpcu.cases.stats import CountingGateway
from hpcu.executor.executor import Executor
from hpcu.gateway.gateway import Gateway, GatewayResponse
from hpcu.grounder.grounder import Grounder
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.observation.base import Observer
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.scene import SceneDelta
from hpcu.schemas.ui_element import UIElement

pytestmark = pytest.mark.failure_injection

SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


def _box(x=10, y=10, width=100, height=30):
    return BoundingBox(space=SPACE, x=x, y=y, width=width, height=height)


class _Observer(Observer):
    def __init__(self, elements):
        super().__init__("failure-session")
        self.elements = tuple(elements)
        self.version = 0

    async def observe(self):
        self.version += 1
        if self.version == 1:
            return SceneDelta(base_version=0, new_version=1, added=self.elements)
        return SceneDelta(
            base_version=self.version - 1,
            new_version=self.version,
            modified=self.elements,
        )


class _Injector(InputInjector):
    def __init__(self):
        super().__init__("failure-session")
        self.calls = []

    async def semantic(self, element, action):
        self.calls.append(("semantic", action))
        return ExecutionResult(success=False, mode="semantic")

    async def physical(self, point, action, text=None):
        self.calls.append(("physical", action, text))
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self):
        return InputCapabilities(physical_pointer=Capability.SUPPORTED)


class _Gateway(Gateway):
    @property
    def provider_id(self):
        return "minimax"

    @property
    def model_id(self):
        return "MiniMax-M3"

    def __init__(self, content="not-json", error=False):
        self.content = content
        self.error = error

    def call(self, prompt, system_prompt="", max_tokens=None, *, purpose=None):
        if self.error:
            raise TimeoutError("injected model timeout")
        return GatewayResponse(
            content=self.content, model="MiniMax-M3", provider="minimax"
        )


async def _instant(_delay):
    return None


def _spec(**kwargs):
    values = {
        "id": "failure-case",
        "goal": "select an item",
        "start_url": "https://example.invalid",
        "evidence": EvidenceSpec(require_pick=True),
        "max_attempts": 1,
        "max_model_calls": 1,
    }
    values.update(kwargs)
    return CaseSpec(**values)


@pytest.mark.asyncio
async def test_model_failure_never_uses_content_fallback_click():
    injector = _Injector()
    element = UIElement(
        id="candidate",
        scene_version=1,
        role="product",
        text="number 42",
        bbox=_box(),
    )
    runner = CaseRunner(
        _Observer((element,)),
        Executor(injector),
        Grounder(),
        CountingGateway(_Gateway(error=True)),
        config={
            "cases": {
                "navigate_settle_timeout_ms": 1,
                "navigate_poll_interval_ms": 1,
                "post_click_timeout_ms": 1,
                "post_click_poll_interval_ms": 1,
            },
            "targeting": {"min_token_length": 2, "max_tokens_per_field": 8},
        },
        sleep=_instant,
    )
    stats = await runner.run_case(_spec())
    assert stats.success is False
    assert stats.outcome == "model_failed"
    assert not any(
        call[0] == "physical" and call[1] == "click"
        for call in injector.calls
        if len(call) == 3 and call[2] is None
    )


@pytest.mark.asyncio
async def test_blocked_pack_ends_in_human_handoff_without_retry():
    injector = _Injector()
    element = UIElement(
        id="challenge",
        scene_version=1,
        role="text",
        text="captcha verification required",
        bbox=_box(),
    )
    content = (
        '{"ready_any":["ready"],"success_any":["done"],'
        '"forbid_any":[],"pick_query":"item","pick_required":true,'
        '"dismiss_any":[],"blocked_any":["captcha"],"ignore_any":[]}'
    )
    runner = CaseRunner(
        _Observer((element,)),
        Executor(injector),
        Grounder(),
        CountingGateway(_Gateway(content=content)),
        config={
            "cases": {
                "navigate_settle_timeout_ms": 1,
                "navigate_poll_interval_ms": 1,
                "post_click_timeout_ms": 1,
                "post_click_poll_interval_ms": 1,
            }
        },
        sleep=_instant,
    )
    stats = await runner.run_case(_spec())
    assert stats.outcome == "human_handoff"
    assert stats.handoff_required is True
    assert stats.failure_code == "access_control_blocked"
    task_type_calls = [
        call
        for call in injector.calls
        if call[0] == "physical" and call[1] == "type"
    ]
    assert len(task_type_calls) <= 1
    assert not any(
        call[0] == "physical" and call[1] == "key" and call[2] == "Enter"
        for call in injector.calls
    )
    assert not any(
        call[0] == "physical" and call[1] == "key" and call[2] == "Enter"
        for call in injector.calls
    )
