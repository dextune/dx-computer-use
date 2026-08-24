"""Application lifecycle contracts used by platform plugins."""

from hpcu.lifecycle.launcher import (
    ApplicationCandidate,
    ApplicationDiscoveryResult,
    ApplicationLaunchCapabilities,
    ApplicationLauncher,
    ApplicationLaunchResult,
)
from hpcu.lifecycle.resolver import ApplicationPlanResolver

__all__ = [
    "ApplicationCandidate",
    "ApplicationDiscoveryResult",
    "ApplicationLaunchCapabilities",
    "ApplicationLauncher",
    "ApplicationLaunchResult",
    "ApplicationPlanResolver",
]
