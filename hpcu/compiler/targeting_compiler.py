"""Goal to TargetingPack compiler with one typed semantic boundary."""

from __future__ import annotations

import re
from typing import Optional

from hpcu.gateway.async_gateway import call_gateway_async
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.gateway.json_response import select_json_object
from hpcu.runtime_config import load_runtime_config, semantic_call_timeout_ms
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.targeting import TargetingPack

_PUNCTUATION_SPLIT = re.compile(
    r"[，。！？、\s,;:.!?()\[\]{}<>\"'\-_/\\|@#$%^&*+=~`]+"
)
_TARGETING_KEYS = frozenset(
    {
        "ready_any",
        "success_any",
        "forbid_any",
        "pick_query",
        "pick_required",
        "dismiss_any",
        "blocked_any",
        "ignore_any",
    }
)


def _tokenize_goal(goal: str, min_length: int) -> list[str]:
    raw = _PUNCTUATION_SPLIT.split(goal)
    seen: set[str] = set()
    tokens: list[str] = []
    for token in raw:
        stripped = token.strip()
        if len(stripped) >= min_length and stripped not in seen:
            seen.add(stripped)
            tokens.append(stripped)
    return tokens


class TargetingCompilationError(RuntimeError):
    """A configured semantic compilation failed before an executable plan."""

    def __init__(self, diagnostic: str, failure_code: FailureCode) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic
        self.failure_code = failure_code.value


class GoalFallbackTokenizer:
    """Convert a goal string to a pack without site or CTA dictionaries."""

    def __init__(self, *, config: Optional[dict] = None):
        runtime = config if config is not None else load_runtime_config()
        targeting = runtime.get("targeting", {})
        self._min_length = int(targeting.get("min_token_length", 2))
        self._max_tokens = int(targeting.get("max_tokens_per_field", 8))

    def tokenize(self, goal_id: str, goal: str) -> TargetingPack:
        tokens = _tokenize_goal(goal, self._min_length)
        if not tokens:
            tokens = [goal.strip()]
        capped = tuple(tokens[: self._max_tokens])
        return TargetingPack(
            goal_id=goal_id,
            source="goal_tokens",
            ready_any=capped,
            success_any=capped,
            forbid_any=(),
            pick_query=" ".join(tokens),
            pick_required=True,
            dismiss_any=(),
            blocked_any=(),
            ignore_any=(),
        )


