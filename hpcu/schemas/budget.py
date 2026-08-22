"""Immutable task budget limits."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskBudgetSpec:
    max_model_calls: int = 2
    max_model_tokens: int = 4096
    max_model_latency_ms: int = 60_000

    def __post_init__(self) -> None:
        if self.max_model_calls < 0:
            raise ValueError("max_model_calls must be non-negative")
        if self.max_model_tokens < 0:
            raise ValueError("max_model_tokens must be non-negative")
        if self.max_model_latency_ms < 0:
            raise ValueError("max_model_latency_ms must be non-negative")
