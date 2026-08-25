"""Deterministic geometry helpers for neutral pixel layout regions."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Iterable

import numpy as np

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.region import RegionKind, RegionNode


def intersect_bbox(first: BoundingBox, second: BoundingBox) -> BoundingBox | None:
    """Return positive-area intersection for boxes in the same coordinate space."""
    if first.space != second.space:
        return None
    x0 = max(first.x, second.x)
    y0 = max(first.y, second.y)
    x1 = min(first.x + first.width, second.x + second.width)
    y1 = min(first.y + first.height, second.y + second.height)
    if x1 <= x0 or y1 <= y0:
        return None
    return BoundingBox(first.space, x0, y0, x1 - x0, y1 - y0)


def union_bboxes(boxes: Iterable[BoundingBox]) -> BoundingBox | None:
    """Return the union of compatible boxes, rejecting mixed coordinate spaces."""
    items = tuple(boxes)
    if not items:
        return None
    space = items[0].space
    if any(box.space != space for box in items):
        raise ValueError("cannot union bounding boxes from different coordinate spaces")
    x0 = min(box.x for box in items)
    y0 = min(box.y for box in items)
    x1 = max(box.x + box.width for box in items)
    y1 = max(box.y + box.height for box in items)
    return BoundingBox(space, x0, y0, x1 - x0, y1 - y0)


def bbox_intersects(first: BoundingBox, second: BoundingBox) -> bool:
    return intersect_bbox(first, second) is not None


def projection_profiles(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return row and column foreground densities for a 2-D boolean mask."""
    if mask.ndim != 2:
        raise ValueError("projection mask must be 2-D")
    if mask.size == 0:
        return np.zeros(mask.shape[0]), np.zeros(mask.shape[1])
    boolean = mask.astype(bool, copy=False)
    return boolean.mean(axis=1), boolean.mean(axis=0)


def whitespace_runs(
    profile: np.ndarray,
    *,
    density_threshold: float,
    min_gap_px: int,
) -> tuple[tuple[int, int], ...]:
    """Return half-open low-density runs with at least ``min_gap_px`` samples."""
    if not 0.0 <= density_threshold <= 1.0:
        raise ValueError("density_threshold must be between 0 and 1")
    if min_gap_px <= 0:
        raise ValueError("min_gap_px must be > 0")
    low = np.asarray(profile) <= density_threshold
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(low):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= min_gap_px:
                runs.append((start, index))
            start = None
    if start is not None and len(low) - start >= min_gap_px:
        runs.append((start, len(low)))
    return tuple(runs)


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, first: int, second: int) -> None:
        root_first = self.find(first)
        root_second = self.find(second)
        if root_first == root_second:
            return
        if self.rank[root_first] < self.rank[root_second]:
            root_first, root_second = root_second, root_first
        self.parent[root_second] = root_first
        if self.rank[root_first] == self.rank[root_second]:
            self.rank[root_first] += 1


def connected_component_boxes(
    mask: np.ndarray,
    *,
    origin_x: float,
    origin_y: float,
    space: CoordinateSpace,
    min_area_px: int,
    max_components: int,
) -> tuple[BoundingBox, ...]:
    """Run-length connected components without a SciPy/OpenCV dependency."""
    if mask.ndim != 2:
        raise ValueError("component mask must be 2-D")
    if min_area_px <= 0:
        raise ValueError("min_area_px must be > 0")
    if max_components <= 0:
        raise ValueError("max_components must be > 0")

    boolean = mask.astype(bool, copy=False)
    runs: list[tuple[int, int, int]] = []
    rows: list[list[int]] = []
    for row_index, row in enumerate(boolean):
        padded = np.pad(row.astype(np.int8), (1, 1))
        changes = np.diff(padded)
        starts = np.flatnonzero(changes == 1)
        ends = np.flatnonzero(changes == -1)
        current_indices: list[int] = []
        for start, end in zip(starts.tolist(), ends.tolist(), strict=True):
            current_indices.append(len(runs))
            runs.append((row_index, int(start), int(end)))
        rows.append(current_indices)

    if not runs:
        return ()

    dsu = _DisjointSet(len(runs))
    for row_index in range(1, len(rows)):
        previous_indices = rows[row_index - 1]
        current_indices = rows[row_index]
        previous_cursor = 0
        for current in current_indices:
            _, start, end = runs[current]
            while previous_cursor < len(previous_indices):
                _, _, previous_end = runs[previous_indices[previous_cursor]]
                if previous_end > start:
                    break
                previous_cursor += 1
            cursor = previous_cursor
            while cursor < len(previous_indices):
                previous = previous_indices[cursor]
                _, previous_start, previous_end = runs[previous]
                if previous_start >= end:
                    break
                dsu.union(current, previous)
                cursor += 1

    bounds: dict[int, list[int]] = {}
    areas: dict[int, int] = {}
    for index, (row, start, end) in enumerate(runs):
        root = dsu.find(index)
        current = bounds.setdefault(root, [start, row, end, row + 1])
        current[0] = min(current[0], start)
        current[1] = min(current[1], row)
        current[2] = max(current[2], end)
        current[3] = max(current[3], row + 1)
        areas[root] = areas.get(root, 0) + (end - start)

    ranked: list[tuple[int, BoundingBox]] = []
    for root, (x0, y0, x1, y1) in bounds.items():
        area = areas[root]
        if area < min_area_px:
            continue
        ranked.append(
            (
                area,
                BoundingBox(
                    space=space,
                    x=origin_x + x0,
                    y=origin_y + y0,
                    width=x1 - x0,
                    height=y1 - y0,
                ),
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1].y, item[1].x))
    return tuple(box for _, box in ranked[:max_components])


