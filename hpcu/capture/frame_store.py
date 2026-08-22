"""Bounded in-process pixel buffers keyed by frame handle id.

This implementation does not claim shared-memory semantics: capture backends
store bytes here and perception retrieves them by handle.  Capacity is bounded
so long-running sessions cannot retain every historical screenshot.
"""

from collections import OrderedDict


class FrameStore:
    def __init__(self, max_frames: int = 8) -> None:
        if max_frames <= 0:
            raise ValueError("FrameStore.max_frames must be positive")
        self._max_frames = max_frames
        self._frames: OrderedDict[str, bytes] = OrderedDict()

    @property
    def max_frames(self) -> int:
        return self._max_frames

    def put(self, shm_id: str, data: bytes) -> None:
        if not shm_id:
            raise ValueError("frame id must be non-empty")
        if shm_id in self._frames:
            self._frames.pop(shm_id)
        self._frames[shm_id] = data
        while len(self._frames) > self._max_frames:
            self._frames.popitem(last=False)

    def get(self, shm_id: str) -> bytes:
        return self._frames[shm_id]

    def discard(self, shm_id: str) -> None:
        self._frames.pop(shm_id, None)

    def __contains__(self, shm_id: str) -> bool:
        return shm_id in self._frames

    def __len__(self) -> int:
        return len(self._frames)
