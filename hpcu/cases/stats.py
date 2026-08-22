"""Redacted action logs and provider-neutral semantic call counters."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from hpcu.gateway.gateway import (
    Gateway,
    GatewayResponse,
    ModelCallPurpose,
    SemanticIdentity,
)
from hpcu.runtime_config import configured_semantic_identity

_DEFAULT_IDENTITY = configured_semantic_identity()
DEFAULT_PROVIDER_ID = _DEFAULT_IDENTITY.provider_id
DEFAULT_MODEL_ID = _DEFAULT_IDENTITY.model_id


@dataclass(frozen=True)
class AiCallRecord:
    """Redacted, structured metadata for one semantic model call."""

    call_number: int
    purpose: str
    model: str
    prompt_length: int
    provider: str = ""
    response_tokens: int = 0
    latency_ms: int = 0
    error: str = ""


@dataclass
class ActionRecord:
    kind: str
    detail: str
    ok: bool = True


@dataclass
class AttemptRecord:
    """Redacted evidence correlation for one bounded case attempt."""

    attempt: int
    scene_version_start: int = 0
    selected_target: str = ""
    action_ok: bool = False
    post_scene_version: int = 0
    reanalysis_ok: bool = False
    verification_satisfied: bool = False
    model_call_start: int = 0
    model_call_end: int = 0
    failure: str = ""


@dataclass
class CaseStats:
    case_id: str
    success: bool = False
    runner_success: bool = False
    verified_success: bool = False
    human_confirmed: bool = False
    outcome: str = ""
    failure_code: str = ""
    challenge_kind: str = ""
    handoff_required: bool = False
    final_scene_version: int = 0
    final_frame_id: str = ""
    attempts: int = 0
    max_attempts: int = 0
    action_count: int = 0
    model_call_count: int = 0
    model_error_count: int = 0
    model_tokens: int = 0
    compile_call_count: int = 0
    grounding_call_count: int = 0
    provider: str = DEFAULT_PROVIDER_ID
    model: str = DEFAULT_MODEL_ID
    selected_text: str = ""
    failure: str = ""
    actions: list[ActionRecord] = field(default_factory=list)
    ai_calls: list[AiCallRecord] = field(default_factory=list)
    evidence_status: str = "not_evaluated"
    evidence_scene_version: int = 0
    evidence_frame_id: str = ""
    evidence_tokens: list[str] = field(default_factory=list)
    evidence_element_ids: list[str] = field(default_factory=list)
    stale_rejection_count: int = 0
    reanalysis_count: int = 0
    elapsed_ms: int = 0
    attempts_detail: list[AttemptRecord] = field(default_factory=list)
    artifact_manifest: list[str] = field(default_factory=list)
    decision_diagnostics: list[str] = field(default_factory=list)


class CountingGateway(Gateway):
    """Count calls while enforcing one configured semantic deployment."""

    def __init__(
        self,
        inner: Gateway,
        *,
        expected_provider: str = DEFAULT_PROVIDER_ID,
        expected_model: str = DEFAULT_MODEL_ID,
    ):
        self._expected_provider = expected_provider
        self._expected_model = expected_model
        inner.require_configured_identity(
            SemanticIdentity(expected_provider, expected_model)
        )
        self._inner = inner
        self.call_count = 0
        self.error_count = 0
        self.tokens = 0
        self.model = expected_model
        self.provider = expected_provider
        self.http_log: list[str] = []
        self.ai_calls: list[AiCallRecord] = []

    @property
    def provider_id(self) -> str:
        return self._expected_provider

    @property
    def model_id(self) -> str:
        return self._expected_model

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: Optional[int] = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        if not isinstance(purpose, ModelCallPurpose):
            raise ValueError("purpose must be a ModelCallPurpose")
        self.call_count += 1
        call_number = self.call_count
        self.http_log.append(
            f"MODEL_CALL provider={self._expected_provider} "
            f"model={self._expected_model} purpose={purpose.value} #{call_number}"
        )
        try:
            response = self._inner.call(
                prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                purpose=purpose,
            )
            if response.model != self._expected_model:
                raise ValueError(f"unexpected model response: {response.model!r}")
            if response.provider and response.provider != self._expected_provider:
                raise ValueError(
                    f"unexpected provider response: {response.provider!r}"
                )
        except Exception as error:
            self.error_count += 1
            self.http_log.append(
                f"MODEL_ERROR provider={self._expected_provider} "
                f"model={self._expected_model} purpose={purpose.value} #{call_number}"
            )
            self.ai_calls.append(
                AiCallRecord(
                    call_number=call_number,
                    purpose=purpose.value,
                    model=self._expected_model,
                    provider=self._expected_provider,
                    prompt_length=len(prompt),
                    error=type(error).__name__,
                )
            )
            raise
        self.tokens += int(response.tokens_used)
        self.ai_calls.append(
            AiCallRecord(
                call_number=call_number,
                purpose=purpose.value,
                model=response.model,
                provider=response.provider or self._expected_provider,
                prompt_length=len(prompt),
                response_tokens=int(response.tokens_used),
                latency_ms=int(response.latency_ms),
            )
        )
        return response


def _case_dict(row: CaseStats) -> dict[str, Any]:
    return asdict(row)


def aggregate(rows: list[CaseStats]) -> dict[str, Any]:
    purpose_totals = {
        purpose.value: sum(
            1 for row in rows for call in row.ai_calls if call.purpose == purpose.value
        )
        for purpose in ModelCallPurpose
    }
    provider = rows[0].provider if rows else DEFAULT_PROVIDER_ID
    model = rows[0].model if rows else DEFAULT_MODEL_ID
    return {
        "provider": provider,
        "model": model,
        "cases": [_case_dict(row) for row in rows],
        "totals": {
            "cases": len(rows),
            "successes": sum(1 for row in rows if row.success),
            "action_count": sum(row.action_count for row in rows),
            "model_call_count": sum(row.model_call_count for row in rows),
            "model_error_count": sum(row.model_error_count for row in rows),
            "model_tokens": sum(row.model_tokens for row in rows),
            "compile_call_count": sum(row.compile_call_count for row in rows),
            "grounding_call_count": sum(row.grounding_call_count for row in rows),
            "purpose_call_counts": purpose_totals,
        },
    }


def dumps_stats(rows: list[CaseStats]) -> str:
    return json.dumps(aggregate(rows), ensure_ascii=False, indent=2)
