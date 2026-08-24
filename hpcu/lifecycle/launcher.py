"""Application lifecycle contract shared by core and platform plugins."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from hpcu.schemas.capability import Capability


@dataclass(frozen=True)
class ApplicationLaunchCapabilities:
    """Capabilities exposed by an application lifecycle plugin."""

    launch: Capability = Capability.UNSUPPORTED
    discovery: Capability = Capability.UNSUPPORTED


@dataclass(frozen=True)
class ApplicationLaunchResult:
    """Result of ensuring a logical application is running and observable."""

    success: bool
    application: str
    failure_code: str | None = None
    evidence_element_id: str | None = None


class ApplicationLauncher(ABC):
    """Platform boundary for application discovery and launch."""

    def __init__(self, session_id: str):
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @abstractmethod
    async def launch(
        self,
        application: str,
        *,
        timeout_ms: int,
        poll_interval_ms: int,
    ) -> ApplicationLaunchResult:
        """Ensure the logical application is running and return scene evidence."""
        ...

    @abstractmethod
    def capabilities(self) -> ApplicationLaunchCapabilities:
        """Return discovery and launch capability status."""
        ...