def _axis_overlap(
    first_start: float,
    first_size: float,
    second_start: float,
    second_size: float,
) -> float:
    overlap = max(
        0.0,
        min(first_start + first_size, second_start + second_size)
        - max(first_start, second_start),
    )
    denominator = min(first_size, second_size)
    return overlap / denominator if denominator > 0 else 0.0


def _bbox_gap(first: BoundingBox, second: BoundingBox) -> tuple[float, float]:
    horizontal = max(
        first.x - (second.x + second.width),
        second.x - (first.x + first.width),
        0.0,
    )
    vertical = max(
        first.y - (second.y + second.height),
        second.y - (first.y + first.height),
        0.0,
    )
    return horizontal, vertical


def merge_component_boxes(
    boxes: Iterable[BoundingBox],
    *,
    gap_px: float,
    alignment_tolerance_px: float,
    max_regions: int,
) -> tuple[BoundingBox, ...]:
    """Merge nearby aligned components with DSU, bounded by ``max_regions``."""
    if gap_px < 0 or alignment_tolerance_px < 0:
        raise ValueError("merge thresholds must be >= 0")
    if max_regions <= 0:
        raise ValueError("max_regions must be > 0")
    items = list(boxes)
    if not items:
        return ()
    dsu = _DisjointSet(len(items))
    for index, first in enumerate(items):
        for other_index in range(index + 1, len(items)):
            second = items[other_index]
            if first.space != second.space:
                continue
            horizontal_gap, vertical_gap = _bbox_gap(first, second)
            vertical_overlap = _axis_overlap(first.y, first.height, second.y, second.height)
            horizontal_overlap = _axis_overlap(first.x, first.width, second.x, second.width)
            same_row = (
                abs(first.center[1] - second.center[1]) <= alignment_tolerance_px
                or vertical_overlap >= 0.5
            )
            same_column = (
                abs(first.center[0] - second.center[0]) <= alignment_tolerance_px
                or horizontal_overlap >= 0.5
            )
            if (same_row and horizontal_gap <= gap_px) or (
                same_column and vertical_gap <= gap_px
            ):
                dsu.union(index, other_index)

    groups: dict[int, list[BoundingBox]] = {}
    for index, box in enumerate(items):
        groups.setdefault(dsu.find(index), []).append(box)
    merged = [union_bboxes(group) for group in groups.values()]
    result = [box for box in merged if box is not None]
    result.sort(key=lambda box: (-box.area, box.y, box.x))
    return tuple(result[:max_regions])


def region_fingerprint(
    bbox: BoundingBox,
    *,
    quantum_px: int,
) -> str:
    """Stable geometry fingerprint; neutral kind changes do not change identity."""
    if quantum_px <= 0:
        raise ValueError("quantum_px must be > 0")
    geometry = (
        bbox.space.value,
        round(bbox.x / quantum_px),
        round(bbox.y / quantum_px),
        round(bbox.width / quantum_px),
        round(bbox.height / quantum_px),
    )
    return hashlib.sha256(repr(geometry).encode("utf-8")).hexdigest()[:16]


def make_region(
    bbox: BoundingBox,
    *,
    scene_version: int,
    kind: RegionKind,
    quantum_px: int,
    stable_frames: int = 1,
) -> RegionNode:
    fingerprint = region_fingerprint(bbox, quantum_px=quantum_px)
    identity_geometry = (
        bbox.space.value,
        round(bbox.x, 3),
        round(bbox.y, 3),
        round(bbox.width, 3),
        round(bbox.height, 3),
    )
    identity = hashlib.sha256(
        repr(identity_geometry).encode("utf-8")
    ).hexdigest()[:16]
    return RegionNode(
        id=f"region_{identity}",
        scene_version=scene_version,
        bbox=bbox,
        kind=kind,
        fingerprint=fingerprint,
        stable_frames=stable_frames,
    )


