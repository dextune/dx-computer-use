"""Text-LLM gateway — the model is an expensive, swappable dependency.

Only the `Gateway` contract and its response DTO live here.  Concrete
adapters (e.g. MiniMaxAdapter) implement the same ABC and are injected.
"""

from hpcu.gateway.gateway import Gateway, GatewayResponse
from hpcu.gateway.minimax_adapter import (
    MiniMaxAdapter,
    schema_validate,
    strip_thinking,
)

__all__ = [
    "Gateway",
    "GatewayResponse",
    "MiniMaxAdapter",
    "schema_validate",
    "strip_thinking",
]
