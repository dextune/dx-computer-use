"""VLM and visual grounding stubs (Phase 5).

Real VLM models and image processing are deferred to platform CI.
These stubs provide the contract interfaces with fake implementations.
"""

from dataclasses import dataclass, field
from typing import Optional

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace, ScreenPoint
from hpcu.schemas.ui_element import UIElement


@dataclass(frozen=True)
class ObjectMark:
    """A label overlaid on a screen element for Set-of-Mark prompting."""
    number: int
    element_id: str
    bbox: BoundingBox
    label: str = ""


@dataclass(frozen=True)
class MarkedImage:
    """An image with object marks overlaid for VLM input."""
    marks: tuple[ObjectMark, ...] = ()
    width: int = 0
    height: int = 0


class SoMOverlay:
    """Set-of-Mark overlay generator — creates marked images for VLM prompting."""

    def generate_marks(self, elements: list[UIElement]) -> list[ObjectMark]:
        """Assign numbers to each UI element candidate."""
        marks: list[ObjectMark] = []
        for i, el in enumerate(elements):
            if el.bbox is not None:
                marks.append(ObjectMark(
                    number=i + 1,
                    element_id=el.id,
                    bbox=el.bbox,
                    label=el.name or el.text or f"element_{i}",
                ))
        return marks

    def build_marked_image(self, marks: list[ObjectMark], width: int = 0, height: int = 0) -> MarkedImage:
        return MarkedImage(marks=tuple(marks), width=width, height=height)


class CropTransform:
    """Transform crop-local coordinates back to original screen coordinates."""

    def to_original(self, crop_point: ScreenPoint, crop_origin: ScreenPoint) -> ScreenPoint:
        return ScreenPoint(
            space=CoordinateSpace.SCREEN_PHYSICAL_PX,
            x=crop_point.x + crop_origin.x,
            y=crop_point.y + crop_origin.y,
        )

    def to_crop(
        self, original_point: ScreenPoint, crop_origin: ScreenPoint,
        crop_width: float, crop_height: float,
    ) -> ScreenPoint:
        return ScreenPoint(
            space=CoordinateSpace.CROP_LOCAL_PX,
            x=original_point.x - crop_origin.x,
            y=original_point.y - crop_origin.y,
        )


@dataclass(frozen=True)
class ZoomRequest:
    """Request to zoom into a region of the screen for finer analysis."""
    region: BoundingBox
    zoom_level: float = 2.0
    reason: str = ""


@dataclass(frozen=True)
class VlmCandidateSelection:
    """Result of a VLM selecting from marked candidates."""
    selected_number: int
    selected_element_id: str
    confidence: float
    reasoning: str = ""


class SmallVlmSelector:
    """Small VLM candidate selector stub — returns fake selections."""

    def select(
        self, marked_image: MarkedImage, prompt: str,
    ) -> Optional[VlmCandidateSelection]:
        """Select a candidate from the marked image. Stub returns None."""
        return None


class LargeVlmFallback:
    """Large VLM coarse-to-fine search stub — returns fake results."""

    def search(
        self, marked_image: MarkedImage, prompt: str,
    ) -> Optional[VlmCandidateSelection]:
        """Search with coarse-to-fine strategy. Stub returns None."""
        return None


class VisualVerifier:
    """Visual verification — check if a clicked element looks correct."""

    def verify(
        self, before_image: MarkedImage, after_image: MarkedImage,
        clicked_element: UIElement,
    ) -> bool:
        """Verify that the visual state changed as expected. Stub returns True."""
        return True