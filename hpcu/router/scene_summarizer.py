"""Top-k scene summarization — condense the scene graph for the text LLM.

When the router decides the model must be consulted, it is handed a compact
`SceneSummary` instead of the raw scene graph, so prompts stay small and cheap.
"""

from dataclasses import dataclass
from typing import Optional

from hpcu.router.candidate_scoring import TargetQuery, score_candidates
from hpcu.schemas.scene import Scene


@dataclass(frozen=True)
class CandidateSummary:
    """One condensed candidate forwarded to the text LLM."""

    element_id: str
    role: str
    text: Optional[str]
    score: float
    confidence: float


@dataclass(frozen=True)
class SceneSummary:
    """Compact, immutable summary of a scene for the LLM."""

    window_title: Optional[str]
    candidate_count: int
    top_candidates: tuple[CandidateSummary, ...]
    scene_version: int = 0


def summarize_scene(
    scene: Scene,
    top_k: int = 20,
    target_query: "TargetQuery | str | None" = None,
) -> SceneSummary:
    """Summarize the top-k scored candidates of a scene.

    If `target_query` is provided the candidates are ranked against it;
    otherwise they are ranked by their generic grounding strength
    (source reliability + structure), which keeps the most trustworthy
    elements at the top for the model.
    """
    if top_k < 0:
        raise ValueError(f"top_k must be >= 0, got {top_k}")

    elements = list(scene.elements.values())
    query = target_query if isinstance(target_query, TargetQuery) else TargetQuery(
        text=str(target_query or "")
    )
    scored = score_candidates(elements, query)

    top = scored[:top_k]
    element_by_id = scene.elements

    summaries = tuple(
        CandidateSummary(
            element_id=candidate.element_id,
            role=element_by_id[candidate.element_id].role,
            text=element_by_id[candidate.element_id].text,
            score=candidate.score,
            confidence=candidate.confidence,
        )
        for candidate in top
    )
    return SceneSummary(
        window_title=scene.window_title,
        candidate_count=len(elements),
        top_candidates=summaries,
        scene_version=scene.version,
    )
