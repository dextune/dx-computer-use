"""Browser input placeholder.

Phase 1 maps browser input to a placeholder InputInjector that reports every
capability as UNSUPPORTED and fails both semantic and physical paths with the
corresponding failure codes.  `probe()` is False so bootstrap never registers
it; a real CDP/Playwright input adapter replaces it later.
"""

from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import ScreenPoint
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.ui_element import UIElement


def probe() -> bool:
    """Return whether the browser input injector is usable.

    Phase 1 placeholder is always unavailable.
    """
    return False


class BrowserInputInjector(InputInjector):
    """Declares browser input intent without any capability."""

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)

    async def semantic(self, element: UIElement, action: str) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            mode="semantic",
            failure_code=FailureCode.INPUT_SEMANTIC_UNSUPPORTED.value,
        )

    async def physical(self, point: ScreenPoint, action: str) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            mode="physical",
            failure_code=FailureCode.INPUT_PHYSICAL_UNSUPPORTED.value,
        )

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(
            semantic_invoke=Capability.UNSUPPORTED,
            physical_pointer=Capability.UNSUPPORTED,
            physical_keyboard=Capability.UNSUPPORTED,
            global_hotkey=Capability.UNSUPPORTED,
            background_input=Capability.UNSUPPORTED,
        )
