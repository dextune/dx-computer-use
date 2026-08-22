"""macOS platform stubs — fake backends with ABC contract compliance.

Real ScreenCaptureKit, AXUIElement, and CGEvent are deferred to platform CI.
These stubs return UNSUPPORTED for all capabilities.
"""

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.input.injector import InputInjector, InputCapabilities, ExecutionResult
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import ScreenPoint
from hpcu.schemas.scene import FrameHandle
from hpcu.schemas.ui_element import UIElement


class MacosScreenCaptureKitCapture(CaptureBackend):
    """ScreenCaptureKit stub — UNSUPPORTED."""

    async def start(self) -> None:
        pass

    async def grab(self) -> FrameHandle:
        raise RuntimeError("macOS ScreenCaptureKit not available")

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(
            pixel_grab=Capability.UNSUPPORTED,
            max_fps=0,
        )


class MacosAxObserver:
    """AXUIElement observer stub — UNSUPPORTED."""

    async def observe(self) -> list[UIElement]:
        return []

    def capabilities(self) -> dict[str, Capability]:
        return {
            "structure_tree": Capability.UNSUPPORTED,
        }


class MacosCgEventInjector(InputInjector):
    """CGEventPost stub — UNSUPPORTED."""

    async def semantic(self, element: UIElement, action: str) -> ExecutionResult:
        return ExecutionResult(
            success=False, mode="semantic",
            failure_code="input_semantic_unsupported",
        )

    async def physical(self, point: ScreenPoint, action: str, text: str | None = None) -> ExecutionResult:
        return ExecutionResult(
            success=False, mode="physical",
            failure_code="input_physical_unsupported",
        )

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(
            semantic_invoke=Capability.UNSUPPORTED,
            physical_pointer=Capability.UNSUPPORTED,
            physical_keyboard=Capability.UNSUPPORTED,
            global_hotkey=Capability.UNSUPPORTED,
        )


def probe() -> bool:
    """Check if macOS platform is available."""
    return False