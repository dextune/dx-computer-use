"""Browser capture placeholder.

Phase 1 maps browser capture to a placeholder backend that reports every
capability as UNSUPPORTED and whose `probe()` is False, so bootstrap never
registers it.  A real CDP/Playwright screencast backend replaces it later.
"""

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.schemas.capability import Capability
from hpcu.schemas.scene import FrameHandle


def probe() -> bool:
    """Return whether the browser capture backend is usable.

    Phase 1 placeholder is always unavailable.
    """
    return False


class BrowserCaptureBackend(CaptureBackend):
    """Declares browser capture intent without any capability."""

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)

    async def start(self) -> None:
        pass

    async def grab(self) -> FrameHandle:
        raise RuntimeError(
            "browser capture is a Phase 1 placeholder (probe()=False); "
            "a real CDP/Playwright backend must be registered to grab frames."
        )

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(
            pixel_grab=Capability.UNSUPPORTED,
            dirty_rects=Capability.UNSUPPORTED,
            cursor_separate=Capability.UNSUPPORTED,
            max_fps=0,
        )
