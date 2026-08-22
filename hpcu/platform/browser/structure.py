"""Browser structure placeholder.

Phase 1 maps browser DOM/accessibility structure to a placeholder that
returns an empty tree and UNSUPPORTED capabilities.  `probe()` is False so
it is never registered; a real DOM/AX observer replaces it later.
"""

from hpcu.observation.structure_observer import StructureCapabilities, StructureObserver
from hpcu.schemas.capability import Capability
from hpcu.schemas.ui_element import UIElement


def probe() -> bool:
    """Return whether the browser structure observer is usable.

    Phase 1 placeholder is always unavailable.
    """
    return False


class BrowserStructureObserver(StructureObserver):
    """Declares browser structure intent without any capability."""

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)

    async def observe_structure(self) -> tuple[UIElement, ...]:
        return ()

    def capabilities(self) -> StructureCapabilities:
        return StructureCapabilities(
            tree=Capability.UNSUPPORTED,
            live_events=Capability.UNSUPPORTED,
        )
