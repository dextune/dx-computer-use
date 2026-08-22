"""Coordinate systems and bounding box for the HPCU Runtime."""

from dataclasses import dataclass, field
from enum import Enum


class CoordinateSpace(str, Enum):
    """Coordinate spaces supported by the runtime.

    Every UIElement.bounding_box MUST declare its space.
    """

    SCREEN_PHYSICAL_PX = "screen_physical_px"
    SCREEN_LOGICAL_PX = "screen_logical_px"
    MONITOR_LOCAL_PX = "monitor_local_px"
    WINDOW_FRAME_PX = "window_frame_px"
    CLIENT_AREA_PX = "client_area_px"
    VIEWPORT_PX = "viewport_px"
    CSS_PX = "css_px"
    CROP_LOCAL_PX = "crop_local_px"
    NORMALIZED = "normalized"


@dataclass(frozen=True)
class BoundingBox:
    """Axis-aligned bounding box in a named coordinate space."""

    space: CoordinateSpace
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def contains(self, px: float, py: float) -> bool:
        return (self.x <= px <= self.x + self.width) and (
            self.y <= py <= self.y + self.height
        )


@dataclass(frozen=True)
class ScreenPoint:
    """A single point in a named coordinate space."""

    space: CoordinateSpace
    x: float
    y: float