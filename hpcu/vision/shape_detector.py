"""Shape detection DTOs and the ShapeDetector interface.

The default `ShapeDetector` is a **deterministic fake** that returns
shape candidates registered in a per-frame fixture store.  No native
vision backend is invoked.
"""

from dataclasses import dataclass
from typing import Optional

from hpcu.schemas.coordinates import BoundingBox
from hpcu.schemas.scene import FrameHandle

SHAPE_TYPES: frozenset[str] = frozenset({"rectangle", "rounded_rect", "line", "circle"})


@dataclass(frozen=True)
class ShapeCandidate:
    """A detected geometric shape on screen."""

    bbox: BoundingBox
    shape_type: str
    confidence: float

    def __post_init__(self) -> None:
        if self.shape_type not in SHAPE_TYPES:
            raise ValueError(f"unknown shape_type: {self.shape_type!r}")


def _bboxes_intersect(first: BoundingBox, second: BoundingBox) -> bool:
    return not (
        first.x + first.width < second.x
        or second.x + second.width < first.x
        or first.y + first.height < second.y
        or second.y + second.height < first.y
    )


class ShapeDetector:
    """Detect geometric shapes on a captured `FrameHandle`.

    Fake implementation: `detect` returns the deterministic
    `ShapeCandidate` results registered for the frame's `shm_id`,
    optionally restricted to an `roi`.
    """

    def __init__(self, fixture: Optional[dict[str, tuple[ShapeCandidate, ...]]] = None) -> None:
        self._fixture: dict[str, tuple[ShapeCandidate, ...]] = (
            dict(fixture) if fixture is not None else {}
        )

    def set_fixture(self, frame_handle: FrameHandle, *candidates: ShapeCandidate) -> None:
        """Register deterministic shape candidates for a frame's `shm_id`."""
        self._fixture[frame_handle.shm_id] = tuple(candidates)

    def detect(self, frame_handle: FrameHandle, roi: Optional[BoundingBox] = None) -> list[ShapeCandidate]:
        """Return shape candidates for `frame_handle`, optionally clipped to `roi`."""
        candidates = self._fixture.get(frame_handle.shm_id, ())
        if roi is None:
            return list(candidates)
        return [candidate for candidate in candidates if _bboxes_intersect(candidate.bbox, roi)]