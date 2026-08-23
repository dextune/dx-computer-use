"""Regression tests for fail-closed targeting-model schema normalization."""

import pytest

from hpcu.compiler.targeting_compiler import (
    TargetingCompilationError,
    TargetingCompiler,
)
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.schemas.failure_codes import FailureCode

pytestmark = pytest.mark.unit


class _Gateway(Gateway):
    def __init__(self, content: str) -> None:
        self.content = content

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        del prompt, system_prompt, max_tokens, purpose
        return GatewayResponse(content=self.content, model="fake", provider="fake")


def _payload(*, blocked: str = "captcha", ready: str = "ready") -> str:
    return (
        '{"ready_any":["' + ready + '"],'
        '"success_any":["done"],"forbid_any":[],'
        '"pick_query":" target ","pick_required":true,'
        '"dismiss_any":[],"blocked_any":["' + blocked + '"],'
        '"ignore_any":[]}'
    )


def test_model_array_tokens_are_trimmed_at_schema_boundary():
    pack = TargetingCompiler(_Gateway(_payload(ready=" ready "))).compile(
        "case", "goal"
    )

    assert pack.ready_any == ("ready",)
    assert pack.pick_query == "target"


def test_empty_blocked_token_is_model_schema_invalid():
    compiler = TargetingCompiler(_Gateway(_payload(blocked="   ")))

    with pytest.raises(TargetingCompilationError) as exc_info:
        compiler.compile("case", "goal")

    assert exc_info.value.failure_code == FailureCode.MODEL_SCHEMA_INVALID.value
    assert "blocked_any must contain non-empty strings" in exc_info.value.diagnostic
