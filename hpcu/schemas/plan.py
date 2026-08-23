"""Typed PlanIR, deterministic planning context, and bounded patch contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Mapping

from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import GoalEnvelope
from hpcu.schemas.surface import SurfaceKind


@dataclass(frozen=True)
class TargetQuerySpec:
    text: str = ""
    role: str | None = None
    confidence_threshold: float | None = None
    min_margin: float | None = None

    def __post_init__(self) -> None:
        if self.confidence_threshold is not None and not (
            0.0 <= self.confidence_threshold <= 1.0
        ):
            raise ValueError("confidence_threshold must be in [0, 1]")
        if self.min_margin is not None and not 0.0 <= self.min_margin <= 1.0:
            raise ValueError("min_margin must be in [0, 1]")

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"text": self.text}
        if self.role is not None:
            result["role"] = self.role
        if self.confidence_threshold is not None:
            result["confidence_threshold"] = self.confidence_threshold
        if self.min_margin is not None:
            result["min_margin"] = self.min_margin
        return result


@dataclass(frozen=True)
class PlanningContext:
    """Typed local facts available to the deterministic compiler.

    Query names are abstract roles such as ``search_field`` or
    ``submission_confirmation``. They are not current element IDs or screen
    coordinates. ``allowed_ops`` is the capability gate for the selected
    strategy; the compiler refuses to emit an unavailable operation.
    """

    target_queries: Mapping[str, TargetQuerySpec] = field(default_factory=dict)
    values: Mapping[str, str] = field(default_factory=dict)
    allowed_ops: frozenset[ActionOp] = field(
        default_factory=lambda: frozenset(ActionOp)
    )
    blocked_tokens: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        queries = dict(self.target_queries)
        values = dict(self.values)
        blocked_tokens = tuple(self.blocked_tokens)
        if any(not key.strip() for key in queries):
            raise ValueError("planning-context query names must be non-empty")
        if any(not key.strip() for key in values):
            raise ValueError("planning-context value names must be non-empty")
        if any(
            not isinstance(token, str) or not token.strip()
            for token in blocked_tokens
        ):
            raise ValueError("blocked_tokens must contain non-empty strings")
        blocked_tokens = tuple(token.strip() for token in blocked_tokens)
        object.__setattr__(self, "target_queries", MappingProxyType(queries))
        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "allowed_ops", frozenset(self.allowed_ops))
        object.__setattr__(self, "blocked_tokens", blocked_tokens)

    def require_query(self, name: str) -> TargetQuerySpec:
        try:
            return self.target_queries[name]
        except KeyError as exc:
            raise ValueError(f"planning context is missing query {name!r}") from exc

    def require_value(self, name: str) -> str:
        try:
            value = self.values[name]
        except KeyError as exc:
            raise ValueError(f"planning context is missing value {name!r}") from exc
        if not value.strip():
            raise ValueError(f"planning context value {name!r} must be non-empty")
        return value


@dataclass(frozen=True)
class GroundingHints:
    tokens: tuple[str, ...] = ()
    ignore_tokens: tuple[str, ...] = ()
    source: str = "goal"


@dataclass(frozen=True)
class PlanNode:
    id: str
    action: Action
    surface: SurfaceKind
    target_query: TargetQuerySpec | None = None
    verification_query: TargetQuerySpec | None = None
    grounding_hints: GroundingHints | None = None
    success_edge: str | None = None
    failure_edges: Mapping[str, str] = field(default_factory=dict)
    evidence_requirements: tuple[str, ...] = ()
    irreversible: bool = False

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("plan node id is required")
        if any(not item.strip() for item in self.evidence_requirements):
            raise ValueError("evidence requirements must be non-empty strings")
        object.__setattr__(
            self, "failure_edges", MappingProxyType(dict(self.failure_edges))
        )


def _node_payload(node: PlanNode) -> dict[str, object]:
    action = node.action
    return {
        "id": node.id,
        "action": {
            "id": action.id,
            "op": action.op.value,
            "target": {
                "element_id": action.target.element_id,
                "locator": action.target.locator,
            },
            "pre": [
                (condition.kind.value, condition.target, condition.value)
                for condition in action.preconditions
            ],
            "post": [
                (
                    condition.kind.value,
                    condition.target,
                    condition.value_ref,
                    condition.value,
                )
                for condition in action.postconditions
            ],
            "timeout_ms": action.timeout_ms,
            "retry": {
                "max_attempts": action.retry.max_attempts,
                "alternate_modes": list(action.retry.alternate_modes),
            },
            "value": action.value,
            "value_ref": action.value_ref,
            "key": action.key,
            "modifiers": list(action.modifiers),
            "dx": action.dx,
            "dy": action.dy,
        },
        "query": node.target_query.as_dict() if node.target_query else None,
        "verification_query": (
            node.verification_query.as_dict() if node.verification_query else None
        ),
        "grounding_hints": (
            {
                "tokens": list(node.grounding_hints.tokens),
                "ignore_tokens": list(node.grounding_hints.ignore_tokens),
                "source": node.grounding_hints.source,
            }
            if node.grounding_hints is not None
            else None
        ),
        "surface": node.surface.value,
        "success": node.success_edge,
        "failure": sorted(node.failure_edges.items()),
        "evidence": list(node.evidence_requirements),
        "irreversible": node.irreversible,
    }


def _budget_payload(budget: TaskBudgetSpec) -> dict[str, int | None]:
    return {
        "max_model_calls": budget.max_model_calls,
        "max_model_tokens": budget.max_model_tokens,
        "max_model_latency_ms": budget.max_model_latency_ms,
        "planning_call_ceiling": budget.planning_call_ceiling,
        "recovery_call_reserve": budget.recovery_call_reserve,
    }


@dataclass(frozen=True)
class PlanIR:
    goal: GoalEnvelope
    strategy_id: str
    entry_node_id: str
    nodes: Mapping[str, PlanNode]
    task_budget: TaskBudgetSpec
    compiler_version: str = "1"
    patch_lineage: tuple[str, ...] = ()
    blocked_tokens: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        nodes = dict(self.nodes)
        blocked_tokens = tuple(self.blocked_tokens)
        if not self.strategy_id.strip() or not self.compiler_version.strip():
            raise ValueError("strategy_id and compiler_version are required")
        if any(
            not isinstance(token, str) or not token.strip()
            for token in blocked_tokens
        ):
            raise ValueError("blocked_tokens must contain non-empty strings")
        blocked_tokens = tuple(token.strip() for token in blocked_tokens)
        if self.entry_node_id not in nodes:
            raise ValueError("entry_node_id must reference an existing node")
        for key, node in nodes.items():
            if key != node.id:
                raise ValueError("PlanIR mapping keys must equal PlanNode.id")
            if node.success_edge is not None and node.success_edge not in nodes:
                raise ValueError(f"unknown success edge {node.success_edge!r}")
            for edge in node.failure_edges.values():
                if edge not in nodes:
                    raise ValueError(f"unknown failure edge {edge!r}")
        reachable = self._reachable(nodes)
        if reachable != set(nodes):
            missing = sorted(set(nodes) - reachable)
            raise ValueError(f"PlanIR contains unreachable nodes: {missing!r}")
        object.__setattr__(self, "nodes", MappingProxyType(nodes))
        object.__setattr__(self, "blocked_tokens", blocked_tokens)

    def _reachable(self, nodes: Mapping[str, PlanNode]) -> set[str]:
        pending = [self.entry_node_id]
        visited: set[str] = set()
        while pending:
            node_id = pending.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            node = nodes[node_id]
            if node.success_edge is not None:
                pending.append(node.success_edge)
            pending.extend(node.failure_edges.values())
        return visited

    @property
    def plan_hash(self) -> str:
        payload = {
            "goal": self.goal.canonical_hash,
            "strategy": self.strategy_id,
            "entry": self.entry_node_id,
            "nodes": [_node_payload(self.nodes[key]) for key in sorted(self.nodes)],
            "budget": _budget_payload(self.task_budget),
            "compiler": self.compiler_version,
            "lineage": list(self.patch_lineage),
            "blocked": list(self.blocked_tokens),
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def apply_patch(
        self,
        patch: PlanPatch,
        *,
        completed_node_ids: tuple[str, ...] = (),
    ) -> PlanIR:
        if patch.parent_plan_hash != self.plan_hash:
            raise ValueError("plan patch parent hash does not match current plan")
        completed = set(completed_node_ids)
        current_ids = set(self.nodes)
        replaced = set(patch.replaced_node_ids)
        patch_ids = set(patch.nodes)
        unknown_replacements = replaced - current_ids
        if unknown_replacements:
            raise ValueError(
                f"plan patch replaces unknown nodes: {sorted(unknown_replacements)!r}"
            )
        if completed - current_ids:
            raise ValueError("completed node set is not part of the current plan")
        if completed & replaced:
            raise ValueError("plan patch cannot replace a verified completed node")
        overwritten_without_declaration = (patch_ids & current_ids) - replaced
        if overwritten_without_declaration:
            raise ValueError(
                "plan patch must declare every overwritten current node as replaced"
            )
        if patch.resume_node_id in completed:
            raise ValueError("plan patch cannot resume at a verified completed node")
        for node in patch.nodes.values():
            outgoing = set(node.failure_edges.values())
            if node.success_edge is not None:
                outgoing.add(node.success_edge)
            if outgoing & completed:
                raise ValueError(
                    "plan patch cannot introduce an edge to a verified completed node"
                )

        nodes = dict(self.nodes)
        for node_id in replaced:
            nodes.pop(node_id)
        nodes.update(patch.nodes)
        if patch.resume_node_id not in nodes:
            raise ValueError("plan patch resume node does not exist")
        return replace(
            self,
            nodes=nodes,
            patch_lineage=(*self.patch_lineage, patch.patch_hash),
        )


@dataclass(frozen=True)
class TaskBudgetSnapshot:
    remaining_calls: int
    remaining_tokens: int
    remaining_latency_ms: int

    def __post_init__(self) -> None:
        if min(
            self.remaining_calls,
            self.remaining_tokens,
            self.remaining_latency_ms,
        ) < 0:
            raise ValueError("remaining task budget values must be non-negative")


@dataclass(frozen=True)
class ReplanRequest:
    reason: FailureCode
    failed_node_id: str
    scene_version: int
    unresolved_slots: tuple[str, ...]
    completed_nodes: tuple[str, ...]
    remaining_budget: TaskBudgetSnapshot

    def __post_init__(self) -> None:
        if not self.failed_node_id.strip():
            raise ValueError("failed_node_id is required")
        if self.scene_version < 0:
            raise ValueError("scene_version must be non-negative")


@dataclass(frozen=True)
class PlanPatch:
    parent_plan_hash: str
    replaced_node_ids: tuple[str, ...]
    nodes: Mapping[str, PlanNode]
    resume_node_id: str
    reason: FailureCode

    def __post_init__(self) -> None:
        if not self.parent_plan_hash.strip() or not self.resume_node_id.strip():
            raise ValueError("plan patch parent hash and resume node are required")
        if len(set(self.replaced_node_ids)) != len(self.replaced_node_ids):
            raise ValueError("plan patch replaced_node_ids must be unique")
        nodes = dict(self.nodes)
        if not self.replaced_node_ids and not nodes:
            raise ValueError("plan patch must replace or add at least one node")
        for key, node in nodes.items():
            if key != node.id:
                raise ValueError("PlanPatch mapping keys must equal PlanNode.id")
        object.__setattr__(self, "nodes", MappingProxyType(nodes))

    @property
    def patch_hash(self) -> str:
        payload = {
            "parent": self.parent_plan_hash,
            "replaced": list(self.replaced_node_ids),
            "nodes": [_node_payload(self.nodes[key]) for key in sorted(self.nodes)],
            "resume": self.resume_node_id,
            "reason": self.reason.value,
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
