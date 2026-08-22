"""Goal → TargetingPack compiler with a configured semantic provider.

T1: GoalFallbackTokenizer — goal string only, no model, no hardcoded dictionary.
T2: TargetingCompiler — wraps CountingGateway, validates schemas, and strips
provider-specific reasoning.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from hpcu.cases.stats import CountingGateway
from hpcu.gateway.gateway import ModelCallPurpose
from hpcu.runtime_config import load_runtime_config
from hpcu.schemas.targeting import TargetingPack

_PUNCTUATION_SPLIT = re.compile(r"[，。！？、\s,;:.!?()\[\]{}<>\"'\-_/\\|@#$%^&*+=~`]+")


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

    The resulting tokens are derived ONLY from the goal string.  No
    hardcoded site/brand/CTA dictionary is consulted.
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
    """Compile a user goal into a TargetingPack via the configured provider.

    Calls the model at plan-time (≤1 per task). On failure, returns a
    goal-derived pack marked ``goal_tokens``; the action runner must reject
    that pack when a configured gateway is present rather than silently
    clicking from lexical fallback data.
    """

    _THINKING_STRIP = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)

    def __init__(
        self,
        gateway: Optional[CountingGateway] = None,
        *,
        config: Optional[dict] = None,
    ):
        self._gateway = gateway
        runtime = config if config is not None else load_runtime_config()
        limits = runtime.get("semantic", {}).get("request_limits", {})
        self._max_tokens = int(limits.get("plan_compile_max_tokens", 1024))
        self._retry_attempts = max(
            1, int(limits.get("plan_compile_retry_attempts", 2))
        )
        self._fallback = GoalFallbackTokenizer(config=config)

    def compile(self, goal_id: str, goal: str, start_url: str = "") -> TargetingPack:
        if self._gateway is None:
            return self._fallback.tokenize(goal_id, goal)
        prompt = _build_compile_prompt(goal, start_url)
        for _attempt in range(self._retry_attempts):
            try:
                response = self._gateway.call(
                    prompt=prompt,
                    system_prompt=(
                        "JSON only. Return one compact JSON object and no markdown."
                    ),
                    max_tokens=self._max_tokens,
                    purpose=ModelCallPurpose.PLAN_COMPILE,
                )
            except Exception:
                continue

            content = self._THINKING_STRIP.sub("", response.content).strip()
            payload = _load_json_object(content)
            if isinstance(payload, dict):
                return _pack_from_json(goal_id, goal, payload, self._fallback)

        return self._fallback.tokenize(goal_id, goal)


def _load_json_object(content: str) -> dict | None:
    """Load a JSON object from a provider response without semantic repair."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match is None:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _build_compile_prompt(goal: str, start_url: str) -> str:
    url_line = f"Start URL: {start_url}\n" if start_url else ""
    return (
        f"You help a computer-use agent navigate a web page. "
        f"Return JSON only.\n"
        f"{url_line}"
        f"Goal: {goal}\n\n"
        f"Predict short tokens (1-4 words each) that will appear on screen:\n"
        f"- ready_any: tokens that signal the page has loaded\n"
        f"- success_any: tokens that signal the goal is achieved "
        f"(empty = same as ready_any)\n"
        f"- forbid_any: tokens that signal a wrong page or failure\n"
        f"- pick_query: the best search phrase to find the click target\n"
        f"- pick_required: true if the user must click something\n"
        f"- dismiss_any: tokens for consent/close/overlay buttons\n"
        f"- blocked_any: tokens for anti-bot/blocked/captcha pages\n"
        f"- ignore_any: tokens to skip when picking\n\n"
        f'Return JSON: {{"ready_any":[],"success_any":[],"forbid_any":[],'
        f'"pick_query":"","pick_required":true,"dismiss_any":[],'
        f'"blocked_any":[],"ignore_any":[]}}'
    )


def _pack_from_json(
    goal_id: str,
    goal: str,
    payload: dict,
    fallback: GoalFallbackTokenizer,
) -> TargetingPack:
    try:
        pick_required = payload.get("pick_required", True)
        if not isinstance(pick_required, bool):
            raise ValueError("pick_required must be boolean")
        return TargetingPack(
            goal_id=goal_id,
            source="model",
            ready_any=tuple(_list_field(payload, "ready_any")),
            success_any=tuple(_list_field(payload, "success_any")),
            forbid_any=tuple(_list_field(payload, "forbid_any")),
            pick_query=str(payload.get("pick_query", "")),
            pick_required=pick_required,
            dismiss_any=tuple(_list_field(payload, "dismiss_any")),
            blocked_any=tuple(_list_field(payload, "blocked_any")),
            ignore_any=tuple(_list_field(payload, "ignore_any")),
        )
    except Exception:
        return fallback.tokenize(goal_id, goal)


def _list_field(payload: dict, key: str) -> list[str]:
    value = payload.get(key, [])
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str)]
    return []
