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


@pytest.mark.unit
def test_frame_store_missing_raises():
    store = FrameStore()
    with pytest.raises(KeyError):
        store.get("nope")
