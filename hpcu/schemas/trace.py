"""Trace record types — every observable event in the runtime.

Images are never included in trace records.  Only FrameHandle references
are stored.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TraceEventType(str, Enum):
    CAPTURE = "capture"
    SCENE_UPDATE = "scene_update"
    ACTION = "action"
    VERIFICATION = "verification"
    MODEL_CALL = "model_call"
    RECOVERY = "recovery"
    COMPLETION = "completion"


@dataclass(frozen=True)
class TraceRecord:
    """A single event in the execution trace."""

    seq: int
    event_type: TraceEventType
    timestamp_ns: int
    scene_version: int
    payload: dict = field(default_factory=dict)
    element_id: Optional[str] = None
    failure_code: Optional[str] = None
    model_call_count: int = 0
    latency_us: int = 0