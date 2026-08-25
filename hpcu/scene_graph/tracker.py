"""Track UI elements across scene versions.

An `ElementTracker` matches elements in a current scene against a
previous scene to discover which identifiers persist and which are new.
Matching is deterministic: by stable fingerprint first, then by optimal
(Hungarian) bounding-box IoU assignment.
"""

from typing import Mapping

import numpy as np

from hpcu.schemas.coordinates import BoundingBox
from hpcu.schemas.ui_element import UIElement

_NO_MATCH_COST = 1e6


class ElementTracker:
    """Tracks new elements in a scene against a previous scene.

    `match_by_fingerprint` pairs elements sharing a stable fingerprint.
    `match_by_iou` performs an optimal assignment (Hungarian algorithm)
    by bounding-box overlap, keeping pairs above `iou_threshold`.
    `track_new_elements` reports current ids with no preceding match.
    """

    def __init__(self, iou_threshold: float = 0.0) -> None:
        self.iou_threshold = iou_threshold

    def match_by_fingerprint(
        self,
        current: Mapping[str, UIElement],
        previous: Mapping[str, UIElement],
    ) -> dict[str, str]:
        """Map each current element id to a previous id with the same fingerprint."""
        previous_by_fingerprint: dict[str, list[str]] = {}
        for element_id, element in previous.items():
            if element.fingerprint:
                previous_by_fingerprint.setdefault(
                    element.fingerprint, []
                ).append(element_id)

        result: dict[str, str] = {}
        allocated: set[str] = set()
        for element_id, element in current.items():
            if not element.fingerprint:
                continue
            for candidate in previous_by_fingerprint.get(
                element.fingerprint, ()
            ):
                if candidate not in allocated:
                    result[element_id] = candidate
                    allocated.add(candidate)
                    break
        return result

    def match_by_iou(
        self,
        current: Mapping[str, UIElement],
        previous: Mapping[str, UIElement],
    ) -> dict[str, str]:
        """Map current ids to previous ids via optimal bounding-box IoU assignment."""
        if not current or not previous:
            return {}
        current_ids = list(current)
        previous_ids = list(previous)
        cost = np.full(
            (len(current_ids), len(previous_ids)),
            _NO_MATCH_COST,
        )
        for row, current_id in enumerate(current_ids):
            current_box = current[current_id].bbox
            if current_box is None:
                continue
            for col, previous_id in enumerate(previous_ids):
                previous_box = previous[previous_id].bbox
                if previous_box is None:
                    continue
                iou = _bounding_box_iou(current_box, previous_box)
                cost[row, col] = 1.0 - iou

        assignment = minimum_cost_assignment(cost)
        result: dict[str, str] = {}
        for row, col in assignment:
            iou = 1.0 - cost[row, col]
            if iou > self.iou_threshold:
                result[current_ids[row]] = previous_ids[col]
        return result

    def track_new_elements(
        self,
        current: Mapping[str, UIElement],
        previous: Mapping[str, UIElement],
    ) -> list[str]:
        """Return current ids not matched to a previous element."""
        matched: dict[str, str] = {}
        matched.update(self.match_by_fingerprint(current, previous))
        for current_id, previous_id in self.match_by_iou(
            current, previous
        ).items():
            matched.setdefault(current_id, previous_id)
        return [
            element_id
            for element_id in current
            if element_id not in matched
        ]


def _bounding_box_iou(first: BoundingBox, second: BoundingBox) -> float:
    intersect_width = max(
        0.0,
        min(first.x + first.width, second.x + second.width)
        - max(first.x, second.x),
    )
    intersect_height = max(
        0.0,
        min(first.y + first.height, second.y + second.height)
        - max(first.y, second.y),
    )
    intersection = intersect_width * intersect_height
    union = (
        first.width * first.height
        + second.width * second.height
        - intersection
    )
    if union <= 0.0:
        return 0.0
    return intersection / union


def minimum_cost_assignment(
    cost_matrix: np.ndarray,
) -> list[tuple[int, int]]:
    """Return deterministic minimum-cost row/column assignments.

    This is the shared Hungarian primitive for temporal tracking and
    cross-source observation fusion.
    """
    return _hungarian_assign(cost_matrix)


def _hungarian_assign(cost_matrix: np.ndarray) -> list[tuple[int, int]]:
    """Min-cost assignment for a (possibly rectangular) cost matrix.

    Returns a list of `(row, col)` pairs choosing the minimum total cost,
    where each row and each column is used at most once. The matrix is
    padded with a large sentinel cost to square it for the Hungarian
    algorithm; padded rows/columns are omitted from the result.
    """
    costs = np.asarray(cost_matrix, dtype=np.float64)
    rows, cols = costs.shape
    size = max(rows, cols)
    padded = np.full((size, size), _NO_MATCH_COST)
    padded[:rows, :cols] = costs

    infimum = 1e12
    potentials_u = np.zeros(size, dtype=np.float64)
    potentials_v = np.zeros(size, dtype=np.float64)
    parent_row = np.zeros(size + 1, dtype=np.int64)
    predecessor = np.zeros(size + 1, dtype=np.int64)

    for index in range(1, size + 1):
        parent_row[0] = index
        free_col = 0
        min_reduced = np.full(size + 1, infimum, dtype=np.float64)
        used = np.zeros(size + 1, dtype=bool)

        while True:
            used[free_col] = True
            row = parent_row[free_col]
            delta = infimum
            best_col = 0
            for candidate_col in range(1, size + 1):
                if not used[candidate_col]:
                    reduced_cost = (
                        padded[row - 1, candidate_col - 1]
                        - potentials_u[row - 1]
                        - potentials_v[candidate_col - 1]
                    )
                    if min_reduced[candidate_col] > reduced_cost:
                        min_reduced[candidate_col] = reduced_cost
                        predecessor[candidate_col] = free_col
                    if min_reduced[candidate_col] < delta:
                        delta = min_reduced[candidate_col]
                        best_col = candidate_col
            for candidate_col in range(size + 1):
                if used[candidate_col]:
                    potentials_u[parent_row[candidate_col] - 1] += delta
                    potentials_v[candidate_col] -= delta
                else:
                    min_reduced[candidate_col] -= delta
            free_col = best_col
            if parent_row[free_col] == 0:
                break

        while True:
            next_col = predecessor[free_col]
            parent_row[free_col] = parent_row[next_col]
            free_col = next_col
            if free_col == 0:
                break

    assignment: list[tuple[int, int]] = []
    for col in range(1, size + 1):
        row = parent_row[col] - 1
        if row < rows and col - 1 < cols:
            assignment.append((int(row), int(col - 1)))
    return assignment
