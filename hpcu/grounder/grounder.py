"""Grounder — indexed deterministic target resolution over one fresh Scene."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from hpcu.router.candidate_scoring import (
    ScoringPolicy,
    TargetQuery,
    score_candidates,
)
from hpcu.runtime_config import load_runtime_config
from hpcu.scene_graph.index import SceneIndex
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement


@dataclass(frozen=True)
class GroundingCandidate:
    """A candidate grounding with calibrated confidence and diagnostics."""

    element_id: str
    confidence: float
    source: Optional[str] = None
    score: float | None = None
    feature_coverage: float = 0.0
    temporal_stability: float = 0.0

    @property
    def id(self) -> str:
        return self.element_id


@dataclass(frozen=True)
class GroundingResult:
    """Resolved target plus a deterministic ranked candidate shortlist."""

    element_id: Optional[str]
    confidence: float
    candidates: tuple[GroundingCandidate, ...]
    failure_code: FailureCode | None = None
    margin: float = 0.0
    indexed: bool = False
    scanned_candidates: int = 0

    @property
    def is_resolved(self) -> bool:
        return self.element_id is not None

    @property
    def confident(self) -> bool:
        return self.is_resolved


class Grounder:
    """CPU-only resolver using SceneIndex before transparent V2 scoring."""

    def __init__(
        self,
        confidence_threshold: Optional[float] = None,
        min_margin: Optional[float] = None,
        config: Optional[dict] = None,
        *,
        reference_mode: bool = False,
    ):
        runtime = config if config is not None else load_runtime_config()
        confidence = runtime.get("confidence", {})
        self._confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else float(confidence.get("local_execute_threshold", 0.88))
        )
        self._min_margin = (
            min_margin
            if min_margin is not None
            else float(confidence.get("local_margin_min", 0.20))
        )
        graph = runtime.get("scene_graph", {})
        grounding = runtime.get("grounding", {})
        self._index_cell_size_px = int(graph.get("index_cell_size_px", 128))
        self._min_index_candidates = max(
            1, int(grounding.get("min_index_candidates_before_narrowing", 1))
        )
        self._max_returned_candidates = max(
            2, int(grounding.get("max_returned_candidates", 8))
        )
        self._reference_mode = bool(
            grounding.get("reference_mode", reference_mode)
        )
        self._scoring_policy = ScoringPolicy.from_config(runtime)

    def resolve(self, target_query: dict, scene: Scene) -> GroundingResult:
        query = self._query(target_query)
        if self._reference_mode:
            elements = self._reference_candidates(scene, query)
            return self._resolve_scored(
                target_query,
                scene,
                query,
                elements,
                indexed=False,
            )

        index = SceneIndex(scene, cell_size_px=self._index_cell_size_px)
        elements = self._indexed_candidates(scene, index, query)
        result = self._resolve_scored(
            target_query,
            scene,
            query,
            elements,
            indexed=True,
        )

        threshold, min_margin = self._gates(target_query)
        if (
            result.failure_code is not None
            or len(elements) < self._min_index_candidates
            or result.confidence < threshold
            or result.margin < min_margin
        ):
            reference = self._reference_candidates(scene, query)
            if len(reference) != len(elements) or {
                item.id for item in reference
            } != {item.id for item in elements}:
                return self._resolve_scored(
                    target_query,
                    scene,
                    query,
                    reference,
                    indexed=False,
                )
        return result

    def resolve_reference(self, target_query: dict, scene: Scene) -> GroundingResult:
        """Brute-force reference mode used by replay/qualification tests."""
        query = self._query(target_query)
        return self._resolve_scored(
            target_query,
            scene,
            query,
            self._reference_candidates(scene, query),
            indexed=False,
        )

    @staticmethod
    def _query(target_query: dict) -> TargetQuery:
        return TargetQuery(
            text=str(target_query.get("text") or target_query.get("name") or ""),
            role=target_query.get("role"),
            relation=target_query.get("relation"),
            anchor_element_id=(
                target_query.get("anchor_element_id")
                or target_query.get("anchor")
            ),
        )

    def _indexed_candidates(
        self,
        scene: Scene,
        index: SceneIndex,
        query: TargetQuery,
    ) -> tuple[UIElement, ...]:
        candidate_ids = set(scene.elements)

        relation = (query.relation or "").strip()
        anchor = (query.anchor_element_id or "").strip()
        if relation:
            relation_ids = set(index.by_relation(relation, anchor))
            if not relation_ids:
                return ()
            candidate_ids.intersection_update(relation_ids)

        role = (query.role or "").strip()
        if role:
            role_ids = set(index.by_role(role))
            if role_ids:
                candidate_ids.intersection_update(role_ids)

        text = query.text.strip()
        if text:
            text_ids = set(index.by_text_candidates(text))
            narrowed = candidate_ids & text_ids
            if len(narrowed) >= self._min_index_candidates:
                candidate_ids = narrowed

        return self._fresh_executable(scene, candidate_ids)

    def _reference_candidates(
        self,
        scene: Scene,
        query: TargetQuery,
    ) -> tuple[UIElement, ...]:
        candidate_ids = set(scene.elements)
        relation = (query.relation or "").strip().casefold()
        anchor = (query.anchor_element_id or "").strip()
        if relation:
            candidate_ids = {
                element.id
                for element in scene.elements.values()
                if _relation_holds(element, relation, anchor)
            }
            if not candidate_ids:
                return ()

        role = (query.role or "").strip().casefold()
        if role:
            role_ids = {
                element.id
                for element in scene.elements.values()
                if (element.role or "").strip().casefold() == role
            }
            if role_ids:
                candidate_ids.intersection_update(role_ids)
        return self._fresh_executable(scene, candidate_ids)

    @staticmethod
    def _fresh_executable(
        scene: Scene,
        candidate_ids: set[str],
    ) -> tuple[UIElement, ...]:
        return tuple(
            scene.elements[element_id]
            for element_id in sorted(candidate_ids)
            if scene.elements[element_id].scene_version == scene.version
            and scene.elements[element_id].state.visible
            and not scene.elements[element_id].state.occluded
        )

    def _resolve_scored(
        self,
        target_query: dict,
        scene: Scene,
        query: TargetQuery,
        elements: tuple[UIElement, ...],
        *,
        indexed: bool,
    ) -> GroundingResult:
        scored = score_candidates(
            elements,
            query,
            min_score=0.0,
            policy=self._scoring_policy,
        )
        ranked = tuple(
            GroundingCandidate(
                element_id=item.element_id,
                confidence=item.confidence,
                source=item.reason,
                score=item.score,
                feature_coverage=item.feature_coverage,
                temporal_stability=item.temporal_stability,
            )
            for item in scored[: self._max_returned_candidates]
        )
        if not ranked:
            return GroundingResult(
                element_id=None,
                confidence=0.0,
                candidates=(),
                failure_code=FailureCode.GROUNDING_NO_CANDIDATES,
                margin=0.0,
                indexed=indexed,
                scanned_candidates=len(elements),
            )

        threshold, min_margin = self._gates(target_query)
        top = ranked[0]
        margin = (
            top.confidence - ranked[1].confidence
            if len(ranked) >= 2
            else top.confidence
        )
        if top.confidence < threshold:
            return GroundingResult(
                element_id=None,
                confidence=top.confidence,
                candidates=ranked,
                failure_code=FailureCode.GROUNDING_CONFIDENCE_LOW,
                margin=margin,
                indexed=indexed,
                scanned_candidates=len(elements),
            )
        if len(ranked) >= 2 and margin < min_margin:
            return GroundingResult(
                element_id=None,
                confidence=top.confidence,
                candidates=ranked,
                failure_code=FailureCode.GROUNDING_AMBIGUOUS,
                margin=margin,
                indexed=indexed,
                scanned_candidates=len(elements),
            )
        return GroundingResult(
            element_id=top.element_id,
            confidence=top.confidence,
            candidates=ranked,
            margin=margin,
            indexed=indexed,
            scanned_candidates=len(elements),
        )

    def _gates(self, target_query: dict) -> tuple[float, float]:
        threshold = float(
            target_query.get("confidence_threshold", self._confidence_threshold)
        )
        min_margin = float(target_query.get("min_margin", self._min_margin))
        return threshold, min_margin

    def score_candidates(
        self,
        candidates: Sequence[GroundingCandidate],
        query: Optional[dict] = None,
        confidence_threshold: float = 0.0,
    ) -> list[GroundingCandidate]:
        """Sort already-scored candidates. Does not re-score."""
        effective = confidence_threshold
        if query and "confidence_threshold" in query:
            effective = float(query["confidence_threshold"])
        scored = [item for item in candidates if item.confidence >= effective]
        scored.sort(key=lambda item: (-item.confidence, item.element_id))
        return scored


def _relation_holds(element: UIElement, relation: str, anchor: str) -> bool:
    relations = element.relations
    if relation in ("inside", "parent"):
        return relations.parent == anchor
    values = getattr(relations, relation, ())
    return anchor in values
