"""Unit tests for CompositeObserver — capture + structure facade.

Fake backends only; no real OS pixel capture or accessibility trees.
"""

import pytest

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.capture.frame_store import FrameStore
from hpcu.observation.facade import CompositeObserver
from hpcu.schemas.scene import SceneDelta
from hpcu.observation.structure_observer import (
    StructureCapabilities,
    StructureObserver,
)
from hpcu.schemas.capability import Capability
from hpcu.schemas.scene import FrameHandle
from hpcu.schemas.ui_element import UIElement


class FakeCapture(CaptureBackend):
    def __init__(self, session_id: str = "test-session"):
        super().__init__(session_id)
        self.started: int = 0
        self.grabs: int = 0
        self._frame_px = 0

    async def start(self) -> None:
        self.started += 1

    async def grab(self) -> FrameHandle:
        self.grabs += 1
        self._frame_px += 1
        return FrameHandle(
            shm_id=f"shm-{self._frame_px}",
            width=1920,
            height=1080,
            stride=7680,
            pixel_format="BGRA",
            timestamp_ns=self._frame_px,
            space="screen_physical_px",
            source="fake",
        )

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(pixel_grab=Capability.SUPPORTED)


class FakeStructure(StructureObserver):
    def __init__(self, session_id: str = "test-session"):
        super().__init__(session_id)
        self.calls: int = 0

    async def observe_structure(self) -> tuple[UIElement, ...]:
        self.calls += 1
        return (UIElement(id="btn_1", scene_version=self.calls),)

    def capabilities(self) -> StructureCapabilities:
        return StructureCapabilities(tree=Capability.SUPPORTED)


@pytest.mark.unit
async def test_observe_with_only_capture_returns_frame_delta():
    # Given an observer with a capture backend and no structure
    capture = FakeCapture()
    observer = CompositeObserver("test-session", capture_backend=capture)

    # When observe() is called
    delta = await observer.observe()

    # Then the delta carries a frame but an empty structure
    assert isinstance(delta, SceneDelta)
    assert delta.frame is not None
    assert delta.frame.shm_id == "shm-1"
    assert delta.added == ()
    assert delta.scene_version == 1


@pytest.mark.unit
async def test_observe_with_only_structure_returns_tree_delta():
    # Given an observer with a structure observer and no capture
    structure = FakeStructure()
    observer = CompositeObserver("test-session", structure_observer=structure)

    # When observe() is called
    delta = await observer.observe()

    # Then the delta carries structure but no frame
    assert delta.frame is None
    assert len(delta.added) == 1
    assert delta.added[0].id == "btn_1"


@pytest.mark.unit
async def test_observe_composes_capture_and_structure():
    # Given an observer wired to both backends
    capture = FakeCapture()
    structure = FakeStructure()
    observer = CompositeObserver("test-session", capture, structure)

    # When observe() is called
    delta = await observer.observe()

    # Then both pixel and tree data are merged into one delta
    assert delta.frame is not None
    assert len(delta.added) == 1
    assert capture.grabs == 1
    assert structure.calls == 1


@pytest.mark.unit
async def test_observe_increments_scene_version():
    # Given an observer with both backends
    observer = CompositeObserver("test-session", FakeCapture(), FakeStructure())

    # When observed repeatedly
    first = await observer.observe()
    second = await observer.observe()

    # Then the scene version is monotonic across calls
    assert first.scene_version == 1
    assert second.scene_version == 2


@pytest.mark.unit
async def test_capture_start_is_lazy_and_single():
    # Given an observer with a capture backend
    capture = FakeCapture()
    observer = CompositeObserver("test-session", capture_backend=capture)

    # When observed many times
    await observer.observe()
    await observer.observe()

    # Then the capture backend is started exactly once and grabbed twice
    assert capture.started == 1
    assert capture.grabs == 2


@pytest.mark.unit
async def test_observer_with_no_backends_returns_timestamp_delta():
    # Given an observer with no backends at all
    observer = CompositeObserver("test-session")

    # When observe() is called
    delta = await observer.observe()

    # Then an empty delta with a fresh version is returned
    assert delta.frame is None
    assert delta.added == ()
    assert delta.scene_version == 1


