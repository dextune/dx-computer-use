"""Integration-style unit coverage for observer-side cross-source fusion."""

import pytest

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.observation.facade import CompositeObserver
from hpcu.observation.structure_observer import (
    StructureCapabilities,
    StructureObserver,
)
from hpcu.perception.fusion import FusionEngine, FusionPolicy
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.scene import FrameHandle
from hpcu.schemas.ui_element import ElementSource, UIElement

pytestmark = pytest.mark.unit


def _box(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(
        space=CoordinateSpace.SCREEN_PHYSICAL_PX,
        x=x,
        y=y,
        width=width,
        height=height,
    )


class _Capture(CaptureBackend):
    def __init__(self) -> None:
        super().__init__("fusion-session")

    async def start(self) -> None:
        return None

    async def grab(self) -> FrameHandle:
        return FrameHandle(
            shm_id="frame",
            width=800,
            height=600,
            stride=3200,
            pixel_format="BGRA",
            timestamp_ns=1,
            space=CoordinateSpace.SCREEN_PHYSICAL_PX,
            source="fake",
        )

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(pixel_grab=Capability.SUPPORTED)


class _Structure(StructureObserver):
    def __init__(self) -> None:
        super().__init__("fusion-session")

    async def observe_structure(self) -> tuple[UIElement, ...]:
        return (
            UIElement(
                id="native_save",
                scene_version=1,
                role="button",
                name="Save",
                bbox=_box(100, 100, 100, 40),
                sources=(ElementSource(type="atspi", ref="save"),),
            ),
        )

    def capabilities(self) -> StructureCapabilities:
        return StructureCapabilities(tree=Capability.SUPPORTED)


class _Perception:
    async def elements_from_frame_async(
        self,
        frame: FrameHandle,
        scene_version: int,
        roi=None,
    ) -> tuple[UIElement, ...]:
        return (
            UIElement(
                id="ocr_save",
                scene_version=scene_version,
                role="text",
                text="Save",
                bbox=_box(120, 110, 50, 20),
                sources=(ElementSource(type="ocr", confidence=0.85),),
            ),
        )


def _fusion() -> FusionEngine:
    return FusionEngine(
        FusionPolicy(
            min_score=0.72,
            min_geometry_overlap=0.50,
            min_assignment_margin=0.08,
            max_center_distance_px=96,
            spatial_cell_size_px=128,
            text_max_chars=160,
            fingerprint_quantum_px=4,
            weight_iou=0.22,
            weight_containment=0.18,
            weight_center=0.14,
            weight_text=0.22,
            weight_role=0.14,
            weight_source_ref=0.10,
            source_reliability={
                "atspi": 0.93,
                "ocr": 0.55,
            },
        )
    )


async def test_observer_fuses_structure_and_ocr_before_scene_diff():
    observer = CompositeObserver(
        "fusion-session",
        capture_backend=_Capture(),
        structure_observer=_Structure(),
        perception=_Perception(),
        fusion_engine=_fusion(),
    )

    delta = await observer.observe()

    assert len(delta.added) == 1
    element = delta.added[0]
    assert element.id == "native_save"
    assert element.role == "button"
    assert {source.type for source in element.sources} == {"atspi", "ocr"}
