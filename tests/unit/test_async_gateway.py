"""Tests for the non-blocking semantic gateway boundary."""

import asyncio
import threading

import pytest

from hpcu.gateway.async_gateway import AsyncGatewayTimeout, call_gateway_async
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose

pytestmark = pytest.mark.unit


class _ThreadGateway(Gateway):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        del prompt, system_prompt, max_tokens, purpose
        self.started.set()
        self.release.wait(timeout=1)
        return GatewayResponse(content="{}", model="fake")


class _NativeAsyncGateway(_ThreadGateway):
    async def acall(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        del prompt, system_prompt, max_tokens, purpose
        await asyncio.sleep(1)
        return GatewayResponse(content="{}", model="fake")


@pytest.mark.asyncio
async def test_sync_gateway_does_not_block_event_loop():
    gateway = _ThreadGateway()
    task = asyncio.create_task(call_gateway_async(gateway, "ping"))

    started = await asyncio.to_thread(gateway.started.wait, 1)
    assert started is True
    ticked = False

    async def tick() -> None:
        nonlocal ticked
        await asyncio.sleep(0)
        ticked = True

    await tick()
    assert ticked is True
    gateway.release.set()
    assert (await task).content == "{}"


@pytest.mark.asyncio
async def test_native_async_gateway_timeout_is_typed():
    with pytest.raises(AsyncGatewayTimeout, match="deadline"):
        await call_gateway_async(
            _NativeAsyncGateway(),
            "ping",
            timeout_ms=1,
        )


@pytest.mark.asyncio
async def test_native_async_gateway_can_be_cancelled():
    task = asyncio.create_task(
        call_gateway_async(_NativeAsyncGateway(), "ping")
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_timeout_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        asyncio.run(
            call_gateway_async(
                _ThreadGateway(),
                "ping",
                timeout_ms=0,
            )
        )
