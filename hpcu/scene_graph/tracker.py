"""Track stable UI identity across scene versions.

Temporal tracking is deterministic and conservative. Exact source references
and stable fingerprints are resolved before a gated composite Hungarian
assignment. Dirty rectangles can bound the expensive composite stage while
exact identity evidence remains globally valid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Mapping, Sequence

import numpy as np

from hpcu.runtime_config import load_runtime_config
from hpcu.schemas.coordinates import BoundingBox
from hpcu.schemas.ui_element import UIElement

_NO_MATCH_COST = 1e6
_NEUTRAL_ROLES = frozenset({"", "unknown", "generic", "text", "none"})


@dataclass(frozen=True)
class TemporalTrackingPolicy:
    """Config-driven thresholds for composite temporal identity matching."""

    min_score: float
    min_assignment_margin: float
    max_center_distance_px: float
    dirty_boundary_px: float
    text_max_chars: int
    weight_iou: float
    weight_center: float
    weight_text: float
    weight_role: float
    weight_source_continuity: float
    weight_parent_continuity: float

    def __post_init__(self) -> None:
        for name in ("min_score", "min_assignment_margin"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.max_center_distance_px <= 0:
            raise ValueError("max_center_distance_px must be > 0")
        if self.dirty_boundary_px < 0:
            raise ValueError("dirty_boundary_px must be >= 0")
        if self.text_max_chars <= 0:
            raise ValueError("text_max_chars must be > 0")
        weights = (
            self.weight_iou,
            self.weight_center,
            self.weight_text,
            self.weight_role,
            self.weight_source_continuity,
            self.weight_parent_continuity,
        )
        if any(weight < 0 for weight in weights) or sum(weights) <= 0:
            raise ValueError(
                "temporal weights must be non-negative with a positive sum"
            )

    @classmethod
    def from_config(cls, config: dict | None = None) -> "TemporalTrackingPolicy":
        runtime = load_runtime_config()
        scene_graph = runtime.get("scene_graph", {})
        temporal = dict(scene_graph.get("temporal", {}))
        weights = dict(temporal.get("weights", {}))

        if config is not None:
            supplied_scene = config.get("scene_graph", {})
            if isinstance(supplied_scene, dict):
                supplied_temporal = supplied_scene.get("temporal", {})
                if isinstance(supplied_temporal, dict):
                    supplied_weights = supplied_temporal.get("weights", {})
                    temporal.update(
                        {
                            key: value
                            for key, value in supplied_temporal.items()
                            if key != "weights"
                        }
                    )
                    if isinstance(supplied_weights, dict):
                        weights.update(supplied_weights)

        return cls(
            min_score=float(temporal["min_score"]),
            min_assignment_margin=float(temporal["min_assignment_margin"]),
            max_center_distance_px=float(temporal["max_center_distance_px"]),
            dirty_boundary_px=float(temporal["dirty_boundary_px"]),
            text_max_chars=int(temporal["text_max_chars"]),
            weight_iou=float(weights["iou"]),
            weight_center=float(weights["center"]),
            weight_text=float(weights["text"]),
            weight_role=float(weights["role"]),
            weight_source_continuity=float(weights["source_continuity"]),
            weight_parent_continuity=float(weights["parent_continuity"]),
        )


class ElementTracker:
    """Match current UI elements to stable identifiers from the prior scene."""

    def __init__(
        self,
        iou_threshold: float = 0.0,
        *,
        temporal_policy: TemporalTrackingPolicy | None = None,
        config: dict | None = None,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.temporal_policy = temporal_policy or TemporalTrackingPolicy.from_config(
            config
        )

    def match_by_source_ref(
        self,
        current: Mapping[str, UIElement],
        previous: Mapping[str, UIElement],
    ) -> dict[str, str]:
        """Match unique exact `(source type, source ref)` identities one-to-one."""
        current_by_ref = _elements_by_source_ref(current)
        previous_by_ref = _elements_by_source_ref(previous)
        candidates: set[tuple[str, str, str, str]] = set()
        for source_key in current_by_ref.keys() & previous_by_ref.keys():
            current_ids = current_by_ref[source_key]
            previous_ids = previous_by_ref[source_key]
            if len(current_ids) != 1 or len(previous_ids) != 1:
                continue
            candidates.add(
                (source_key[0], source_key[1], current_ids[0], previous_ids[0])
            )

        result: dict[str, str] = {}
        allocated_previous: set[str] = set()
        for _source_type, _source_ref, current_id, previous_id in sorted(candidates):
            if current_id in result or previous_id in allocated_previous:
                continue
            result[current_id] = previous_id
            allocated_previous.add(previous_id)
        return result

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

    def temporal_score(
        self,
        current: UIElement,
        previous: UIElement,
        *,
        parent_matches: Mapping[str, str] | None = None,
    ) -> float:
        """Return a bounded composite continuity score for one candidate pair."""
        current_box = current.bbox
        previous_box = previous.bbox
        if not _valid_box(current_box) or not _valid_box(previous_box):
            return 0.0
        if current_box.space != previous_box.space:
            return 0.0
        if _strong_role_conflict(current, previous):
            return 0.0

        iou = _bounding_box_iou(current_box, previous_box)
        center_distance = _center_distance(current_box, previous_box)
        if center_distance > self.temporal_policy.max_center_distance_px and iou <= 0.0:
            return 0.0
        center = max(
            0.0,
            1.0 - center_distance / self.temporal_policy.max_center_distance_px,
        )
        text = _text_similarity(
            _element_text(current),
            _element_text(previous),
            self.temporal_policy.text_max_chars,
        )
        role = _role_similarity(current.role, previous.role)
        source = _source_continuity(current, previous)
        parent = _parent_continuity(current, previous, parent_matches)

        weighted: list[tuple[float, float]] = [
            (iou, self.temporal_policy.weight_iou),
            (center, self.temporal_policy.weight_center),
        ]
        for value, weight in (
            (text, self.temporal_policy.weight_text),
            (role, self.temporal_policy.weight_role),
            (source, self.temporal_policy.weight_source_continuity),
            (parent, self.temporal_policy.weight_parent_continuity),
        ):
            if value is not None:
                weighted.append((value, weight))

        denominator = sum(weight for _value, weight in weighted)
        if denominator <= 0:
            return 0.0
        return sum(value * weight for value, weight in weighted) / denominator

    def match_composite(
        self,
        current: Mapping[str, UIElement],
        previous: Mapping[str, UIElement],
        *,
        dirty_rects: Sequence[BoundingBox] = (),
        parent_matches: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Run gated composite Hungarian assignment on impacted candidates."""
        if not current or not previous:
            return {}
        current_ids = self._impacted_ids(current, dirty_rects)
        previous_ids = self._impacted_ids(previous, dirty_rects)
        if not current_ids or not previous_ids:
            return {}

        scores: dict[tuple[str, str], float] = {}
        matrix = np.full(
            (len(current_ids), len(previous_ids)),
            _NO_MATCH_COST,
            dtype=np.float64,
        )
        for row, current_id in enumerate(current_ids):
            for col, previous_id in enumerate(previous_ids):
                score = self.temporal_score(
                    current[current_id],
                    previous[previous_id],
                    parent_matches=parent_matches,
                )
                if score < self.temporal_policy.min_score:
                    continue
                scores[(current_id, previous_id)] = score
                matrix[row, col] = 1.0 - score

        result: dict[str, str] = {}
        for row, col in minimum_cost_assignment(matrix):
            current_id = current_ids[row]
            previous_id = previous_ids[col]
            score = scores.get((current_id, previous_id))
            if score is None:
                continue
            alternatives = [
                candidate_score
                for (
                    candidate_current,
                    candidate_previous,
                ), candidate_score in scores.items()
                if (
                    candidate_current == current_id
                    and candidate_previous != previous_id
                )
                or (
                    candidate_previous == previous_id
                    and candidate_current != current_id
                )
            ]
            if (
                alternatives
                and score - max(alternatives)
                < self.temporal_policy.min_assignment_margin
            ):
                continue
            result[current_id] = previous_id
        return result

    def match(
        self,
        current: Mapping[str, UIElement],
        previous: Mapping[str, UIElement],
        *,
        dirty_rects: Sequence[BoundingBox] = (),
    ) -> dict[str, str]:
        """Resolve temporal identity from strongest to weakest evidence."""
        if not current or not previous:
            return {}
        matched: dict[str, str] = {}
        allocated_previous: set[str] = set()

        source_matches = self.match_by_source_ref(current, previous)
        for current_id, previous_id in sorted(source_matches.items()):
            if previous_id in allocated_previous:
                continue
            if _strong_role_conflict(current[current_id], previous[previous_id]):
                continue
            matched[current_id] = previous_id
            allocated_previous.add(previous_id)

        # A stable native id is itself strong source-native continuity. Reserve it
        # before weaker duplicate fingerprints can steal the prior identifier.
        for element_id in sorted(current.keys() & previous.keys()):
            if element_id in matched or element_id in allocated_previous:
                continue
            if _identity_conflict(
                current[element_id],
                previous[element_id],
                self.temporal_policy.max_center_distance_px,
            ):
                continue
            matched[element_id] = element_id
            allocated_previous.add(element_id)

        remaining_current = {
            element_id: current[element_id]
            for element_id in sorted(current)
            if element_id not in matched
        }
        remaining_previous = {
            element_id: previous[element_id]
            for element_id in sorted(previous)
            if element_id not in allocated_previous
        }
        fingerprint_matches = self.match_by_fingerprint(
            remaining_current,
            remaining_previous,
        )
        for current_id, previous_id in sorted(fingerprint_matches.items()):
            if _evidence_conflict(
                current[current_id],
                previous[previous_id],
            ):
                continue
            matched[current_id] = previous_id
            allocated_previous.add(previous_id)

        remaining_current = {
            element_id: current[element_id]
            for element_id in sorted(current)
            if element_id not in matched
        }
        remaining_previous = {
            element_id: previous[element_id]
            for element_id in sorted(previous)
            if element_id not in allocated_previous
        }
        composite_matches = self.match_composite(
            remaining_current,
            remaining_previous,
            dirty_rects=dirty_rects,
            parent_matches=matched,
        )
        for current_id, previous_id in sorted(composite_matches.items()):
            if previous_id in allocated_previous:
                continue
            matched[current_id] = previous_id
            allocated_previous.add(previous_id)
        return matched

    def track_new_elements(
        self,
        current: Mapping[str, UIElement],
        previous: Mapping[str, UIElement],
    ) -> list[str]:
        """Return current ids not matched to a previous element."""
        matched = self.match(current, previous)
        return [element_id for element_id in current if element_id not in matched]

    def _impacted_ids(
        self,
        elements: Mapping[str, UIElement],
        dirty_rects: Sequence[BoundingBox],
    ) -> list[str]:
        if not dirty_rects:
            return sorted(elements)
        return [
            element_id
            for element_id in sorted(elements)
            if any(
                _intersects_with_padding(
                    elements[element_id].bbox,
                    dirty,
                    self.temporal_policy.dirty_boundary_px,
                )
                for dirty in dirty_rects
            )
        ]


