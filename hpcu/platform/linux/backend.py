"""Linux platform stubs — fake backends with ABC contract compliance.

Real X11, PipeWire, AT-SPI, XTest, and portal are deferred to platform CI.
These stubs return UNSUPPORTED for all capabilities.
"""

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.input.injector import InputInjector, InputCapabilities, ExecutionResult
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import ScreenPoint
from hpcu.schemas.scene import FrameHandle
from hpcu.schemas.ui_element import UIElement


class LinuxX11Capture(CaptureBackend):
    """X11/XShm capture stub — UNSUPPORTED."""

    async def start(self) -> None:
        pass

    async def grab(self) -> FrameHandle:
        raise RuntimeError("Linux X11 capture not available")

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(
            pixel_grab=Capability.UNSUPPORTED,
            dirty_rects=Capability.UNSUPPORTED,
            max_fps=0,
        )


class LinuxPipewireCapture(CaptureBackend):
    """PipeWire screencast portal stub — UNSUPPORTED."""

    async def start(self) -> None:
        pass

    async def grab(self) -> FrameHandle:
        raise RuntimeError("Linux PipeWire capture not available")

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(
            pixel_grab=Capability.UNSUPPORTED,
            max_fps=0,
        )


class LinuxAtSpiObserver:
    """AT-SPI observer stub — UNSUPPORTED."""

    async def observe(self) -> list[UIElement]:
        return []

    def capabilities(self) -> dict[str, Capability]:
        return {
            "structure_tree": Capability.UNSUPPORTED,
            "events": Capability.UNSUPPORTED,
        }


class LinuxXtestInjector(InputInjector):
    """XTest input stub — UNSUPPORTED."""

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
        )


class LinuxPortalInjector(InputInjector):
    """RemoteDesktop/input portal stub — UNSUPPORTED."""

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
        )


def probe() -> bool:
    """Check if Linux platform is available."""
    return False