"""Unit tests for hpcu.gateway.minimax_adapter."""

import pytest

from hpcu.gateway.minimax_adapter import (
    MiniMaxAdapter,
    schema_validate,
    strip_thinking,
)

# ---------------------------------------------------------------------------
# strip_thinking
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_strip_thinking_removes_reasoning_block():
    """Given content with a thinking block, only the reply remains."""
    # Given
    content = (
        " thinking\nThe user wants the submit button"
        "\n response\n{\"target_id\": \"submit\"}"
    )
    # When
    stripped = strip_thinking(content)
    # Then
    assert stripped == '{"target_id": "submit"}'


@pytest.mark.unit
def test_strip_thinking_removes_leading_prose_before_json():
    content = "Here is the requested JSON:\n{\"action\": \"none\"}"
    assert strip_thinking(content) == '{"action": "none"}'


@pytest.mark.unit
def test_strip_thinking_no_block_unchanged():
    """Given content without a thinking block, it is unchanged."""
    # Given
    content = "Just a normal reply."
    # When
    stripped = strip_thinking(content)
    # Then
    assert stripped == "Just a normal reply."


@pytest.mark.unit
def test_strip_thinking_lone_end_marker_preserved():
    """Given a stray ' response' with no thinking start, it is preserved."""
    # Given
    content = "response is not a keyword"
    # When
    stripped = strip_thinking(content)
    # Then
    assert stripped == "response is not a keyword"


@pytest.mark.unit
def test_strip_thinking_unclosed_block_drops_tail():
    """Given an unclosed thinking block, the tail is dropped."""
    # Given
    content = "okay start thinking\npartially leaked reasoning"
    # When
    stripped = strip_thinking(content)
    # Then
    assert stripped == "okay start"


@pytest.mark.unit
def test_strip_thinking_multiple_blocks():
    """Given multiple thinking blocks, all are stripped."""
    # Given
    content = "a thinking\none response middle thinking\ntwo response b"
    # When
    stripped = strip_thinking(content)
    # Then
    assert stripped == "a middle b"


# ---------------------------------------------------------------------------
# schema_validate
# ---------------------------------------------------------------------------

_SCHEMA = {
    "type": "object",
    "required": ["target_id", "confidence"],
    "properties": {
        "target_id": {"type": "string"},
        "confidence": {"type": "number"},
    },
}


@pytest.mark.unit
def test_schema_validate_matches():
    """Given a conforming response, validation passes."""
    # Given
    response = {"target_id": "btn_1", "confidence": 0.9}
    # When
    valid = schema_validate(response, _SCHEMA)
    # Then
    assert valid is True


@pytest.mark.unit
def test_schema_validate_missing_required_fails():
    """Given a missing required field, validation fails."""
    # Given
    response = {"target_id": "btn_1"}
    # When
    valid = schema_validate(response, _SCHEMA)
    # Then
    assert valid is False


@pytest.mark.unit
def test_schema_validate_wrong_type_fails():
    """Given a wrong-typed field, validation fails."""
    # Given
    response = {"target_id": 123, "confidence": 0.9}
    # When
    valid = schema_validate(response, _SCHEMA)
    # Then
    assert valid is False


@pytest.mark.unit
def test_schema_validate_malformed_schema_fails_closed():
    """Given a malformed schema, validation fails closed (no raise)."""
    # Given
    malformed = {"type": "nonsense-type-xyz"}
    # When
    valid = schema_validate({"a": 1}, malformed)
    # Then
    assert valid is False


# ---------------------------------------------------------------------------
# MiniMaxAdapter.call
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, data):
        self._data = data
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(self._data)


@pytest.mark.unit
def test_minimax_call_strips_thinking_and_reports_metadata():
    """Given a fake client, the adapter returns a thinking-stripped response."""
    # Given
    raw = " thinking\nThe user wants to submit\n response\n{\"target_id\": \"submit\"}"
    data = {
        "model": "MiniMax-M3",
        "choices": [{"message": {"content": raw}}],
        "usage": {"total_tokens": 120},
    }
    client = _FakeClient(data)
    adapter = MiniMaxAdapter(api_key="sk-test", client=client)
    # When
    response = adapter.call("find submit", system_prompt="be brief")
    # Then
    assert response.content == '{"target_id": "submit"}'
    assert response.model == "MiniMax-M3"
    assert response.tokens_used == 120
    assert response.latency_ms >= 0


@pytest.mark.unit
def test_minimax_call_builds_correct_request():
    """Given a fake client, the request URL/headers/payload are accurate."""
    # Given
    data = {
        "model": "MiniMax-M3",
        "choices": [{"message": {"content": ""}}],
        "usage": {},
    }
    client = _FakeClient(data)
    adapter = MiniMaxAdapter(api_key="sk-secret", client=client)
    # When
    adapter.call("hello")
    # Then
    call = client.calls[0]
    assert call["url"] == "https://api.minimax.io/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-secret"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"]["model"] == "MiniMax-M3"
    assert call["json"]["stream"] is False


@pytest.mark.unit
def test_minimax_call_sends_system_prompt_only_when_provided():
    """Given a system prompt, it is included; without it, omitted."""
    # Given
    data = {"choices": [{"message": {"content": ""}}], "usage": {}}
    client = _FakeClient(data)
    adapter = MiniMaxAdapter(api_key="sk-secret", client=client)
    # When
    adapter.call("hi", system_prompt="be terse")
    adapter.call("hi")
    # Then
    assert client.calls[0]["json"]["messages"][0]["role"] == "system"
    assert client.calls[0]["json"]["messages"][0]["content"] == "be terse"
    assert all(m["role"] != "system" for m in client.calls[1]["json"]["messages"])


@pytest.mark.unit
def test_minimax_adapter_rejects_empty_api_key():
    """Given an empty api_key, construction is rejected."""
    # When / Then
    with pytest.raises(ValueError):
        MiniMaxAdapter(api_key="", client=_FakeClient({}))


@pytest.mark.unit
def test_load_minimax_api_key_from_env_file(tmp_path, monkeypatch):
    from hpcu.gateway.minimax_adapter import MiniMaxAdapter, load_minimax_api_key

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("MINIMAX_API_KEY=sk-from-file\n", encoding="utf-8")
    assert load_minimax_api_key(env_path=env_file) == "sk-from-file"
    adapter = MiniMaxAdapter.from_env(client=_FakeClient(
        {"choices": [{"message": {"content": "ok"}}], "usage": {}}
    ), env_path=env_file)
    assert adapter._api_key == "sk-from-file"


@pytest.mark.unit
def test_from_env_raises_when_key_missing(tmp_path, monkeypatch):
    from hpcu.gateway.minimax_adapter import MiniMaxAdapter

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    missing = tmp_path / "empty.env"
    missing.write_text("OTHER=1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        MiniMaxAdapter.from_env(env_path=missing)
