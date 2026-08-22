"""Trace recorder — monotonic sequence, event append.

Records every observable event in the runtime.  Images are never
included — only FrameHandle references are stored.
"""

import time
from typing import Optional

from hpcu.schemas.trace import TraceEventType, TraceRecord


class TraceRecorder:
    """Monotonic sequence recorder for execution traces.

    The recorder is used during both normal execution and offline replay.
    """

    def __init__(self):
        self._seq: int = 0
        self._records: list[TraceRecord] = []
        self._model_call_count: int = 0

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def model_call_count(self) -> int:
        return self._model_call_count

    def append(
        self,
        event_type: TraceEventType,
        scene_version: int,
        *,
        payload: Optional[dict] = None,
        element_id: Optional[str] = None,
        failure_code: Optional[str] = None,
        is_model_call: bool = False,
        latency_us: int = 0,
    ) -> TraceRecord:
        """Append a new trace record and return it.

        The sequence number is monotonically increasing.
        """
        self._seq += 1
        if is_model_call:
            self._model_call_count += 1

        record = TraceRecord(
            seq=self._seq,
            event_type=event_type,
            timestamp_ns=time.time_ns(),
            scene_version=scene_version,
            payload=payload or {},
            element_id=element_id,
            failure_code=failure_code,
            model_call_count=self._model_call_count,
            latency_us=latency_us,
        )
        self._records.append(record)
        return record

    def records(self) -> tuple[TraceRecord, ...]:
        """Return all recorded events as an immutable tuple."""
        return tuple(self._records)

    def reset(self) -> None:
        """Reset the recorder to initial state."""
        self._seq = 0
        self._records.clear()
        self._model_call_count = 0

    def is_stale(self, scene_version: int) -> bool:
        """Check if the given scene_version is older than the last recorded."""
        if not self._records:
            return False
        return scene_version < self._records[-1].scene_version