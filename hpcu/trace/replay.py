"""Offline trace replay — deterministic verification.

Replays a stored trajectory and validates that:
  - The same sequence of events is produced.
  - Stale actions (scene_version mismatch) are detected.
  - No model calls are made during replay.
"""

from hpcu.schemas.trace import TraceEventType, TraceRecord


class ReplayEngine:
    """Replays a stored trace and validates determinism."""

    def __init__(self, records: tuple[TraceRecord, ...]):
        self._records = records
        self._position: int = 0
        self._stale_count: int = 0

    @property
    def position(self) -> int:
        return self._position

    @property
    def stale_count(self) -> int:
        return self._stale_count

    def next_record(self) -> TraceRecord | None:
        """Return the next record in the trace, or None if done."""
        if self._position >= len(self._records):
            return None
        record = self._records[self._position]
        self._position += 1
        return record

    def check_stale(self, current_scene_version: int) -> bool:
        """Check if the current scene is stale relative to the trace.

        Returns True if the action should be rejected (scene moved on).
        """
        if self._position < len(self._records):
            expected = self._records[self._position]
            if expected.scene_version > current_scene_version:
                self._stale_count += 1
                return True
        return False

    def validate_trajectory(
        self, actual: tuple[TraceRecord, ...]
    ) -> tuple[bool, int]:
        """Validate that an actual trace matches the stored trajectory.

        Returns (is_valid, mismatch_count).
        """
        mismatch_count = 0
        max_len = max(len(self._records), len(actual))

        for i in range(max_len):
            if i >= len(self._records):
                mismatch_count += 1
                continue
            if i >= len(actual):
                mismatch_count += 1
                continue

            expected = self._records[i]
            got = actual[i]

            if expected.event_type != got.event_type:
                mismatch_count += 1
            elif expected.scene_version != got.scene_version:
                mismatch_count += 1
            elif expected.element_id != got.element_id:
                mismatch_count += 1

        return (mismatch_count == 0, mismatch_count)