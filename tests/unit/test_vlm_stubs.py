"""Unit tests for VLM stubs (Phase 5)."""

import pytest

pytestmark = pytest.mark.unit

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace, ScreenPoint
from hpcu.schemas.ui_element import UIElement
from hpcu.vision.vlm_stubs import (
    CropTransform, LargeVlmFallback, ObjectMark, SmallVlmSelector,
    SoMOverlay, VisualVerifier, VlmCandidateSelection, ZoomRequest,
)


# ---- SoMOverlay ----

def test_som_generate_marks():
    overlay = SoMOverlay()
    elements = [
        UIElement(
            id="btn_1", scene_version=1, role="button", name="Submit",
            bbox=BoundingBox(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=0, y=0, width=100, height=40),
        ),
        UIElement(
            id="btn_2", scene_version=1, role="button", name="Cancel",
            bbox=BoundingBox(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=120, y=0, width=100, height=40),
        ),
    ]
    marks = overlay.generate_marks(elements)
    assert len(marks) == 2
    assert marks[0].number == 1
    assert marks[0].element_id == "btn_1"
    assert marks[1].number == 2


def test_som_no_bbox_skipped():
    overlay = SoMOverlay()
    elements = [
        UIElement(id="no_bbox", scene_version=1, role="button"),
    ]
    marks = overlay.generate_marks(elements)
    assert len(marks) == 0


def test_build_marked_image():
    overlay = SoMOverlay()
    marks = [
        ObjectMark(number=1, element_id="a", bbox=BoundingBox(
            space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=0, y=0, width=10, height=10)),
    ]
    img = overlay.build_marked_image(marks, 1920, 1080)
    assert img.width == 1920
    assert img.height == 1080
    assert len(img.marks) == 1


# ---- CropTransform ----

def test_crop_transform_to_original():
    ct = CropTransform()
    crop_point = ScreenPoint(space=CoordinateSpace.CROP_LOCAL_PX, x=50, y=30)
    origin = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=100, y=200)
    result = ct.to_original(crop_point, origin)
    assert result.x == 150.0
    assert result.y == 230.0
    assert result.space == CoordinateSpace.SCREEN_PHYSICAL_PX


def test_crop_transform_to_crop():
    ct = CropTransform()
    original = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=150, y=230)
    origin = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=100, y=200)
    result = ct.to_crop(original, origin, 500, 500)
    assert result.x == 50.0
    assert result.y == 30.0
    assert result.space == CoordinateSpace.CROP_LOCAL_PX


# ---- ZoomRequest ----

def test_zoom_request():
    region = BoundingBox(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=0, y=0, width=100, height=100)
    zr = ZoomRequest(region=region, zoom_level=3.0, reason="small text")
    assert zr.zoom_level == 3.0
    assert zr.reason == "small text"


# ---- SmallVlmSelector ----

def test_small_vlm_selector_returns_none():
    selector = SmallVlmSelector()
    result = selector.select(None, "prompt")  # type: ignore
    assert result is None


# ---- LargeVlmFallback ----

def test_large_vlm_fallback_returns_none():
    fallback = LargeVlmFallback()
    result = fallback.search(None, "prompt")  # type: ignore
    assert result is None


# ---- VisualVerifier ----

def test_visual_verifier_returns_true():
    verifier = VisualVerifier()
    el = UIElement(id="btn_1", scene_version=1)
    result = verifier.verify(None, None, el)  # type: ignore
    assert result is True