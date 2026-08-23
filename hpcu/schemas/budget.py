"""Immutable task budget limits."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskBudgetSpec:
    max_model_calls: int = 2
    max_model_tokens: int = 4096
    max_model_latency_ms: int = 60_000
    planning_call_ceiling: int | None = None
    recovery_call_reserve: int = 0

    def __post_init__(self) -> None:
        if self.max_model_calls < 0:
            raise ValueError("max_model_calls must be non-negative")
        if self.max_model_tokens < 0:
            raise ValueError("max_model_tokens must be non-negative")
        if self.max_model_latency_ms < 0:
            raise ValueError("max_model_latency_ms must be non-negative")
        if self.planning_call_ceiling is not None:
            if self.planning_call_ceiling < 0:
                raise ValueError("planning_call_ceiling must be non-negative")
            if self.planning_call_ceiling > self.max_model_calls:
                raise ValueError(
                    "planning_call_ceiling cannot exceed max_model_calls"
                )
        if self.recovery_call_reserve < 0:
            raise ValueError("recovery_call_reserve must be non-negative")
        if self.recovery_call_reserve > self.max_model_calls:
            raise ValueError(
                "recovery_call_reserve cannot exceed max_model_calls"
            )
