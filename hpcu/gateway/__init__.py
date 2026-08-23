"""Provider-neutral semantic gateway contracts and injected adapters."""

from hpcu.gateway.async_gateway import (
    AsyncGatewayTimeout,
    call_gateway_async,
)
from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
    RetryableGateway,
    SemanticIdentity,
    walk_gateway_chain,
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
    "AsyncGatewayTimeout",
    "Gateway",
    "GatewayRegistry",
    "GatewayResponse",
    "MiniMaxAdapter",
    "ModelCallPurpose",
    "RetryableGateway",
    "SemanticGatewayRegistry",
    "SemanticIdentity",
    "call_gateway_async",
    "create_gateway",
    "create_semantic_gateway",
    "default_gateway_registry",
    "schema_validate",
    "strip_thinking",
    "walk_gateway_chain",
]
