"""Async boundary for synchronous and native-async semantic gateways."""

from __future__ import annotations

import asyncio

from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
)
from hpcu.schemas.failure_codes import FailureCode


class AsyncGatewayTimeout(TimeoutError):
    """The task deadline expired while awaiting a semantic provider."""

    failure_code = FailureCode.MODEL_TIMEOUT.value


async def call_gateway_async(
    gateway: Gateway,
    prompt: str,
    system_prompt: str = "",
    max_tokens: int | None = None,
    *,
    purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    timeout_ms: int | None = None,
) -> GatewayResponse:
    """Call a gateway without blocking the runtime event loop.

    Gateways may expose a native ``acall`` method. Synchronous adapters are
    isolated in a worker thread. Cancelling this coroutine immediately releases
    the task path; the provider's own timeout remains the hard bound for a
    synchronous request already running inside that worker.
    """
    if timeout_ms is not None and timeout_ms <= 0:
        raise ValueError("semantic timeout_ms must be positive")

    native = getattr(gateway, "acall", None)
    if callable(native):
        awaitable = native(
            prompt,
            system_prompt,
            max_tokens,
            purpose=purpose,
        )
    else:
        awaitable = asyncio.to_thread(
            gateway.call,
            prompt,
            system_prompt,
            max_tokens,
            purpose=purpose,
        )

    try:
        if timeout_ms is None:
            return await awaitable
        async with asyncio.timeout(timeout_ms / 1000.0):
            return await awaitable
    except TimeoutError as error:
        raise AsyncGatewayTimeout(
            f"semantic provider exceeded {timeout_ms}ms task deadline"
        ) from error
