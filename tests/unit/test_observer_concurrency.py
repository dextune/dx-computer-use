"""Concurrency and timeout contracts for CompositeObserver."""

import asyncio

import pytest

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.observation.facade import CompositeObserver, ObservationTransactionTimeout
from hpcu.observation.structure_observer import (
    StructureCapabilities,
    StructureObserver,
)
from hpcu.schemas.capability import Capability
from hpcu.schemas.scene import FrameHandle
from hpcu.schemas.ui_element import UIElement

pytestmark = pytest.mark.unit


class _Rendezvous:
    def __init__(self) -> None:
        self.arrivals = 0
        self.ready = asyncio.Event()

    async def arrive(self) -> None:
        self.arrivals += 1
        if self.arrivals == 2:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=0.2)


class _Capture(CaptureBackend):
    def __init__(self, rendezvous: _Rendezvous | None = None) -> None:
        super().__init__("session")
        self.rendezvous = rendezvous
        self.frames = 0

    async def start(self) -> None:
        return None

    async def grab(self) -> FrameHandle:
        if self.rendezvous is not None:
            await self.rendezvous.arrive()
        self.frames += 1
        return FrameHandle(
            shm_id=f"frame-{self.frames}",
            width=100,
            height=100,
            stride=400,
            pixel_format="BGRA",
            timestamp_ns=self.frames,
            space="screen_physical_px",
            source="fake",
        )

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(pixel_grab=Capability.SUPPORTED)


class _Structure(StructureObserver):
    def __init__(self, rendezvous: _Rendezvous | None = None) -> None:
        super().__init__("session")
        self.rendezvous = rendezvous
        self.calls = 0

    async def observe_structure(self) -> tuple[UIElement, ...]:
        if self.rendezvous is not None:
            await self.rendezvous.arrive()
        self.calls += 1
        return (UIElement(id="button", scene_version=self.calls),)

    def capabilities(self) -> StructureCapabilities:
        return StructureCapabilities(tree=Capability.SUPPORTED)


class _SerialCapture(_Capture):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0

    async def grab(self) -> FrameHandle:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            return await super().grab()
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_capture_and_structure_start_in_parallel():
    rendezvous = _Rendezvous()
    observer = CompositeObserver(
        "session",
        _Capture(rendezvous),
        _Structure(rendezvous),
    )

    delta = await observer.observe()

    assert rendezvous.arrivals == 2
    assert delta.frame is not None
    assert delta.added[0].id == "button"
    metrics = observer.performance_snapshot.as_dict()
    assert metrics["capture"]["count"] == 1
    assert metrics["structure"]["count"] == 1
    assert metrics["capture_structure_transaction"]["count"] == 1


@pytest.mark.asyncio
async def test_concurrent_observe_calls_serialize_scene_versions():
    capture = _SerialCapture()
    observer = CompositeObserver("session", capture, _Structure())

    first, second = await asyncio.gather(
        observer.observe(),
        observer.observe(),
    )

    assert sorted((first.scene_version, second.scene_version)) == [1, 2]
    assert observer.capture_backend.frames == 2
    assert capture.max_active == 1


@pytest.mark.asyncio
async def test_observation_timeout_cancels_transaction():
    class _BlockedCapture(_Capture):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = asyncio.Event()

        async def grab(self) -> FrameHandle:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()
            raise AssertionError("unreachable")

    capture = _BlockedCapture()
    observer = CompositeObserver(
        "session",
        capture,
        _Structure(),
        config={"performance": {"observe_timeout_ms": 1}},
    )

    with pytest.raises(ObservationTransactionTimeout, match="transaction"):
        await observer.observe()

    assert capture.cancelled.is_set()
    metrics = observer.performance_snapshot.as_dict()
    assert metrics["capture_structure_transaction"]["timeout_count"] == 1
    assert metrics["capture_structure_transaction"]["error_count"] == 0


@pytest.mark.asyncio
async def test_backend_timeout_is_not_reclassified_as_observer_deadline():
    class _TimeoutCapture(_Capture):
        async def grab(self) -> FrameHandle:
            raise TimeoutError("capture backend timed out")

    observer = CompositeObserver(
        "session",
        _TimeoutCapture(),
        _Structure(),
        config={"performance": {"observe_timeout_ms": 100}},
    )

    with pytest.raises(TimeoutError, match="capture backend timed out") as exc_info:
        await observer.observe()

    assert not isinstance(exc_info.value, ObservationTransactionTimeout)
    metrics = observer.performance_snapshot.as_dict()
    assert metrics["capture"]["timeout_count"] == 1
    assert metrics["capture_structure_transaction"]["timeout_count"] == 0
    assert metrics["capture_structure_transaction"]["error_count"] == 1


@pytest.mark.asyncio
async def test_non_positive_observation_timeout_is_rejected():
    observer = CompositeObserver(
        "session",
        _Capture(),
        _Structure(),
        config={"performance": {"observe_timeout_ms": 0}},
    )

    with pytest.raises(ValueError, match="positive"):
        await observer.observe()
