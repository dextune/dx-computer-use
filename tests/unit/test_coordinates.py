"""Unit tests for coordinate transforms and spaces."""

import math

import numpy as np
import pytest

pytestmark = pytest.mark.unit

from hpcu.coordinates.transform import (
    compose,
    identity,
    inverse,
    rotation,
    scale,
    transform_bbox,
    transform_point,
    translation,
)
from hpcu.coordinates.spaces import (
    compute_safe_click_point,
    crop_local_roundtrip,
    css_to_viewport,
    from_crop_local,
    logical_to_physical,
    physical_to_logical,
    physical_to_logical_bbox,
    quartz_to_top_left,
    to_crop_local,
    top_left_to_quartz,
    viewport_to_css,
    virtual_to_monitor_local,
    monitor_local_to_virtual,
)
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace, ScreenPoint


# ---- transform primitives ----

def test_identity():
    t = identity()
    assert transform_point(t, 5.0, 10.0) == (5.0, 10.0)


def test_translation():
    t = translation(10.0, -5.0)
    assert transform_point(t, 0.0, 0.0) == (10.0, -5.0)
    assert transform_point(t, 5.0, 5.0) == (15.0, 0.0)


def test_scale():
    t = scale(2.0, 0.5)
    assert transform_point(t, 10.0, 10.0) == (20.0, 5.0)


def test_rotation_90():
    t = rotation(math.pi / 2)
    x, y = transform_point(t, 1.0, 0.0)
    assert abs(x - 0.0) < 1e-10
    assert abs(y - 1.0) < 1e-10


def test_compose():
    t1 = translation(10.0, 0.0)
    t2 = scale(2.0, 2.0)
    t = compose(t1, t2)
    # translate then scale: (10, 0) → (20, 0)
    assert transform_point(t, 0.0, 0.0) == (20.0, 0.0)


def test_inverse():
    t = translation(10.0, 20.0)
    inv = inverse(t)
    x, y = transform_point(inv, 10.0, 20.0)
    assert abs(x - 0.0) < 1e-10
    assert abs(y - 0.0) < 1e-10


def test_transform_bbox_no_rotation():
    t = translation(5.0, 5.0)
    x, y, w, h = transform_bbox(t, 0.0, 0.0, 100.0, 50.0)
    assert x == 5.0
    assert y == 5.0
    assert w == 100.0
    assert h == 50.0


# ---- DPI conversions ----

def test_physical_to_logical_100():
    x, y = physical_to_logical(100.0, 200.0, 100)
    assert x == 100.0
    assert y == 200.0


def test_physical_to_logical_125():
    x, y = physical_to_logical(125.0, 250.0, 125)
    assert x == 100.0
    assert y == 200.0


def test_physical_to_logical_150():
    x, y = physical_to_logical(150.0, 300.0, 150)
    assert x == 100.0
    assert y == 200.0


def test_logical_to_physical_125():
    x, y = logical_to_physical(100.0, 200.0, 125)
    assert x == 125.0
    assert y == 250.0


def test_physical_to_logical_bbox():
    bbox = BoundingBox(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=125, y=250, width=250, height=500)
    result = physical_to_logical_bbox(bbox, 125)
    assert result.x == 100.0
    assert result.y == 200.0
    assert result.width == 200.0
    assert result.height == 400.0
    assert result.space == CoordinateSpace.SCREEN_LOGICAL_PX


# ---- crop-local roundtrip ----

def test_crop_local_roundtrip_within_1px():
    """Crop-local round-trip must be within 1 px."""
    test_cases = [
        (0.0, 0.0, 100.0, 200.0),
        (500.0, 300.0, 50.0, 75.0),
        (1920.0, 1080.0, 0.0, 0.0),
        (1234.0, 567.0, 800.0, 600.0),
    ]
    for x, y, crop_x, crop_y in test_cases:
        rx, ry = crop_local_roundtrip(x, y, crop_x, crop_y)
        assert abs(rx - x) < 1.0, f"x mismatch: {rx} vs {x}"
        assert abs(ry - y) < 1.0, f"y mismatch: {ry} vs {y}"


def test_to_from_crop_local():
    original = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=500, y=300)
    cropped = to_crop_local(original, 100, 200)
    assert cropped.space == CoordinateSpace.CROP_LOCAL_PX
    assert cropped.x == 400.0
    assert cropped.y == 100.0

    restored = from_crop_local(cropped, 100, 200)
    assert restored.space == CoordinateSpace.SCREEN_PHYSICAL_PX
    assert restored.x == 500.0
    assert restored.y == 300.0


# ---- multi-monitor negative origin ----

def test_virtual_to_monitor_local_negative_origin():
    x, y = virtual_to_monitor_local(100.0, 200.0, -1920.0, 0.0)
    assert x == 2020.0
    assert y == 200.0


def test_monitor_local_to_virtual_negative_origin():
    x, y = monitor_local_to_virtual(2020.0, 200.0, -1920.0, 0.0)
    assert x == 100.0
    assert y == 200.0


# ---- devicePixelRatio ----

def test_css_to_viewport():
    x, y = css_to_viewport(100.0, 200.0, 2.0)
    assert x == 200.0
    assert y == 400.0


def test_viewport_to_css():
    x, y = viewport_to_css(200.0, 400.0, 2.0)
    assert x == 100.0
    assert y == 200.0


# ---- macOS Quartz ----

def test_quartz_to_top_left():
    x, y = quartz_to_top_left(100.0, 880.0, 1080.0)
    assert x == 100.0
    assert y == 200.0


def test_top_left_to_quartz():
    x, y = top_left_to_quartz(100.0, 200.0, 1080.0)
    assert x == 100.0
    assert y == 880.0


# ---- safe click point ----

def test_safe_click_point_inside_bbox():
    bbox = BoundingBox(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=0, y=0, width=100, height=50)
    point = compute_safe_click_point(bbox, margin_ratio=0.15)
    margin_x = 15.0
    margin_y = 7.5
    assert point.x >= margin_x
    assert point.x <= 100.0 - margin_x
    assert point.y >= margin_y
    assert point.y <= 50.0 - margin_y


def test_safe_click_point_small_bbox():
    bbox = BoundingBox(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=0, y=0, width=10, height=10)
    point = compute_safe_click_point(bbox)
    assert point.x >= 0
    assert point.x <= 10
    assert point.y >= 0
    assert point.y <= 10