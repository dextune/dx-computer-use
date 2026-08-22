"""Coordinate space registry and conversions.

All space conversions go through this module.  No other module
should perform ad-hoc coordinate math.
"""

from typing import Optional

from hpcu.coordinates.transform import compose, inverse, scale, transform_point, translation
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace, ScreenPoint

# ---------------------------------------------------------------------------
# DPI scale factors
# ---------------------------------------------------------------------------

# physical_px → logical_px = 1 / dpi_scale
# logical_px → physical_px = dpi_scale
_DPI_SCALES: dict[int, float] = {
    100: 1.0,
    125: 1.25,
    150: 1.50,
    175: 1.75,
    200: 2.0,
}


def register_dpi_scale(percent: int, scale: float) -> None:
    """Register a custom DPI scale factor."""
    _DPI_SCALES[percent] = scale


def get_dpi_scale(percent: int) -> float:
    """Get the scale factor for a DPI percentage."""
    return _DPI_SCALES.get(percent, percent / 100.0)


def physical_to_logical(
    x: float, y: float, dpi_percent: int = 100
) -> tuple[float, float]:
    """Convert physical screen pixels to logical (DPI-independent) pixels."""
    s = get_dpi_scale(dpi_percent)
    return (x / s, y / s)


def logical_to_physical(
    x: float, y: float, dpi_percent: int = 100
) -> tuple[float, float]:
    """Convert logical (DPI-independent) pixels to physical screen pixels."""
    s = get_dpi_scale(dpi_percent)
    return (x * s, y * s)


def physical_to_logical_bbox(
    bbox: BoundingBox, dpi_percent: int = 100
) -> BoundingBox:
    """Convert a bounding box from physical to logical coordinates."""
    x, y = physical_to_logical(bbox.x, bbox.y, dpi_percent)
    w, h = physical_to_logical(bbox.width, bbox.height, dpi_percent)
    return BoundingBox(space=CoordinateSpace.SCREEN_LOGICAL_PX, x=x, y=y, width=w, height=h)


def logical_to_physical_bbox(
    bbox: BoundingBox, dpi_percent: int = 100
) -> BoundingBox:
    """Convert a bounding box from logical to physical coordinates."""
    x, y = logical_to_physical(bbox.x, bbox.y, dpi_percent)
    w, h = logical_to_physical(bbox.width, bbox.height, dpi_percent)
    return BoundingBox(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=x, y=y, width=w, height=h)


# ---------------------------------------------------------------------------
# crop-local ↔ original
# ---------------------------------------------------------------------------

def to_crop_local(
    point: ScreenPoint, crop_origin_x: float, crop_origin_y: float
) -> ScreenPoint:
    """Convert a point from original coordinates to crop-local coordinates."""
    return ScreenPoint(
        space=CoordinateSpace.CROP_LOCAL_PX,
        x=point.x - crop_origin_x,
        y=point.y - crop_origin_y,
    )


def from_crop_local(
    point: ScreenPoint, crop_origin_x: float, crop_origin_y: float
) -> ScreenPoint:
    """Convert a point from crop-local coordinates back to original coordinates."""
    return ScreenPoint(
        space=CoordinateSpace.SCREEN_PHYSICAL_PX,
        x=point.x + crop_origin_x,
        y=point.y + crop_origin_y,
    )


def crop_local_roundtrip(
    x: float, y: float, crop_origin_x: float, crop_origin_y: float
) -> tuple[float, float]:
    """Round-trip a point through crop-local and back, verifying precision."""
    original = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=x, y=y)
    cropped = to_crop_local(original, crop_origin_x, crop_origin_y)
    restored = from_crop_local(cropped, crop_origin_x, crop_origin_y)
    return (restored.x, restored.y)


# ---------------------------------------------------------------------------
# multi-monitor negative origin
# ---------------------------------------------------------------------------

def virtual_to_monitor_local(
    x: float, y: float, monitor_origin_x: float, monitor_origin_y: float
) -> tuple[float, float]:
    """Convert virtual-desktop coordinates to monitor-local coordinates.

    Multi-monitor setups may have negative origin monitors.
    """
    return (x - monitor_origin_x, y - monitor_origin_y)


def monitor_local_to_virtual(
    x: float, y: float, monitor_origin_x: float, monitor_origin_y: float
) -> tuple[float, float]:
    """Convert monitor-local coordinates back to virtual-desktop coordinates."""
    return (x + monitor_origin_x, y + monitor_origin_y)


# ---------------------------------------------------------------------------
# devicePixelRatio (browser)
# ---------------------------------------------------------------------------

def css_to_viewport(x: float, y: float, device_pixel_ratio: float = 1.0) -> tuple[float, float]:
    """Convert CSS pixels to viewport (physical) pixels."""
    return (x * device_pixel_ratio, y * device_pixel_ratio)


def viewport_to_css(x: float, y: float, device_pixel_ratio: float = 1.0) -> tuple[float, float]:
    """Convert viewport (physical) pixels to CSS pixels."""
    return (x / device_pixel_ratio, y / device_pixel_ratio)


# ---------------------------------------------------------------------------
# macOS Quartz bottom-left → top-left
# ---------------------------------------------------------------------------

def quartz_to_top_left(x: float, y: float, screen_height: float) -> tuple[float, float]:
    """Convert Quartz (bottom-left origin) to top-left origin."""
    return (x, screen_height - y)


def top_left_to_quartz(x: float, y: float, screen_height: float) -> tuple[float, float]:
    """Convert top-left origin to Quartz (bottom-left origin)."""
    return (x, screen_height - y)


# ---------------------------------------------------------------------------
# safe click point
# ---------------------------------------------------------------------------

def compute_safe_click_point(
    bbox: BoundingBox, margin_ratio: float = 0.15
) -> ScreenPoint:
    """Compute a safe click point inside a bounding box.

    Never clicks the absolute center of the bbox.  Instead, the point
    is shifted slightly inward from the center to avoid edges and
    overlapping elements.

    Args:
        bbox: The element's bounding box.
        margin_ratio: Fraction of width/height to use as margin from edges.

    Returns:
        A ScreenPoint in the same space as the bbox.
    """
    margin_x = bbox.width * margin_ratio
    margin_y = bbox.height * margin_ratio

    cx = bbox.x + bbox.width / 2.0
    cy = bbox.y + bbox.height / 2.0

    safe_x = max(bbox.x + margin_x, min(bbox.x + bbox.width - margin_x, cx))
    safe_y = max(bbox.y + margin_y, min(bbox.y + bbox.height - margin_y, cy))

    return ScreenPoint(space=bbox.space, x=safe_x, y=safe_y)