class TargetingCompiler:
    """Compile a user goal once and fail closed for configured providers."""

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        *,
        config: Optional[dict] = None,
    ):
        self._gateway = gateway
        runtime = config if config is not None else load_runtime_config()
        limits = runtime.get("semantic", {}).get("request_limits", {})
        self._max_tokens = int(limits.get("plan_compile_max_tokens", 1024))
        self._timeout_ms = semantic_call_timeout_ms(runtime)
        self._fallback = GoalFallbackTokenizer(config=runtime)
        self.last_diagnostic = ""

    def compile(self, goal_id: str, goal: str, start_url: str = "") -> TargetingPack:
        self.last_diagnostic = ""
        if self._gateway is None:
            self.last_diagnostic = "gateway_unavailable"
            return self._fallback.tokenize(goal_id, goal)
        try:
            response = self._gateway.call(
                prompt=_build_compile_prompt(goal, start_url),
                system_prompt=_compile_system_prompt(),
                max_tokens=self._max_tokens,
                purpose=ModelCallPurpose.PLAN_COMPILE,
            )
        except Exception as error:
            self._raise_gateway_error(error)
        return self._pack_response(goal_id, response)

    async def compile_async(
        self,
        goal_id: str,
        goal: str,
        start_url: str = "",
    ) -> TargetingPack:
        """Compile without blocking the task runtime event loop."""
        self.last_diagnostic = ""
        if self._gateway is None:
            self.last_diagnostic = "gateway_unavailable"
            return self._fallback.tokenize(goal_id, goal)
        try:
            response = await call_gateway_async(
                self._gateway,
                _build_compile_prompt(goal, start_url),
                system_prompt=_compile_system_prompt(),
                max_tokens=self._max_tokens,
                purpose=ModelCallPurpose.PLAN_COMPILE,
                timeout_ms=self._timeout_ms,
            )
        except Exception as error:
            self._raise_gateway_error(error)
        return self._pack_response(goal_id, response)

    def _raise_gateway_error(self, error: Exception) -> None:
        diagnostic = f"gateway_error:{type(error).__name__}"
        self.last_diagnostic = diagnostic
        failure_code = self._gateway_failure_code(error)
        raise TargetingCompilationError(diagnostic, failure_code) from error

    def _pack_response(
        self,
        goal_id: str,
        response: GatewayResponse,
    ) -> TargetingPack:
        try:
            payload = select_json_object(
                response.content,
                required_keys=_TARGETING_KEYS,
                allowed_keys=_TARGETING_KEYS,
                schema_name="targeting plan",
            )
        except ValueError as error:
            diagnostic = "schema_error:no_unique_targeting_object"
            self.last_diagnostic = diagnostic
            raise TargetingCompilationError(
                diagnostic,
                FailureCode.MODEL_SCHEMA_INVALID,
            ) from error

        try:
            return _pack_from_json(goal_id, payload)
        except ValueError as error:
            diagnostic = f"schema_error:{error}"
            self.last_diagnostic = diagnostic
            raise TargetingCompilationError(
                diagnostic,
                FailureCode.MODEL_SCHEMA_INVALID,
            ) from error

    @staticmethod
    def _gateway_failure_code(error: Exception) -> FailureCode:
        raw = getattr(error, "failure_code", FailureCode.MODEL_FAILED.value)
        try:
            return FailureCode(raw)
        except (TypeError, ValueError):
            return FailureCode.MODEL_FAILED


def _compile_system_prompt() -> str:
    return (
        "Return exactly one compact JSON object. No markdown, commentary, "
        "reasoning, or null values."
    )


def _build_compile_prompt(goal: str, start_url: str) -> str:
    url_line = f"Start URL: {start_url}\n" if start_url else ""
    return (
        "Compile one navigation targeting plan for a computer-use agent.\n"
        f"{url_line}"
        f"Goal: {goal}\n\n"
        "Return exactly one JSON object with these eight fields:\n"
        "- ready_any: array of strings visible when the page is ready\n"
        "- success_any: array of strings visible when the goal is complete\n"
        "- forbid_any: array of strings indicating a wrong or failed page\n"
        "- pick_query: string describing the intended target\n"
        "- pick_required: boolean\n"
        "- dismiss_any: array of strings for overlays or consent controls\n"
        "- blocked_any: array of strings for access-control screens\n"
        "- ignore_any: array of strings to exclude from target matching\n\n"
        "Use short observed-text candidates. All eight fields are required. "
        "Never use null, markdown, prose, or a second JSON object.\n"
        '{"ready_any":[],"success_any":[],"forbid_any":[],'
        '"pick_query":"","pick_required":true,"dismiss_any":[],'
        '"blocked_any":[],"ignore_any":[]}'
    )


def _pack_from_json(goal_id: str, payload: dict) -> TargetingPack:
    pick_required = payload["pick_required"]
    if not isinstance(pick_required, bool):
        raise ValueError("pick_required must be boolean")

    pick_query = payload["pick_query"]
    if not isinstance(pick_query, str):
        raise ValueError("pick_query must be a string")

    return TargetingPack(
        goal_id=goal_id,
        source="model",
        ready_any=tuple(_list_field(payload, "ready_any")),
        success_any=tuple(_list_field(payload, "success_any")),
        forbid_any=tuple(_list_field(payload, "forbid_any")),
        pick_query=pick_query,
        pick_required=pick_required,
        dismiss_any=tuple(_list_field(payload, "dismiss_any")),
        blocked_any=tuple(_list_field(payload, "blocked_any")),
        ignore_any=tuple(_list_field(payload, "ignore_any")),
    )


def _list_field(payload: dict, key: str) -> list[str]:
    value = payload[key]
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array of strings")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must contain strings only")
    return value
