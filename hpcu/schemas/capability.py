"""Capability enumeration for platform feature detection.

Every platform backend MUST declare its capabilities via the `Capability`
enum.  Unsupported features are returned as `UNSUPPORTED` — never silently
fall back to a different mechanism.
"""

from enum import Enum


class Capability(str, Enum):
    """Platform capability status.

    Values:
        SUPPORTED:   Feature works as documented.
        DEGRADED:    Feature works but with known limitations.
        UNSUPPORTED: Feature is not available on this platform.
    """

    SUPPORTED = "supported"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"