def deduplicate_regions(regions: Iterable[RegionNode]) -> tuple[RegionNode, ...]:
    """Remove exact geometry duplicates, preferring stronger neutral structure kinds."""
    priority = {
        RegionKind.CONTAINER_CANDIDATE: 3,
        RegionKind.REPEATED_BLOCK: 2,
        RegionKind.TEXT_CLUSTER: 1,
        RegionKind.CHROME_CANDIDATE: 1,
        RegionKind.UNKNOWN: 0,
    }
    selected: dict[tuple[object, ...], RegionNode] = {}
    for region in regions:
        box = region.bbox
        key = (box.space, box.x, box.y, box.width, box.height)
        existing = selected.get(key)
        if existing is None or priority[region.kind] > priority[existing.kind]:
            selected[key] = region
    return tuple(
        sorted(
            selected.values(),
            key=lambda item: (-item.bbox.area, item.bbox.y, item.bbox.x, item.id),
        )
    )


def build_region_hierarchy(
    regions: Iterable[RegionNode],
    *,
    max_depth: int,
) -> tuple[RegionNode, ...]:
    """Assign the smallest strictly larger containing region as parent."""
    if max_depth <= 0:
        raise ValueError("max_depth must be > 0")
    items = list(deduplicate_regions(regions))
    if not items:
        return ()

    parent_by_id: dict[str, str | None] = {region.id: None for region in items}
    by_id = {region.id: region for region in items}
    for child in items:
        candidates: list[RegionNode] = []
        for parent in items:
            if parent.id == child.id or parent.bbox.space != child.bbox.space:
                continue
            if parent.bbox.area <= child.bbox.area:
                continue
            if (
                child.bbox.x >= parent.bbox.x
                and child.bbox.y >= parent.bbox.y
                and child.bbox.x + child.bbox.width <= parent.bbox.x + parent.bbox.width
                and child.bbox.y + child.bbox.height <= parent.bbox.y + parent.bbox.height
            ):
                candidates.append(parent)
        if candidates:
            parent_by_id[child.id] = min(
                candidates, key=lambda region: (region.bbox.area, region.id)
            ).id

    for region in items:
        depth = 0
        current_id = region.id
        seen: set[str] = set()
        while parent_by_id[current_id] is not None:
            if current_id in seen:
                parent_by_id[current_id] = None
                break
            seen.add(current_id)
            parent_id = parent_by_id[current_id]
            depth += 1
            if depth >= max_depth:
                parent_by_id[current_id] = None
                break
            current_id = parent_id

    children: dict[str, list[str]] = {region.id: [] for region in items}
    for child_id, parent_id in parent_by_id.items():
        if parent_id is not None and parent_id in by_id:
            children[parent_id].append(child_id)

    updated = [
        replace(
            region,
            parent_id=parent_by_id[region.id],
            child_ids=tuple(
                sorted(
                    children[region.id],
                    key=lambda child_id: (
                        by_id[child_id].bbox.y,
                        by_id[child_id].bbox.x,
                        child_id,
                    ),
                )
            ),
        )
        for region in items
    ]
    return tuple(
        sorted(updated, key=lambda item: (item.bbox.y, item.bbox.x, -item.bbox.area, item.id))
    )


def mark_repeated_blocks(
    regions: Iterable[RegionNode],
    *,
    size_tolerance_ratio: float,
    alignment_tolerance_px: float,
    minimum_group_size: int,
) -> tuple[RegionNode, ...]:
    """Mark aligned similarly-sized runs as neutral ``REPEATED_BLOCK`` regions."""
    if not 0.0 <= size_tolerance_ratio <= 1.0:
        raise ValueError("size_tolerance_ratio must be between 0 and 1")
    if alignment_tolerance_px < 0:
        raise ValueError("alignment_tolerance_px must be >= 0")
    if minimum_group_size < 3:
        raise ValueError("minimum_group_size must be >= 3")

    items = list(regions)
    repeated_ids: set[str] = set()
    for index, anchor in enumerate(items):
        peers = [anchor]
        for candidate in items[index + 1 :]:
            if candidate.bbox.space != anchor.bbox.space:
                continue
            width_ratio = min(anchor.bbox.width, candidate.bbox.width) / max(
                anchor.bbox.width, candidate.bbox.width
            )
            height_ratio = min(anchor.bbox.height, candidate.bbox.height) / max(
                anchor.bbox.height, candidate.bbox.height
            )
            same_column = abs(anchor.bbox.x - candidate.bbox.x) <= alignment_tolerance_px
            same_row = abs(anchor.bbox.y - candidate.bbox.y) <= alignment_tolerance_px
            if (
                width_ratio >= 1.0 - size_tolerance_ratio
                and height_ratio >= 1.0 - size_tolerance_ratio
                and (same_column or same_row)
            ):
                peers.append(candidate)
        if len(peers) >= minimum_group_size:
            repeated_ids.update(region.id for region in peers)

    return tuple(
        replace(region, kind=RegionKind.REPEATED_BLOCK)
        if region.id in repeated_ids and region.kind is RegionKind.UNKNOWN
        else region
        for region in items
    )
