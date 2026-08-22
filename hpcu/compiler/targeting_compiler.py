"""Goal → TargetingPack compiler.  MiniMax-M3 plan-time, fallback otherwise.

T1: GoalFallbackTokenizer — goal string only, no model, no hardcoded 사전.
T2: TargetingCompiler — wraps CountingGateway, schema-validates, strips thinking.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from hpcu.cases.stats import CountingGateway
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
    """Compile a user goal into a TargetingPack via MiniMax-M3.

    Calls the model at plan-time (≤1 per task).  On any failure —
    broken JSON, timeout, schema mismatch — falls back to
    GoalFallbackTokenizer so the runner never halts.
    """

    _THINKING_STRIP = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)

    def __init__(
        self,
        gateway: Optional[CountingGateway] = None,
        *,
        config: Optional[dict] = None,
    ):
        self._gateway = gateway
        self._fallback = GoalFallbackTokenizer(config=config)

    def compile(self, goal_id: str, goal: str, start_url: str = "") -> TargetingPack:
        if self._gateway is None:
            return self._fallback.tokenize(goal_id, goal)
        try:
            response = self._gateway.call(
                prompt=_build_compile_prompt(goal, start_url),
                system_prompt="JSON only.",
                max_tokens=512,
            )
        except Exception:
            return self._fallback.tokenize(goal_id, goal)

        content = self._THINKING_STRIP.sub("", response.content).strip()
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return self._fallback.tokenize(goal_id, goal)

        if not isinstance(payload, dict):
            return self._fallback.tokenize(goal_id, goal)

        return _pack_from_json(goal_id, payload, self._fallback)


def _build_compile_prompt(goal: str, start_url: str) -> str:
    url_line = f"Start URL: {start_url}\n" if start_url else ""
    return (
        f"You help a computer-use agent navigate a web page. "
        f"Return JSON only.\n"
        f"{url_line}"
        f"Goal: {goal}\n\n"
        f"Predict short tokens (1-4 words each) that will appear on screen:\n"
        f"- ready_any: tokens that signal the page has loaded\n"
        f"- success_any: tokens that signal the goal is achieved (empty = same as ready_any)\n"
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
    goal_id: str, payload: dict, fallback: GoalFallbackTokenizer
) -> TargetingPack:
    try:
        return TargetingPack(
            goal_id=goal_id,
            source="minimax",
            ready_any=tuple(_list_field(payload, "ready_any")),
            success_any=tuple(_list_field(payload, "success_any")),
            forbid_any=tuple(_list_field(payload, "forbid_any")),
            pick_query=str(payload.get("pick_query", "")),
            pick_required=bool(payload.get("pick_required", True)),
            dismiss_any=tuple(_list_field(payload, "dismiss_any")),
            blocked_any=tuple(_list_field(payload, "blocked_any")),
            ignore_any=tuple(_list_field(payload, "ignore_any")),
        )
    except Exception:
        return fallback.tokenize(goal_id, payload.get("goal_id", goal_id))


def _list_field(payload: dict, key: str) -> list[str]:
    value = payload.get(key, [])
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str)]
    return []