def _elements_by_source_ref(
    elements: Mapping[str, UIElement],
) -> dict[tuple[str, str], list[str]]:
    by_ref: dict[tuple[str, str], list[str]] = {}
    for element_id, element in elements.items():
        for source_type, source_ref in _source_ref_keys(element):
            by_ref.setdefault((source_type, source_ref), []).append(element_id)
    for element_ids in by_ref.values():
        element_ids.sort()
    return by_ref


def _source_ref_keys(element: UIElement) -> set[tuple[str, str]]:
    return {
        (source.type.casefold(), source.ref.strip())
        for source in element.sources
        if source.ref and source.ref.strip()
    }


def _source_types(element: UIElement) -> set[str]:
    return {
        source.type.casefold()
        for source in element.sources
        if source.type and source.type.strip()
    }


def _element_text(element: UIElement) -> str | None:
    return element.text or element.name


def _normalize_text(text: str) -> str:
    return " ".join(text.casefold().split())


def _text_similarity(
    first: str | None,
    second: str | None,
    max_chars: int,
) -> float | None:
    if not first or not second:
        return None
    left = _normalize_text(first)[:max_chars]
    right = _normalize_text(second)[:max_chars]
    if not left or not right:
        return None
    if left == right:
        return 1.0
    substring = 0.0
    if left in right or right in left:
        substring = min(len(left), len(right)) / max(len(left), len(right))
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    token = 0.0
    if left_tokens and right_tokens:
        token = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    edit = SequenceMatcher(None, left, right, autojunk=False).ratio()
    return max(substring, token, edit)


