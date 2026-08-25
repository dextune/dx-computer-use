"""CPU-first incremental pixel layout segmentation."""

from __future__ import annotations

import io
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image

from hpcu.perception.regions import (
    bbox_intersects,
    build_region_hierarchy,
    connected_component_boxes,
    deduplicate_regions,
    intersect_bbox,
    make_region,
    mark_repeated_blocks,
    merge_component_boxes,
    projection_profiles,
    union_bboxes,
    whitespace_runs,
)
from hpcu.runtime_core.performance import PerformanceTrace
from hpcu.schemas.coordinates import BoundingBox
from hpcu.schemas.region import RegionKind, RegionNode
from hpcu.schemas.scene import FrameHandle


@dataclass(frozen=True)
class SegmentationPolicy:
    """Configuration-driven limits and thresholds for pixel layout analysis."""

    gradient_threshold: int
    whitespace_density_threshold: float
    min_whitespace_gap_px: int
    min_region_width_px: int
    min_region_height_px: int
    min_region_area_px: int
    max_depth: int
    max_regions_per_frame: int
    max_components_per_roi: int
    merge_gap_px: float
    alignment_tolerance_px: float
    dirty_roi_max_ratio: float
    fingerprint_quantum_px: int
    repeated_size_tolerance_ratio: float
    repeated_min_group_size: int

    def __post_init__(self) -> None:
        if not 0 <= self.gradient_threshold <= 255:
            raise ValueError("gradient_threshold must be between 0 and 255")
        if not 0.0 <= self.whitespace_density_threshold <= 1.0:
            raise ValueError("whitespace_density_threshold must be between 0 and 1")
        if self.min_whitespace_gap_px <= 0:
            raise ValueError("min_whitespace_gap_px must be > 0")
        if self.min_region_width_px <= 0 or self.min_region_height_px <= 0:
            raise ValueError("minimum region dimensions must be > 0")
        if self.min_region_area_px <= 0:
            raise ValueError("min_region_area_px must be > 0")
        if self.max_depth <= 0:
            raise ValueError("max_depth must be > 0")
        if self.max_regions_per_frame <= 0:
            raise ValueError("max_regions_per_frame must be > 0")
        if self.max_components_per_roi <= 0:
            raise ValueError("max_components_per_roi must be > 0")
        if self.merge_gap_px < 0 or self.alignment_tolerance_px < 0:
            raise ValueError("merge thresholds must be >= 0")
        if not 0.0 <= self.dirty_roi_max_ratio <= 1.0:
            raise ValueError("dirty_roi_max_ratio must be between 0 and 1")
        if self.fingerprint_quantum_px <= 0:
            raise ValueError("fingerprint_quantum_px must be > 0")
        if not 0.0 <= self.repeated_size_tolerance_ratio <= 1.0:
            raise ValueError("repeated_size_tolerance_ratio must be between 0 and 1")
        if self.repeated_min_group_size < 3:
            raise ValueError("repeated_min_group_size must be >= 3")

    @classmethod
    def from_config(cls, config: dict) -> "SegmentationPolicy":
        perception = config["perception"]
        segmentation = perception["segmentation"]
        return cls(
            gradient_threshold=int(segmentation["gradient_threshold"]),
            whitespace_density_threshold=float(
                segmentation["whitespace_density_threshold"]
            ),
            min_whitespace_gap_px=int(segmentation["min_whitespace_gap_px"]),
            min_region_width_px=int(segmentation["min_region_width_px"]),
            min_region_height_px=int(segmentation["min_region_height_px"]),
            min_region_area_px=int(segmentation["min_region_area_px"]),
            max_depth=int(segmentation["max_depth"]),
            max_regions_per_frame=int(segmentation["max_regions_per_frame"]),
            max_components_per_roi=int(segmentation["max_components_per_roi"]),
            merge_gap_px=float(segmentation["merge_gap_px"]),
            alignment_tolerance_px=float(segmentation["alignment_tolerance_px"]),
            dirty_roi_max_ratio=float(perception["dirty_roi_max_ratio"]),
            fingerprint_quantum_px=int(segmentation["fingerprint_quantum_px"]),
            repeated_size_tolerance_ratio=float(
                segmentation["repeated_size_tolerance_ratio"]
            ),
            repeated_min_group_size=int(segmentation["repeated_min_group_size"]),
        )


