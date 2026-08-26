"""Offline trace and verified-experience replay."""

from __future__ import annotations

from typing import Any

from hpcu.schemas.trace import TraceRecord
from hpcu.trace.experience import ExperienceCache, ExperienceReplayResult, GrounderLike


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
        if self._position >= len(self._records):
            return None
        record = self._records[self._position]
        self._position += 1
        return record

    def check_stale(self, current_scene_version: int) -> bool:
        if self._position < len(self._records):
            expected = self._records[self._position]
            if expected.scene_version > current_scene_version:
                self._stale_count += 1
                return True
        return False

    def validate_trajectory(
        self, actual: tuple[TraceRecord, ...]
    ) -> tuple[bool, int]:
        mismatch_count = 0
        max_len = max(len(self._records), len(actual))

        for i in range(max_len):
            if i >= len(self._records) or i >= len(actual):
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


class ExperienceReplayEngine:
    """Thin replay boundary that guarantees fresh re-grounding through the cache."""

    def __init__(self, cache: ExperienceCache, grounder: GrounderLike):
        self._cache = cache
        self._grounder = grounder

    def reground(
        self,
        *,
        target_query: dict[str, Any],
        scene: Any,
        surface_fingerprint: str,
        target_signature: str,
        plan_node_signature: str = "",
    ) -> ExperienceReplayResult:
        return self._cache.replay(
            grounder=self._grounder,
            target_query=target_query,
            scene=scene,
            surface_fingerprint=surface_fingerprint,
            target_signature=target_signature,
            plan_node_signature=plan_node_signature,
        )
