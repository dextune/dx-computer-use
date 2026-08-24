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
    GrokSandboxApplicationLauncher,
    GrokSandboxCapture,
    GrokSandboxInjector,
    GrokSandboxStructure,
    create_sandbox_backends,
    create_sandbox_launcher,
    probe as probe_sandbox,
)

__all__ = [
    "GrokSandboxApplicationLauncher",
    "GrokSandboxCapture",
    "GrokSandboxInjector",
    "GrokSandboxStructure",
    "LinuxAtSpiObserver",
    "LinuxPipewireCapture",
    "LinuxPortalInjector",
    "LinuxX11Capture",
    "LinuxXtestInjector",
    "create_sandbox_backends",
    "create_sandbox_launcher",
    "probe_native",
    "probe_sandbox",
]
