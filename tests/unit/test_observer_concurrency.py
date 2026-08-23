"""Concurrency and timeout contracts for CompositeObserver."""

import asyncio

import pytest

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.observation.facade import CompositeObserver
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
    observer = CompositeObserver("session", _Capture(), _Structure())

    first, second = await asyncio.gather(
        observer.observe(),
        observer.observe(),
    )

    assert sorted((first.scene_version, second.scene_version)) == [1, 2]
    assert observer.capture_backend.frames == 2


@pytest.mark.asyncio
async def test_observation_timeout_cancels_transaction():
    class _BlockedCapture(_Capture):
        async def grab(self) -> FrameHandle:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    observer = CompositeObserver(
        "session",
        _BlockedCapture(),
        _Structure(),
        config={"performance": {"observe_timeout_ms": 1}},
    )

    with pytest.raises(TimeoutError, match="transaction"):
        await observer.observe()
