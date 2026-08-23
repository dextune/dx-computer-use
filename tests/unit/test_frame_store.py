"""Unit tests for the common FrameStore."""

import pytest

from hpcu.capture.frame_store import FrameStore


@pytest.mark.unit
def test_frame_store_put_get_contains():
    store = FrameStore()
    store.put("shm-1", b"\x89PNG")
    assert "shm-1" in store
    assert "missing" not in store
    assert store.get("shm-1") == b"\x89PNG"
    assert store.total_bytes == 4


@pytest.mark.unit
def test_frame_store_missing_raises():
    store = FrameStore()
    with pytest.raises(KeyError):
        store.get("nope")


@pytest.mark.unit
def test_frame_store_evicts_oldest_by_count():
    store = FrameStore(max_frames=2)
    store.put("a", b"aa")
    store.put("b", b"bb")
    store.put("c", b"cc")
    assert "a" not in store
    assert "b" in store and "c" in store
    assert store.total_bytes == 4


@pytest.mark.unit
def test_frame_store_evicts_oldest_by_byte_budget():
    store = FrameStore(max_frames=8, max_bytes=5)
    store.put("a", b"aaa")
    store.put("b", b"bb")
    store.put("c", b"cc")
    assert "a" not in store
    assert "b" in store and "c" in store
    assert store.total_bytes == 4


@pytest.mark.unit
def test_frame_store_replacement_discard_and_clear_keep_byte_accounting():
    store = FrameStore(max_bytes=8)
    store.put("a", b"aa")
    store.put("a", b"aaaa")
    assert store.total_bytes == 4
    store.discard("a")
    assert store.total_bytes == 0
    store.put("b", b"bb")
    store.clear()
    assert len(store) == 0
    assert store.total_bytes == 0


@pytest.mark.unit
def test_frame_store_rejects_invalid_limits_and_oversized_single_frame():
    with pytest.raises(ValueError, match="max_frames"):
        FrameStore(max_frames=0)
    with pytest.raises(ValueError, match="max_bytes"):
        FrameStore(max_bytes=0)
    store = FrameStore(max_bytes=2)
    with pytest.raises(ValueError, match="single frame"):
        store.put("too-big", b"abc")
