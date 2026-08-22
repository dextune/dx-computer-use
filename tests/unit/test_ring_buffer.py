"""Unit tests for the bounded RingBuffer of FrameHandle objects."""

import pytest

pytestmark = pytest.mark.unit

from hpcu.capture.ring_buffer import RingBuffer, RingBufferEmptyError
from hpcu.schemas.scene import FrameHandle


def _frame(handle_id: str) -> FrameHandle:
    return FrameHandle(
        shm_id=handle_id,
        width=100,
        height=100,
        stride=400,
        pixel_format="BGRA",
        timestamp_ns=1,
        space="screen_physical_px",
    )


@pytest.mark.unit
def test_push_and_length():
    """Given frames are pushed, the buffer length reflects the buffer count."""
    # Given
    buffer = RingBuffer(capacity=4)

    # When
    buffer.push(_frame("a"))
    buffer.push(_frame("b"))

    # Then
    assert len(buffer) == 2
    assert bool(buffer) is True


@pytest.mark.unit
def test_latest_returns_most_recent():
    """Given several pushes, latest returns the most recently pushed frame."""
    # Given
    buffer = RingBuffer(capacity=4)

    # When
    buffer.push(_frame("a"))
    buffer.push(_frame("b"))
    latest = buffer.latest()

    # Then
    assert latest.shm_id == "b"
    assert len(buffer) == 2


@pytest.mark.unit
def test_drain_empties_buffer():
    """Given buffered frames, drain returns them in order and empties the buffer."""
    # Given
    buffer = RingBuffer(capacity=4)

    # When
    buffer.push(_frame("a"))
    buffer.push(_frame("b"))
    drained = buffer.drain()

    # Then
    assert [frame.shm_id for frame in drained] == ["a", "b"]
    assert len(buffer) == 0
    assert bool(buffer) is False


@pytest.mark.unit
def test_latest_empty_raises():
    """Given an empty buffer, latest raises RingBufferEmptyError."""
    # Given
    buffer = RingBuffer(capacity=2)

    # When / Then
    with pytest.raises(RingBufferEmptyError):
        buffer.latest()


@pytest.mark.unit
def test_bounded_oldest_discarded():
    """Given more pushes than capacity, the oldest buffered frame is discarded."""
    # Given
    buffer = RingBuffer(capacity=2)

    # When
    buffer.push(_frame("a"))
    buffer.push(_frame("b"))
    buffer.push(_frame("c"))

    # Then
    assert len(buffer) == 2
    assert [frame.shm_id for frame in buffer.drain()] == ["b", "c"]


@pytest.mark.unit
def test_non_positive_capacity_raises():
    """Given a non-positive capacity, construction raises ValueError."""
    # When / Then
    with pytest.raises(ValueError):
        RingBuffer(capacity=0)
    with pytest.raises(ValueError):
        RingBuffer(capacity=-1)


@pytest.mark.unit
def test_capacity_property():
    """Given a constructed buffer, capacity reports the configured size."""
    # Given
    buffer = RingBuffer(capacity=7)

    # When
    capacity = buffer.capacity

    # Then
    assert capacity == 7