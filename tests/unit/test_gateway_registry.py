"""Unit tests for the configuration-driven semantic gateway factory."""

from typing import Optional

import pytest

from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.gateway.registry import (
    GatewayRegistry,
    create_gateway,
    default_gateway_registry,
)


class _FakeGateway(Gateway):
    def __init__(self, provider_id: str, model_id: str):
        self._provider_id = provider_id
        self._model_id = model_id

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        return GatewayResponse(
            content=prompt,
            model=self._model_id,
            provider=self._provider_id,
        )


@pytest.mark.unit
def test_factory_selects_configured_provider_and_model():
    """Given a configured provider, its factory receives the configured model."""
    # Given
    calls = []
    registry = GatewayRegistry()

    def create_fake(model_id: str, *, marker: str = "") -> Gateway:
        calls.append((model_id, marker))
        return _FakeGateway("fake", model_id)

    registry.register("fake", create_fake)
    config = {"semantic": {"default_provider": "fake", "default_model": "Fake-1"}}
    # When
    gateway = create_gateway(config, registry=registry, marker="injected")
    # Then
    assert calls == [("Fake-1", "injected")]
    assert gateway.provider_id == "fake"
    assert gateway.model_id == "Fake-1"


@pytest.mark.unit
def test_factory_rejects_unsupported_provider():
    """Given an unregistered provider, construction fails closed."""
    # Given
    config = {
        "semantic": {
            "default_provider": "not-installed",
            "default_model": "Unknown-1",
        }
    }
    # When / Then
    with pytest.raises(ValueError, match="unsupported semantic gateway provider"):
        create_gateway(config, registry=GatewayRegistry())


@pytest.mark.unit
def test_default_registry_registers_minimax_provider():
    """Given default bootstrap, MiniMax is available without call-site imports."""
    # When
    registry = default_gateway_registry()
    # Then
    assert registry.has("minimax")


@pytest.mark.unit
def test_factory_rejects_configured_identity_mismatch():
    """Given an adapter with another identity, construction fails closed."""
    # Given
    registry = GatewayRegistry()
    registry.register("fake", lambda model_id: _FakeGateway("fake", "wrong-model"))
    config = {"semantic": {"default_provider": "fake", "default_model": "Fake-1"}}
    # When / Then
    with pytest.raises(ValueError, match="semantic gateway identity mismatch"):
        create_gateway(config, registry=registry)
