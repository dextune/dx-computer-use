"""Provider-neutral semantic filling for unresolved goal slots only."""

from __future__ import annotations

import json

from hpcu.gateway.async_gateway import call_gateway_async
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.gateway.json_response import select_json_object
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.surface import SurfaceKind


class SemanticSlotFiller:
    """Ask the configured gateway only for explicitly unresolved fields."""

    def __init__(
        self,
        gateway: Gateway,
        *,
        max_tokens: int = 256,
        timeout_ms: int | None = None,
    ):
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if timeout_ms is not None and timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self._gateway = gateway
        self._max_tokens = max_tokens
        self._timeout_ms = timeout_ms

    def __call__(self, goal: GoalEnvelope) -> dict[str, str]:
        allowed = tuple(goal.ambiguity_slots)
        if not allowed:
            return {}
        response = self._gateway.call(
            self._prompt(goal, allowed),
            system_prompt=self._system_prompt(),
            max_tokens=self._max_tokens,
            purpose=ModelCallPurpose.INTENT_FILL,
        )
        return self._parse(response, allowed)

    async def fill(self, goal: GoalEnvelope) -> dict[str, str]:
        """Fill slots without blocking capture, timers, or other task coroutines."""
        allowed = tuple(goal.ambiguity_slots)
        if not allowed:
            return {}
        response = await call_gateway_async(
            self._gateway,
            self._prompt(goal, allowed),
            system_prompt=self._system_prompt(),
            max_tokens=self._max_tokens,
            purpose=ModelCallPurpose.INTENT_FILL,
            timeout_ms=self._timeout_ms,
        )
        return self._parse(response, allowed)

    @staticmethod
    def _prompt(goal: GoalEnvelope, allowed: tuple[str, ...]) -> str:
        return json.dumps(
            {
                "instruction": goal.raw_instruction,
                "unresolved_slots": allowed,
                "allowed_intents": [
                    item.value
                    for item in IntentKind
                    if item is not IntentKind.UNKNOWN
                ],
                "allowed_surfaces": [
                    item.value
                    for item in SurfaceKind
                    if item not in (SurfaceKind.UNKNOWN, SurfaceKind.CHALLENGE)
                ],
                "contract": (
                    "Return exactly one JSON object. Use every unresolved slot "
                    "exactly once as a key with a string value. Do not return "
                    "actions, selectors, coordinates, risk or success claims."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "Fill only unresolved user-intent slots. "
            "Do not plan actions or weaken local policy."
        )

    @staticmethod
    def _parse(
        response: GatewayResponse,
        allowed: tuple[str, ...],
    ) -> dict[str, str]:
        parsed = select_json_object(
            response.content,
            required_keys=allowed,
            allowed_keys=allowed,
            schema_name="semantic slot fill",
        )
        values: dict[str, str] = {}
        for key in allowed:
            value = parsed[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"semantic slot {key!r} must be a non-empty string"
                )
            values[key] = value.strip()
        return values
