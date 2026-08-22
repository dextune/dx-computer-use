"""Recovery sub-system for the HPCU Runtime.

Recovery is a common (platform-independent) layer.  It detects and
breaks infinite loops, dismisses popups/overlays, and selects an
alternate interaction mode after a failure.  It never imports
``hpcu.platform`` and never touches the OS directly.
"""

from hpcu.recovery.alternate_modes import AlternateModeSelector
from hpcu.recovery.loop_breaker import (
    LoopBreaker,
    LoopDetection,
    LoopTriggerType,
    RecoveryAction,
)
from hpcu.recovery.popup_recovery import PopupRecovery

__all__ = [
    "AlternateModeSelector",
    "LoopBreaker",
    "LoopDetection",
    "LoopTriggerType",
    "PopupRecovery",
    "RecoveryAction",
]
