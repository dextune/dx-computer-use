"""Scene and SceneDelta schemas — the shared view of the screen."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Optional

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.ui_element import UIElement


@dataclass(frozen=True)
class FrameHandle:
    """A handle to a captured frame — zero-copy, no bytes.

    The actual pixel data lives in shared memory identified by `shm_id`.
    Nothing in the common runtime may copy raw bytes from this handle.
    `space` is a CoordinateSpace (string values are coerced; invalid fail).
    """

    shm_id: str
    width: int
    height: int
    stride: int
    pixel_format: str  # "BGRA" | "BGR"
    timestamp_ns: int
    space: CoordinateSpace
    dirty_rects: tuple[BoundingBox, ...] = ()
    source: str = "unknown"  # "dxgi" | "x11" | "pipewire" | "sckit" | "cdp" | "vnc"

    def __post_init__(self) -> None:
        if isinstance(self.space, CoordinateSpace):
            return
        object.__setattr__(self, "space", CoordinateSpace(self.space))


@dataclass(frozen=True)
class SceneDelta:
    """Incremental update to the scene graph — only changed elements.

    This is the sole observation product. Observer.observe() returns this type.
    """

    base_version: int
    new_version: int
    added: tuple[UIElement, ...] = ()
    removed: tuple[str, ...] = ()
    modified: tuple[UIElement, ...] = ()
    frame: Optional[FrameHandle] = None

    def __post_init__(self) -> None:
        if self.new_version <= self.base_version:
            raise ValueError(
                "scene_version must be monotonic: "
                f"new_version={self.new_version} <= base_version={self.base_version}"
            )

    @property
    def scene_version(self) -> int:
        return self.new_version


@dataclass(frozen=True)
class Scene:
    """A complete snapshot of the UI Scene Graph at a given version."""

    version: int
    window_id: Optional[str] = None
    window_title: Optional[str] = None
    elements: Mapping[str, UIElement] = field(default_factory=dict)
    frame: Optional[FrameHandle] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "elements", MappingProxyType(dict(self.elements)))

    def get(self, element_id: str) -> Optional[UIElement]:
        return self.elements.get(element_id)

    def __contains__(self, element_id: str) -> bool:
        return element_id in self.elements

    def __len__(self) -> int:
        return len(self.elements)