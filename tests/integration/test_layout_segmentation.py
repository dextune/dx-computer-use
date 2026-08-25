"""Integration tests for bounded incremental pixel layout segmentation."""

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from hpcu.perception.segmentation import LayoutSegmenter, SegmentationPolicy
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.region import RegionKind
from hpcu.schemas.scene import FrameHandle

pytestmark = pytest.mark.integration
SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


def _policy(**overrides) -> SegmentationPolicy:
    values = {
        "gradient_threshold": 20,
        "whitespace_density_threshold": 0.01,
        "min_whitespace_gap_px": 8,
        "min_region_width_px": 20,
        "min_region_height_px": 15,
        "min_region_area_px": 20,
        "max_depth": 5,
        "max_regions_per_frame": 128,
        "max_components_per_roi": 256,
        "merge_gap_px": 5.0,
        "alignment_tolerance_px": 5.0,
        "dirty_roi_max_ratio": 0.35,
        "fingerprint_quantum_px": 4,
        "repeated_size_tolerance_ratio": 0.15,
        "repeated_min_group_size": 3,
    }
    values.update(overrides)
    return SegmentationPolicy(**values)


def _png() -> bytes:
    image = Image.new("RGB", (320, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 310, 40), outline="black", width=2)
    draw.rectangle((10, 60, 90, 190), outline="black", width=2)
    draw.rectangle((110, 60, 310, 95), outline="black", width=2)
    draw.rectangle((110, 110, 310, 145), outline="black", width=2)
    draw.rectangle((110, 160, 310, 190), outline="black", width=2)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _frame(
    timestamp_ns: int,
    *,
    dirty_rects: tuple[BoundingBox, ...] = (),
) -> FrameHandle:
    return FrameHandle(
        shm_id=f"frame-{timestamp_ns}",
        width=320,
        height=200,
        stride=1280,
        pixel_format="BGRA",
        timestamp_ns=timestamp_ns,
        space=SPACE,
        dirty_rects=dirty_rects,
    )


def _intersects(first: BoundingBox, second: BoundingBox) -> bool:
    return not (
        first.x + first.width <= second.x
        or second.x + second.width <= first.x
        or first.y + first.height <= second.y
        or second.y + second.height <= first.y
    )


def test_full_frame_segmentation_is_deterministic_and_finds_layout_partitions():
    segmenter = LayoutSegmenter(_policy())

    first = segmenter.segment_frame(_frame(1), _png(), scene_version=1)
    second = segmenter.segment_frame(_frame(2), _png(), scene_version=1)

    first_signature = tuple(
        (region.id, region.kind, region.bbox, region.parent_id, region.child_ids)
        for region in first.regions
    )
    second_signature = tuple(
        (region.id, region.kind, region.bbox, region.parent_id, region.child_ids)
        for region in second.regions
    )
    repeated = [
        region for region in first.regions if region.kind is RegionKind.REPEATED_BLOCK
    ]

    assert first.full_scan is True
    assert first.processed_pixels == 320 * 200
    assert first_signature == second_signature
    assert len(first.regions) <= 128
    assert len(repeated) >= 3


def test_small_dirty_roi_resegments_only_dirty_area_and_keeps_outside_ids():
    segmenter = LayoutSegmenter(_policy())
    initial = segmenter.segment_frame(_frame(1), _png(), scene_version=1)
    dirty = BoundingBox(SPACE, 110, 110, 202, 37)

    updated = segmenter.segment_frame(
        _frame(2, dirty_rects=(dirty,)),
        _png(),
        scene_version=2,
        previous_regions=initial.regions,
    )
    outside_ids = {
        region.id for region in initial.regions if not _intersects(region.bbox, dirty)
    }
    updated_ids = {region.id for region in updated.regions}

    assert updated.full_scan is False
    assert updated.processed_roi == dirty
    assert updated.processed_pixels == int(dirty.area)
    assert outside_ids <= updated_ids
    assert all(region.scene_version == 2 for region in updated.regions)


def test_unchanged_frame_reuses_regions_without_pixel_work():
    segmenter = LayoutSegmenter(_policy())
    initial = segmenter.segment_frame(_frame(1), _png(), scene_version=1)

    reused = segmenter.segment_frame(
        _frame(2),
        _png(),
        scene_version=2,
        previous_regions=initial.regions,
    )

    assert reused.skipped is True
    assert reused.processed_roi is None
    assert reused.processed_pixels == 0
    assert tuple(region.id for region in reused.regions) == tuple(
        region.id for region in initial.regions
    )
    assert all(region.scene_version == 2 for region in reused.regions)


def test_large_dirty_area_falls_back_to_bounded_full_scan():
    segmenter = LayoutSegmenter(_policy(dirty_roi_max_ratio=0.10))
    initial = segmenter.segment_frame(_frame(1), _png(), scene_version=1)
    dirty = BoundingBox(SPACE, 0, 0, 240, 180)

    updated = segmenter.segment_frame(
        _frame(2, dirty_rects=(dirty,)),
        _png(),
        scene_version=2,
        previous_regions=initial.regions,
    )

    assert updated.full_scan is True
    assert updated.processed_roi == BoundingBox(SPACE, 0, 0, 320, 200)
    assert updated.processed_pixels == 320 * 200


def test_segmentation_policy_reads_config_and_rejects_unbounded_values():
    config = {
        "perception": {
            "dirty_roi_max_ratio": 0.3,
            "segmentation": {
                "gradient_threshold": 24,
                "whitespace_density_threshold": 0.02,
                "min_whitespace_gap_px": 10,
                "min_region_width_px": 24,
                "min_region_height_px": 16,
                "min_region_area_px": 64,
                "max_depth": 4,
                "max_regions_per_frame": 200,
                "max_components_per_roi": 400,
                "merge_gap_px": 6,
                "alignment_tolerance_px": 5,
                "fingerprint_quantum_px": 8,
                "repeated_size_tolerance_ratio": 0.15,
                "repeated_min_group_size": 3,
            },
        }
    }

    policy = SegmentationPolicy.from_config(config)

    assert policy.gradient_threshold == 24
    assert policy.dirty_roi_max_ratio == 0.3
    with pytest.raises(ValueError, match="max_regions_per_frame"):
        _policy(max_regions_per_frame=0)
