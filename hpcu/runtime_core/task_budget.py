"""Task-global model budget accounting and a gateway wrapper."""

from dataclasses import dataclass, field

from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.failure_codes import FailureCode


class ModelBudgetExceeded(RuntimeError):
    failure_code = FailureCode.MODEL_BUDGET_EXHAUSTED.value


@dataclass
class TaskBudgetLedger:
    spec: TaskBudgetSpec
    calls_by_purpose: dict[ModelCallPurpose, int] = field(default_factory=dict)
    tokens_used: int = 0
    latency_ms: int = 0

    @property
    def model_calls(self) -> int:
        return sum(self.calls_by_purpose.values())

    @property
    def remaining_calls(self) -> int:
        return max(0, self.spec.max_model_calls - self.model_calls)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.spec.max_model_tokens - self.tokens_used)

    def before_call(self, purpose: ModelCallPurpose) -> None:
        if self.model_calls >= self.spec.max_model_calls:
            raise ModelBudgetExceeded("task model-call budget exhausted")
        if self.tokens_used >= self.spec.max_model_tokens:
            raise ModelBudgetExceeded("task model-token budget exhausted")
        if self.latency_ms >= self.spec.max_model_latency_ms:
            raise ModelBudgetExceeded("task model-latency budget exhausted")
        self.calls_by_purpose[purpose] = self.calls_by_purpose.get(purpose, 0) + 1

    def record(self, response: GatewayResponse) -> None:
        self.tokens_used += max(0, int(response.tokens_used))
        self.latency_ms += max(0, int(response.latency_ms))
        if self.tokens_used > self.spec.max_model_tokens:
            raise ModelBudgetExceeded("model response exceeded task token budget")
        if self.latency_ms > self.spec.max_model_latency_ms:
            raise ModelBudgetExceeded("model response exceeded task latency budget")


class BudgetedGateway(Gateway):
    """One accounting boundary for plan/ground/reanalysis/recovery calls."""

    def __init__(self, inner: Gateway, ledger: TaskBudgetLedger):
        self._inner = inner
        self.ledger = ledger

    @property
    def provider_id(self) -> str:
        return self._inner.provider_id

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        self.ledger.before_call(purpose)
        remaining = self.ledger.remaining_tokens
        effective_max = remaining if max_tokens is None else min(max_tokens, remaining)
        if effective_max <= 0:
            raise ModelBudgetExceeded("task model-token budget exhausted")
        response = self._inner.call(
            prompt,
            system_prompt,
            effective_max,
            purpose=purpose,
        )
        self.ledger.record(response)
        return response
