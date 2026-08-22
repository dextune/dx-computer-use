"""Observer ABC — facade over CaptureBackend + StructureObserver.

The Observer is the single entry point that the control loop uses.
It composes a CaptureBackend and an optional StructureObserver into
a SceneDelta (added / removed / modified).
"""

from abc import ABC, abstractmethod

from hpcu.schemas.scene import SceneDelta


class Observer(ABC):
    """Abstract observer — the control loop's only view of the screen.

    Concrete implementations compose a CaptureBackend and an optional
    StructureObserver (UIA, AT-SPI, AX, DOM).  The control loop does
    not know which backends are in use.
    """

    def __init__(self, session_id: str):
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @abstractmethod
    async def observe(self) -> SceneDelta:
        """Return the latest observation delta versus the previous snapshot.

        The first call yields the full initial state as `added`.
        """
        ...