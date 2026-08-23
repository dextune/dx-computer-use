"""Reliability regressions for one-call targeting compilation."""

import pytest

from hpcu.compiler.targeting_compiler import (
    TargetingCompilationError,
    TargetingCompiler,
)
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.schemas.failure_codes import FailureCode

pytestmark = pytest.mark.unit

CONFIG = {
    "semantic": {
        "request_limits": {
            "plan_compile_max_tokens": 256,
            "plan_compile_retry_attempts": 5,
        }
    },
    "targeting": {"min_token_length": 2, "max_tokens_per_field": 8},
}

_VALID = (
    '{"ready_any":["ready"],"success_any":["done"],'
    '"forbid_any":[],"pick_query":"item","pick_required":true,'
    '"dismiss_any":[],"blocked_any":[],"ignore_any":[]}'
)


class _Gateway(Gateway):
    def __init__(self, content: str = _VALID, error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls = 0
        self.purposes: list[ModelCallPurpose] = []

    @property
    def provider_id(self) -> str:
        return "minimax"

    @property
    def model_id(self) -> str:
        return "MiniMax-M3"

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        self.calls += 1
        self.purposes.append(purpose)
        if self.error is not None:
            raise self.error
        return GatewayResponse(
            content=self.content,
            model=self.model_id,
            provider=self.provider_id,
        )


def test_invalid_plan_never_triggers_second_logical_call():
    gateway = _Gateway(content="not json")
    compiler = TargetingCompiler(gateway, config=CONFIG)

    with pytest.raises(TargetingCompilationError) as captured:
        compiler.compile("case", "노트북을 검색해줘")

    assert gateway.calls == 1
    assert captured.value.failure_code == FailureCode.MODEL_SCHEMA_INVALID.value
    assert compiler.last_diagnostic == "schema_error:no_unique_targeting_object"


def test_gateway_failure_never_triggers_second_logical_call():
    gateway = _Gateway(error=RuntimeError("down"))
    compiler = TargetingCompiler(gateway, config=CONFIG)

    with pytest.raises(TargetingCompilationError) as captured:
        compiler.compile("case", "노트북을 검색해줘")

    assert gateway.calls == 1
    assert captured.value.failure_code == FailureCode.MODEL_FAILED.value
    assert compiler.last_diagnostic == "gateway_error:RuntimeError"


def test_gateway_failure_preserves_typed_budget_code():
    class _BudgetError(RuntimeError):
        failure_code = FailureCode.MODEL_BUDGET_EXHAUSTED.value

    gateway = _Gateway(error=_BudgetError("exhausted"))
    compiler = TargetingCompiler(gateway, config=CONFIG)

    with pytest.raises(TargetingCompilationError) as captured:
        compiler.compile("case", "노트북을 검색해줘")

    assert captured.value.failure_code == FailureCode.MODEL_BUDGET_EXHAUSTED.value


def test_no_gateway_keeps_explicit_local_tokenizer_path():
    compiler = TargetingCompiler(config=CONFIG)

    pack = compiler.compile("case", "노트북을 검색해줘")

    assert pack.source == "goal_tokens"
    assert compiler.last_diagnostic == "gateway_unavailable"


def test_diagnostic_object_is_ignored_before_unique_targeting_object():
    gateway = _Gateway(content='{"diagnostic":"draft"}\n' + _VALID)
    compiler = TargetingCompiler(gateway, config=CONFIG)
    pack = compiler.compile("case", "item을 찾아줘")
    assert gateway.calls == 1
    assert pack.source == "model"
    assert pack.pick_query == "item"
    assert compiler.last_diagnostic == ""


def test_conflicting_targeting_objects_fail_closed():
    second = _VALID.replace('"pick_query":"item"', '"pick_query":"other"')
    gateway = _Gateway(content=_VALID + "\n" + second)
    compiler = TargetingCompiler(gateway, config=CONFIG)

    with pytest.raises(TargetingCompilationError) as captured:
        compiler.compile("case", "item을 찾아줘")

    assert gateway.calls == 1
    assert captured.value.failure_code == FailureCode.MODEL_SCHEMA_INVALID.value
    assert compiler.last_diagnostic == "schema_error:no_unique_targeting_object"


def test_wrong_list_field_type_fails_closed_without_coercion():
    gateway = _Gateway(
        content=_VALID.replace(
            '"ready_any":["ready"]',
            '"ready_any":"ready"',
        )
    )
    compiler = TargetingCompiler(gateway, config=CONFIG)

    with pytest.raises(TargetingCompilationError) as captured:
        compiler.compile("case", "item을 찾아줘")

    assert captured.value.failure_code == FailureCode.MODEL_SCHEMA_INVALID.value
    assert "ready_any must be an array of strings" in compiler.last_diagnostic


def test_compile_uses_plan_purpose_once():
    gateway = _Gateway()
    compiler = TargetingCompiler(gateway, config=CONFIG)
    compiler.compile("case", "item을 찾아줘", "https://example.com")
    assert gateway.purposes == [ModelCallPurpose.PLAN_COMPILE]
