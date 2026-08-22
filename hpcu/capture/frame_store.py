"""Bounded in-process frame buffers keyed by FrameHandle.shm_id.

This is a compatibility store for backends that have not yet moved to shared
memory. It is intentionally bounded so repeated capture cannot grow process
memory without limit.
"""

from __future__ import annotations

from collections import OrderedDict


class FrameStore:
    def __init__(
        self,
        *,
        max_frames: int = 16,
        max_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        if max_frames <= 0:
            raise ValueError("max_frames must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_frames = int(max_frames)
        self._max_bytes = int(max_bytes)
        self._frames: OrderedDict[str, bytes] = OrderedDict()
        self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def max_frames(self) -> int:
        return self._max_frames

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def put(self, shm_id: str, data: bytes) -> None:
        if not shm_id.strip():
            raise ValueError("shm_id is required")
        payload = bytes(data)
        if len(payload) > self._max_bytes:
            raise ValueError("single frame exceeds FrameStore.max_bytes")
        previous = self._frames.pop(shm_id, None)
        if previous is not None:
            self._total_bytes -= len(previous)
        self._frames[shm_id] = payload
        self._total_bytes += len(payload)
        self._evict()

    def get(self, shm_id: str) -> bytes:
        return self._frames[shm_id]

    def discard(self, shm_id: str) -> None:
        payload = self._frames.pop(shm_id, None)
        if payload is not None:
            self._total_bytes -= len(payload)

    def clear(self) -> None:
        self._frames.clear()
        self._total_bytes = 0

    def __contains__(self, shm_id: str) -> bool:
        return shm_id in self._frames

    def __len__(self) -> int:
        return len(self._frames)

    def _evict(self) -> None:
        while (
            len(self._frames) > self._max_frames
            or self._total_bytes > self._max_bytes
        ):
            _, payload = self._frames.popitem(last=False)
            self._total_bytes -= len(payload)