def _normalized_role(role: str | None) -> str:
    return (role or "").casefold().strip()


def _role_similarity(first: str | None, second: str | None) -> float | None:
    left = _normalized_role(first)
    right = _normalized_role(second)
    if left in _NEUTRAL_ROLES and right in _NEUTRAL_ROLES:
        return None
    if left == right:
        return 1.0
    if left in _NEUTRAL_ROLES or right in _NEUTRAL_ROLES:
        return 0.5
    return 0.0


def _strong_role_conflict(first: UIElement, second: UIElement) -> bool:
    left = _normalized_role(first.role)
    right = _normalized_role(second.role)
    return (
        left not in _NEUTRAL_ROLES
        and right not in _NEUTRAL_ROLES
        and left != right
    )


def _source_continuity(first: UIElement, second: UIElement) -> float | None:
    first_refs = _source_ref_keys(first)
    second_refs = _source_ref_keys(second)
    if first_refs & second_refs:
        return 1.0
    first_types = _source_types(first)
    second_types = _source_types(second)
    if not first_types or not second_types:
        return None
    return len(first_types & second_types) / len(first_types | second_types)


def _parent_continuity(
    current: UIElement,
    previous: UIElement,
    parent_matches: Mapping[str, str] | None,
) -> float | None:
    current_parent = current.relations.parent
    previous_parent = previous.relations.parent
    if current_parent is None or previous_parent is None:
        return None
    if current_parent == previous_parent:
        return 1.0
    if (
        parent_matches is not None
        and parent_matches.get(current_parent) == previous_parent
    ):
        return 1.0
    return 0.0


