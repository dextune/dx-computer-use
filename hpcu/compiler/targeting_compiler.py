"""Goal → TargetingPack compiler with a configured semantic provider.

T1: GoalFallbackTokenizer — goal string only, no model, no hardcoded dictionary.
T2: TargetingCompiler — one logical plan call, strict field typing, and a
bounded goal-token fallback. Provider transport retries belong in the gateway,
not in this compiler.
"""

from __future__ import annotations

import re
from typing import Optional

from hpcu.gateway.gateway import Gateway, ModelCallPurpose
from hpcu.gateway.json_response import extract_json_objects
from hpcu.runtime_config import load_runtime_config
from hpcu.schemas.targeting import TargetingPack

_PUNCTUATION_SPLIT = re.compile(r"[，。！？、\s,;:.!?()\[\]{}<>\"'\-_/\\|@#$%^&*+=~`]+")
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
    """Split a goal string into tokens, filtering short ones."""
    raw = _PUNCTUATION_SPLIT.split(goal)
    seen: set[str] = set()
    tokens: list[str] = []
    for token in raw:
        stripped = token.strip()
        if len(stripped) >= min_length and stripped not in seen:
            seen.add(stripped)
            tokens.append(stripped)
    return tokens


class GoalFallbackTokenizer:
    """Convert a goal string into a TargetingPack with zero model calls.

    The resulting tokens are derived ONLY from the goal string. No hardcoded
    site/brand/CTA dictionary is consulted.
    """

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
        pick_query = " ".join(tokens)
        return TargetingPack(
            goal_id=goal_id,
            source="goal_tokens",
            ready_any=capped,
            success_any=capped,
            forbid_any=(),
            pick_query=pick_query,
            pick_required=True,
            dismiss_any=(),
            blocked_any=(),
            ignore_any=(),
        )


class TargetingCompiler:
    """Compile a user goal into a TargetingPack via one logical model call.

    Network retries are handled below this layer by ``RetryableGateway``. An
    invalid response therefore cannot silently consume a second semantic
    planning call. On any failure, a goal-derived pack is returned and the
    caller can inspect ``last_diagnostic``; the action runner remains
    responsible for rejecting lexical fallback when a semantic plan is
    mandatory.
    """

    _THINKING_STRIP = re.compile(
        r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?>",
        re.DOTALL | re.IGNORECASE,
    )

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
        self._fallback = GoalFallbackTokenizer(config=config)
        self.last_diagnostic = ""

    def compile(self, goal_id: str, goal: str, start_url: str = "") -> TargetingPack:
        self.last_diagnostic = ""
        if self._gateway is None:
            self.last_diagnostic = "gateway_unavailable"
            return self._fallback.tokenize(goal_id, goal)

        try:
            response = self._gateway.call(
                prompt=_build_compile_prompt(goal, start_url),
                system_prompt=(
                    "Return exactly one compact JSON object. No markdown, "
                    "commentary, reasoning, or null values."
                ),
                max_tokens=self._max_tokens,
                purpose=ModelCallPurpose.PLAN_COMPILE,
            )
        except Exception as error:
            self.last_diagnostic = f"gateway_error:{type(error).__name__}"
            return self._fallback.tokenize(goal_id, goal)

        content = self._THINKING_STRIP.sub("", response.content).strip()
        payload = _load_json_object(content)
        if payload is None:
            self.last_diagnostic = "schema_error:no_unique_targeting_object"
            return self._fallback.tokenize(goal_id, goal)

        try:
            return _pack_from_json(goal_id, payload)
        except ValueError as error:
            self.last_diagnostic = f"schema_error:{error}"
            return self._fallback.tokenize(goal_id, goal)


def _load_json_object(content: str) -> dict | None:
    """Select one targeting-shaped object without repairing model semantics.

    Diagnostic/wrapper objects are ignored when exactly one object contains a
    known targeting field. Identical duplicates are already de-duplicated by
    ``extract_json_objects``. Conflicting targeting objects fail closed.

    A single non-targeting object remains accepted for backward-compatible
    default construction; field typing is still validated by ``_pack_from_json``.
    """
    objects = extract_json_objects(content)
    targeting_objects = [item for item in objects if item.keys() & _TARGETING_KEYS]
    if len(targeting_objects) == 1:
        return targeting_objects[0]
    if len(targeting_objects) > 1:
        return None
    return objects[0] if len(objects) == 1 else None


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
        "- dismiss_any: array of strings for overlays/consent controls\n"
        "- blocked_any: array of strings for access-control/captcha screens\n"
        "- ignore_any: array of strings to exclude from target matching\n\n"
        "Use short observed-text candidates. All eight fields are required. "
        "Never use null, markdown, prose, or a second JSON object.\n"
        '{"ready_any":[],"success_any":[],"forbid_any":[],'
        '"pick_query":"","pick_required":true,"dismiss_any":[],'
        '"blocked_any":[],"ignore_any":[]}'
    )


def _pack_from_json(goal_id: str, payload: dict) -> TargetingPack:
    pick_required = payload.get("pick_required", True)
    if not isinstance(pick_required, bool):
        raise ValueError("pick_required must be boolean")

    pick_query = payload.get("pick_query", "")
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
    if key not in payload:
        return []
    value = payload[key]
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array of strings")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must contain strings only")
    return value
