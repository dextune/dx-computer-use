"""Confidence router and text-LLM escalation.

Phase 4 modules: candidate scoring, top-k scene summarization, and the
ModelRouter that decides when the (expensive) model must be interrupted.
No OS-native code lives here.
"""

from hpcu.router.candidate_scoring import (
    ScoredCandidate,
    TargetQuery,
    score_candidates,
)
from hpcu.router.router import (
    Decision,
    EscalationStrategy,
    ModelCallBudgetError,
    ModelRouter,
)
from hpcu.router.scene_summarizer import (
    CandidateSummary,
    SceneSummary,
    summarize_scene,
)

__all__ = [
    "CandidateSummary",
    "Decision",
    "EscalationStrategy",
    "ModelCallBudgetError",
    "ModelRouter",
    "ScoredCandidate",
    "SceneSummary",
    "TargetQuery",
    "score_candidates",
    "summarize_scene",
]
