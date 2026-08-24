"""InputInjector ABC — platform-specific input injection.

The common runtime never sends OS-level input events directly.
It calls InputInjector.semantic() first, and falls back to
InputInjector.physical() only when semantic is unsupported.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import ScreenPoint
from hpcu.schemas.ui_element import UIElement


@dataclass(frozen=True)
class ExecutionResult:
    """Result of an input or lifecycle execution attempt."""

    success: bool
    mode: str  # "semantic" | "physical" | "application"
    failure_code: str | None = None
    latency_us: int = 0
    evidence_element_id: str | None = None


@dataclass(frozen=True)
class InputCapabilities:
    """What this input injector can do."""

    semantic_invoke: Capability = Capability.UNSUPPORTED
    physical_pointer: Capability = Capability.UNSUPPORTED
    physical_keyboard: Capability = Capability.UNSUPPORTED
    global_hotkey: Capability = Capability.UNSUPPORTED
    background_input: Capability = Capability.UNSUPPORTED
    requires_permission: tuple[str, ...] = ()


class InputInjector(ABC):
    """Abstract input injector.

    The executor calls semantic() first; if it returns UNSUPPORTED
    or fails, physical() is tried as a fallback.
    """

    def __init__(self, session_id: str):
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @abstractmethod
    async def semantic(self, element: UIElement, action: str) -> ExecutionResult:
        """Perform a semantic action (Invoke, AXPress, locator.click, AT-SPI).

        Args:
            element: The target UI element with its native refs.
            action: The action to perform ("invoke", "toggle", "select", etc.).

        Returns:
            ExecutionResult with success=True if the action was performed.
        """
        ...

    @abstractmethod
    async def physical(
        self,
        point: ScreenPoint,
        action: str,
        text: str | None = None,
    ) -> ExecutionResult:
        """Perform a physical input action (click, key, type) at a point.

        `text` is used for type/hotkey. Adapters ignore it for pointer clicks.
        """
        ...

    @abstractmethod
    def capabilities(self) -> InputCapabilities:
        """Return this injector's capabilities."""
        ...
