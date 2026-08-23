"""Capability-aware strategy selection and conservative PlanIR compilation."""

from __future__ import annotations

from dataclasses import dataclass

from hpcu.schemas.action import Action, ActionOp, Postcondition, PostconditionKind
from hpcu.schemas.evidence import EvidenceCondition, EvidenceContract, EvidenceKind
from hpcu.schemas.planning import (
    CapabilitySnapshot,
    ExecutionMode,
    GoalEnvelope,
    GoalIntent,
    PlanIR,
    PlanNode,
    StrategyPlan,
)


@dataclass(frozen=True)
class StrategyCandidate:
    plan: StrategyPlan
    utility: float


class StrategyPlanner:
    """Choose a feasible local direction without inventing unavailable tools."""

    def candidates(
        self, goal: GoalEnvelope, capabilities: CapabilitySnapshot
    ) -> tuple[StrategyCandidate, ...]:
        mode = goal.execution_mode
        if mode is ExecutionMode.SCREEN_STRICT and not capabilities.physical_input:
            return ()

        rows: list[StrategyCandidate] = []
        if capabilities.workflow_ids:
            workflow_id = capabilities.workflow_ids[0]
            rows.append(
                StrategyCandidate(
                    StrategyPlan(
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

        if goal.explicit_url and capabilities.surface == "browser":
            rows.append(
                StrategyCandidate(
                    StrategyPlan(
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
                StrategyPlan(
                    id=f"{goal.id}-existing-screen",
                    goal_id=goal.id,
                    execution_mode=mode,
                    surface=capabilities.surface,
                    entry_kind="existing_screen",
                    expected_local_steps=2,
                    expected_model_calls=(1 if goal.ambiguity_slots else 0),
                    rationale="continue from the currently observed surface",
                ),
                utility=0.6 - (0.2 if goal.ambiguity_slots else 0.0),
            )
        )
        return tuple(sorted(rows, key=lambda item: item.utility, reverse=True))

    def select(
        self, goal: GoalEnvelope, capabilities: CapabilitySnapshot
    ) -> StrategyPlan:
        candidates = self.candidates(goal, capabilities)
        if not candidates:
            raise ValueError("no feasible strategy for the observed capabilities")
        return candidates[0].plan


class PlanCompiler:
    """Compile only plans whose deterministic completion contract is known.

    The compiler intentionally refuses broad multi-step guesses. Unknown or
    underspecified goals stay unresolved instead of being converted to a click.
    """

    def compile(self, goal: GoalEnvelope, strategy: StrategyPlan) -> PlanIR:
        if goal.ambiguity_slots:
            raise ValueError(
                "cannot compile unresolved goal slots: "
                + ", ".join(goal.ambiguity_slots)
            )
        if strategy.goal_id != goal.id:
            raise ValueError("strategy does not belong to goal")

        if strategy.entry_kind == "url":
            if goal.intent is not GoalIntent.NAVIGATE:
                raise ValueError("URL entry compilation currently supports navigate goals")
            node_id = f"{goal.id}-navigate"
            destination = goal.entities.get("host", goal.explicit_url)
            evidence = EvidenceContract(
                all=(
                    EvidenceCondition(
                        kind=EvidenceKind.ELEMENT_VISIBLE,
                        product_match=destination,
                    ),
                )
            )
            action = Action(
                id=node_id,
                op=ActionOp.NAVIGATE,
                value=goal.explicit_url,
                postconditions=(
                    Postcondition(
                        kind=PostconditionKind.ELEMENT_COUNT_AT_LEAST,
                        target="ocr",
                        value=1,
                    ),
                ),
            )
            node = PlanNode(
                id=node_id,
                action=action,
                evidence=evidence,
                terminal=True,
                require_scene_change=True,
            )
            return PlanIR(
                id=f"plan-{goal.id}",
                goal=goal,
                strategy=strategy,
                entry_node_id=node.id,
                nodes=(node,),
            )

        raise ValueError(
            "goal requires a task-specific action graph; refusing implicit click plan"
        )
