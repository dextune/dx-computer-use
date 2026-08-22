"""Task-global semantic model budget.

Every semantic call for one user task consumes the same budget, regardless of
whether it is used for planning, grounding, post-action analysis, or recovery.
The budget is deliberately independent of a provider adapter so it can be
shared by all gateway wrappers involved in the task.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from hpcu.gateway.gateway import ModelCallPurpose


class ModelCallBudgetError(RuntimeError):
    """Raised before a provider call when the task budget is exhausted."""


@dataclass(frozen=True)
class TaskBudgetSnapshot:
    """Immutable metrics captured from a :class:`TaskBudget`."""

    max_model_calls: int
    max_model_tokens: int
    model_calls_used: int
    model_tokens_used: int
    purpose_call_counts: tuple[tuple[str, int], ...]

    @property
    def model_calls_remaining(self) -> int:
        return max(0, self.max_model_calls - self.model_calls_used)

    @property
    def model_tokens_remaining(self) -> int:
        return max(0, self.max_model_tokens - self.model_tokens_used)


@dataclass
class TaskBudget:
    """Mutable task-local accounting shared by all semantic call sites.

    A call is reserved *before* touching the provider. Failed provider calls
    still consume a call, because they consumed latency and an external
    request. Token usage is recorded only when a response reports it.
    """

    max_model_calls: int
    max_model_tokens: int = 0
    model_calls_used: int = 0
    model_tokens_used: int = 0
    _purpose_counts: Counter[str] = field(default_factory=Counter, repr=False)

    def __post_init__(self) -> None:
        if self.max_model_calls < 0:
            raise ValueError("max_model_calls must be non-negative")
        if self.max_model_tokens < 0:
            raise ValueError("max_model_tokens must be non-negative")
        if self.model_calls_used < 0 or self.model_tokens_used < 0:
            raise ValueError("used budget values must be non-negative")
        if self.model_calls_used > self.max_model_calls:
            raise ValueError("model_calls_used exceeds max_model_calls")
        if self.max_model_tokens and self.model_tokens_used > self.max_model_tokens:
            raise ValueError("model_tokens_used exceeds max_model_tokens")

    @property
    def model_calls_remaining(self) -> int:
        return max(0, self.max_model_calls - self.model_calls_used)

    @property
    def model_tokens_remaining(self) -> int | None:
        if self.max_model_tokens == 0:
            return None
        return max(0, self.max_model_tokens - self.model_tokens_used)

    @property
    def exhausted(self) -> bool:
        if self.model_calls_used >= self.max_model_calls:
            return True
        return bool(
            self.max_model_tokens
            and self.model_tokens_used >= self.max_model_tokens
        )

    def acquire(self, purpose: ModelCallPurpose) -> None:
        """Reserve one provider call, failing closed when no budget remains."""
        if not isinstance(purpose, ModelCallPurpose):
            raise ValueError("purpose must be a ModelCallPurpose")
        if self.model_calls_used >= self.max_model_calls:
            raise ModelCallBudgetError(
                "task model-call budget exhausted: "
                f"used={self.model_calls_used} max={self.max_model_calls}"
            )
        if self.max_model_tokens and self.model_tokens_used >= self.max_model_tokens:
            raise ModelCallBudgetError(
                "task model-token budget exhausted: "
                f"used={self.model_tokens_used} max={self.max_model_tokens}"
            )
        self.model_calls_used += 1
        self._purpose_counts[purpose.value] += 1

    def record_tokens(self, tokens_used: int) -> None:
        """Record provider-reported response tokens for the reserved call."""
        if tokens_used < 0:
            raise ValueError("tokens_used must be non-negative")
        self.model_tokens_used += int(tokens_used)

    def snapshot(self) -> TaskBudgetSnapshot:
        return TaskBudgetSnapshot(
            max_model_calls=self.max_model_calls,
            max_model_tokens=self.max_model_tokens,
            model_calls_used=self.model_calls_used,
            model_tokens_used=self.model_tokens_used,
            purpose_call_counts=tuple(sorted(self._purpose_counts.items())),
        )
