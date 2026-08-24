"""Application lifecycle contracts used by platform plugins."""

from hpcu.lifecycle.launcher import (
    ApplicationLaunchCapabilities,
    ApplicationLauncher,
    ApplicationLaunchResult,
)

__all__ = [
    "ApplicationLaunchCapabilities",
    "ApplicationLauncher",
    "ApplicationLaunchResult",
]
