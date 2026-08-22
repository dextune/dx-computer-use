"""Configuration-driven registry and factory for semantic gateways."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from hpcu.gateway.gateway import Gateway, SemanticIdentity
from hpcu.gateway.minimax_adapter import MiniMaxAdapter

GatewayFactory = Callable[..., Gateway]


class GatewayRegistry:
    """Map configured provider identifiers to gateway construction factories."""

    def __init__(self) -> None:
        self._factories: dict[str, GatewayFactory] = {}

    def register(self, provider_id: str, factory: GatewayFactory) -> None:
        """Register or replace a factory for one provider identifier."""
        provider_key = provider_id.strip()
        if not provider_key:
            raise ValueError("semantic gateway provider identifier is required")
        if not callable(factory):
            raise TypeError("semantic gateway provider factory must be callable")
        self._factories[provider_key] = factory

    def create(
        self,
        identity: SemanticIdentity,
        **factory_options: Any,
    ) -> Gateway:
        """Construct the gateway selected by ``identity``.

        Factories receive the configured model identifier as their first
        positional argument. Remaining options are dependency injection for
        adapters and tests; provider-specific defaults stay in each adapter.
        """
        factory = self._factories.get(identity.provider_id)
        if factory is None:
            raise ValueError(
                "unsupported semantic gateway provider: "
                f"{identity.provider_id!r}"
            )
        return factory(identity.model_id, **factory_options)

    def has(self, provider_id: str) -> bool:
        """Return whether a provider factory is registered."""
        return provider_id in self._factories

    def providers(self) -> tuple[str, ...]:
        """Return registered provider identifiers in registration order."""
        return tuple(self._factories)


SemanticGatewayRegistry = GatewayRegistry


def _create_minimax(
    model_id: str,
    *,
    client: Optional[object] = None,
    env_path: Optional[Path] = None,
    timeout_s: float = 30.0,
) -> Gateway:
    """Construct MiniMax through its adapter-owned environment/transport path."""
    return MiniMaxAdapter.from_env(
        model=model_id,
        client=client,
        env_path=env_path,
        timeout_s=timeout_s,
    )


def default_gateway_registry() -> GatewayRegistry:
    """Return the built-in provider registry."""
    registry = GatewayRegistry()
    registry.register("minimax", _create_minimax)
    return registry


def create_gateway(
    config: Optional[dict[str, Any]] = None,
    *,
    registry: Optional[GatewayRegistry] = None,
    **factory_options: Any,
) -> Gateway:
    """Create the semantic gateway selected by runtime configuration.

    The configured provider and model are validated against the concrete
    adapter identity before the gateway is returned. A mismatch fails closed
    instead of allowing an adapter to answer as another deployment.
    """
    from hpcu.runtime_config import configured_semantic_identity, load_runtime_config

    runtime_config = config if config is not None else load_runtime_config()
    identity = configured_semantic_identity(runtime_config)
    selected_registry = registry if registry is not None else default_gateway_registry()
    if registry is None and "timeout_s" not in factory_options:
        factory_options["timeout_s"] = float(
            runtime_config.get("performance", {}).get("model_call_timeout_ms", 30000)
        ) / 1000.0
    gateway = selected_registry.create(identity, **factory_options)
    gateway.require_configured_identity(identity)
    return gateway


create_semantic_gateway = create_gateway

__all__ = [
    "GatewayFactory",
    "GatewayRegistry",
    "SemanticGatewayRegistry",
    "create_gateway",
    "create_semantic_gateway",
    "default_gateway_registry",
]
