"""Unit tests for neutral pixel layout geometry helpers."""

import numpy as np
import pytest

from hpcu.perception.regions import (
    build_region_hierarchy,
    connected_component_boxes,
    make_region,
    mark_repeated_blocks,
    merge_component_boxes,
    projection_profiles,
    whitespace_runs,
)
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.region import RegionKind

pytestmark = pytest.mark.unit

SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


def _box(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(SPACE, x, y, width, height)


def test_projection_profiles_and_whitespace_runs_find_internal_gap():
    mask = np.zeros((8, 12), dtype=bool)
    mask[1:3, 1:11] = True
    mask[6:8, 1:11] = True

    rows, columns = projection_profiles(mask)
    gaps = whitespace_runs(rows, density_threshold=0.0, min_gap_px=2)

    assert rows[1] > 0
    assert columns[1] > 0
    assert (3, 6) in gaps


def test_connected_component_boxes_are_deterministic_and_bounded():
    mask = np.zeros((12, 20), dtype=bool)
    mask[1:4, 2:7] = True
    mask[7:11, 12:19] = True

    first = connected_component_boxes(
        mask,
        origin_x=100,
        origin_y=50,
        space=SPACE,
        min_area_px=4,
        max_components=1,
    )
    second = connected_component_boxes(
        mask,
        origin_x=100,
        origin_y=50,
        space=SPACE,
        min_area_px=4,
        max_components=1,
    )

    assert first == second
    assert len(first) == 1
    assert first[0] == _box(112, 57, 7, 4)


def test_merge_component_boxes_joins_aligned_neighbors_only():
    merged = merge_component_boxes(
        (
            _box(10, 10, 20, 10),
            _box(33, 10, 20, 10),
            _box(100, 80, 10, 10),
        ),
        gap_px=4,
        alignment_tolerance_px=2,
        max_regions=10,
    )

    assert _box(10, 10, 43, 10) in merged
    assert _box(100, 80, 10, 10) in merged
    assert len(merged) == 2


def test_region_ids_do_not_collide_when_quantized_fingerprint_matches():
    first = make_region(
        _box(1, 1, 20, 10),
        scene_version=1,
        kind=RegionKind.UNKNOWN,
        quantum_px=8,
    )
    second = make_region(
        _box(2, 1, 20, 10),
        scene_version=1,
        kind=RegionKind.UNKNOWN,
        quantum_px=8,
    )

    assert first.fingerprint == second.fingerprint
    assert first.id != second.id


def test_region_hierarchy_uses_smallest_strict_container_without_cycles():
    root = make_region(
        _box(0, 0, 200, 100),
        scene_version=3,
        kind=RegionKind.CONTAINER_CANDIDATE,
        quantum_px=4,
    )
    panel = make_region(
        _box(10, 10, 100, 70),
        scene_version=3,
        kind=RegionKind.CONTAINER_CANDIDATE,
        quantum_px=4,
    )
    child = make_region(
        _box(20, 20, 20, 10),
        scene_version=3,
        kind=RegionKind.UNKNOWN,
        quantum_px=4,
    )

    result = build_region_hierarchy((root, panel, child), max_depth=5)
    by_id = {region.id: region for region in result}

    assert by_id[child.id].parent_id == panel.id
    assert by_id[panel.id].parent_id == root.id
    assert panel.id in by_id[root.id].child_ids
    assert child.id in by_id[panel.id].child_ids


def test_repeated_block_marking_keeps_geometry_identity_stable():
    items = tuple(
        make_region(
            _box(20, 10 + index * 30, 100, 20),
            scene_version=1,
            kind=RegionKind.UNKNOWN,
            quantum_px=4,
        )
        for index in range(3)
    )
    before = tuple(region.id for region in items)

    marked = mark_repeated_blocks(
        items,
        size_tolerance_ratio=0.1,
        alignment_tolerance_px=2,
        minimum_group_size=3,
    )

    assert tuple(region.id for region in marked) == before
    assert all(region.kind is RegionKind.REPEATED_BLOCK for region in marked)
