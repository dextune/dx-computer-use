"""Capability-aware strategy selection and conservative PlanIR compilation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from hpcu.schemas.action import Action
from hpcu.schemas.evidence import EvidenceContract
from hpcu.schemas.planning import (
    CapabilitySnapshot,
    ExecutionMode,
    GoalEnvelope,
    GoalIntent,
    PlanIR,
    PlanNode,
    StrategyPlan,
)

_MUTATING_INTENTS = frozenset(
    {
        GoalIntent.NAVIGATE,
        GoalIntent.SEARCH,
        GoalIntent.SELECT,
        GoalIntent.EDIT,
        GoalIntent.SUBMIT,
    }
)


@dataclass(frozen=True)
class StrategyCandidate:
    """A feasible direction and its deterministic utility estimate."""

    plan: StrategyPlan
    utility: float


class StrategyPlanner:
    """Choose a feasible local direction without inventing unavailable tools."""

    def candidates(
        self,
        goal: GoalEnvelope,
        capabilities: CapabilitySnapshot,
    ) -> tuple[StrategyCandidate, ...]:
        mode = goal.execution_mode
        input_available = capabilities.semantic_input or capabilities.physical_input
        if goal.intent in _MUTATING_INTENTS and not input_available:
            return ()
        if (
            mode is ExecutionMode.SCREEN_STRICT
            and goal.intent in _MUTATING_INTENTS
            and not capabilities.physical_input
        ):
            return ()

        rows: list[StrategyCandidate] = []
        if capabilities.workflow_ids:
            workflow_id = capabilities.workflow_ids[0]
            rows.append(
                StrategyCandidate(
                    plan=StrategyPlan(
                        id=f"{goal.id}-workflow",
                        goal_id=goal.id,
                        execution_mode=mode,
                        surface=capabilities.surface,
                        entry_kind="workflow",
                        workflow_id=workflow_id,
                        expected_local_steps=1,
                        expected_model_calls=0,
                        rationale="qualified local workflow is available",
                    ),
                    utility=1.0,
                )
            )

        if (
            goal.explicit_url
            and capabilities.surface == "browser"
            and (capabilities.keyboard_input or capabilities.semantic_input)
        ):
            rows.append(
                StrategyCandidate(
                    plan=StrategyPlan(
                        id=f"{goal.id}-url",
                        goal_id=goal.id,
                        execution_mode=mode,
                        surface="browser",
                        entry_kind="url",
                        entry_value=goal.explicit_url,
                        expected_local_steps=1,
                        expected_model_calls=0,
                        rationale="explicit user URL is available",
                    ),
                    utility=0.9,
                )
            )

        rows.append(
            StrategyCandidate(
                plan=StrategyPlan(
                    id=f"{goal.id}-existing-screen",
                    goal_id=goal.id,
                    execution_mode=mode,
                    surface=capabilities.surface,
                    entry_kind="existing_screen",
                    expected_local_steps=2,
                    expected_model_calls=1 if goal.ambiguity_slots else 0,
                    rationale="continue from the currently observed surface",
                ),
                utility=0.6 - (0.2 if goal.ambiguity_slots else 0.0),
            )
        )
        return tuple(sorted(rows, key=lambda item: item.utility, reverse=True))

    def select(
        self,
        goal: GoalEnvelope,
        capabilities: CapabilitySnapshot,
    ) -> StrategyPlan:
        candidates = self.candidates(goal, capabilities)
        if not candidates:
            raise ValueError("no feasible strategy for the observed capabilities")
        return candidates[0].plan


class PlanCompiler:
    """Build PlanIR only from explicit Action and evidence contracts.

    This compiler deliberately does not turn a natural-language goal into an
    implicit click. Semantic plan generation may propose nodes, but those nodes
    still have to cross this typed boundary with explicit completion evidence.
    """

    def compile_single_action(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        *,
        action: Action,
        evidence: EvidenceContract,
        target_query: Mapping[str, object] | None = None,
        allow_preexisting_success: bool = False,
        require_scene_change: bool | None = None,
    ) -> PlanIR:
        if goal.ambiguity_slots:
            raise ValueError(
                "cannot compile unresolved goal slots: "
                + ", ".join(goal.ambiguity_slots)
            )
        if strategy.goal_id != goal.id:
            raise ValueError("strategy does not belong to goal")
        if action.op in goal.forbidden_actions:
            raise ValueError(f"action {action.op.value!r} is forbidden by the goal")

        node = PlanNode(
            id=action.id,
            action=action,
            target_query=target_query or {},
            evidence=evidence,
            terminal=True,
            allow_preexisting_success=allow_preexisting_success,
            require_scene_change=require_scene_change,
        )
        return PlanIR(
            id=f"plan-{goal.id}",
            goal=goal,
            strategy=strategy,
            entry_node_id=node.id,
            nodes=(node,),
        )

    def compile(
        self,
        goal: GoalEnvelope,
        strategy: StrategyPlan,
        *,
        action: Action | None = None,
        evidence: EvidenceContract | None = None,
        target_query: Mapping[str, object] | None = None,
    ) -> PlanIR:
        """Compatibility entry point that remains fail-closed by default."""
        if goal.ambiguity_slots:
            raise ValueError(
                "cannot compile unresolved goal slots: "
                + ", ".join(goal.ambiguity_slots)
            )
        if action is None or evidence is None:
            raise ValueError(
                "plan compilation requires an explicit Action and EvidenceContract"
            )
        return self.compile_single_action(
            goal,
            strategy,
            action=action,
            evidence=evidence,
            target_query=target_query,
        )