def _evidence_conflict(
    current: UIElement,
    previous: UIElement,
) -> bool:
    if _strong_role_conflict(current, previous):
        return True
    current_refs = _refs_by_type(current)
    previous_refs = _refs_by_type(previous)
    for source_type in current_refs.keys() & previous_refs.keys():
        if not (current_refs[source_type] & previous_refs[source_type]):
            return True
    return False


def _identity_conflict(
    current: UIElement,
    previous: UIElement,
    max_center_distance_px: float,
) -> bool:
    if _evidence_conflict(current, previous):
        return True

    current_box = current.bbox
    previous_box = previous.bbox
    if current_box is None or previous_box is None:
        return False
    if current_box.space != previous_box.space:
        return True
    if (
        _bounding_box_iou(current_box, previous_box) <= 0.0
        and _center_distance(current_box, previous_box) > max_center_distance_px
    ):
        return True
    return False


def _refs_by_type(element: UIElement) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for source_type, source_ref in _source_ref_keys(element):
        result.setdefault(source_type, set()).add(source_ref)
    return result


def _valid_box(box: BoundingBox | None) -> bool:
    return box is not None and box.width > 0 and box.height > 0


def _center_distance(first: BoundingBox, second: BoundingBox) -> float:
    first_x, first_y = first.center
    second_x, second_y = second.center
    return math.hypot(first_x - second_x, first_y - second_y)


def _intersects_with_padding(
    box: BoundingBox | None,
    dirty: BoundingBox,
    padding: float,
) -> bool:
    if not _valid_box(box) or not _valid_box(dirty):
        return False
    if box.space != dirty.space:
        return False
    dirty_left = dirty.x - padding
    dirty_top = dirty.y - padding
    dirty_right = dirty.x + dirty.width + padding
    dirty_bottom = dirty.y + dirty.height + padding
    return (
        box.x < dirty_right
        and box.x + box.width > dirty_left
        and box.y < dirty_bottom
        and box.y + box.height > dirty_top
    )


def _bounding_box_iou(first: BoundingBox, second: BoundingBox) -> float:
    if first.space != second.space:
        return 0.0
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
    """Min-cost assignment for a (possibly rectangular) cost matrix."""
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
