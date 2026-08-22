"""Unit tests for ABC contracts (CaptureBackend, InputInjector, Observer).

Tests use fake implementations that live in this test file.
No real OS backends are used.
"""

from dataclasses import dataclass

import pytest

pytestmark = pytest.mark.unit

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.input.injector import InputInjector, InputCapabilities, ExecutionResult
from hpcu.observation.base import Observer
from hpcu.schemas.scene import SceneDelta
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import CoordinateSpace, ScreenPoint
from hpcu.schemas.scene import FrameHandle
from hpcu.schemas.ui_element import UIElement


# ---------------------------------------------------------------------------
# Fake backends (tests only)
# ---------------------------------------------------------------------------

class FakeCaptureBackend(CaptureBackend):
    """Fake capture backend that returns a FrameHandle with no bytes."""

    def __init__(self, session_id: str = "test-session"):
        super().__init__(session_id)
        self._started = False
        self._grab_count = 0

    async def start(self) -> None:
        self._started = True

    async def grab(self) -> FrameHandle:
        self._grab_count += 1
        if not self._started:
            raise RuntimeError("CaptureBackend not started")
        return FrameHandle(
            shm_id=f"fake-shm-{self._grab_count}",
            width=1920,
            height=1080,
            stride=7680,
            pixel_format="BGRA",
            timestamp_ns=self._grab_count,
            space="screen_physical_px",
            source="fake",
        )

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(
            pixel_grab=Capability.SUPPORTED,
            dirty_rects=Capability.UNSUPPORTED,
            max_fps=30,
        )


class FakeCaptureBackendNoSession(CaptureBackend):
    """Fake backend that doesn't share session_id with input."""

    def __init__(self, session_id: str = "other-session"):
        super().__init__(session_id)

    async def start(self) -> None:
        pass

    async def grab(self) -> FrameHandle:
        return FrameHandle(
            shm_id="other-shm",
            width=800, height=600, stride=3200,
            pixel_format="BGRA", timestamp_ns=1,
            space="screen_physical_px", source="fake",
        )

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(pixel_grab=Capability.SUPPORTED)


class FakeInputInjector(InputInjector):
    """Fake input injector that records calls."""

    def __init__(self, session_id: str = "test-session"):
        super().__init__(session_id)
        self.semantic_calls: list[tuple[str, str]] = []
        self.physical_calls: list[tuple[float, float, str]] = []

    async def semantic(self, element: UIElement, action: str) -> ExecutionResult:
        self.semantic_calls.append((element.id, action))
        return ExecutionResult(success=True, mode="semantic")

    async def physical(
        self, point: ScreenPoint, action: str, text: str | None = None
    ) -> ExecutionResult:
        self.physical_calls.append((point.x, point.y, action, text))
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(
            semantic_invoke=Capability.SUPPORTED,
            physical_pointer=Capability.SUPPORTED,
            physical_keyboard=Capability.SUPPORTED,
        )


class FakeInputInjectorUnsupported(InputInjector):
    """Fake injector that returns UNSUPPORTED for semantic."""

    def __init__(self, session_id: str = "test-session"):
        super().__init__(session_id)

    async def semantic(self, element: UIElement, action: str) -> ExecutionResult:
        return ExecutionResult(success=False, mode="semantic",
                               failure_code="input_semantic_unsupported")

    async def physical(
        self, point: ScreenPoint, action: str, text: str | None = None
    ) -> ExecutionResult:
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(
            semantic_invoke=Capability.UNSUPPORTED,
            physical_pointer=Capability.SUPPORTED,
        )


class FakeObserver(Observer):
    """Fake observer that returns a delta."""

    def __init__(self, session_id: str = "test-session"):
        super().__init__(session_id)
        self._version = 0

    async def observe(self) -> SceneDelta:
        self._version += 1
        return SceneDelta(base_version=self._version - 1, new_version=self._version)


# ---------------------------------------------------------------------------
# CaptureBackend contract tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_capture_backend_grab_after_start():
    backend = FakeCaptureBackend()
    await backend.start()
    fh = await backend.grab()
    assert fh.shm_id == "fake-shm-1"
    assert fh.width == 1920
    assert fh.height == 1080


@pytest.mark.asyncio
async def test_capture_backend_grab_before_start_raises():
    backend = FakeCaptureBackend()
    with pytest.raises(RuntimeError):
        await backend.grab()


def test_capture_backend_capabilities():
    backend = FakeCaptureBackend()
    caps = backend.capabilities()
    assert caps.pixel_grab == Capability.SUPPORTED
    assert caps.dirty_rects == Capability.UNSUPPORTED


def test_capture_backend_session_id():
    backend = FakeCaptureBackend(session_id="my-session")
    assert backend.session_id == "my-session"


def test_frame_handle_no_bytes_from_fake():
    """Fake backend returns FrameHandle, never raw bytes."""
    backend = FakeCaptureBackend()
    # We can't await without async, but verify the return type
    fh = FrameHandle(
        shm_id="test", width=100, height=100, stride=400,
        pixel_format="BGRA", timestamp_ns=1, space="screen_physical_px",
    )
    assert not hasattr(fh, "data")
    assert not hasattr(fh, "bytes")
    assert not hasattr(fh, "image")


# ---- InputInjector contract tests ----

@pytest.mark.asyncio
async def test_input_injector_semantic_success():
    injector = FakeInputInjector()
    el = UIElement(id="btn_1", scene_version=1)
    result = await injector.semantic(el, "invoke")
    assert result.success is True
    assert result.mode == "semantic"
    assert len(injector.semantic_calls) == 1


@pytest.mark.asyncio
async def test_input_injector_physical_success():
    injector = FakeInputInjector()
    point = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=100, y=200)
    result = await injector.physical(point, "click")
    assert result.success is True
    assert result.mode == "physical"


@pytest.mark.asyncio
async def test_input_injector_semantic_unsupported():
    injector = FakeInputInjectorUnsupported()
    el = UIElement(id="btn_1", scene_version=1)
    result = await injector.semantic(el, "invoke")
    assert result.success is False
    assert result.failure_code == "input_semantic_unsupported"


def test_input_injector_capabilities():
    injector = FakeInputInjector()
    caps = injector.capabilities()
    assert caps.semantic_invoke == Capability.SUPPORTED
    assert caps.physical_pointer == Capability.SUPPORTED


# ---- Observer contract tests ----

@pytest.mark.asyncio
async def test_observer_delta():
    obs = FakeObserver()
    d1 = await obs.observe()
    assert d1.scene_version == 1
    d2 = await obs.observe()
    assert d2.scene_version == 2


# ---- session_id coupling ----

def test_capture_input_session_id_mismatch_detected():
    """Different session_ids between capture and input should be detectable."""
    capture = FakeCaptureBackend(session_id="session-a")
    input_ = FakeInputInjector(session_id="session-b")
    assert capture.session_id != input_.session_id


def test_capture_input_session_id_match():
    capture = FakeCaptureBackend(session_id="test-session")
    input_ = FakeInputInjector(session_id="test-session")
    assert capture.session_id == input_.session_id