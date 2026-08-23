"""Grounder — resolve a query to an element id using the shared scorer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:
    from hpcu.router.candidate_scoring import TargetQuery

from hpcu.runtime_config import load_runtime_config
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene


@dataclass(frozen=True)
class GroundingCandidate:
    """A candidate grounding with its score and provenance."""

    element_id: str
    confidence: float
    source: Optional[str] = None

    @property
    def id(self) -> str:
        return self.element_id


@dataclass(frozen=True)
class GroundingResult:
    """The resolved target and the full ranked candidate list."""

    element_id: Optional[str]
    confidence: float
    candidates: tuple[GroundingCandidate, ...]
    failure_code: FailureCode | None = None

    @property
    def is_resolved(self) -> bool:
        return self.element_id is not None

    @property
    def confident(self) -> bool:
        return self.is_resolved


class Grounder:
    """Scene-only resolver. Scoring is `router.candidate_scoring` only."""

    def __init__(
        self,
        confidence_threshold: Optional[float] = None,
        min_margin: Optional[float] = None,
        config: Optional[dict] = None,
    ):
        runtime = config if config is not None else load_runtime_config()
        confidence = runtime.get("confidence", {})
        self._confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else float(confidence["local_execute_threshold"])
        )
        self._min_margin = (
            min_margin
            if min_margin is not None
            else float(confidence["local_margin_min"])
        )

    def resolve(self, target_query: dict, scene: Scene) -> GroundingResult:
        from hpcu.router.candidate_scoring import TargetQuery, score_candidates

        query = TargetQuery(
            text=str(target_query.get("text") or target_query.get("name") or ""),
            role=target_query.get("role"),
        )
        scored = score_candidates(scene.elements.values(), query, min_score=0.0)
        ranked = tuple(
            GroundingCandidate(
                element_id=item.element_id,
                confidence=item.confidence,
                source=item.reason,
            )
            for item in scored
        )
        if not ranked:
            return GroundingResult(
                element_id=None,
                confidence=0.0,
                candidates=(),
                failure_code=FailureCode.GROUNDING_NO_CANDIDATES,
            )

        threshold = float(
            target_query.get("confidence_threshold", self._confidence_threshold)
        )
        min_margin = float(target_query.get("min_margin", self._min_margin))
        top = ranked[0]
        if top.confidence < threshold:
            return GroundingResult(
                element_id=None,
                confidence=top.confidence,
                candidates=ranked,
                failure_code=FailureCode.GROUNDING_CONFIDENCE_LOW,
            )
        if len(ranked) >= 2 and top.confidence - ranked[1].confidence < min_margin:
            return GroundingResult(
                element_id=None,
                confidence=top.confidence,
                candidates=ranked,
                failure_code=FailureCode.GROUNDING_AMBIGUOUS,
            )
        return GroundingResult(
            element_id=top.element_id,
            confidence=top.confidence,
            candidates=ranked,
        )

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
        scored.sort(key=lambda item: item.confidence, reverse=True)
        return scored
