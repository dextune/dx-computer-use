"""GoalEnvelope — structured, immutable input to planning.

The raw user instruction is retained.  Cheap deterministic parsing fills the
fields first; semantic assistance may fill only explicitly unresolved slots.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from hpcu.schemas.action import ActionOp
from hpcu.schemas.surface import SurfaceKind


class IntentKind(str, Enum):
    NAVIGATE = "navigate"
    SEARCH = "search"
    SELECT = "select"
    COMPARE = "compare"
    EDIT = "edit"
    SUBMIT = "submit"
    UNKNOWN = "unknown"


class GoalRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Reversibility(str, Enum):
    REVERSIBLE = "reversible"
    PARTIAL = "partial"
    IRREVERSIBLE = "irreversible"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GoalEntity:
    kind: str
    value: str

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.value.strip():
            raise ValueError("goal entity kind and value are required")


@dataclass(frozen=True)
class GoalConstraint:
    kind: str
    value: str
    negated: bool = False

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.value.strip():
            raise ValueError("goal constraint kind and value are required")


@dataclass(frozen=True)
class GoalEnvelope:
    raw_instruction: str
    intent: IntentKind
    terminal_state: str
    entities: tuple[GoalEntity, ...] = ()
    constraints: tuple[GoalConstraint, ...] = ()
    preferred_surface: SurfaceKind | None = None
    forbidden_actions: tuple[ActionOp, ...] = ()
    risk_class: GoalRisk = GoalRisk.LOW
    reversibility: Reversibility = Reversibility.UNKNOWN
    ambiguity_slots: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    latency_budget_ms: int = 30_000
    model_call_budget: int = 2

    def __post_init__(self) -> None:
        if not self.raw_instruction.strip():
            raise ValueError("raw user instruction is required")
        if not self.terminal_state.strip():
            raise ValueError("terminal_state is required")
        if self.latency_budget_ms <= 0:
            raise ValueError("latency budget must be positive")
        if self.model_call_budget < 0:
            raise ValueError("model-call budget must be non-negative")
        if len(set(self.ambiguity_slots)) != len(self.ambiguity_slots):
            raise ValueError("ambiguity slots must be unique")

    @property
    def canonical_hash(self) -> str:
        """Stable hash used by plan/workflow caches."""
        payload = {
            "raw": " ".join(self.raw_instruction.split()).casefold(),
            "intent": self.intent.value,
            "terminal_state": self.terminal_state,
            "entities": [(item.kind, item.value) for item in self.entities],
            "constraints": [
                (item.kind, item.value, item.negated) for item in self.constraints
            ],
            "surface": self.preferred_surface.value if self.preferred_surface else None,
            "forbidden_actions": [item.value for item in self.forbidden_actions],
            "risk": self.risk_class.value,
            "reversibility": self.reversibility.value,
            "evidence": list(self.evidence_requirements),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def entity_values(self, kind: str) -> tuple[str, ...]:
        return tuple(item.value for item in self.entities if item.kind == kind)
