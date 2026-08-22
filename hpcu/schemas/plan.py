"""Typed PlanIR — deterministic runtime program and explicit recovery edges."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from hpcu.schemas.action import Action
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.goal import GoalEnvelope
from hpcu.schemas.surface import SurfaceKind


@dataclass(frozen=True)
class TargetQuerySpec:
    text: str = ""
    role: str | None = None
    confidence_threshold: float | None = None
    min_margin: float | None = None

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

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("plan node id is required")
        object.__setattr__(
            self, "failure_edges", MappingProxyType(dict(self.failure_edges))
        )


@dataclass(frozen=True)
class PlanIR:
    goal: GoalEnvelope
    strategy_id: str
    entry_node_id: str
    nodes: Mapping[str, PlanNode]
    task_budget: TaskBudgetSpec
    compiler_version: str = "1"

    def __post_init__(self) -> None:
        nodes = dict(self.nodes)
        if not self.strategy_id.strip() or not self.compiler_version.strip():
            raise ValueError("strategy_id and compiler_version are required")
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
        object.__setattr__(self, "nodes", MappingProxyType(nodes))

    @property
    def plan_hash(self) -> str:
        def node_payload(node: PlanNode) -> dict[str, object]:
            return {
                "id": node.id,
                "op": node.action.op.value,
                "value": node.action.value,
                "key": node.action.key,
                "query": node.target_query.as_dict() if node.target_query else None,
                "verification_query": (
                    node.verification_query.as_dict()
                    if node.verification_query
                    else None
                ),
                "surface": node.surface.value,
                "success": node.success_edge,
                "failure": sorted(node.failure_edges.items()),
                "post": [
                    (cond.kind.value, cond.target, cond.value)
                    for cond in node.action.postconditions
                ],
            }

        payload = {
            "goal": self.goal.canonical_hash,
            "strategy": self.strategy_id,
            "entry": self.entry_node_id,
            "nodes": [node_payload(self.nodes[key]) for key in sorted(self.nodes)],
            "compiler": self.compiler_version,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
