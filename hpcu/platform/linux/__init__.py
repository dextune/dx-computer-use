"""Linux platform plugins. Bootstrap registers; modules do not self-register."""

from hpcu.platform.linux.backend import (
    LinuxAtSpiObserver,
    LinuxPipewireCapture,
    LinuxPortalInjector,
    LinuxX11Capture,
    LinuxXtestInjector,
    probe as probe_native,
)
from hpcu.platform.linux.sandbox import (
    GrokSandboxCapture,
    GrokSandboxInjector,
    GrokSandboxStructure,
    create_sandbox_backends,
    probe as probe_sandbox,
)

__all__ = [
    "GrokSandboxCapture",
    "GrokSandboxInjector",
    "GrokSandboxStructure",
    "LinuxAtSpiObserver",
    "LinuxPipewireCapture",
    "LinuxPortalInjector",
    "LinuxX11Capture",
    "LinuxXtestInjector",
    "create_sandbox_backends",
    "probe_native",
    "probe_sandbox",
]
