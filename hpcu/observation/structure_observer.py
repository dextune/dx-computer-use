"""StructureObserver ABC — platform accessibility/accessibility-tree plugins.

A StructureObserver exposes a platform's accessibility tree (DOM, UIA,
AT-SPI, AX) as a flat snapshot of UIElement nodes.  It is a sibling of
CaptureBackend (pixels) and InputInjector (input).  Presence is optional:
remote/fresh setups may have no tree at all and emit an empty snapshot,
which is `STRUCTURE_TREE_EMPTY`, never a crash.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from hpcu.schemas.capability import Capability
from hpcu.schemas.ui_element import UIElement


@dataclass(frozen=True)
class StructureCapabilities:
    """What this structure observer can do."""

    tree: Capability = Capability.UNSUPPORTED
    live_events: Capability = Capability.UNSUPPORTED
    requires_permission: tuple[str, ...] = ()


class StructureObserver(ABC):
    """Abstract tree observer.

    Every platform provides a concrete implementation that lives in
    `hpcu.platform.<os>` and is never imported by common modules.  A
    missing tree is an empty snapshot, not an error.
    """

    def __init__(self, session_id: str):
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @abstractmethod
    async def observe_structure(self) -> tuple[UIElement, ...]:
        """Return the current accessibility tree as a flat element snapshot.

        An empty tuple is a valid result when the target has no tree
        (STRUCTURE_TREE_EMPTY path), so the caller must not treat it
        as a failure.
        """
        ...

    @abstractmethod
    def capabilities(self) -> StructureCapabilities:
        """Return this observer's capabilities."""
        ...
