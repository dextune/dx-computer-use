"""Capability-aware strategy selection DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from hpcu.schemas.capability import Capability
from hpcu.schemas.surface import ExecutionMode, SurfaceKind


@dataclass(frozen=True)
class CapabilitySnapshot:
    active_surface: SurfaceKind
    execution_mode: ExecutionMode
    capture: Capability = Capability.UNSUPPORTED
    structure: Capability = Capability.UNSUPPORTED
    semantic_input: Capability = Capability.UNSUPPORTED
    physical_input: Capability = Capability.UNSUPPORTED
    ocr: Capability = Capability.UNSUPPORTED
    dirty_rects: Capability = Capability.UNSUPPORTED
    tool_actions: tuple[str, ...] = ()
    reusable_workflows: tuple[str, ...] = ()
    measured_latency_ms: Mapping[str, float] = field(default_factory=dict)
    observed_reliability: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "measured_latency_ms",
            MappingProxyType(dict(self.measured_latency_ms)),
        )
        object.__setattr__(
            self,
            "observed_reliability",
            MappingProxyType(dict(self.observed_reliability)),
        )


@dataclass(frozen=True)
class StrategyPlan:
    id: str
    surface: SurfaceKind
    execution_mode: ExecutionMode
    route: str
    expected_steps: int
    evidence_strength: float
    expected_reliability: float
    expected_latency_ms: float
    model_calls_expected: int = 0

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.route.strip():
            raise ValueError("strategy id and route are required")
        if self.expected_steps <= 0:
            raise ValueError("strategy expected_steps must be positive")
        for name, value in (
            ("evidence_strength", self.evidence_strength),
            ("expected_reliability", self.expected_reliability),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.expected_latency_ms < 0 or self.model_calls_expected < 0:
            raise ValueError("strategy costs must be non-negative")
