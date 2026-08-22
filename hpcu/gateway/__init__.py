"""Provider-neutral semantic gateway contracts and injected adapters."""

from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
    SemanticIdentity,
)
from hpcu.gateway.minimax_adapter import (
    MiniMaxAdapter,
    schema_validate,
    strip_thinking,
)
from hpcu.gateway.registry import (
    GatewayRegistry,
    SemanticGatewayRegistry,
    create_gateway,
    create_semantic_gateway,
    default_gateway_registry,
)

__all__ = [
    "Gateway",
    "GatewayResponse",
    "ModelCallPurpose",
    "SemanticIdentity",
    "MiniMaxAdapter",
    "schema_validate",
    "strip_thinking",
    "GatewayRegistry",
    "SemanticGatewayRegistry",
    "create_gateway",
    "create_semantic_gateway",
    "default_gateway_registry",
]
