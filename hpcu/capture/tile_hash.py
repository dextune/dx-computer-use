"""Per-tile frame hashing and dirty-region diffing.

Frames are split into a grid of fixed-size tiles.  Each tile gets a
stable `TileHash`; comparing the hashes of two frames reveals which
regions changed (e.g. a cheap event loop pre-filter before full
perception).

This stub hashes tile geometry plus any frames' declared `dirty_rects`
as the change signal, since a `FrameHandle` carries no pixel bytes.
Computation is deterministic and identical frames hash identically.
"""

import hashlib
from dataclasses import dataclass

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.scene import FrameHandle


@dataclass(frozen=True)
class TileHash:
    """A hash of one tile of a captured frame."""

    tile_col: int
    tile_row: int
    x: int
    y: int
    width: int
    height: int
    digest: int
    space: CoordinateSpace


def tile_hash(frame: FrameHandle, tile_size: int = 64) -> list[TileHash]:
    """Hash every tile covering `frame`, left-to-right, top-to-bottom."""
    if tile_size <= 0:
        raise ValueError("tile_size must be a positive integer")
    space = CoordinateSpace(frame.space)
    hashes: list[TileHash] = []
    for row in range(_tile_count(frame.height, tile_size)):
        for col in range(_tile_count(frame.width, tile_size)):
            x = col * tile_size
            y = row * tile_size
            width = min(tile_size, frame.width - x)
            height = min(tile_size, frame.height - y)
            hashes.append(
                TileHash(
                    tile_col=col,
                    tile_row=row,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    digest=_tile_digest(frame, x, y, width, height),
                    space=space,
                )
            )
    return hashes


def compute_dirty_roi(
    prev_hashes: list[TileHash], curr_hashes: list[TileHash]
) -> list[BoundingBox]:
    """Return bounding boxes for tiles whose hash changed between frames."""
    if not curr_hashes:
        return []
    space = curr_hashes[0].space
    previous: dict[tuple[int, int], TileHash] = {
        (tile.tile_col, tile.tile_row): tile for tile in prev_hashes
    }
    dirty: list[tuple[int, int, int, int]] = []
    for tile in curr_hashes:
        prev = previous.get((tile.tile_col, tile.tile_row))
        if prev is None or prev.digest != tile.digest:
            dirty.append((tile.x, tile.y, tile.width, tile.height))
    merged = _merge_rectangles(dirty)
    return [_to_bbox(rect, space) for rect in merged]


def _tile_count(length: int, tile_size: int) -> int:
    return (length + tile_size - 1) // tile_size


def _geometry_digest(x: int, y: int, width: int, height: int) -> int:
    payload = f"{x},{y},{width},{height}".encode()
    return int(hashlib.sha256(payload).hexdigest()[:16], 16)


def _rect_mask(rect: BoundingBox) -> int:
    payload = f"{rect.x},{rect.y},{rect.width},{rect.height}".encode()
    return int(hashlib.sha256(payload).hexdigest()[:16], 16)


def _tile_overlaps_rect(
    x: int, y: int, width: int, height: int, rect: BoundingBox
) -> bool:
    # Strict positive-area overlap: merely sharing an edge must not count.
    return not (
        x + width <= rect.x
        or rect.x + rect.width <= x
        or y + height <= rect.y
        or rect.y + rect.height <= y
    )


def _tile_digest(frame: FrameHandle, x: int, y: int, width: int, height: int) -> int:
    """Stable digest for a tile.

    The digest starts from the tile's geometry.  Tiles covered by a
    declared `dirty_rect` additionally flip bits from that rect, so two
    frames with different dirtied regions hash differently.
    """
    digest = _geometry_digest(x, y, width, height)
    for rect in frame.dirty_rects:
        if _tile_overlaps_rect(x, y, width, height, rect):
            digest ^= _rect_mask(rect)
    return digest


def _rects_touch(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> bool:
    """True if the two (x, y, w, h) rects overlap or share an edge."""
    x1, y1, w1, h1 = first
    x2, y2, w2, h2 = second
    return not (
        x1 + w1 < x2 or x2 + w2 < x1 or y1 + h1 < y2 or y2 + h2 < y1
    )


def _union(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    x = min(first[0], second[0])
    y = min(first[1], second[1])
    right = max(first[0] + first[2], second[0] + second[2])
    bottom = max(first[1] + first[3], second[1] + second[3])
    return (x, y, right - x, bottom - y)


def _merge_rectangles(
    rects: list[tuple[int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    """Merge touching/overlapping rects until no further merges are possible."""
    merged: list[tuple[int, int, int, int]] = []
    for rect in rects:
        merged.append(rect)

    changed = True
    while changed:
        changed = False
        index = 0
        while index < len(merged):
            cursor = index + 1
            while cursor < len(merged):
                if _rects_touch(merged[index], merged[cursor]):
                    merged[index] = _union(merged[index], merged[cursor])
                    merged.pop(cursor)
                    changed = True
                else:
                    cursor += 1
            index += 1
    return merged


def _to_bbox(rect: tuple[int, int, int, int], space: CoordinateSpace) -> BoundingBox:
    x, y, width, height = rect
    return BoundingBox(space=space, x=x, y=y, width=width, height=height)