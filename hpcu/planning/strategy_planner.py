"""Deterministic capability-aware strategy selection."""

from hpcu.schemas.capability import Capability
from hpcu.schemas.goal import GoalEnvelope
from hpcu.schemas.strategy import CapabilitySnapshot, StrategyPlan
from hpcu.schemas.surface import ExecutionMode, SurfaceKind


class StrategyPlanner:
    """Choose the cheapest feasible local strategy before any model call."""

    def select(
        self, goal: GoalEnvelope, capability: CapabilitySnapshot
    ) -> StrategyPlan:
        requested = goal.preferred_surface
        surface = requested or capability.active_surface
        if surface is SurfaceKind.UNKNOWN:
            raise ValueError("cannot plan without a concrete surface")
        if requested is not None and requested is not capability.active_surface:
            # Switching surfaces is allowed only when physical input can drive it.
            if capability.physical_input is Capability.UNSUPPORTED:
                raise ValueError(
                    "requested surface cannot be reached with current capabilities"
                )

        if (
            capability.execution_mode is ExecutionMode.LOCAL_SEMANTIC
            and capability.tool_actions
            and goal.intent.value in capability.tool_actions
        ):
            return StrategyPlan(
                id=f"tool-{goal.intent.value}",
                surface=surface,
                execution_mode=capability.execution_mode,
                route="tool_action",
                expected_steps=1,
                evidence_strength=0.95,
                expected_reliability=0.95,
                expected_latency_ms=self._latency(capability, "tool", 20.0),
            )

        has_grounding = (
            capability.structure is not Capability.UNSUPPORTED
            or capability.ocr is not Capability.UNSUPPORTED
        )
        has_input = (
            capability.semantic_input is not Capability.UNSUPPORTED
            or capability.physical_input is not Capability.UNSUPPORTED
        )
        if not has_grounding or not has_input:
            raise ValueError(
                "no feasible local screen strategy for current capabilities"
            )

        return StrategyPlan(
            id=f"screen-{surface.value}-{goal.intent.value}",
            surface=surface,
            execution_mode=capability.execution_mode,
            route="screen_interaction",
            expected_steps=self._expected_steps(goal.intent.value),
            evidence_strength=(
                0.85 if capability.structure is Capability.SUPPORTED else 0.72
            ),
            expected_reliability=(
                0.90 if capability.structure is Capability.SUPPORTED else 0.78
            ),
            expected_latency_ms=self._latency(capability, "screen", 150.0),
            model_calls_expected=0,
        )

    @staticmethod
    def _latency(capability: CapabilitySnapshot, key: str, default: float) -> float:
        return float(capability.measured_latency_ms.get(key, default))

    @staticmethod
    def _expected_steps(intent: str) -> int:
        return {
            "navigate": 3,
            "search": 4,
            "select": 2,
            "compare": 4,
            "edit": 3,
            "submit": 3,
        }.get(intent, 3)
