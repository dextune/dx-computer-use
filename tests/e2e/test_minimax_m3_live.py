"""Live MiniMax M3 call using MINIMAX_API_KEY from env or .env."""

import pytest

from hpcu.gateway.minimax_adapter import MiniMaxAdapter, load_minimax_api_key


@pytest.mark.e2e
def test_minimax_m3_ping_from_env():
    api_key = load_minimax_api_key()
    if not api_key:
        pytest.skip("MINIMAX_API_KEY is not set")
    adapter = MiniMaxAdapter.from_env()
    response = adapter.call(
        "Reply with the single word pong and nothing else.",
        # MiniMax-M3 emits reasoning before the visible answer; reserve enough
        # output budget for both phases so the live smoke test is deterministic.
        max_tokens=256,
    )
    assert response.content
    assert "pong" in response.content.lower()
    assert response.tokens_used >= 0
    assert response.latency_ms >= 0
