"""Window-chrome targeting from structure regions only.

Adapters supply window rectangles. This module never talks to an OS.
Window selection is geometry-only: area + aspect ratio, no brand strings.
"""

from typing import Optional

from hpcu.schemas.coordinates import BoundingBox, ScreenPoint
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement


def primary_window(scene: Scene) -> Optional[UIElement]:
    """Largest client-area-like window by geometry alone.

    Filters out tiny windows, panels, and full-screen desktop overlays
    using area and aspect ratio.  No brand hints.
    """
    windows = [
        element
        for element in scene.elements.values()
        if element.bbox is not None
        and element.bbox.width >= 200
        and element.bbox.height >= 120
    ]
    if not windows:
        windows = [
            element
            for element in scene.elements.values()
            if element.bbox is not None
        ]
    if not windows:
        return None

    def score(element: UIElement) -> float:
        if element.bbox is None:
            return 0.0
        area = element.bbox.width * element.bbox.height
        # Penalize extreme aspect ratios (panels, full-screen overlays)
        aspect = element.bbox.width / max(element.bbox.height, 1.0)
        if aspect > 30 or aspect < 0.03:
            area *= 0.1
        return area

    return max(windows, key=score)


def omnibox_point(window: UIElement) -> Optional[ScreenPoint]:
    """A click point inside a typical address-bar strip of `window`."""
    if window.bbox is None:
        return None
    bbox = window.bbox
    x = bbox.x + min(max(bbox.width * 0.38, 80.0), max(bbox.width - 40.0, 40.0))
    chrome_height = max(16.0, min(28.0, bbox.height * 0.04))
    y = bbox.y + chrome_height
    return ScreenPoint(space=bbox.space, x=x, y=y)


def chrome_bottom(window: UIElement) -> float:
    """Y below which content (not toolbar) typically starts."""
    if window.bbox is None:
        return 48.0
    return window.bbox.y + max(40.0, min(96.0, window.bbox.height * 0.08))


def is_in_chrome(element: UIElement, window: Optional[UIElement]) -> bool:
    if element.bbox is None:
        return False
    if window is None or window.bbox is None:
        return element.bbox.y < 48.0
    return element.bbox.y < chrome_bottom(window)


def content_roi(window: Optional[UIElement]) -> Optional[BoundingBox]:
    """Client area below typical browser chrome, above a desktop panel."""
    if window is None or window.bbox is None:
        return None
    bbox = window.bbox
    top = max(chrome_bottom(window), bbox.y + min(120.0, bbox.height * 0.18))
    bottom_margin = min(56.0, bbox.height * 0.08)
    height = bbox.y + bbox.height - top - bottom_margin
    if height < 40:
        return bbox
    return BoundingBox(
        space=bbox.space,
        x=max(0.0, bbox.x),
        y=top,
        width=bbox.width,
        height=height,
    )