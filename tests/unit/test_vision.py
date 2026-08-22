"""Unit tests for the hpcu.vision perception primitives."""

import pytest

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.scene import FrameHandle
from hpcu.vision.ocr import OcrEngine, TextRegion
from hpcu.vision.shape_detector import ShapeCandidate, ShapeDetector
from hpcu.vision.template_matcher import TemplateMatch, TemplateMatcher
from hpcu.vision.text_component_association import ComponentGroup, associate

SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


def _bbox(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(space=SPACE, x=x, y=y, width=width, height=height)


def _frame(handle_id: str = "frame-1") -> FrameHandle:
    return FrameHandle(
        shm_id=handle_id,
        width=1024,
        height=768,
        stride=1024 * 4,
        pixel_format="BGRA",
        timestamp_ns=1,
        space="screen_physical_px",
    )


# ---------------------------------------------------------------------------
# OcrEngine
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ocr_detect_returns_fixture_regions():
    """Given a registered fixture, detect returns exactly those regions."""
    # Given
    engine = OcrEngine()
    first = TextRegion(text="hello", bbox=_bbox(10, 20, 50, 20), confidence=0.9)
    second = TextRegion(text="world", bbox=_bbox(100, 20, 50, 20), confidence=0.8)
    engine.set_fixture(_frame("f-1"), first, second)

    # When
    result = engine.detect(_frame("f-1"))

    # Then
    assert [region.text for region in result] == ["hello", "world"]
    assert all(region.language == "eng" for region in result)


@pytest.mark.unit
def test_ocr_detect_unknown_frame_is_empty():
    """Given an unregistered frame, detect returns an empty list."""
    # Given
    engine = OcrEngine()

    # When
    result = engine.detect(_frame("unregistered"))

    # Then
    assert result == []


@pytest.mark.unit
def test_ocr_detect_roi_filters_regions():
    """Given an roi, only intersecting regions are returned."""
    # Given
    engine = OcrEngine()
    inside = TextRegion(text="inside", bbox=_bbox(100, 100, 40, 20), confidence=0.7)
    outside = TextRegion(text="outside", bbox=_bbox(500, 500, 40, 20), confidence=0.7)
    engine.set_fixture(_frame("f-2"), inside, outside)
    roi = _bbox(90, 90, 100, 100)

    # When
    result = engine.detect(_frame("f-2"), roi=roi)

    # Then
    assert [region.text for region in result] == ["inside"]


@pytest.mark.unit
def test_ocr_constructor_fixture_is_deterministic():
    """Given a fixture supplied at construction, results are identical."""
    # Given
    region = TextRegion(text="fixed", bbox=_bbox(0, 0, 20, 20), confidence=0.5)
    engine = OcrEngine(fixture={"f-3": (region,)})

    # When
    first_call = engine.detect(_frame("f-3"))
    second_call = engine.detect(_frame("f-3"))

    # Then
    assert first_call == second_call
    assert first_call[0].text == "fixed"


# ---------------------------------------------------------------------------
# ShapeDetector
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_shape_detector_detect_returns_fixture_shapes():
    """Given a registered fixture, detect returns exactly those shapes."""
    # Given
    detector = ShapeDetector()
    rect = ShapeCandidate(bbox=_bbox(0, 0, 100, 60), shape_type="rectangle", confidence=0.9)
    circle = ShapeCandidate(bbox=_bbox(200, 0, 40, 40), shape_type="circle", confidence=0.8)
    detector.set_fixture(_frame("s-1"), rect, circle)

    # When
    result = detector.detect(_frame("s-1"))

    # Then
    assert len(result) == 2
    assert result[0].shape_type == "rectangle"
    assert result[1].shape_type == "circle"


@pytest.mark.unit
def test_shape_detector_roi_filters_shapes():
    """Given an roi, only intersecting shapes are returned."""
    # Given
    detector = ShapeDetector()
    near = ShapeCandidate(bbox=_bbox(10, 10, 30, 30), shape_type="rectangle", confidence=0.9)
    far = ShapeCandidate(bbox=_bbox(600, 600, 30, 30), shape_type="circle", confidence=0.9)
    detector.set_fixture(_frame("s-2"), near, far)

    # When
    result = detector.detect(_frame("s-2"), roi=_bbox(0, 0, 100, 100))

    # Then
    assert result == [near]


@pytest.mark.unit
def test_shape_unknown_type_raises_value_error():
    """Given an unsupported shape_type, construction fails."""
    # Given
    box = _bbox(0, 0, 10, 10)

    # When / Then
    with pytest.raises(ValueError):
        ShapeCandidate(bbox=box, shape_type="hexagon", confidence=0.5)


# ---------------------------------------------------------------------------
# TemplateMatcher
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_template_match_filters_requested_templates():
    """Given a fixture, match returns only requested template ids."""
    # Given
    matcher = TemplateMatcher()
    logo = TemplateMatch(template_id="logo", bbox=_bbox(0, 0, 40, 40), score=0.95)
    submit = TemplateMatch(template_id="submit_btn", bbox=_bbox(300, 100, 80, 30), score=0.7)
    matcher.set_fixture(_frame("t-1"), logo, submit)

    # When
    result = matcher.match(_frame("t-1"), ["logo"])

    # Then
    assert [match.template_id for match in result] == ["logo"]


@pytest.mark.unit
def test_template_match_sorts_by_score_descending():
    """Given multiple requested templates, results are highest score first."""
    # Given
    matcher = TemplateMatcher()
    low = TemplateMatch(template_id="a", bbox=_bbox(0, 0, 10, 10), score=0.4)
    high = TemplateMatch(template_id="b", bbox=_bbox(0, 0, 10, 10), score=0.9)
    matcher.set_fixture(_frame("t-2"), low, high)

    # When
    result = matcher.match(_frame("t-2"), ["a", "b"])

    # Then
    assert [match.template_id for match in result] == ["b", "a"]


@pytest.mark.unit
def test_template_match_unrequested_returns_none():
    """Given only unrequested templates, match returns an empty list."""
    # Given
    matcher = TemplateMatcher()
    matcher.set_fixture(
        _frame("t-3"), TemplateMatch(template_id="other", bbox=_bbox(0, 0, 10, 10), score=0.9)
    )

    # When
    result = matcher.match(_frame("t-3"), ["missing"])

    # Then
    assert result == []


# ---------------------------------------------------------------------------
# TextComponentAssociation
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_associate_text_inside_shape_with_label():
    """Given a shape with inner text and a left label, one group is produced."""
    # Given
    shape = ShapeCandidate(
        bbox=_bbox(100, 100, 200, 100), shape_type="rectangle", confidence=0.9
    )
    inner = TextRegion(text="alice@example.com", bbox=_bbox(120, 120, 120, 30), confidence=0.9)
    label = TextRegion(text="Email", bbox=_bbox(60, 130, 35, 20), confidence=0.9)

    # When
    groups = associate([inner, label], [shape])

    # Then
    assert len(groups) == 1
    group = groups[0]
    assert group.text_inside_shape is inner
    assert group.label is label


@pytest.mark.unit
def test_associate_shape_without_label_no_group():
    """Given text inside a shape but no label, the group still carries inner text."""
    # Given
    shape = ShapeCandidate(
        bbox=_bbox(100, 100, 200, 100), shape_type="rectangle", confidence=0.9
    )
    inner = TextRegion(text="Free text", bbox=_bbox(120, 120, 120, 30), confidence=0.9)

    # When
    groups = associate([inner], [shape])

    # Then
    assert len(groups) == 1
    assert groups[0].text_inside_shape is inner
    assert groups[0].label is None


@pytest.mark.unit
def test_associate_price_below_name():
    """Given a name with a numeric price directly below, they are grouped."""
    # Given
    name = TextRegion(text="Organic Coffee", bbox=_bbox(50, 10, 120, 20), confidence=0.9)
    price = TextRegion(text="$12.50", bbox=_bbox(50, 40, 60, 15), confidence=0.9)

    # When
    groups = associate([name, price], [])

    # Then
    assert len(groups) == 1
    assert groups[0].name is name
    assert groups[0].price is price


@pytest.mark.unit
def test_associate_text_unrelated_left_no_group():
    """Given text not inside a shape and no price relation, no group is produced."""
    # Given
    floating = TextRegion(text="solo", bbox=_bbox(500, 500, 40, 20), confidence=0.9)

    # When
    groups = associate([floating], [])

    # Then
    assert groups == []


@pytest.mark.unit
def test_associate_returns_frozen_component_groups():
    """Given a valid association, the result is a frozen ComponentGroup."""
    # Given
    inner = TextRegion(text="value", bbox=_bbox(120, 120, 40, 20), confidence=0.9)
    shape = ShapeCandidate(
        bbox=_bbox(100, 100, 100, 60), shape_type="rounded_rect", confidence=0.9
    )

    # When
    groups = associate([inner], [shape])

    # Then
    assert isinstance(groups[0], ComponentGroup)
    assert groups[0].text_inside_shape.text == "value"