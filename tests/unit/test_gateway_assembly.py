"""Regression tests for logical-call accounting around transport retries."""

import pytest

from hpcu.cases import __main__ as mainmod
from hpcu.cases.stats import DEFAULT_MODEL_ID, DEFAULT_PROVIDER_ID
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose

pytestmark = pytest.mark.unit

CONFIG = {
    "semantic": {
        "request_limits": {
            "action_decision_retry_attempts": 1,
            "retry_base_delay_ms": 0,
            "retry_max_delay_ms": 0,
        }
    }
}


class ConnectError(Exception):
    """Named like the transient httpx error recognized by RetryableGateway."""


class _FlakyGateway(Gateway):
    def __init__(self, *, always_fail: bool = False):
        self.attempts = 0
        self.always_fail = always_fail

    @property
    def provider_id(self) -> str:
        return DEFAULT_PROVIDER_ID

    @property
    def model_id(self) -> str:
        return DEFAULT_MODEL_ID

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        self.attempts += 1
        if self.always_fail or self.attempts == 1:
            raise ConnectError("transient")
        return GatewayResponse(
            content='{"ok":true}',
            provider=self.provider_id,
            model=self.model_id,
            tokens_used=3,
        )


def test_transport_retry_counts_as_one_logical_model_call(monkeypatch):
    inner = _FlakyGateway()
    monkeypatch.setattr(mainmod, "create_gateway", lambda _config: inner)
    gateway = mainmod._build_gateway(CONFIG)

    response = gateway.call("compile", purpose=ModelCallPurpose.PLAN_COMPILE)

    assert response.content == '{"ok":true}'
    assert inner.attempts == 2
    assert gateway.call_count == 1
    assert gateway.error_count == 0
    assert len(gateway.ai_calls) == 1


def test_exhausted_transport_retries_record_one_logical_error(monkeypatch):
    inner = _FlakyGateway(always_fail=True)
    monkeypatch.setattr(mainmod, "create_gateway", lambda _config: inner)
    gateway = mainmod._build_gateway(CONFIG)

    with pytest.raises(ConnectError):
        gateway.call("compile", purpose=ModelCallPurpose.PLAN_COMPILE)

    assert inner.attempts == 2
    assert gateway.call_count == 1
    assert gateway.error_count == 1
    assert len(gateway.ai_calls) == 1
