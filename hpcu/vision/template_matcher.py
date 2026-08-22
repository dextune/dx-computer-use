"""Template matching DTOs and the TemplateMatcher interface.

The default `TemplateMatcher` is a **deterministic fake** that filters
template matches registered in a per-frame fixture store.  No native
vision backend is invoked.
"""

from dataclasses import dataclass
from typing import Optional

from hpcu.schemas.coordinates import BoundingBox
from hpcu.schemas.scene import FrameHandle


@dataclass(frozen=True)
class TemplateMatch:
    """A located match of a named template within a frame."""

    template_id: str
    bbox: BoundingBox
    score: float


class TemplateMatcher:
    """Match named templates against a captured `FrameHandle`.

    `match` returns the deterministic matches registered for the frame's
    `shm_id`, filtered to the requested `templates` and sorted by score
    descending.
    """

    def __init__(self, fixture: Optional[dict[str, tuple[TemplateMatch, ...]]] = None) -> None:
        self._fixture: dict[str, tuple[TemplateMatch, ...]] = (
            dict(fixture) if fixture is not None else {}
        )

    def set_fixture(self, frame_handle: FrameHandle, *matches: TemplateMatch) -> None:
        """Register deterministic template matches for a frame's `shm_id`."""
        self._fixture[frame_handle.shm_id] = tuple(matches)

    def match(self, frame_handle: FrameHandle, templates: list[str]) -> list[TemplateMatch]:
        """Return matches for the requested `templates`, highest score first."""
        matches = self._fixture.get(frame_handle.shm_id, ())
        wanted = set(templates)
        selected = [match for match in matches if match.template_id in wanted]
        return sorted(selected, key=lambda match: match.score, reverse=True)