@dataclass(frozen=True)
class SegmentationResult:
    """One deterministic layout update for a fresh Scene version."""

    regions: tuple[RegionNode, ...]
    processed_roi: BoundingBox | None
    processed_pixels: int
    component_count: int
    skipped: bool
    full_scan: bool

    def __post_init__(self) -> None:
        if self.processed_pixels < 0 or self.component_count < 0:
            raise ValueError("segmentation counters must be >= 0")
        if self.skipped and self.processed_roi is not None:
            raise ValueError("skipped segmentation cannot report a processed roi")
        if self.skipped and self.processed_pixels != 0:
            raise ValueError("skipped segmentation must process zero pixels")


class LayoutSegmenter:
    """Bounded pixel layout analyzer that emits neutral RegionNode objects only."""

    def __init__(
        self,
        policy: SegmentationPolicy,
        *,
        performance_trace: PerformanceTrace | None = None,
    ) -> None:
        self._policy = policy
        self._performance = performance_trace or PerformanceTrace()

    @classmethod
    def from_config(
        cls,
        config: dict,
        *,
        performance_trace: PerformanceTrace | None = None,
    ) -> "LayoutSegmenter":
        return cls(
            SegmentationPolicy.from_config(config),
            performance_trace=performance_trace,
        )

    @property
    def performance_trace(self) -> PerformanceTrace:
        return self._performance

    def segment_frame(
        self,
        frame: FrameHandle,
        png: bytes,
        *,
        scene_version: int,
        previous_regions: tuple[RegionNode, ...] = (),
        requested_roi: BoundingBox | None = None,
    ) -> SegmentationResult:
        """Segment a frame, reusing regions outside the selected dirty ROI."""
        if scene_version < 0:
            raise ValueError("scene_version must be >= 0")
        frame_roi = BoundingBox(
            space=frame.space,
            x=0,
            y=0,
            width=frame.width,
            height=frame.height,
        )
        if requested_roi is not None:
            if requested_roi.space != frame.space:
                raise ValueError("requested_roi must use the frame coordinate space")
            scan_bounds = intersect_bbox(frame_roi, requested_roi)
            if scan_bounds is None:
                raise ValueError("requested_roi does not intersect the frame")
        else:
            scan_bounds = frame_roi

        processed_roi, full_scan = self._select_processed_roi(
            frame,
            scan_bounds,
            previous_regions,
        )
        if previous_regions and processed_roi is None:
            reused = self._refresh_reused(previous_regions, scene_version)
            self._performance.increment_counter("segmentation_skipped_frames")
            return SegmentationResult(
                regions=reused,
                processed_roi=None,
                processed_pixels=0,
                component_count=0,
                skipped=True,
                full_scan=False,
            )

        processed_roi = processed_roi or scan_bounds
        with self._performance.measure("segmentation_total"):
            mask = self._edge_mask(png, processed_roi)
            area = max(0, int(round(processed_roi.width * processed_roi.height)))
            self._performance.increment_counter("segmentation_processed_pixels", area)
            self._performance.increment_counter("segmentation_scans")
            if full_scan:
                self._performance.increment_counter("segmentation_full_scans")
            else:
                self._performance.increment_counter("segmentation_roi_scans")

            with self._performance.measure("segmentation_xy_cut"):
                partitions = self._xy_cut(mask, processed_roi)

            with self._performance.measure("segmentation_components"):
                components = connected_component_boxes(
                    mask,
                    origin_x=processed_roi.x,
                    origin_y=processed_roi.y,
                    space=processed_roi.space,
                    min_area_px=self._policy.min_region_area_px,
                    max_components=self._policy.max_components_per_roi,
                )
            self._performance.increment_counter(
                "segmentation_components", len(components)
            )

            with self._performance.measure("segmentation_region_merge"):
                merged_components = merge_component_boxes(
                    components,
                    gap_px=self._policy.merge_gap_px,
                    alignment_tolerance_px=self._policy.alignment_tolerance_px,
                    max_regions=self._policy.max_regions_per_frame,
                )

            new_regions = self._make_regions(
                processed_roi,
                partitions,
                merged_components,
                scene_version,
            )
            retained = self._retain_outside(
                previous_regions,
                processed_roi,
                scene_version,
            )
            combined = deduplicate_regions((*retained, *new_regions))
            combined = combined[: self._policy.max_regions_per_frame]
            combined = mark_repeated_blocks(
                combined,
                size_tolerance_ratio=self._policy.repeated_size_tolerance_ratio,
                alignment_tolerance_px=self._policy.alignment_tolerance_px,
                minimum_group_size=self._policy.repeated_min_group_size,
            )
            regions = build_region_hierarchy(
                combined,
                max_depth=self._policy.max_depth,
            )

        return SegmentationResult(
            regions=regions,
            processed_roi=processed_roi,
            processed_pixels=area,
            component_count=len(components),
            skipped=False,
            full_scan=full_scan,
        )

    def _select_processed_roi(
        self,
        frame: FrameHandle,
        scan_bounds: BoundingBox,
        previous_regions: tuple[RegionNode, ...],
    ) -> tuple[BoundingBox | None, bool]:
        if not previous_regions:
            return scan_bounds, True
        if not frame.dirty_rects:
            return None, False

        compatible_dirty = tuple(
            rect
            for rect in frame.dirty_rects
            if rect.space == scan_bounds.space and rect.width > 0 and rect.height > 0
        )
        dirty_union = union_bboxes(compatible_dirty)
        if dirty_union is None:
            return None, False
        dirty = intersect_bbox(dirty_union, scan_bounds)
        if dirty is None:
            return None, False
        ratio = dirty.area / scan_bounds.area if scan_bounds.area > 0 else 1.0
        if ratio <= self._policy.dirty_roi_max_ratio:
            return dirty, False
        return scan_bounds, True

    def _edge_mask(self, png: bytes, roi: BoundingBox) -> np.ndarray:
        if not png:
            raise ValueError("png must be non-empty")
        image = Image.open(io.BytesIO(png)).convert("L")
        x0 = max(0, int(np.floor(roi.x)))
        y0 = max(0, int(np.floor(roi.y)))
        x1 = min(image.width, int(np.ceil(roi.x + roi.width)))
        y1 = min(image.height, int(np.ceil(roi.y + roi.height)))
        if x1 <= x0 or y1 <= y0:
            raise ValueError("processed roi has no image pixels")
        grayscale = np.asarray(image.crop((x0, y0, x1, y1)), dtype=np.int16)
        horizontal = np.zeros_like(grayscale)
        vertical = np.zeros_like(grayscale)
        if grayscale.shape[1] > 1:
            horizontal[:, 1:] = np.abs(np.diff(grayscale, axis=1))
        if grayscale.shape[0] > 1:
            vertical[1:, :] = np.abs(np.diff(grayscale, axis=0))
        return np.maximum(horizontal, vertical) >= self._policy.gradient_threshold

    def _xy_cut(
        self,
        mask: np.ndarray,
        roi: BoundingBox,
    ) -> tuple[BoundingBox, ...]:
        leaves: list[BoundingBox] = []

        def recurse(
            submask: np.ndarray,
            x_offset: int,
            y_offset: int,
            depth: int,
        ) -> None:
            height, width = submask.shape
            if (
                depth >= self._policy.max_depth
                or width < self._policy.min_region_width_px * 2
                or height < self._policy.min_region_height_px * 2
            ):
                leaves.append(
                    BoundingBox(
                        roi.space,
                        roi.x + x_offset,
                        roi.y + y_offset,
                        width,
                        height,
                    )
                )
                return

            rows, columns = projection_profiles(submask)
            row_gaps = whitespace_runs(
                rows,
                density_threshold=self._policy.whitespace_density_threshold,
                min_gap_px=self._policy.min_whitespace_gap_px,
            )
            column_gaps = whitespace_runs(
                columns,
                density_threshold=self._policy.whitespace_density_threshold,
                min_gap_px=self._policy.min_whitespace_gap_px,
            )
            candidate = self._best_cut(
                row_gaps,
                column_gaps,
                width=width,
                height=height,
            )
            if candidate is None:
                leaves.append(
                    BoundingBox(
                        roi.space,
                        roi.x + x_offset,
                        roi.y + y_offset,
                        width,
                        height,
                    )
                )
                return

            axis, start, end = candidate
            cut = (start + end) // 2
            if axis == "row":
                if (
                    cut < self._policy.min_region_height_px
                    or height - cut < self._policy.min_region_height_px
                ):
                    leaves.append(
                        BoundingBox(
                            roi.space,
                            roi.x + x_offset,
                            roi.y + y_offset,
                            width,
                            height,
                        )
                    )
                    return
                recurse(submask[:cut, :], x_offset, y_offset, depth + 1)
                recurse(submask[cut:, :], x_offset, y_offset + cut, depth + 1)
            else:
                if (
                    cut < self._policy.min_region_width_px
                    or width - cut < self._policy.min_region_width_px
                ):
                    leaves.append(
                        BoundingBox(
                            roi.space,
                            roi.x + x_offset,
                            roi.y + y_offset,
                            width,
                            height,
                        )
                    )
                    return
                recurse(submask[:, :cut], x_offset, y_offset, depth + 1)
                recurse(submask[:, cut:], x_offset + cut, y_offset, depth + 1)

        recurse(mask, 0, 0, 0)
        leaves = [
            box
            for box in leaves
            if box.area >= self._policy.min_region_area_px
        ]
        leaves.sort(key=lambda box: (box.y, box.x, -box.area))
        return tuple(leaves[: self._policy.max_regions_per_frame])

    def _best_cut(
        self,
        row_gaps: tuple[tuple[int, int], ...],
        column_gaps: tuple[tuple[int, int], ...],
        *,
        width: int,
        height: int,
    ) -> tuple[str, int, int] | None:
        candidates: list[tuple[float, str, int, int]] = []
        for start, end in row_gaps:
            if start == 0 or end == height:
                continue
            candidates.append(((end - start) / max(1, height), "row", start, end))
        for start, end in column_gaps:
            if start == 0 or end == width:
                continue
            candidates.append(((end - start) / max(1, width), "column", start, end))
        if not candidates:
            return None
        _, axis, start, end = max(
            candidates,
            key=lambda item: (item[0], item[1] == "column", -item[2]),
        )
        return axis, start, end

    def _make_regions(
        self,
        processed_roi: BoundingBox,
        partitions: tuple[BoundingBox, ...],
        components: tuple[BoundingBox, ...],
        scene_version: int,
    ) -> tuple[RegionNode, ...]:
        regions: list[RegionNode] = [
            make_region(
                processed_roi,
                scene_version=scene_version,
                kind=RegionKind.CONTAINER_CANDIDATE,
                quantum_px=self._policy.fingerprint_quantum_px,
            )
        ]
        for box in partitions:
            regions.append(
                make_region(
                    box,
                    scene_version=scene_version,
                    kind=RegionKind.CONTAINER_CANDIDATE,
                    quantum_px=self._policy.fingerprint_quantum_px,
                )
            )
        for box in components:
            if box.area < self._policy.min_region_area_px:
                continue
            regions.append(
                make_region(
                    box,
                    scene_version=scene_version,
                    kind=RegionKind.UNKNOWN,
                    quantum_px=self._policy.fingerprint_quantum_px,
                )
            )
        return deduplicate_regions(regions)

    @staticmethod
    def _retain_outside(
        previous_regions: tuple[RegionNode, ...],
        processed_roi: BoundingBox,
        scene_version: int,
    ) -> tuple[RegionNode, ...]:
        retained = []
        for region in previous_regions:
            if bbox_intersects(region.bbox, processed_roi):
                continue
            retained.append(
                replace(
                    region,
                    scene_version=scene_version,
                    parent_id=None,
                    child_ids=(),
                    stable_frames=region.stable_frames + 1,
                )
            )
        return tuple(retained)

    @staticmethod
    def _refresh_reused(
        previous_regions: tuple[RegionNode, ...],
        scene_version: int,
    ) -> tuple[RegionNode, ...]:
        return tuple(
            replace(
                region,
                scene_version=scene_version,
                stable_frames=region.stable_frames + 1,
            )
            for region in previous_regions
        )
