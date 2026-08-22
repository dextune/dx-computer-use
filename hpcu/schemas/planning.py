"""Typed contracts from a user command to an executable plan.

The planning layer carries intent, constraints, risk and evidence explicitly.
It never stores click coordinates or plan-time element ids. Grounding remains a
runtime operation against the current :class:`Scene`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.evidence import EvidenceContract
from hpcu.schemas.targeting import TargetingPack


class ExecutionMode(str, Enum):
    """How side effects may cross the platform boundary."""

    SCREEN_STRICT = "screen_strict"
    LOCAL_SEMANTIC = "local_semantic"


class GoalIntent(str, Enum):
    """Small domain-neutral command families used for strategy selection."""

    UNKNOWN = "unknown"
    NAVIGATE = "navigate"
    SEARCH = "search"
    SELECT = "select"
    COMPARE = "compare"
    EDIT = "edit"
    SUBMIT = "submit"
    READ = "read"
    MONITOR = "monitor"


@dataclass(frozen=True)
class GoalConstraint:
    """A normalized user constraint preserving its original text."""

    name: str
    value: str
    operator: str = "equals"
    raw_text: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("GoalConstraint.name is required")
        if not self.operator.strip():
            raise ValueError("GoalConstraint.operator is required")


@dataclass(frozen=True)
class GoalEnvelope:
    """Structured planning input derived from exactly one user instruction."""

    id: str
    raw_instruction: str
    intent: GoalIntent = GoalIntent.UNKNOWN
    terminal_state: str = ""
    constraints: tuple[GoalConstraint, ...] = ()
    entities: Mapping[str, str] = field(default_factory=dict)
    explicit_url: str = ""
    preferred_surface: str = ""
    execution_mode: ExecutionMode = ExecutionMode.LOCAL_SEMANTIC
    forbidden_actions: tuple[ActionOp, ...] = ()
    ambiguity_slots: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    max_model_calls: int = 2
    max_model_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("GoalEnvelope.id is required")
        if not self.raw_instruction.strip():
            raise ValueError("GoalEnvelope.raw_instruction is required")
        if self.max_model_calls < 0 or self.max_model_tokens < 0:
            raise ValueError("GoalEnvelope model budgets must be non-negative")
        if len(set(self.ambiguity_slots)) != len(self.ambiguity_slots):
            raise ValueError("GoalEnvelope.ambiguity_slots must be unique")
        object.__setattr__(self, "entities", MappingProxyType(dict(self.entities)))


@dataclass(frozen=True)
class CapabilitySnapshot:
    """Capabilities observed before strategy selection."""

    surface: str
    capture: bool = True
    structure: bool = False
    semantic_input: bool = False
    physical_input: bool = True
    keyboard_input: bool = True
    ocr: bool = False
    dirty_regions: bool = False
    external_tools: tuple[str, ...] = ()
    workflow_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.surface.strip():
            raise ValueError("CapabilitySnapshot.surface is required")
        if not self.capture:
            raise ValueError("computer-use execution requires capture capability")


@dataclass(frozen=True)
class StrategyPlan:
    """One capability-aware direction selected before Action compilation."""

    id: str
    goal_id: str
    execution_mode: ExecutionMode
    surface: str
    entry_kind: str = "existing_screen"
    entry_value: str = ""
    workflow_id: str = ""
    expected_local_steps: int = 0
    expected_model_calls: int = 0
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.goal_id.strip():
            raise ValueError("StrategyPlan id and goal_id are required")
        if not self.surface.strip():
            raise ValueError("StrategyPlan.surface is required")
        if self.entry_kind not in {
            "existing_screen",
            "url",
            "terminal_command",
            "workflow",
        }:
            raise ValueError(f"unsupported strategy entry_kind: {self.entry_kind!r}")
        if self.entry_kind == "workflow":
            if not self.workflow_id.strip():
                raise ValueError("workflow strategy requires workflow_id")
        elif self.entry_kind in {"url", "terminal_command"}:
            if not self.entry_value.strip():
                raise ValueError("strategy entry requires a value")
        if self.expected_local_steps < 0 or self.expected_model_calls < 0:
            raise ValueError("strategy cost estimates must be non-negative")


_MUTATING_OPS = frozenset(
    {
        ActionOp.NAVIGATE,
        ActionOp.INVOKE,
        ActionOp.CLICK,
        ActionOp.DOUBLE_CLICK,
        ActionOp.RIGHT_CLICK,
        ActionOp.TYPE,
        ActionOp.REPLACE_TEXT,
        ActionOp.HOTKEY,
        ActionOp.SELECT,
        ActionOp.TOGGLE,
        ActionOp.SCROLL,
        ActionOp.DRAG,
        ActionOp.CALL_TOOL,
    }
)


@dataclass(frozen=True)
class PlanNode:
    """A typed Action node with explicit success/failure edges and evidence."""

    id: str
    action: Action
    target_query: Mapping[str, object] = field(default_factory=dict)
    evidence: EvidenceContract | None = None
    grounding_hints: TargetingPack | None = None
    success_next: str | None = None
    failure_next: str | None = None
    terminal: bool = False
    allow_preexisting_success: bool = False
    require_scene_change: bool | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("PlanNode.id is required")
        if self.action.id != self.id:
            raise ValueError("PlanNode.id must equal Action.id")
        if self.terminal and self.success_next is not None:
            raise ValueError("terminal PlanNode cannot have success_next")
        if not self.terminal and self.success_next is None:
            raise ValueError("non-terminal PlanNode requires success_next")
        if self.success_next == self.id or self.failure_next == self.id:
            raise ValueError("PlanNode cannot point directly to itself")
        if self.terminal and not self.action.postconditions and self.evidence is None:
            raise ValueError(
                "terminal PlanNode requires postconditions or an EvidenceContract"
            )
        if self.allow_preexisting_success and not self.terminal:
            raise ValueError(
                "allow_preexisting_success is only meaningful for terminal nodes"
            )
        if self.require_scene_change is None:
            object.__setattr__(
                self,
                "require_scene_change",
                self.action.op in _MUTATING_OPS,
            )
        object.__setattr__(self, "target_query", MappingProxyType(dict(self.target_query)))


@dataclass(frozen=True)
class PlanIR:
    """Executable directed graph for one user task."""

    id: str
    goal: GoalEnvelope
    strategy: StrategyPlan
    entry_node_id: str
    nodes: tuple[PlanNode, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported PlanIR schema_version")
        if not self.id.strip() or not self.entry_node_id.strip():
            raise ValueError("PlanIR id and entry_node_id are required")
        if self.strategy.goal_id != self.goal.id:
            raise ValueError("PlanIR strategy must belong to its goal")
        by_id = {node.id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise ValueError("PlanIR node ids must be unique")
        if self.entry_node_id not in by_id:
            raise ValueError("PlanIR entry_node_id is missing")
        for node in self.nodes:
            for edge in (node.success_next, node.failure_next):
                if edge is not None and edge not in by_id:
                    raise ValueError(
                        f"PlanIR node {node.id!r} references missing edge {edge!r}"
                    )
        if not any(node.terminal for node in self.nodes):
            raise ValueError("PlanIR requires at least one terminal node")
        self._validate_reachable(by_id)

    def node(self, node_id: str) -> PlanNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def _validate_reachable(self, by_id: dict[str, PlanNode]) -> None:
        visited: set[str] = set()
        pending = [self.entry_node_id]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            node = by_id[current]
            for edge in (node.success_next, node.failure_next):
                if edge is not None:
                    pending.append(edge)
        unreachable = set(by_id) - visited
        if unreachable:
            raise ValueError(
                "PlanIR contains unreachable nodes: " + ", ".join(sorted(unreachable))
            )
