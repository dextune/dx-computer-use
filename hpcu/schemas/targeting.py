"""TargetingPack — per-goal vocabulary compiled at plan-time.

A TargetingPack is the ONLY source of site/CTA/consent tokens that the
common runtime uses.  No site enum, no regex fields, no click coordinates.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetingPack:
    """Per-goal lexical pack for the common runtime.

    Fields from §4 of 14-goal-compiled-targeting.md.  All token lists are
    derived from the model response or from the goal string fallback —
    never from a hardcoded site dictionary.
    """

    goal_id: str
    source: str = "goal_tokens"  # "semantic_model" | "goal_tokens"
    ready_any: tuple[str, ...] = ()
    success_any: tuple[str, ...] = ()
    forbid_any: tuple[str, ...] = ()
    pick_query: str = ""
    pick_required: bool = True
    dismiss_any: tuple[str, ...] = ()
    blocked_any: tuple[str, ...] = ()
    ignore_any: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.goal_id:
            raise ValueError("TargetingPack.goal_id must be non-empty")
        if self.source not in ("model", "goal_tokens"):
            raise ValueError(
                "TargetingPack.source must be a recognized provenance, "
                f"got {self.source!r}"
            )
