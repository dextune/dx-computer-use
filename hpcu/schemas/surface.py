"""Shared surface and execution-mode schemas.

Surface kinds live in the common schema layer so case fixtures, planners and
runtime code do not define parallel enums for the same boundary.
"""

from enum import Enum


class SurfaceKind(str, Enum):
    BROWSER = "browser"
    TERMINAL = "terminal"
    DESKTOP = "desktop"
    CHALLENGE = "challenge"
    UNKNOWN = "unknown"


class ExecutionMode(str, Enum):
    """How side effects are allowed to reach the controlled surface."""

    SCREEN_STRICT = "screen_strict"
    LOCAL_SEMANTIC = "local_semantic"
