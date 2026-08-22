"""Fail-closed execution and bounded local resource contracts."""

import pytest

from hpcu.capture.frame_store import FrameStore
from hpcu.executor.executor import Executor
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace, ScreenPoint
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement


class _Injector(InputInjector):
    def __init__(self):
        super().__init__("session")
        self.physical_calls = 0
        self.semantic_calls = 0

    async def semantic(self, element, action: str) -> ExecutionResult:
        self.semantic_calls += 1
        return ExecutionResult(success=True, mode="semantic")

    async def physical(self, point, action: str, text: str | None = None):
        self.physical_calls += 1
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(
            semantic_invoke=Capability.SUPPORTED,
            physical_pointer=Capability.SUPPORTED,
        )


@pytest.mark.unit
async def test_unknown_physical_command_never_becomes_click():
    injector = _Injector()
    executor = Executor(injector)
    point = ScreenPoint(
        space=CoordinateSpace.SCREEN_PHYSICAL_PX,
        x=1,
        y=1,
    )
    result = await executor.inject_physical(point, "clik")
    assert result.success is False
    assert result.failure_code == FailureCode.ACTION_UNSUPPORTED.value
    assert injector.physical_calls == 0


@pytest.mark.unit
async def test_prepared_action_rejects_scene_version_drift():
    injector = _Injector()
    executor = Executor(injector)
    bbox = BoundingBox(
        space=CoordinateSpace.SCREEN_PHYSICAL_PX,
        x=0,
        y=0,
        width=10,
        height=10,
    )
    first = UIElement(id="button", scene_version=1, bbox=bbox, fingerprint="same")
    second = UIElement(id="button", scene_version=2, bbox=bbox, fingerprint="same")
    source = Scene(version=1, elements={first.id: first})
    current = Scene(version=2, elements={second.id: second})
    action = Action(id="click", op=ActionOp.CLICK)
    prepared = executor.prepare(
        action,
        first.id,
        element=first,
        physical_point=ScreenPoint(
            space=CoordinateSpace.SCREEN_PHYSICAL_PX,
            x=5,
            y=5,
        ),
        source_scene=source,
    )
    result = await executor.execute(prepared, current_scene=current)
    assert result.success is False
    assert result.failure_code == FailureCode.STALE_DECISION.value
    assert injector.semantic_calls == 0
    assert injector.physical_calls == 0


@pytest.mark.unit
def test_frame_store_evicts_oldest_frame_and_tracks_bytes():
    store = FrameStore(max_frames=2, max_bytes=6)
    store.put("a", b"aa")
    store.put("b", b"bb")
    store.put("c", b"cc")
    assert "a" not in store
    assert "b" in store and "c" in store
    assert len(store) == 2
    assert store.total_bytes == 4
