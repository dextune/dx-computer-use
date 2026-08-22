"""Unit tests for tile-based frame hashing and dirty-region diffing."""

import pytest

from hpcu.capture.tile_hash import TileHash, compute_dirty_roi, tile_hash
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace


def _frame(
    width: int = 200,
    height: int = 140,
    *,
    dirty_rects: tuple[BoundingBox, ...] = (),
) -> "FrameHandle":
    from hpcu.schemas.scene import FrameHandle

    return FrameHandle(
        shm_id="frame",
        width=width,
        height=height,
        stride=width * 4,
        pixel_format="BGRA",
        timestamp_ns=1,
        space="screen_physical_px",
        dirty_rects=dirty_rects,
    )


def _dirty(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(
        space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=x, y=y, width=width, height=height
    )


@pytest.mark.unit
def test_tile_hash_covers_frame():
    """Given a frame, tile_hash produces tiles covering the whole frame."""
    # Given
    frame = _frame(width=200, height=140)

    # When
    hashes = tile_hash(frame, tile_size=64)

    # Then
    assert len(hashes) == 12  # 4 cols x 3 rows
    last = hashes[-1]
    assert last.tile_col == 3
    assert last.tile_row == 2
    assert (last.x, last.y, last.width, last.height) == (192, 128, 8, 12)


@pytest.mark.unit
def test_tile_hash_is_deterministic_for_identical_frames():
    """Given two identical frames, their tile hashes are equal."""
    # Given
    first = tile_hash(_frame())
    second = tile_hash(_frame())

    # When
    equality = first == second

    # Then
    assert equality is True
    assert all(isinstance(tile, TileHash) for tile in first)


@pytest.mark.unit
def test_non_positive_tile_size_raises():
    """Given a non-positive tile_size, tile_hash raises ValueError."""
    # Given
    frame = _frame()

    # When / Then
    with pytest.raises(ValueError):
        tile_hash(frame, tile_size=0)
    with pytest.raises(ValueError):
        tile_hash(frame, tile_size=-8)


@pytest.mark.unit
def test_compute_dirty_roi_unchanged_is_empty():
    """Given identical previous and current hashes, the dirty roi is empty."""
    # Given
    previous = tile_hash(_frame())
    current = tile_hash(_frame())

    # When
    dirty = compute_dirty_roi(previous, current)

    # Then
    assert dirty == []


@pytest.mark.unit
def test_compute_dirty_roi_detects_changed_tile():
    """Given a dirtied tile region, the dirty roi covers that tile."""
    # Given
    previous = tile_hash(_frame())
    current_frame = _frame(dirty_rects=(_dirty(64, 0, 64, 64),))
    current = tile_hash(current_frame)

    # When
    dirty = compute_dirty_roi(previous, current)

    # Then
    assert len(dirty) == 1
    box = dirty[0]
    assert (box.x, box.y, box.width, box.height) == (64, 0, 64, 64)


@pytest.mark.unit
def test_compute_dirty_roi_merges_adjacent_tiles():
    """Given adjacent changed tiles, the dirty roi is a single merged box."""
    # Given
    previous = tile_hash(_frame())
    current_frame = _frame(dirty_rects=(_dirty(64, 0, 128, 64),))
    current = tile_hash(current_frame)

    # When
    dirty = compute_dirty_roi(previous, current)

    # Then
    assert len(dirty) == 1
    box = dirty[0]
    assert (box.x, box.y, box.width, box.height) == (64, 0, 128, 64)


@pytest.mark.unit
def test_compute_dirty_roi_handles_new_tile():
    """Given a new tile absent from previous hashes, it is reported as dirty."""
    # Given
    previous = tile_hash(_frame(width=128, height=64))
    current = tile_hash(_frame(width=192, height=64))

    # When
    dirty = compute_dirty_roi(previous, current)

    # Then
    extra = [box for box in dirty if box.x >= 128]
    assert len(extra) == 1
    assert extra[0].x == 128