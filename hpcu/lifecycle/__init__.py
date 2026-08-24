"""Application lifecycle contracts used by platform plugins."""

from hpcu.lifecycle.launcher import (
    ApplicationCandidate,
    ApplicationDiscoveryResult,
    ApplicationLaunchCapabilities,
    ApplicationLauncher,
    ApplicationLaunchResult,
)

__all__ = [
    "ApplicationCandidate",
    "ApplicationDiscoveryResult",
    "ApplicationLaunchCapabilities",
    "ApplicationLauncher",
    "ApplicationLaunchResult",
]
