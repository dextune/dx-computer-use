"""Candidate scoring — turn scene elements into ranked, calibrated candidates.

Scoring factors (weighted sum, all normalised to [0, 1]):

- text_match:      how well element text/name matches the target query text
- role_match:      whether the element role matches the requested role
- source_reliability: trust (DOM > UIA/AT-SPI/AX > OCR) times source confidence
- structure_match: visibility/enabled/occlusion + presence of a bounding box

The resulting `score` and `confidence` are both the same weighted value in
[0, 1] — high-confidence candidates can be executed without calling the model.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

from hpcu.schemas.ui_element import UIElement

TEXT_WEIGHT = 0.45
ROLE_WEIGHT = 0.15
SOURCE_WEIGHT = 0.25
STRUCTURE_WEIGHT = 0.15

# Source type -> base reliability factor.
_SOURCE_RELIABILITY: dict[str, float] = {
    "dom": 1.0,
    "uia": 0.95,
    "atspi": 0.90,
    "ax": 0.90,
    "ocr": 0.50,
    "template": 0.60,
}


@dataclass(frozen=True)
class TargetQuery:
    """What the executor is looking for in the scene.

    A plain string can also be passed to `score_candidates` and is promoted
    to a `TargetQuery` with only `text` set.
    """

    text: str = ""
    role: Optional[str] = None


@dataclass(frozen=True)
class ScoredCandidate:
    """A scene element after scoring — the boundary DTO out of this module."""

    element_id: str
    score: float
    confidence: float
    reason: str


def _normalize(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(text.strip().lower().split())


def _text_match(element: UIElement, query_text: str) -> float:
    q = _normalize(query_text)
    if not q:
        return 0.0
    hay = _normalize(" ".join(t for t in (element.text, element.name) if t))
    if not hay:
        return 0.0
    if q == hay:
        return 1.0
    if q in hay:
        # Strong substring: reward proximity to a full match.
        return min(1.0, 0.9 + 0.1 * (len(q) / len(hay)))
    q_tokens = set(q.split())
    hay_tokens = set(hay.split())
    if q_tokens and hay_tokens:
        overlap = len(q_tokens & hay_tokens) / len(q_tokens)
        if overlap >= 0.5:
            return overlap
    return 0.0


def _role_match(element: UIElement, query: TargetQuery) -> float:
    requested_role = (query.role or "").strip().lower()
    if not requested_role:
        return 0.5  # neutral — role was not requested
    if element.role == "unknown":
        return 0.0
    return 1.0 if element.role.lower() == requested_role else 0.0


def _source_reliability(element: UIElement) -> float:
    if not element.sources:
        return 0.5
    best = 0.0
    for source in element.sources:
        reliability = _SOURCE_RELIABILITY.get(source.type, 0.5)
        best = max(best, reliability * source.confidence)
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
    return max(0.0, min(1.0, value))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _score_element(element: UIElement, query: TargetQuery) -> ScoredCandidate:
    normalized_text = _normalize(query.text)
    requested_role = (query.role or "").strip()
    text = _text_match(element, query.text)
    role = _role_match(element, query)
    source = _source_reliability(element)
    structure = _structure_match(element)

    # An empty query carries no targeting information. Source/structure quality
    # must never be allowed to turn it into an executable candidate.
    if not normalized_text and not requested_role:
        score = 0.0
    else:
        # When the query is role-only, redistribute text weight to the other
        # factors so a strong structural match can reach the local threshold.
        if not normalized_text:
            text_weight = 0.0
            role_weight = ROLE_WEIGHT + TEXT_WEIGHT * 0.50
            source_weight = SOURCE_WEIGHT + TEXT_WEIGHT * 0.34
            structure_weight = STRUCTURE_WEIGHT + TEXT_WEIGHT * 0.16
        else:
            text_weight = TEXT_WEIGHT
            role_weight = ROLE_WEIGHT
            source_weight = SOURCE_WEIGHT
            structure_weight = STRUCTURE_WEIGHT

        score = _clamp(
            text * text_weight
            + role * role_weight
            + source * source_weight
            + structure * structure_weight
        )
    reason = (
        f"text={text:.2f};role={role:.2f};"
        f"source={source:.2f};structure={structure:.2f}"
    )
    return ScoredCandidate(
        element_id=element.id,
        score=round(score, 4),
        confidence=round(score, 4),
        reason=reason,
    )


def score_candidates(
    candidates: Iterable[UIElement],
    target_query: "TargetQuery | str",
    *,
    min_score: float = 0.0,
) -> list[ScoredCandidate]:
    """Score scene elements against a target query, sorted best-first.

    Elements scoring below `min_score` are filtered out.  The list is sorted
    by descending score (and confidence, then stable insertion order).
    """
    if not isinstance(target_query, TargetQuery):
        target_query = TargetQuery(text=str(target_query or ""))

    scored: list[ScoredCandidate] = []
    for element in candidates:
        candidate = _score_element(element, target_query)
        if candidate.score >= min_score:
            scored.append(candidate)

    scored.sort(key=lambda c: (c.score, c.confidence), reverse=True)
    return scored