@pytest.mark.unit
def test_session_id_mismatch_capture_raises():
    # Given a capture bound to a different session
    capture = FakeCapture(session_id="other-session")

    # When the observer is constructed with it
    # Then the session mismatch is rejected at wiring time
    with pytest.raises(ValueError):
        CompositeObserver("test-session", capture_backend=capture)


@pytest.mark.unit
def test_session_id_mismatch_structure_raises():
    # Given a structure observer bound to a different session
    structure = FakeStructure(session_id="other-session")

    # When the observer is constructed with it
    # Then the session mismatch is rejected at wiring time
    with pytest.raises(ValueError):
        CompositeObserver("test-session", structure_observer=structure)


@pytest.mark.unit
async def test_matching_sessions_are_accepted():
    # Given a capture and structure sharing the observer session
    capture = FakeCapture(session_id="test-session")
    structure = FakeStructure(session_id="test-session")

    # When the observer is constructed
    observer = CompositeObserver("test-session", capture, structure)

    # Then wiring succeeds and observe() produces a merged delta
    delta = await observer.observe()
    assert delta.frame is not None
    assert len(delta.added) == 1


@pytest.mark.unit
async def test_second_observe_marks_removed_when_structure_empty():
    class VanishingStructure(FakeStructure):
        async def observe_structure(self) -> tuple[UIElement, ...]:
            self.calls += 1
            if self.calls == 1:
                return (UIElement(id="btn_1", scene_version=1),)
            return ()

    observer = CompositeObserver("test-session", structure_observer=VanishingStructure())
    first = await observer.observe()
    second = await observer.observe()
    assert first.added[0].id == "btn_1"
    assert second.removed == ("btn_1",)
    assert second.added == ()


@pytest.mark.unit
async def test_fingerprint_keeps_element_id_across_native_id_change():
    class ShiftingStructure(StructureObserver):
        def __init__(self):
            super().__init__("test-session")
            self.calls = 0

        async def observe_structure(self) -> tuple[UIElement, ...]:
            self.calls += 1
            native_id = "native_a" if self.calls == 1 else "native_b"
            return (
                UIElement(
                    id=native_id,
                    scene_version=self.calls,
                    fingerprint="stable-btn",
                    name="OK",
                ),
            )

        def capabilities(self) -> StructureCapabilities:
            return StructureCapabilities(tree=Capability.SUPPORTED)

    observer = CompositeObserver("test-session", structure_observer=ShiftingStructure())
    first = await observer.observe()
    second = await observer.observe()
    assert first.added[0].id == "native_a"
    assert second.added == ()
    assert second.removed == ()
    assert second.modified[0].id == "native_a"


class _FakePerception:
    def elements_from_frame(self, frame, scene_version, roi=None):
        return (
            UIElement(
                id="ocr_line_0",
                scene_version=scene_version,
                text="12,900원",
                name="12,900원",
            ),
        )

    async def elements_from_frame_async(self, frame, scene_version, roi=None):
        return self.elements_from_frame(frame, scene_version, roi=roi)


@pytest.mark.unit
async def test_observe_merges_injected_perception_elements():
    observer = CompositeObserver(
        "test-session",
        FakeCapture(),
        FakeStructure(),
        perception=_FakePerception(),
    )
    delta = await observer.observe()
    ids = {element.id for element in delta.added}
    assert "btn_1" in ids
    assert "ocr_line_0" in ids


@pytest.mark.unit
async def test_auto_perception_with_store_does_not_raise():
    class StoredCapture(FakeCapture):
        def __init__(self):
            super().__init__()
            self.store = FrameStore()

        async def grab(self) -> FrameHandle:
            handle = await super().grab()
            self.store.put(
                handle.shm_id,
                (
                    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
                    b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x01\x00"
                    b"\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
                ),
            )
            return handle

    observer = CompositeObserver("test-session", StoredCapture(), FakeStructure())
    delta = await observer.observe()
    assert delta.frame is not None
    assert any(element.id == "btn_1" for element in delta.added)
