"""Provider-neutral semantic filling for unresolved GoalEnvelope slots only."""

from __future__ import annotations

import json

from hpcu.gateway.gateway import Gateway, ModelCallPurpose
from hpcu.gateway.json_response import select_json_object
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.surface import SurfaceKind


class SemanticSlotFiller:
    """Ask the configured gateway only for explicitly unresolved goal fields.

    The returned object is intentionally tiny: no actions, coordinates,
    selectors, risk overrides or completion claims are accepted here. Provider
    presentation noise is handled only by the shared JSON framing boundary.
    """

    def __init__(self, gateway: Gateway, *, max_tokens: int = 256):
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self._gateway = gateway
        self._max_tokens = max_tokens

    def __call__(self, goal: GoalEnvelope) -> dict[str, str]:
        allowed = tuple(goal.ambiguity_slots)
        if not allowed:
            return {}
        prompt = json.dumps(
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
        response = self._gateway.call(
            prompt,
            system_prompt=(
                "Fill only unresolved user-intent slots. "
                "Do not plan actions or weaken local policy."
            ),
            max_tokens=self._max_tokens,
            purpose=ModelCallPurpose.INTENT_FILL,
        )
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
                raise ValueError(f"semantic slot {key!r} must be a non-empty string")
            values[key] = value.strip()
        return values
