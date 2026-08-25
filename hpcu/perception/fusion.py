"""Cross-source fusion for neutral UI observations before temporal tracking."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from types import MappingProxyType
from typing import Iterable, Mapping

import numpy as np

from hpcu.runtime_config import load_runtime_config
from hpcu.scene_graph.tracker import minimum_cost_assignment
from hpcu.schemas.coordinates import BoundingBox
from hpcu.schemas.ui_element import ElementRelations, ElementSource, UIElement

_NO_MATCH_COST = 1e6
_STRUCTURE_SOURCES = frozenset({"dom", "uia", "atspi", "ax"})
_NEUTRAL_ROLES = frozenset({"", "unknown", "generic", "text", "none"})


@dataclass(frozen=True)
class FusionPolicy:
    """Config-driven thresholds and weights for cross-source object fusion."""

    min_score: float
    min_geometry_overlap: float
    min_assignment_margin: float
    max_center_distance_px: float
    spatial_cell_size_px: int
    text_max_chars: int
    fingerprint_quantum_px: int
    weight_iou: float
    weight_containment: float
    weight_center: float
    weight_text: float
    weight_role: float
    weight_source_ref: float
    source_reliability: Mapping[str, float]

    def __post_init__(self) -> None:
        for name in ("min_score", "min_geometry_overlap", "min_assignment_margin"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.max_center_distance_px <= 0:
            raise ValueError("max_center_distance_px must be > 0")
        if self.spatial_cell_size_px <= 0:
            raise ValueError("spatial_cell_size_px must be > 0")
        if self.text_max_chars <= 0:
            raise ValueError("text_max_chars must be > 0")
        if self.fingerprint_quantum_px <= 0:
            raise ValueError("fingerprint_quantum_px must be > 0")
        weights = (
            self.weight_iou,
            self.weight_containment,
            self.weight_center,
            self.weight_text,
            self.weight_role,
            self.weight_source_ref,
        )
        if any(weight < 0 for weight in weights) or sum(weights) <= 0:
            raise ValueError(
                "fusion weights must be non-negative with a positive sum"
            )
        reliability = {
            str(key).casefold(): float(value)
            for key, value in self.source_reliability.items()
        }
        if any(not 0.0 <= value <= 1.0 for value in reliability.values()):
            raise ValueError("source reliability must be between 0 and 1")
        object.__setattr__(
            self,
            "source_reliability",
            MappingProxyType(reliability),
        )

    @classmethod
    def from_config(cls, config: dict | None = None) -> "FusionPolicy":
        runtime = config if config is not None else load_runtime_config()
        perception = runtime["perception"]
        fusion = perception["fusion"]
        weights = fusion["weights"]
        return cls(
            min_score=float(fusion["min_score"]),
            min_geometry_overlap=float(fusion["min_geometry_overlap"]),
            min_assignment_margin=float(fusion["min_assignment_margin"]),
            max_center_distance_px=float(fusion["max_center_distance_px"]),
            spatial_cell_size_px=int(fusion["spatial_cell_size_px"]),
            text_max_chars=int(fusion["text_max_chars"]),
            fingerprint_quantum_px=int(fusion["fingerprint_quantum_px"]),
            weight_iou=float(weights["iou"]),
            weight_containment=float(weights["containment"]),
            weight_center=float(weights["center"]),
            weight_text=float(weights["text"]),
            weight_role=float(weights["role"]),
            weight_source_ref=float(weights["source_ref"]),
            source_reliability=dict(fusion["source_reliability"]),
        )


@dataclass(frozen=True)
class FusionResult:
    """One deterministic fusion pass plus bounded diagnostic counts."""

    elements: tuple[UIElement, ...]
    input_count: int
    output_count: int
    candidate_pairs: int
    accepted_pairs: int
    id_remap: tuple[tuple[str, str], ...]

    @property
    def merged_count(self) -> int:
        return self.input_count - self.output_count


class _DisjointSet:
    def __init__(self, element_ids: Iterable[str]) -> None:
        self.parent = {element_id: element_id for element_id in element_ids}

    def find(self, element_id: str) -> str:
        root = element_id
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[element_id] != element_id:
            parent = self.parent[element_id]
            self.parent[element_id] = root
            element_id = parent
        return root

    def union(self, first: str, second: str) -> str:
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root == second_root:
            return first_root
        keep, move = sorted((first_root, second_root))
        self.parent[move] = keep
        return keep


class FusionEngine:
    """Fuse cross-source observations without inventing semantic meaning."""

    def __init__(
        self,
        policy: FusionPolicy | None = None,
        *,
        config: dict | None = None,
    ) -> None:
        self._policy = policy or FusionPolicy.from_config(config)

    @property
    def policy(self) -> FusionPolicy:
        return self._policy

    def fuse(self, elements: Iterable[UIElement]) -> FusionResult:
        ordered = tuple(sorted(elements, key=lambda element: element.id))
        by_id = {element.id: element for element in ordered}
        if len(by_id) != len(ordered):
            raise ValueError("fusion input element ids must be unique")
        if len(ordered) < 2:
            return FusionResult(
                elements=ordered,
                input_count=len(ordered),
                output_count=len(ordered),
                candidate_pairs=0,
                accepted_pairs=0,
                id_remap=tuple((element.id, element.id) for element in ordered),
            )

        primary_source = {
            element.id: self._primary_source(element) for element in ordered
        }
        pair_scores = self._candidate_pair_scores(ordered, primary_source)
        assigned_edges = self._assign_pairs(pair_scores, primary_source)

        dsu = _DisjointSet(by_id)
        cluster_sources = {
            element_id: set(_source_types(element) or {primary_source[element_id]})
            for element_id, element in by_id.items()
        }
        accepted_pairs = 0
        for score, first_id, second_id in sorted(
            assigned_edges,
            key=lambda item: (-item[0], item[1], item[2]),
        ):
            first_root = dsu.find(first_id)
            second_root = dsu.find(second_id)
            if first_root == second_root:
                continue
            if cluster_sources[first_root] & cluster_sources[second_root]:
                continue
            combined_sources = (
                cluster_sources[first_root] | cluster_sources[second_root]
            )
            new_root = dsu.union(first_root, second_root)
            old_root = second_root if new_root == first_root else first_root
            cluster_sources[new_root] = combined_sources
            cluster_sources.pop(old_root, None)
            accepted_pairs += 1

        clusters: dict[str, list[UIElement]] = {}
        for element in ordered:
            clusters.setdefault(dsu.find(element.id), []).append(element)

        merged_by_id: dict[str, UIElement] = {}
        id_remap: dict[str, str] = {}
        for cluster in clusters.values():
            merged = self._merge_cluster(cluster)
            merged_by_id[merged.id] = merged
            for member in cluster:
                id_remap[member.id] = merged.id

        final = tuple(
            sorted(
                (
                    replace(
                        element,
                        relations=_remap_relations(
                            element.relations,
                            id_remap,
                            owner_id=element.id,
                        ),
                    )
                    for element in merged_by_id.values()
                ),
                key=_element_order_key,
            )
        )
        return FusionResult(
            elements=final,
            input_count=len(ordered),
            output_count=len(final),
            candidate_pairs=len(pair_scores),
            accepted_pairs=accepted_pairs,
            id_remap=tuple(sorted(id_remap.items())),
        )

    def pair_score(self, first: UIElement, second: UIElement) -> float:
        """Return calibrated same-object likelihood for one cross-source pair."""

        if first.id == second.id:
            return 0.0
        first_types = _source_types(first)
        second_types = _source_types(second)
        if first_types & second_types:
            return 0.0
        if first.bbox is None or second.bbox is None:
            return 0.0
        if first.bbox.space != second.bbox.space:
            return 0.0

        role = _role_similarity(first, second)
        if (
            role == 0.0
            and _is_structure_element(first)
            and _is_structure_element(second)
        ):
            return 0.0

        iou, containment, center_distance = _geometry_features(
            first.bbox, second.bbox
        )
        source_ref_match = _source_ref_match(first, second)
        if (
            max(iou, containment) < self._policy.min_geometry_overlap
            and not source_ref_match
        ):
            return 0.0

        center = max(
            0.0,
            1.0 - center_distance / self._policy.max_center_distance_px,
        )
        text = _text_similarity(
            _element_text(first),
            _element_text(second),
            self._policy.text_max_chars,
        )
        weighted = [
            (iou, self._policy.weight_iou),
            (containment, self._policy.weight_containment),
            (center, self._policy.weight_center),
            (role, self._policy.weight_role),
        ]
        if text is not None:
            weighted.append((text, self._policy.weight_text))
        if source_ref_match:
            weighted.append((1.0, self._policy.weight_source_ref))

        denominator = sum(weight for _value, weight in weighted)
        if denominator <= 0:
            return 0.0
        return sum(value * weight for value, weight in weighted) / denominator

    def _candidate_pair_scores(
        self,
        elements: tuple[UIElement, ...],
        primary_source: Mapping[str, str],
    ) -> dict[tuple[str, str], float]:
        grid: dict[tuple[object, int, int], set[str]] = {}
        by_id = {element.id: element for element in elements}
        for element in elements:
            if not _valid_box(element.bbox):
                continue
            for cell_x, cell_y in _cells_for_bbox(
                element.bbox,
                self._policy.spatial_cell_size_px,
            ):
                grid.setdefault(
                    (element.bbox.space, cell_x, cell_y), set()
                ).add(element.id)

        scores: dict[tuple[str, str], float] = {}
        for element in elements:
            if not _valid_box(element.bbox):
                continue
            candidates: set[str] = set()
            for cell_x, cell_y in _cells_for_bbox(
                element.bbox,
                self._policy.spatial_cell_size_px,
                expand_px=self._policy.max_center_distance_px,
            ):
                candidates.update(
                    grid.get((element.bbox.space, cell_x, cell_y), ())
                )
            for other_id in candidates:
                if other_id <= element.id:
                    continue
                if primary_source[element.id] == primary_source[other_id]:
                    continue
                other = by_id[other_id]
                score = self.pair_score(element, other)
                if score >= self._policy.min_score:
                    scores[(element.id, other_id)] = score
        return scores

    def _assign_pairs(
        self,
        pair_scores: Mapping[tuple[str, str], float],
        primary_source: Mapping[str, str],
    ) -> tuple[tuple[float, str, str], ...]:
        grouped: dict[
            tuple[str, str],
            dict[tuple[str, str], float],
        ] = {}
        for (first_id, second_id), score in pair_scores.items():
            first_source = primary_source[first_id]
            second_source = primary_source[second_id]
            if first_source < second_source:
                left_id, right_id = first_id, second_id
                key = (first_source, second_source)
            else:
                left_id, right_id = second_id, first_id
                key = (second_source, first_source)
            grouped.setdefault(key, {})[(left_id, right_id)] = score

        accepted: list[tuple[float, str, str]] = []
        for edges in grouped.values():
            for left_ids, right_ids in _bipartite_components(edges):
                matrix = np.full(
                    (len(left_ids), len(right_ids)),
                    _NO_MATCH_COST,
                    dtype=np.float64,
                )
                left_index = {
                    element_id: index for index, element_id in enumerate(left_ids)
                }
                right_index = {
                    element_id: index for index, element_id in enumerate(right_ids)
                }
                for (left_id, right_id), score in edges.items():
                    if left_id in left_index and right_id in right_index:
                        matrix[
                            left_index[left_id], right_index[right_id]
                        ] = 1.0 - score

                for row, col in minimum_cost_assignment(matrix):
                    left_id = left_ids[row]
                    right_id = right_ids[col]
                    score = edges.get((left_id, right_id))
                    if score is None:
                        continue
                    alternatives = [
                        candidate_score
                        for (candidate_left, candidate_right), candidate_score
                        in edges.items()
                        if (
                            candidate_left == left_id
                            and candidate_right != right_id
                        )
                        or (
                            candidate_right == right_id
                            and candidate_left != left_id
                        )
                    ]
                    if (
                        alternatives
                        and score - max(alternatives)
                        < self._policy.min_assignment_margin
                    ):
                        continue
                    accepted.append((score, left_id, right_id))
        return tuple(accepted)

    def _primary_source(self, element: UIElement) -> str:
        if not element.sources:
            return "unknown"
        ranked = sorted(
            element.sources,
            key=lambda source: (
                -self._source_strength_for(source),
                source.type.casefold(),
                source.ref or "",
            ),
        )
        return ranked[0].type.casefold()

    def _source_strength_for(self, source: ElementSource) -> float:
        base = float(
            self._policy.source_reliability.get(source.type.casefold(), 0.5)
        )
        confidence = max(0.0, min(1.0, float(source.confidence)))
        return base * confidence

    def _element_strength(self, element: UIElement) -> float:
        if not element.sources:
            return 0.0
        return max(self._source_strength_for(source) for source in element.sources)

    def _merge_cluster(self, cluster: list[UIElement]) -> UIElement:
        if len(cluster) == 1:
            return cluster[0]

        ranked = sorted(
            cluster,
            key=lambda element: (
                0 if _is_structure_element(element) else 1,
                -self._element_strength(element),
                0 if any(source.ref for source in element.sources) else 1,
                0 if element.bbox is not None else 1,
                element.id,
            ),
        )
        canonical = ranked[0]
        role_source = _best_element(
            cluster,
            self._element_strength,
            predicate=lambda element: (
                _is_structure_element(element)
                and (element.role or "").casefold() not in _NEUTRAL_ROLES
            ),
        )
        state_source = _best_element(
            cluster,
            self._element_strength,
            predicate=_is_structure_element,
        )
        bbox_source = _best_element(
            cluster,
            self._element_strength,
            predicate=lambda element: (
                _is_structure_element(element) and element.bbox is not None
            ),
        ) or _best_element(
            cluster,
            self._element_strength,
            predicate=lambda element: element.bbox is not None,
        )
        name_source = _best_element(
            cluster,
            self._element_strength,
            predicate=lambda element: bool(element.name),
        )
        text_source = _best_element(
            cluster,
            self._element_strength,
            predicate=lambda element: bool(element.text),
        )
        normalized_bbox_source = _best_element(
            cluster,
            self._element_strength,
            predicate=lambda element: element.bbox_normalized is not None,
        )

        sources = _merge_sources(cluster, self._source_strength_for)
        semantic_tags = tuple(
            sorted(
                {
                    tag
                    for element in cluster
                    for tag in element.semantic_tags
                }
            )
        )
        positive_first_seen = [
            element.first_seen_at
            for element in cluster
            if element.first_seen_at > 0
        ]
        return replace(
            canonical,
            scene_version=max(element.scene_version for element in cluster),
            role=(role_source or canonical).role,
            name=(name_source.name if name_source is not None else canonical.name),
            text=(text_source.text if text_source is not None else canonical.text),
            bbox=(bbox_source.bbox if bbox_source is not None else canonical.bbox),
            bbox_normalized=(
                normalized_bbox_source.bbox_normalized
                if normalized_bbox_source is not None
                else canonical.bbox_normalized
            ),
            state=(state_source or canonical).state,
            relations=(state_source or canonical).relations,
            sources=sources,
            semantic_tags=semantic_tags,
            fingerprint=self._fusion_fingerprint(cluster, canonical),
            first_seen_at=min(positive_first_seen) if positive_first_seen else 0,
            last_seen_at=max(element.last_seen_at for element in cluster),
            stable_frames=max(element.stable_frames for element in cluster),
        )

    def _fusion_fingerprint(
        self,
        cluster: list[UIElement],
        canonical: UIElement,
    ) -> str:
        source_refs = sorted(
            f"{source.type.casefold()}:{source.ref}"
            for element in cluster
            for source in element.sources
            if source.ref
        )
        stable_fingerprints = sorted(
            element.fingerprint
            for element in cluster
            if element.fingerprint and _is_structure_element(element)
        )
        box = canonical.bbox
        geometry: tuple[object, ...] = ()
        if box is not None:
            quantum = self._policy.fingerprint_quantum_px
            geometry = (
                box.space.value,
                round(box.x / quantum),
                round(box.y / quantum),
                round(box.width / quantum),
                round(box.height / quantum),
            )
        payload = repr(
            (
                tuple(source_refs),
                tuple(stable_fingerprints),
                (canonical.role or "unknown").casefold(),
                geometry,
            )
        ).encode("utf-8")
        return f"fusion:{hashlib.sha256(payload).hexdigest()[:16]}"


def _best_element(
    elements: Iterable[UIElement],
    strength,
    *,
    predicate,
) -> UIElement | None:
    candidates = [element for element in elements if predicate(element)]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda element: (-strength(element), element.id),
    )[0]


def _merge_sources(
    cluster: Iterable[UIElement],
    strength,
) -> tuple[ElementSource, ...]:
    selected: dict[tuple[str, str | None], ElementSource] = {}
    for element in cluster:
        for source in element.sources:
            key = (source.type.casefold(), source.ref)
            existing = selected.get(key)
            if existing is None or strength(source) > strength(existing):
                selected[key] = source
    return tuple(
        selected[key]
        for key in sorted(
            selected,
            key=lambda item: (item[0], item[1] or ""),
        )
    )


def _source_types(element: UIElement) -> frozenset[str]:
    return frozenset(
        source.type.casefold() for source in element.sources if source.type
    )


def _is_structure_element(element: UIElement) -> bool:
    return bool(_source_types(element) & _STRUCTURE_SOURCES)


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _element_text(element: UIElement) -> str:
    return " ".join(
        value for value in (element.name, element.text) if value
    )


def _text_similarity(
    first: str,
    second: str,
    max_chars: int,
) -> float | None:
    left = _normalize_text(first)[:max_chars]
    right = _normalize_text(second)[:max_chars]
    if not left or not right:
        return None
    if left == right:
        return 1.0
    if left in right or right in left:
        ratio = min(len(left), len(right)) / max(len(left), len(right))
        return 0.8 + 0.2 * ratio
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    token_score = (
        len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        if left_tokens and right_tokens
        else 0.0
    )
    return max(token_score, SequenceMatcher(None, left, right).ratio())


def _role_similarity(first: UIElement, second: UIElement) -> float:
    left = (first.role or "unknown").strip().casefold()
    right = (second.role or "unknown").strip().casefold()
    if left == right and left not in _NEUTRAL_ROLES:
        return 1.0
    if left in _NEUTRAL_ROLES or right in _NEUTRAL_ROLES:
        return 0.5
    if _is_structure_element(first) and _is_structure_element(second):
        return 0.0
    return 0.5


def _source_ref_match(first: UIElement, second: UIElement) -> bool:
    left = {source.ref for source in first.sources if source.ref}
    right = {source.ref for source in second.sources if source.ref}
    return bool(left & right)


def _geometry_features(
    first: BoundingBox,
    second: BoundingBox,
) -> tuple[float, float, float]:
    if first.space != second.space:
        return 0.0, 0.0, math.inf
    x0 = max(first.x, second.x)
    y0 = max(first.y, second.y)
    x1 = min(first.x + first.width, second.x + second.width)
    y1 = min(first.y + first.height, second.y + second.height)
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = first.area + second.area - intersection
    iou = intersection / union if union > 0 else 0.0
    smaller = min(first.area, second.area)
    containment = intersection / smaller if smaller > 0 else 0.0
    return iou, containment, math.dist(first.center, second.center)


def _valid_box(box: BoundingBox | None) -> bool:
    return box is not None and box.width > 0 and box.height > 0


def _cells_for_bbox(
    bbox: BoundingBox,
    cell_size: int,
    *,
    expand_px: float = 0.0,
) -> tuple[tuple[int, int], ...]:
    left = bbox.x - expand_px
    top = bbox.y - expand_px
    right = math.nextafter(bbox.x + bbox.width + expand_px, -math.inf)
    bottom = math.nextafter(bbox.y + bbox.height + expand_px, -math.inf)
    first_x = math.floor(left / cell_size)
    last_x = math.floor(right / cell_size)
    first_y = math.floor(top / cell_size)
    last_y = math.floor(bottom / cell_size)
    return tuple(
        (cell_x, cell_y)
        for cell_x in range(first_x, last_x + 1)
        for cell_y in range(first_y, last_y + 1)
    )


def _bipartite_components(
    edges: Mapping[tuple[str, str], float],
) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    left_to_right: dict[str, set[str]] = {}
    right_to_left: dict[str, set[str]] = {}
    for left_id, right_id in edges:
        left_to_right.setdefault(left_id, set()).add(right_id)
        right_to_left.setdefault(right_id, set()).add(left_id)

    components: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    visited_left: set[str] = set()
    visited_right: set[str] = set()
    for start in sorted(left_to_right):
        if start in visited_left:
            continue
        pending: list[tuple[str, bool]] = [(start, True)]
        left_members: set[str] = set()
        right_members: set[str] = set()
        while pending:
            element_id, is_left = pending.pop()
            if is_left:
                if element_id in visited_left:
                    continue
                visited_left.add(element_id)
                left_members.add(element_id)
                pending.extend(
                    (right_id, False)
                    for right_id in left_to_right.get(element_id, ())
                    if right_id not in visited_right
                )
            else:
                if element_id in visited_right:
                    continue
                visited_right.add(element_id)
                right_members.add(element_id)
                pending.extend(
                    (left_id, True)
                    for left_id in right_to_left.get(element_id, ())
                    if left_id not in visited_left
                )
        components.append(
            (tuple(sorted(left_members)), tuple(sorted(right_members)))
        )
    return tuple(components)


def _remap_relations(
    relations: ElementRelations,
    id_remap: Mapping[str, str],
    *,
    owner_id: str,
) -> ElementRelations:
    def scalar(value: str | None) -> str | None:
        if value is None:
            return None
        mapped = id_remap.get(value, value)
        return None if mapped == owner_id else mapped

    def many(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    mapped
                    for value in values
                    if (mapped := id_remap.get(value, value)) != owner_id
                }
            )
        )

    return replace(
        relations,
        parent=scalar(relations.parent),
        label_for=scalar(relations.label_for),
        same_row=many(relations.same_row),
        same_column=many(relations.same_column),
        above=many(relations.above),
        below=many(relations.below),
        left_of=many(relations.left_of),
        right_of=many(relations.right_of),
        contains=many(relations.contains),
        overlays=many(relations.overlays),
        modal_owner=scalar(relations.modal_owner),
        scroll_container=scalar(relations.scroll_container),
    )


def _element_order_key(
    element: UIElement,
) -> tuple[str, float, float, str]:
    if element.bbox is None:
        return ("~", math.inf, math.inf, element.id)
    return (
        element.bbox.space.value,
        element.bbox.y,
        element.bbox.x,
        element.id,
    )
