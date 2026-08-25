"""Candidate scoring — deterministic Screen Understanding grounding V2.

The scorer receives an already-compiled target query. It never invents target
semantics from user language. Features are bounded to [0, 1] and the public
ranking is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from hpcu.schemas.ui_element import UIElement

_DEFAULT_WEIGHTS: dict[str, float] = {
    "text": 0.60,
    "role": 0.10,
    "source": 0.10,
    "structure": 0.07,
    "relation": 0.08,
    "temporal": 0.05,
}

_DEFAULT_SOURCE_RELIABILITY: dict[str, float] = {
    "dom": 1.00,
    "uia": 0.96,
    "atspi": 0.93,
    "ax": 0.93,
    "ocr": 0.55,
    "template": 0.60,
    "unknown": 0.40,
}

_SUPPORTED_RELATIONS = frozenset(
    {
        "inside",
        "parent",
        "same_row",
        "same_column",
        "above",
        "below",
        "left_of",
        "right_of",
        "contains",
    }
)


@dataclass(frozen=True)
class TargetQuery:
    """Structured target semantics compiled before local grounding."""

    text: str = ""
    role: Optional[str] = None
    relation: Optional[str] = None
    anchor_element_id: Optional[str] = None

    def __post_init__(self) -> None:
        relation = (self.relation or "").strip().casefold()
        anchor = (self.anchor_element_id or "").strip()
        if relation and relation not in _SUPPORTED_RELATIONS:
            raise ValueError(f"unsupported target relation: {self.relation!r}")
        if bool(relation) != bool(anchor):
            raise ValueError(
                "relation and anchor_element_id must be supplied together"
            )


@dataclass(frozen=True)
class ScoringPolicy:
    """Config-driven weights and source calibration for local grounding."""

    weights: Mapping[str, float]
    source_reliability: Mapping[str, float]
    temporal_stable_frames: int = 3
    confidence_coverage_weight: float = 0.10

    def __post_init__(self) -> None:
        weights = {str(key): float(value) for key, value in self.weights.items()}
        missing = set(_DEFAULT_WEIGHTS) - set(weights)
        if missing:
            raise ValueError(f"missing grounding weights: {sorted(missing)!r}")
        if any(value < 0 for value in weights.values()) or sum(weights.values()) <= 0:
            raise ValueError("grounding weights must be non-negative and non-zero")
        reliability = {
            str(key).casefold(): float(value)
            for key, value in self.source_reliability.items()
        }
        if any(not 0.0 <= value <= 1.0 for value in reliability.values()):
            raise ValueError("source reliability must be in [0, 1]")
        if self.temporal_stable_frames <= 0:
            raise ValueError("temporal_stable_frames must be positive")
        if not 0.0 <= self.confidence_coverage_weight <= 1.0:
            raise ValueError("confidence_coverage_weight must be in [0, 1]")
        object.__setattr__(self, "weights", weights)
        object.__setattr__(self, "source_reliability", reliability)

    @classmethod
    def from_config(cls, config: dict | None = None) -> "ScoringPolicy":
        grounding = (config or {}).get("grounding", {})
        configured_weights = grounding.get("weights", {})
        weights = dict(_DEFAULT_WEIGHTS)
        if isinstance(configured_weights, dict):
            weights.update(configured_weights)
        reliability = dict(_DEFAULT_SOURCE_RELIABILITY)
        configured_reliability = grounding.get("source_reliability", {})
        if isinstance(configured_reliability, dict):
            reliability.update(configured_reliability)
        return cls(
            weights=weights,
            source_reliability=reliability,
            temporal_stable_frames=int(
                grounding.get("temporal_stable_frames", 3)
            ),
            confidence_coverage_weight=float(
                grounding.get("confidence_coverage_weight", 0.10)
            ),
        )


@dataclass(frozen=True)
class ScoredCandidate:
    """One ranked local candidate and transparent feature diagnostics."""

    element_id: str
    score: float
    confidence: float
    reason: str
    feature_coverage: float = 0.0
    relation_match: float = 0.0
    temporal_stability: float = 0.0


def _normalize(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(text.strip().casefold().split())


def _text_match(element: UIElement, query_text: str) -> float:
    q = _normalize(query_text)
    if not q:
        return 0.0
    values = tuple(
        dict.fromkeys(
            normalized
            for normalized in (_normalize(element.text), _normalize(element.name))
            if normalized
        )
    )
    if not values:
        return 0.0
    if q in values:
        return 1.0
    hay = " ".join(values)
    if q == hay:
        return 1.0
    if q in hay:
        ratio = len(q) / len(hay)
        return min(0.9, 0.72 + 0.18 * ratio)
    q_tokens = set(q.split())
    hay_tokens = set(hay.split())
    if q_tokens and hay_tokens:
        overlap = len(q_tokens & hay_tokens) / len(q_tokens)
        if overlap >= 0.5:
            return min(0.9, 0.65 + 0.25 * overlap)
    return 0.0


def _role_match(element: UIElement, query: TargetQuery) -> float:
    requested_role = (query.role or "").strip().casefold()
    if not requested_role:
        return 0.0
    role = (element.role or "").strip().casefold()
    if role in ("", "unknown"):
        return 0.0
    return 1.0 if role == requested_role else 0.0


def _source_reliability(element: UIElement, policy: ScoringPolicy) -> float:
    if not element.sources:
        return float(policy.source_reliability.get("unknown", 0.4))
    best = 0.0
    for source in element.sources:
        reliability = policy.source_reliability.get(
            source.type.casefold(),
            policy.source_reliability.get("unknown", 0.4),
        )
        confidence = max(0.0, min(1.0, float(source.confidence)))
        best = max(best, reliability * confidence)
    return best


def _structure_match(element: UIElement) -> float:
    value = 0.5
    if element.state.visible:
        value += 0.2
    if element.state.enabled:
        value += 0.1
    if element.state.occluded:
        value -= 0.4
    if element.bbox is not None:
        value += 0.2
    return _clamp(value)


def _relation_match(element: UIElement, query: TargetQuery) -> float:
    relation = (query.relation or "").strip().casefold()
    anchor = (query.anchor_element_id or "").strip()
    if not relation:
        return 0.0
    relations = element.relations
    if relation in ("inside", "parent"):
        return 1.0 if relations.parent == anchor else 0.0
    values = getattr(relations, relation, ())
    return 1.0 if anchor in values else 0.0


def _temporal_stability(element: UIElement, policy: ScoringPolicy) -> float:
    frames = max(1, int(element.stable_frames))
    return min(1.0, frames / float(policy.temporal_stable_frames))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _effective_weights(query: TargetQuery, policy: ScoringPolicy) -> dict[str, float]:
    """Redistribute absent semantic-feature weights without inventing features."""
    weights = {name: float(value) for name, value in policy.weights.items()}
    has_text = bool(_normalize(query.text))
    has_role = bool((query.role or "").strip())
    has_relation = bool((query.relation or "").strip())

    if not has_text and has_role:
        moved = weights["text"]
        weights["text"] = 0.0
        weights["role"] += moved * 0.70
        weights["source"] += moved * 0.20
        weights["structure"] += moved * 0.10
    elif has_text and not has_role:
        moved = weights["role"]
        weights["role"] = 0.0
        weights["text"] += moved * 0.80
        weights["source"] += moved * 0.10
        weights["temporal"] += moved * 0.10

    if not has_relation:
        moved = weights["relation"]
        weights["relation"] = 0.0
        if has_text:
            weights["text"] += moved * 0.80
            weights["source"] += moved * 0.10
            weights["temporal"] += moved * 0.10
        elif has_role:
            weights["role"] += moved * 0.70
            weights["source"] += moved * 0.20
            weights["structure"] += moved * 0.10
    return weights


def _query_feature_coverage(
    query: TargetQuery,
    *,
    text: float,
    role: float,
    relation: float,
) -> float:
    requested: list[float] = []
    if _normalize(query.text):
        requested.append(text)
    if (query.role or "").strip():
        requested.append(role)
    if (query.relation or "").strip():
        requested.append(relation)
    if not requested:
        return 0.0
    return sum(requested) / len(requested)


def _score_element(
    element: UIElement,
    query: TargetQuery,
    policy: ScoringPolicy,
) -> ScoredCandidate:
    text = _text_match(element, query.text)
    role = _role_match(element, query)
    source = _source_reliability(element, policy)
    structure = _structure_match(element)
    relation = _relation_match(element, query)
    temporal = _temporal_stability(element, policy)
    features = {
        "text": text,
        "role": role,
        "source": source,
        "structure": structure,
        "relation": relation,
        "temporal": temporal,
    }
    weights = _effective_weights(query, policy)
    denominator = sum(weights.values())
    score = 0.0
    if denominator > 0:
        score = sum(features[name] * weights[name] for name in features) / denominator
    score = _clamp(score)
    coverage = _query_feature_coverage(
        query,
        text=text,
        role=role,
        relation=relation,
    )
    confidence_multiplier = (
        1.0
        - policy.confidence_coverage_weight
        + policy.confidence_coverage_weight * coverage
    )
    confidence = _clamp(score * confidence_multiplier)
    reason = (
        f"text={text:.2f};role={role:.2f};source={source:.2f};"
        f"structure={structure:.2f};relation={relation:.2f};"
        f"temporal={temporal:.2f};coverage={coverage:.2f}"
    )
    return ScoredCandidate(
        element_id=element.id,
        score=round(score, 4),
        confidence=round(confidence, 4),
        reason=reason,
        feature_coverage=round(coverage, 4),
        relation_match=round(relation, 4),
        temporal_stability=round(temporal, 4),
    )


def score_candidates(
    candidates: Iterable[UIElement],
    target_query: "TargetQuery | str",
    *,
    min_score: float = 0.0,
    policy: ScoringPolicy | None = None,
) -> list[ScoredCandidate]:
    """Score executable candidates against a compiled query, best-first."""
    if not isinstance(target_query, TargetQuery):
        target_query = TargetQuery(text=str(target_query or ""))
    if (
        not _normalize(target_query.text)
        and not (target_query.role or "").strip()
        and not (target_query.relation or "").strip()
    ):
        return []
    effective_policy = policy or ScoringPolicy.from_config()
    scored: list[ScoredCandidate] = []
    for element in candidates:
        if not element.state.visible or element.state.occluded:
            continue
        candidate = _score_element(element, target_query, effective_policy)
        if candidate.score >= min_score:
            scored.append(candidate)
    scored.sort(
        key=lambda item: (
            -item.score,
            -item.confidence,
            item.element_id,
        )
    )
    return scored
