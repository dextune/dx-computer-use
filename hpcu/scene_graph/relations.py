"""Deterministic geometry relations for neutral Scene elements."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from hpcu.schemas.coordinates import BoundingBox
from hpcu.schemas.ui_element import ElementRelations, UIElement


@dataclass(frozen=True)
class RelationPolicy:
    """Geometry thresholds injected from runtime configuration."""

    containment_ratio: float
    row_center_tolerance_px: float
    column_center_tolerance_px: float
    axis_overlap_ratio: float
    gap_tolerance_px: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.containment_ratio <= 1.0:
            raise ValueError("containment_ratio must be between 0 and 1")
        if not 0.0 <= self.axis_overlap_ratio <= 1.0:
            raise ValueError("axis_overlap_ratio must be between 0 and 1")
        if self.row_center_tolerance_px < 0:
            raise ValueError("row_center_tolerance_px must be >= 0")
        if self.column_center_tolerance_px < 0:
            raise ValueError("column_center_tolerance_px must be >= 0")
        if self.gap_tolerance_px < 0:
            raise ValueError("gap_tolerance_px must be >= 0")

    @classmethod
    def from_config(cls, config: dict) -> "RelationPolicy":
        values = config["scene_graph"]["relations"]
        return cls(
            containment_ratio=float(values["containment_ratio"]),
            row_center_tolerance_px=float(values["row_center_tolerance_px"]),
            column_center_tolerance_px=float(values["column_center_tolerance_px"]),
            axis_overlap_ratio=float(values["axis_overlap_ratio"]),
            gap_tolerance_px=float(values["gap_tolerance_px"]),
        )


def derive_spatial_relations(
    elements: Iterable[UIElement],
    policy: RelationPolicy,
) -> tuple[UIElement, ...]:
    """Return immutable elements with deterministic neutral geometry relations."""

    ordered = tuple(sorted(elements, key=lambda item: item.id))
    same_row = {element.id: set() for element in ordered}
    same_column = {element.id: set() for element in ordered}
    above = {element.id: set() for element in ordered}
    below = {element.id: set() for element in ordered}
    left_of = {element.id: set() for element in ordered}
    right_of = {element.id: set() for element in ordered}
    contains = {element.id: set() for element in ordered}
    parent_candidates: dict[str, list[tuple[float, str]]] = {
        element.id: [] for element in ordered
    }

    for index, first in enumerate(ordered):
        if not _valid_box(first.bbox):
            continue
        for second in ordered[index + 1 :]:
            if not _valid_box(second.bbox) or first.bbox.space != second.bbox.space:
                continue
            first_box = first.bbox
            second_box = second.bbox

            first_contains_second = _containment_ratio(first_box, second_box)
            second_contains_first = _containment_ratio(second_box, first_box)
            if (
                first_box.area >= second_box.area
                and first_contains_second >= policy.containment_ratio
            ):
                contains[first.id].add(second.id)
                parent_candidates[second.id].append((first_box.area, first.id))
            if (
                second_box.area >= first_box.area
                and second_contains_first >= policy.containment_ratio
            ):
                contains[second.id].add(first.id)
                parent_candidates[first.id].append((second_box.area, second.id))

            vertical_overlap = _axis_overlap(
                first_box.y,
                first_box.height,
                second_box.y,
                second_box.height,
            )
            horizontal_overlap = _axis_overlap(
                first_box.x,
                first_box.width,
                second_box.x,
                second_box.width,
            )
            row_aligned = (
                vertical_overlap >= policy.axis_overlap_ratio
                or abs(first_box.center[1] - second_box.center[1])
                <= policy.row_center_tolerance_px
            )
            column_aligned = (
                horizontal_overlap >= policy.axis_overlap_ratio
                or abs(first_box.center[0] - second_box.center[0])
                <= policy.column_center_tolerance_px
            )

            if row_aligned:
                same_row[first.id].add(second.id)
                same_row[second.id].add(first.id)
                if (
                    first_box.x + first_box.width
                    <= second_box.x + policy.gap_tolerance_px
                ):
                    left_of[first.id].add(second.id)
                    right_of[second.id].add(first.id)
                elif (
                    second_box.x + second_box.width
                    <= first_box.x + policy.gap_tolerance_px
                ):
                    left_of[second.id].add(first.id)
                    right_of[first.id].add(second.id)

            if column_aligned:
                same_column[first.id].add(second.id)
                same_column[second.id].add(first.id)
                if (
                    first_box.y + first_box.height
                    <= second_box.y + policy.gap_tolerance_px
                ):
                    above[first.id].add(second.id)
                    below[second.id].add(first.id)
                elif (
                    second_box.y + second_box.height
                    <= first_box.y + policy.gap_tolerance_px
                ):
                    above[second.id].add(first.id)
                    below[first.id].add(second.id)

    updated: list[UIElement] = []
    for element in ordered:
        existing = element.relations
        derived_parent = None
        candidates = parent_candidates[element.id]
        if candidates:
            derived_parent = min(candidates, key=lambda item: (item[0], item[1]))[1]
        updated.append(
            replace(
                element,
                relations=ElementRelations(
                    parent=existing.parent or derived_parent,
                    label_for=existing.label_for,
                    same_row=_merged(existing.same_row, same_row[element.id]),
                    same_column=_merged(
                        existing.same_column, same_column[element.id]
                    ),
                    above=_merged(existing.above, above[element.id]),
                    below=_merged(existing.below, below[element.id]),
                    left_of=_merged(existing.left_of, left_of[element.id]),
                    right_of=_merged(existing.right_of, right_of[element.id]),
                    contains=_merged(existing.contains, contains[element.id]),
                    overlays=existing.overlays,
                    modal_owner=existing.modal_owner,
                    scroll_container=existing.scroll_container,
                    repeated_group=existing.repeated_group,
                ),
            )
        )
    return tuple(updated)


def _valid_box(box: BoundingBox | None) -> bool:
    return box is not None and box.width > 0 and box.height > 0


def _intersection_area(first: BoundingBox, second: BoundingBox) -> float:
    if first.space != second.space:
        return 0.0
    width = max(
        0.0,
        min(first.x + first.width, second.x + second.width)
        - max(first.x, second.x),
    )
    height = max(
        0.0,
        min(first.y + first.height, second.y + second.height)
        - max(first.y, second.y),
    )
    return width * height


def _containment_ratio(container: BoundingBox, child: BoundingBox) -> float:
    if child.area <= 0:
        return 0.0
    return _intersection_area(container, child) / child.area


def _axis_overlap(
    first_start: float,
    first_length: float,
    second_start: float,
    second_length: float,
) -> float:
    denominator = min(first_length, second_length)
    if denominator <= 0:
        return 0.0
    overlap = max(
        0.0,
        min(first_start + first_length, second_start + second_length)
        - max(first_start, second_start),
    )
    return overlap / denominator


def _merged(existing: tuple[str, ...], derived: set[str]) -> tuple[str, ...]:
    return tuple(sorted(set(existing).union(derived)))
