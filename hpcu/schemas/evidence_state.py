"""Structured evidence state for truthful task outcomes."""

from dataclasses import dataclass
from enum import Enum


class EvidenceStatus(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNPROVABLE = "unprovable"
    STALE = "stale"


@dataclass(frozen=True)
class EvidenceObservation:
    """One condition's screen-grounded observation."""

    condition_index: int
    status: EvidenceStatus
    scene_version: int
    matched_element_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class EvidenceState:
    """The independent verifier state for one observed scene."""

    scene_version: int
    status: EvidenceStatus
    observations: tuple[EvidenceObservation, ...] = ()

    @property
    def satisfied(self) -> bool:
        return self.status is EvidenceStatus.SATISFIED
