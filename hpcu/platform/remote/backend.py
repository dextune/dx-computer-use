"""Remote VNC/RDP platform stubs.

Real VNC/RDP framebuffer capture and RFB input are deferred to platform CI.
"""

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.input.injector import InputInjector, InputCapabilities, ExecutionResult
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import ScreenPoint
from hpcu.schemas.scene import FrameHandle
from hpcu.schemas.ui_element import UIElement


class RemoteVncCapture(CaptureBackend):
    """VNC/RDP framebuffer capture stub — UNSUPPORTED."""

    async def start(self) -> None:
        pass

    async def grab(self) -> FrameHandle:
        raise RuntimeError("Remote VNC capture not available")

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(
            pixel_grab=Capability.UNSUPPORTED,
            max_fps=0,
        )


class RemoteVncInjector(InputInjector):
    """RFB pointer/key stub — UNSUPPORTED."""

    async def semantic(self, element: UIElement, action: str) -> ExecutionResult:
        return ExecutionResult(
            success=False, mode="semantic",
            failure_code="input_semantic_unsupported",
        )

    async def physical(self, point: ScreenPoint, action: str) -> ExecutionResult:
        return ExecutionResult(
            success=False, mode="physical",
            failure_code="input_physical_unsupported",
        )

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(
            semantic_invoke=Capability.UNSUPPORTED,
            physical_pointer=Capability.UNSUPPORTED,
            physical_keyboard=Capability.UNSUPPORTED,
        )


def probe() -> bool:
    """Check if remote platform is available."""
    return False