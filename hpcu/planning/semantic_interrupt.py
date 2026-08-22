"""Provider-neutral semantic filling for unresolved GoalEnvelope slots only."""

from __future__ import annotations

import json

from hpcu.gateway.gateway import Gateway, ModelCallPurpose
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.surface import SurfaceKind


class SemanticSlotFiller:
    """Ask the configured gateway only for explicitly unresolved goal fields.

    The returned object is intentionally tiny: no actions, coordinates,
    selectors, risk overrides or completion claims are accepted here.
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
                    "Return exactly one JSON object. Use only unresolved_slots as "
                    "keys and string values. Do not return actions, selectors, "
                    "coordinates, risk or success claims."
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
        try:
            parsed = json.loads(response.content)
        except json.JSONDecodeError as exc:
            raise ValueError("semantic slot fill must be one JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValueError("semantic slot fill must be a JSON object")
        unknown = set(parsed) - set(allowed)
        if unknown:
            raise ValueError(
                "semantic slot fill returned protected fields: "
                f"{sorted(unknown)!r}"
            )
        values: dict[str, str] = {}
        for key, value in parsed.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"semantic slot {key!r} must be a non-empty string")
            values[str(key)] = value.strip()
        return values
