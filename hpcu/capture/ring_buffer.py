"""Bounded circular buffer of `FrameHandle` objects.

The buffer keeps a fixed capacity of most recent frames.  When full, the
oldest frame is discarded automatically (`deque(maxlen=...)` semantics),
so readers almost always see the freshest capture.
"""

from collections import deque

from hpcu.schemas.scene import FrameHandle


class RingBufferEmptyError(Exception):
    """Raised when reading the latest frame from an empty `RingBuffer`."""


class RingBuffer:
    """A bounded FIFO ring buffer of `FrameHandle` objects.

    `push` appends a frame, evicting the oldest frame if the buffer is at
    capacity.  `latest` returns the most recently pushed frame and
    `drain` empties the buffer returning all buffered frames in order.
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._buffer: deque[FrameHandle] = deque(maxlen=capacity)

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        return len(self._buffer)

    def __bool__(self) -> bool:
        return bool(self._buffer)

    def push(self, frame: FrameHandle) -> None:
        """Push `frame`; the oldest buffered frame is discarded when full."""
        self._buffer.append(frame)

    def latest(self) -> FrameHandle:
        """Return the most recently pushed frame, or raise if empty."""
        if not self._buffer:
            raise RingBufferEmptyError("no frames in the ring buffer")
        return self._buffer[-1]

    def drain(self) -> list[FrameHandle]:
        """Return all buffered frames (oldest first) and empty the buffer."""
        frames = list(self._buffer)
        self._buffer.clear()
        return frames