"""Failure-injection coverage for the MiniMax-M3 gateway boundary."""

import pytest

from hpcu.cases.stats import CountingGateway
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.gateway.minimax_adapter import MiniMaxAdapter

pytestmark = pytest.mark.unit


class _ForeignGateway(Gateway):
    @property
    def model_id(self) -> str:
        return "foreign-model"

    def call(
        self,
        prompt,
        system_prompt="",
        max_tokens=None,
        *,
        purpose=ModelCallPurpose.SITUATION_ANALYSIS,
    ):
        return GatewayResponse(content="{}", model="foreign-model")


def test_counting_gateway_rejects_foreign_gateway_before_call():
    with pytest.raises(ValueError, match="MiniMax-M3"):
        CountingGateway(_ForeignGateway())


def test_minimax_adapter_rejects_foreign_constructor_model():
    with pytest.raises(ValueError, match="MiniMax-M3"):
        MiniMaxAdapter(api_key="test", model="foreign-model", client=object())


def test_counting_gateway_records_explicit_purpose_without_prompt_content():
    class _MiniMaxGateway(_ForeignGateway):
        @property
        def provider_id(self) -> str:
            return "minimax"

        @property
        def model_id(self) -> str:
            return "MiniMax-M3"

        def call(
            self,
            prompt,
            system_prompt="",
            max_tokens=None,
            *,
            purpose=ModelCallPurpose.SITUATION_ANALYSIS,
        ):
            return GatewayResponse(
                content="{}", model="MiniMax-M3", provider="minimax", tokens_used=3
            )

    gateway = CountingGateway(_MiniMaxGateway())
    gateway.call(
        "secret password",
        purpose=ModelCallPurpose.POST_ACTION_REANALYSIS,
    )
    assert gateway.ai_calls[0].purpose == "post_action_reanalysis"
    assert "secret password" not in gateway.http_log[0]
    assert gateway.ai_calls[0].prompt_length == len("secret password")
