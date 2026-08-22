"""Unit tests for hpcu.gateway.gateway (Gateway ABC and GatewayResponse)."""

import pytest

from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
)
from hpcu.runtime_config import configured_semantic_identity


@pytest.mark.unit
def test_gateway_response_is_frozen_dto():
    """Given valid fields, GatewayResponse carries the call's metadata."""
    # Given / When
    response = GatewayResponse(
        content="result", model="MiniMax-M3", tokens_used=42, latency_ms=120
    )
    # Then
    assert response.content == "result"
    assert response.model == "MiniMax-M3"
    assert response.tokens_used == 42
    assert response.latency_ms == 120


@pytest.mark.unit
def test_gateway_response_fields_default():
    """Given omitted fields, GatewayResponse defaults to zero-filled values."""
    # Given / When
    response = GatewayResponse(content="hello", model="MiniMax-M3")
    # Then
    assert response.tokens_used == 0
    assert response.latency_ms == 0


class _ConcreteGateway(Gateway):
    @property
    def provider_id(self) -> str:
        return "minimax"

    @property
    def model_id(self) -> str:
        return "MiniMax-M3"

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        return GatewayResponse(content=f"echo:{prompt}", model="MiniMax-M3")


@pytest.mark.unit
def test_gateway_abstract_cannot_be_instantiated():
    """Given the abstract Gateway, direct instantiation is rejected."""
    # When / Then
    with pytest.raises(TypeError):
        Gateway()


@pytest.mark.unit
def test_gateway_identity_matches_configured_deployment():
    gateway = _ConcreteGateway()
    gateway.require_configured_identity(configured_semantic_identity())


@pytest.mark.unit
def test_gateway_concrete_implementation_calls():
    """Given a concrete Gateway, call() returns a GatewayResponse."""
    # Given
    gateway = _ConcreteGateway()
    # When
    response = gateway.call("ping", system_prompt="be brief")
    # Then
    assert response.content == "echo:ping"
    assert response.model == "MiniMax-M3"
