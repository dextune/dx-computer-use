"""Bounded in-process pixel buffers keyed by frame handle id.

This implementation does not claim shared-memory semantics: capture backends
store bytes here and perception retrieves them by handle. Capacity is bounded
by frame count and can also be bounded by total bytes so long-running sessions
cannot retain unbounded screenshot memory.
"""

from collections import OrderedDict


class FrameStore:
    def __init__(
        self,
        max_frames: int = 8,
        max_bytes: int | None = None,
    ) -> None:
        if max_frames <= 0:
            raise ValueError("FrameStore.max_frames must be positive")
        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("FrameStore.max_bytes must be positive")
        self._max_frames = int(max_frames)
        self._max_bytes = int(max_bytes) if max_bytes is not None else None
        self._frames: OrderedDict[str, bytes] = OrderedDict()
        self._total_bytes = 0

    @property
    def max_frames(self) -> int:
        return self._max_frames

    @property
    def max_bytes(self) -> int | None:
        return self._max_bytes

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def put(self, shm_id: str, data: bytes) -> None:
        if not shm_id:
            raise ValueError("frame id must be non-empty")
        if self._max_bytes is not None and len(data) > self._max_bytes:
            raise ValueError("single frame exceeds FrameStore.max_bytes")
        previous = self._frames.pop(shm_id, None)
        if previous is not None:
            self._total_bytes -= len(previous)
        self._frames[shm_id] = data
        self._total_bytes += len(data)
        self._evict()

    def get(self, shm_id: str) -> bytes:
        return self._frames[shm_id]

    def discard(self, shm_id: str) -> None:
        data = self._frames.pop(shm_id, None)
        if data is not None:
            self._total_bytes -= len(data)

    def clear(self) -> None:
        self._frames.clear()
        self._total_bytes = 0

    def __contains__(self, shm_id: str) -> bool:
        return shm_id in self._frames

    def __len__(self) -> int:
        return len(self._frames)

    def _evict(self) -> None:
        while len(self._frames) > self._max_frames or self._over_byte_limit():
            _, data = self._frames.popitem(last=False)
            self._total_bytes -= len(data)

    def _over_byte_limit(self) -> bool:
        return self._max_bytes is not None and self._total_bytes > self._max_bytes
