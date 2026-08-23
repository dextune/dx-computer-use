"""Hot-path regression tests for ScreenPerception."""

import asyncio
import threading

import pytest

from hpcu.capture.frame_store import FrameStore
from hpcu.perception import engine
from hpcu.perception.engine import (
    PerceptionQueueFull,
    ScreenPerception,
    elements_from_ocr,
)
from hpcu.runtime_core.resource_policy import LocalResourcePolicy
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.scene import FrameHandle
from hpcu.vision.ocr import TextRegion

pytestmark = pytest.mark.unit
SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


def _box(x: float, y: float, width: float = 30) -> BoundingBox:
    return BoundingBox(SPACE, x, y, width, 12)


def _region(text: str, x: float, y: float) -> TextRegion:
    return TextRegion(text=text, bbox=_box(x, y), confidence=0.9)


def _frame(
    frame_id: str,
    timestamp: int,
    *,
    dirty: tuple[BoundingBox, ...] = (),
) -> FrameHandle:
    return FrameHandle(
        shm_id=frame_id,
        width=200,
        height=100,
        stride=800,
        pixel_format="BGRA",
        timestamp_ns=timestamp,
        space=SPACE,
        dirty_rects=dirty,
        source="fake",
    )


def _config() -> dict:
    return {
        "perception": {
            "ocr_min_regions_for_dense": 1,
            "ocr_cache_entries": 4,
            "dirty_roi_max_ratio": 0.5,
        },
        "performance": {
            "max_perception_workers": 1,
            "max_perception_queue_depth": 2,
        },
    }


def test_ocr_identity_is_stable_across_scene_versions():
    first = elements_from_ocr([_region("가격 12,000원", 10, 10)], 1)[0]
    second = elements_from_ocr([_region("가격 12,000원", 10, 10)], 2)[0]

    assert first.id == second.id
    assert first.fingerprint == second.fingerprint
    assert first.scene_version == 1
    assert second.scene_version == 2


def test_identical_png_content_reuses_cached_ocr(monkeypatch):
    calls = 0

    def fake_detect(png, *, languages, psm):
        nonlocal calls
        del png, languages, psm
        calls += 1
        return [_region("cached", 10, 10)]

    monkeypatch.setattr(engine, "detect_png", fake_detect)
    store = FrameStore()
    store.put("first", b"same-png")
    store.put("second", b"same-png")
    perception = ScreenPerception(store, config=_config())

    first = perception.elements_from_frame(_frame("first", 1), 1)
    second = perception.elements_from_frame(_frame("second", 2), 2)

    assert calls == 1
    assert first[0].id == second[0].id
    assert second[0].scene_version == 2
    assert perception.performance_snapshot.as_dict()["ocr_cache_hit"][
        "count"
    ] == 1


def test_small_dirty_roi_preserves_unchanged_ocr(monkeypatch):
    responses = iter(
        [
            [_region("left", 10, 10), _region("right", 100, 50)],
            [_region("left-new", 10, 10)],
        ]
    )

    def fake_detect(png, *, languages, psm):
        del png, languages, psm
        return next(responses)

    monkeypatch.setattr(engine, "detect_png", fake_detect)
    monkeypatch.setattr(
        engine,
        "crop_png",
        lambda png, roi: (png, roi.x, roi.y),
    )
    store = FrameStore()
    store.put("first", b"png-one")
    store.put("second", b"png-two")
    perception = ScreenPerception(store, config=_config())

    first = perception.elements_from_frame(_frame("first", 1), 1)
    second = perception.elements_from_frame(
        _frame("second", 2, dirty=(_box(0, 0, 50),)),
        2,
    )

    assert {item.text for item in first} == {"left", "right"}
    assert {item.text for item in second} == {"left-new", "right"}
    assert all(item.scene_version == 2 for item in second)


@pytest.mark.asyncio
async def test_bounded_worker_queue_rejects_excess_work(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    store = FrameStore()
    store.put("frame", b"png")
    policy = LocalResourcePolicy(
        cpu_count=2,
        reserve_cores=1,
        perception_workers=1,
        perception_queue_depth=1,
    )
    perception = ScreenPerception(
        store,
        config=_config(),
        resource_policy=policy,
    )

    def slow_elements(frame, scene_version, roi=None):
        del frame, scene_version, roi
        started.set()
        release.wait(timeout=1)
        return ()

    monkeypatch.setattr(perception, "elements_from_frame", slow_elements)
    first = asyncio.create_task(
        perception.elements_from_frame_async(_frame("frame", 1), 1)
    )
    assert await asyncio.to_thread(started.wait, 1) is True

    with pytest.raises(PerceptionQueueFull, match="queue"):
        await perception.elements_from_frame_async(_frame("frame", 2), 2)

    release.set()
    await first
