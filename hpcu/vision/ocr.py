"""OCR DTOs and the OcrEngine interface.

The default `OcrEngine` is a **deterministic fake**: it returns text
regions registered in a per-frame fixture store.  No native OCR backend
is invoked, so this module is fully unit-testable and portable.
"""

from dataclasses import dataclass
from typing import Optional

from hpcu.schemas.coordinates import BoundingBox
from hpcu.schemas.scene import FrameHandle


@dataclass(frozen=True)
class TextRegion:
    """A detected region of on-screen text."""

    text: str
    bbox: BoundingBox
    confidence: float
    language: str = "eng"


def _bboxes_intersect(first: BoundingBox, second: BoundingBox) -> bool:
    return not (
        first.x + first.width < second.x
        or second.x + second.width < first.x
        or first.y + first.height < second.y
        or second.y + second.height < first.y
    )


class OcrEngine:
    """Optical character recognition on a captured `FrameHandle`.

    Fake implementation: `detect` returns the deterministic `TextRegion`
    results registered for the frame's `shm_id`, optionally restricted to
    an `roi`.  Unknown frames yield an empty result.
    """

    def __init__(self, fixture: Optional[dict[str, tuple[TextRegion, ...]]] = None) -> None:
        self._fixture: dict[str, tuple[TextRegion, ...]] = (
            dict(fixture) if fixture is not None else {}
        )

    def set_fixture(self, frame_handle: FrameHandle, *regions: TextRegion) -> None:
        """Register deterministic text regions for a frame's `shm_id`."""
        self._fixture[frame_handle.shm_id] = tuple(regions)

    def detect(self, frame_handle: FrameHandle, roi: Optional[BoundingBox] = None) -> list[TextRegion]:
        """Return text regions for `frame_handle`, optionally clipped to `roi`."""
        regions = self._fixture.get(frame_handle.shm_id, ())
        if roi is None:
            return list(regions)
        return [region for region in regions if _bboxes_intersect(region.bbox, roi)]