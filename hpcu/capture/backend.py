"""CaptureBackend ABC — platform-specific pixel acquisition.

The common runtime never calls native capture APIs directly.
It only calls this ABC, which is implemented by platform plugins.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from hpcu.schemas.capability import Capability
from hpcu.schemas.scene import FrameHandle


@dataclass(frozen=True)
class CaptureCapabilities:
    """What this capture backend can do."""

    pixel_grab: Capability = Capability.UNSUPPORTED
    dirty_rects: Capability = Capability.UNSUPPORTED
    cursor_separate: Capability = Capability.UNSUPPORTED
    max_fps: int = 0
    requires_permission: tuple[str, ...] = ()


class CaptureBackend(ABC):
    """Abstract capture backend.

    Every platform provides exactly one concrete implementation.
    The implementation lives in `hpcu.platform.<os>` and is never
    imported by common modules.
    """

    def __init__(self, session_id: str):
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @abstractmethod
    async def start(self) -> None:
        """Initialize the capture backend.  Must be called before `grab`."""
        ...

    @abstractmethod
    async def grab(self) -> FrameHandle:
        """Return a handle to the latest captured frame.

        The returned FrameHandle MUST NOT contain raw bytes.
        Pixel data is referenced via `shm_id`.
        """
        ...

    @abstractmethod
    def capabilities(self) -> CaptureCapabilities:
        """Return this backend's capabilities."""
        ...