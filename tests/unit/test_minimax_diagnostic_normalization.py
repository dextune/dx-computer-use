"""Regression tests for MiniMax nullable diagnostic metadata."""

import json

import pytest

from hpcu.gateway.gateway import ModelCallPurpose
from hpcu.gateway.minimax_adapter import (
    MiniMaxAdapter,
    normalize_nullable_diagnostics,
)

pytestmark = pytest.mark.unit


def test_reason_code_null_is_normalized_without_touching_action_fields():
    content = (
        '{"reason_code":null,"target_id":null,"value":"null",'
        '"other":null}'
    )
    normalized = normalize_nullable_diagnostics(content)
    payload = json.loads(normalized)
    assert payload["reason_code"] == "provider_unspecified"
    assert payload["target_id"] is None
    assert payload["value"] == "null"
    assert payload["other"] is None


def test_multiple_objects_normalize_only_reason_code_tokens():
    content = (
        '{"diagnostic":true,"reason_code": null}\n'
        '{"reason_code":"already_set"}'
    )
    normalized = normalize_nullable_diagnostics(content)
    assert normalized.count('"provider_unspecified"') == 1
    assert '"reason_code":"already_set"' in normalized


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "model": "MiniMax-M3",
            "choices": [
                {
                    "message": {
                        "content": (
                            "```json\n"
                            '{"schema_version":1,"scene_version":9,'
                            '"goal_state":"success","challenge_kind":"none",'
                            '"confidence":0.9,"reason_code":null}'
                            "\n```"
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 12},
        }


class _Client:
    def post(self, *_args, **_kwargs) -> _Response:
        return _Response()


def test_adapter_returns_normalized_reanalysis_content():
    adapter = MiniMaxAdapter(api_key="test", client=_Client())
    result = adapter.call(
        "reanalyze",
        purpose=ModelCallPurpose.POST_ACTION_REANALYSIS,
    )
    payload = json.loads(result.content)
    assert payload["reason_code"] == "provider_unspecified"
    assert payload["scene_version"] == 9
