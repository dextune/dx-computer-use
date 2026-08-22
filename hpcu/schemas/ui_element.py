"""UIElement schema — the core node of the Unified UI Scene Graph."""

from dataclasses import dataclass, field
from typing import Optional

from hpcu.schemas.coordinates import BoundingBox


@dataclass(frozen=True)
class ElementState:
    visible: bool = True
    enabled: bool = True
    selected: bool = False
    occluded: bool = False


@dataclass(frozen=True)
class ElementRelations:
    parent: Optional[str] = None
    label_for: Optional[str] = None
    same_row: tuple[str, ...] = ()
    same_column: tuple[str, ...] = ()
    above: tuple[str, ...] = ()
    below: tuple[str, ...] = ()
    left_of: tuple[str, ...] = ()
    right_of: tuple[str, ...] = ()
    contains: tuple[str, ...] = ()
    overlays: tuple[str, ...] = ()
    modal_owner: Optional[str] = None
    scroll_container: Optional[str] = None
    repeated_group: Optional[str] = None


@dataclass(frozen=True)
class ElementSource:
    type: str  # "dom" | "uia" | "atspi" | "ax" | "ocr" | "template"
    ref: Optional[str] = None
    confidence: float = 1.0
    text: Optional[str] = None


@dataclass(frozen=True)
class UIElement:
    """A single node in the UI Scene Graph.

    Every element has a stable id and a scene_version that is
    monotonically increasing.  The id is NOT a bare coordinate.
    """

    id: str
    scene_version: int
    role: str = "unknown"
    name: Optional[str] = None
    text: Optional[str] = None
    semantic_tags: tuple[str, ...] = ()
    bbox: Optional[BoundingBox] = None
    bbox_normalized: Optional[BoundingBox] = None
    state: ElementState = field(default_factory=ElementState)
    relations: ElementRelations = field(default_factory=ElementRelations)
    sources: tuple[ElementSource, ...] = ()
    fingerprint: Optional[str] = None
    first_seen_at: int = 0
    last_seen_at: int = 0
    stable_frames: int = 0