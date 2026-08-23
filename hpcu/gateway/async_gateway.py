"""Async boundary for synchronous and native-async semantic gateways."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
)
from hpcu.schemas.failure_codes import FailureCode


class AsyncGatewayTimeout(TimeoutError):
    """The task deadline expired while awaiting a semantic provider."""

    failure_code = FailureCode.MODEL_TIMEOUT.value


async def _cancel_and_wait(task: asyncio.Future[GatewayResponse]) -> None:
    """Cancel an in-flight gateway task and consume its cancellation."""
    if task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


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

    Deadline expiry is detected with ``asyncio.wait`` rather than by catching
    ``TimeoutError``. On Python 3.11+ ``asyncio.TimeoutError`` aliases the
    built-in exception, so exception-type matching cannot distinguish a runtime
    deadline from a provider that raises ``TimeoutError`` itself.
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

    task = asyncio.ensure_future(awaitable)
    try:
        if timeout_ms is None:
            return await task

        done, _pending = await asyncio.wait(
            {task},
            timeout=timeout_ms / 1000.0,
        )
        if task in done:
            # Propagate provider exceptions unchanged. In particular, a
            # provider-raised TimeoutError is not the runtime deadline.
            return task.result()

        await _cancel_and_wait(task)
        raise AsyncGatewayTimeout(
            f"semantic provider exceeded {timeout_ms}ms task deadline"
        )
    except BaseException:
        await _cancel_and_wait(task)
        raise
