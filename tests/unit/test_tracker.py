"""Unit tests for scene-graph element tracking."""

import pytest

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.ui_element import UIElement
from hpcu.scene_graph.tracker import ElementTracker, _bounding_box_iou

SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


def _element(handle_id: str, *, bbox=None, fingerprint=None, version: int = 1) -> UIElement:
    return UIElement(
        id=handle_id,
        scene_version=version,
        role="control",
        bbox=bbox,
        fingerprint=fingerprint,
    )


def _bbox(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(space=SPACE, x=x, y=y, width=width, height=height)


# ---------------------------------------------------------------------------
# match_by_fingerprint
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_match_by_fingerprint_matches_same_fingerprint():
    """Given matching fingerprints, current ids map to previous ids."""
    # Given
    current = {
        "c1": _element("c1", fingerprint="fp-a"),
        "c2": _element("c2", fingerprint="fp-b"),
    }
    previous = {"p1": _element("p1", fingerprint="fp-a")}

    # When
    match = ElementTracker().match_by_fingerprint(current, previous)

    # Then
    assert match == {"c1": "p1"}


@pytest.mark.unit
def test_match_by_fingerprint_ignores_elements_without_fingerprint():
    """Given elements without a fingerprint, they are not matched."""
    # Given
    current = {"c1": _element("c1", fingerprint=None)}
    previous = {"p1": _element("p1", fingerprint=None)}

    # When
    match = ElementTracker().match_by_fingerprint(current, previous)

    # Then
    assert match == {}


@pytest.mark.unit
def test_match_by_fingerprint_is_one_to_one():
    """Given duplicate fingerprints, each current id maps to a distinct previous id."""
    # Given
    current = {"c1": _element("c1", fingerprint="fp-x"), "c2": _element("c2", fingerprint="fp-x")}
    previous = {"p1": _element("p1", fingerprint="fp-x"), "p2": _element("p2", fingerprint="fp-x")}

    # When
    match = ElementTracker().match_by_fingerprint(current, previous)

    # Then
    assert set(match) == {"c1", "c2"}
    assert len(set(match.values())) == 2


# ---------------------------------------------------------------------------
# match_by_iou
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_match_by_iou_matches_overlapping_elements():
    """Given overlapping bounding boxes, current ids map to previous ids."""
    # Given
    current = {"c1": _element("c1", bbox=_bbox(0, 0, 100, 50))}
    previous = {"p1": _element("p1", bbox=_bbox(10, 0, 100, 50))}

    # When
    match = ElementTracker().match_by_iou(current, previous)

    # Then
    assert match == {"c1": "p1"}


@pytest.mark.unit
def test_match_by_iou_no_overlap_is_empty():
    """Given non-overlapping bounding boxes, no match is returned."""
    # Given
    current = {"c1": _element("c1", bbox=_bbox(0, 0, 10, 10))}
    previous = {"p1": _element("p1", bbox=_bbox(1000, 1000, 10, 10))}

    # When
    match = ElementTracker().match_by_iou(current, previous)

    # Then
    assert match == {}


@pytest.mark.unit
def test_match_by_iou_hungarian_finds_optimal_assignment():
    """Given a set of boxes, Hungarian yields the highest-overlap pairing."""
    # Given — each current element overlaps a distinct previous element.
    current = {
        "a": _element("a", bbox=_bbox(0, 0, 50, 50)),
        "b": _element("b", bbox=_bbox(100, 100, 50, 50)),
        "c": _element("c", bbox=_bbox(200, 200, 50, 50)),
    }
    previous = {
        "x": _element("x", bbox=_bbox(0, 0, 50, 50)),
        "y": _element("y", bbox=_bbox(100, 100, 50, 50)),
        "z": _element("z", bbox=_bbox(200, 200, 50, 50)),
    }

    # When
    match = ElementTracker().match_by_iou(current, previous)

    # Then
    assert match == {"a": "x", "b": "y", "c": "z"}


@pytest.mark.unit
def test_match_by_iou_empty_inputs():
    """Given empty input scenes, no matches are returned."""
    # Given
    element = _element("c1", bbox=_bbox(0, 0, 10, 10))

    # When
    with_empty_previous = ElementTracker().match_by_iou({"c1": element}, {})
    with_empty_current = ElementTracker().match_by_iou({}, {"p1": element})

    # Then
    assert with_empty_previous == {}
    assert with_empty_current == {}


@pytest.mark.unit
def test_bounding_box_iou_returns_expected_values():
    """Given two boxes, IoU is computed correctly."""
    # When / Then
    assert _bounding_box_iou(_bbox(0, 0, 10, 10), _bbox(0, 0, 10, 10)) == pytest.approx(1.0)
    assert _bounding_box_iou(_bbox(0, 0, 10, 10), _bbox(10, 0, 10, 10)) == pytest.approx(0.0)
    half = _bounding_box_iou(_bbox(0, 0, 10, 10), _bbox(5, 0, 10, 10))
    assert half == pytest.approx(0.3333333, rel=1e-3)


# ---------------------------------------------------------------------------
# track_new_elements
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_track_new_elements_reports_unmatched_current_ids():
    """Given a matchable and a new element, only the new one is reported."""
    # Given
    current = {
        "c1": _element("c1", fingerprint="fp-a"),
        "c2": _element("c2", fingerprint="fp-new"),
    }
    previous = {"p1": _element("p1", fingerprint="fp-a")}

    # When
    new_elements = ElementTracker().track_new_elements(current, previous)

    # Then
    assert new_elements == ["c2"]