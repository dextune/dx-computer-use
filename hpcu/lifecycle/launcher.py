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
class ApplicationCandidate:
    """OS-discovered application candidate safe to expose to planning."""

    id: str
    label: str
    window_class: str = ""
    executable: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.label.strip():
            raise ValueError("application candidate id and label are required")


@dataclass(frozen=True)
class ApplicationDiscoveryResult:
    """Local discovery result before any application side effect occurs."""

    application: str
    candidates: tuple[ApplicationCandidate, ...] = ()
    preferred_candidate_id: str | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        ids = tuple(candidate.id for candidate in self.candidates)
        if len(ids) != len(set(ids)):
            raise ValueError("application candidate ids must be unique")
        preferred = self.preferred_candidate_id
        if preferred is not None and preferred not in ids:
            raise ValueError("preferred application candidate must be discovered")


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
    async def discover(self, application: str) -> ApplicationDiscoveryResult:
        """Return local candidates without launching or focusing an application."""
        ...

    @abstractmethod
    async def launch(
        self,
        application: str,
        *,
        candidate_id: str | None = None,
        timeout_ms: int,
        poll_interval_ms: int,
    ) -> ApplicationLaunchResult:
        """Ensure the logical application is running and return scene evidence."""
        ...

    @abstractmethod
    def capabilities(self) -> ApplicationLaunchCapabilities:
        """Return discovery and launch capability status."""
        ...
