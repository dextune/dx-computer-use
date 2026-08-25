"""Neutral region DTOs for CPU-first screen structuring."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hpcu.schemas.coordinates import BoundingBox


class RegionKind(str, Enum):
    """Pixel-layout classifications that do not assert UI semantics."""

    UNKNOWN = "unknown"
    TEXT_CLUSTER = "text_cluster"
    CONTAINER_CANDIDATE = "container_candidate"
    REPEATED_BLOCK = "repeated_block"
    CHROME_CANDIDATE = "chrome_candidate"


@dataclass(frozen=True)
class RegionNode:
    """An immutable neutral layout region bound to one Scene version."""

    id: str
    scene_version: int
    bbox: BoundingBox
    parent_id: str | None = None
    child_ids: tuple[str, ...] = ()
    source: str = "pixel_layout"
    kind: RegionKind = RegionKind.UNKNOWN
    fingerprint: str = ""
    stable_frames: int = 0

    def __post_init__(self) -> None:
        normalized_id = self.id.strip()
        if not normalized_id:
            raise ValueError("region id must be non-empty")
        object.__setattr__(self, "id", normalized_id)
        if self.scene_version < 0:
            raise ValueError("scene_version must be >= 0")
        if self.bbox.width <= 0 or self.bbox.height <= 0:
            raise ValueError("region bbox must have positive width and height")
        if self.parent_id == self.id:
            raise ValueError("region cannot be its own parent")
        if self.id in self.child_ids:
            raise ValueError("region cannot contain itself")
        if len(set(self.child_ids)) != len(self.child_ids):
            raise ValueError("region child_ids must be unique")
        normalized_source = self.source.strip()
        if not normalized_source:
            raise ValueError("region source must be non-empty")
        object.__setattr__(self, "source", normalized_source)
        if not isinstance(self.kind, RegionKind):
            object.__setattr__(self, "kind", RegionKind(self.kind))
        if self.stable_frames < 0:
            raise ValueError("stable_frames must be >= 0")
