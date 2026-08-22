"""Strict, provider-neutral semantic decisions bound to one observed Scene.

CPU perception supplies neutral scene facts. A configured semantic provider
returns a strict decision, and this module validates only the decision schema,
scene binding, and physical target safety. Provider identity is metadata from
the gateway and is checked at the gateway boundary, never hardcoded here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from hpcu.gateway.gateway import SemanticIdentity
from hpcu.runtime_config import configured_semantic_identity
from hpcu.schemas.scene import Scene

DECISION_SCHEMA_VERSION = 1


class GoalState(str, Enum):
    UNKNOWN = "unknown"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    BLOCKED = "blocked"
    HUMAN_HANDOFF = "human_handoff"
    FAILED = "failed"


class DecisionAction(str, Enum):
    NONE = "none"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE = "type"
    KEY = "key"
    SCROLL = "scroll"
    FOCUS_WINDOW = "focus_window"
    HALT = "halt"


_ALLOWED_CHALLENGE_KINDS = frozenset(
    {"none", "captcha", "login_required", "security_check", "unknown_access_control"}
)
_ACTIONS_REQUIRING_TARGET = frozenset(
    {
        DecisionAction.CLICK.value,
        DecisionAction.DOUBLE_CLICK.value,
        DecisionAction.RIGHT_CLICK.value,
    }
)
_EXACT_SITUATION_KEYS = frozenset(
    {
        "schema_version",
        "scene_version",
        "situation_class",
        "goal_state",
        "challenge_kind",
        "action_required",
        "confidence",
        "reason_code",
    }
)
_EXACT_ACTION_KEYS = frozenset(
    {
        "schema_version",
        "scene_version",
        "action",
        "target_id",
        "value",
        "key",
        "goal_state",
        "confidence",
        "reason_code",
        "expected_postcondition",
    }
)
_EXACT_REANALYSIS_KEYS = frozenset(
    {
        "schema_version",
        "scene_version",
        "goal_state",
        "challenge_kind",
        "confidence",
        "reason_code",
    }
)


def _default_model_id() -> str:
    """Use the configured deployment only as response metadata default."""
    return configured_semantic_identity().model_id


@dataclass(frozen=True)
class SituationAnalysis:
    """A semantic situation classification for exactly one scene version."""

    schema_version: int
    scene_version: int
    situation_class: str
    goal_state: GoalState
    challenge_kind: str
    action_required: bool
    confidence: float
    reason_code: str
    model: str = ""
    provider: str = ""
    identity: SemanticIdentity | None = None

    def __post_init__(self) -> None:
        _validate_common(self.schema_version, self.scene_version, self.confidence)
        if not self.situation_class.strip():
            raise ValueError("situation_class must be non-empty")
        if self.challenge_kind not in _ALLOWED_CHALLENGE_KINDS:
            raise ValueError(f"unsupported challenge_kind: {self.challenge_kind!r}")
        if not self.reason_code.strip():
            raise ValueError("reason_code must be non-empty")


@dataclass(frozen=True)
class ActionDecision:
    """A semantic action bound to the current Scene."""

    schema_version: int
    scene_version: int
    action: DecisionAction
    target_id: str | None
    goal_state: GoalState
    confidence: float
    reason_code: str
    expected_postcondition: str
    value: str | None = None
    key: str | None = None
    model: str = ""
    provider: str = ""
    identity: SemanticIdentity | None = None

    def __post_init__(self) -> None:
        _validate_common(self.schema_version, self.scene_version, self.confidence)
        if not self.reason_code.strip():
            raise ValueError("reason_code must be non-empty")
        if not self.expected_postcondition.strip():
            raise ValueError("expected_postcondition must be non-empty")
        if self.action.value in _ACTIONS_REQUIRING_TARGET and not self.target_id:
            raise ValueError("target_id is required for pointer actions")
        if self.action is DecisionAction.TYPE and not self.value:
            raise ValueError("value is required for type")
        if self.action is DecisionAction.KEY and not self.key:
            raise ValueError("key is required for key")
        if self.action in (DecisionAction.NONE, DecisionAction.HALT) and self.target_id:
            raise ValueError("halt/no-op decisions cannot carry target_id")

    def validate_against(self, scene: Scene, candidate_ids: Iterable[str] = ()) -> None:
        """Reject stale, missing, hidden, or model-invented targets."""
        if self.scene_version != scene.version:
            raise ValueError("stale action decision")
        if self.action.value not in _ACTIONS_REQUIRING_TARGET:
            return
        allowed = set(candidate_ids)
        if self.target_id not in allowed:
            raise ValueError("target is outside the neutral candidate set")
        element = scene.get(self.target_id or "")
        if element is None:
            raise ValueError("target is not present in the current scene")
        if element.scene_version != scene.version:
            raise ValueError("target element is stale")
        if (
            not element.state.visible
            or not element.state.enabled
            or element.state.occluded
        ):
            raise ValueError("target is not safely actionable")
        if element.bbox is None:
            raise ValueError("target has no physical bounding box")


@dataclass(frozen=True)
class ReanalysisDecision:
    """Post-action/recovery classification bound to a fresh scene."""

    schema_version: int
    scene_version: int
    goal_state: GoalState
    challenge_kind: str
    confidence: float
    reason_code: str
    model: str = ""
    provider: str = ""
    identity: SemanticIdentity | None = None

    def __post_init__(self) -> None:
        _validate_common(self.schema_version, self.scene_version, self.confidence)
        if self.challenge_kind not in _ALLOWED_CHALLENGE_KINDS:
            raise ValueError(f"unsupported challenge_kind: {self.challenge_kind!r}")
        if not self.reason_code.strip():
            raise ValueError("reason_code must be non-empty")


def parse_situation_analysis(
    content: str,
    *,
    scene_version: int,
    model: str | None = None,
    provider: str = "",
    expected_model: str | None = None,
) -> SituationAnalysis:
    """Parse strict JSON; optionally validate identity supplied by the caller."""
    payload = _load_exact_object(content, _EXACT_SITUATION_KEYS)
    selected_model = model or _default_model_id()
    _require_expected_model(selected_model, expected_model)
    parsed_scene_version = _int(payload, "scene_version")
    if parsed_scene_version != scene_version:
        raise ValueError("stale situation analysis")
    return SituationAnalysis(
        schema_version=_int(payload, "schema_version"),
        scene_version=parsed_scene_version,
        situation_class=_string(payload, "situation_class"),
        goal_state=GoalState(_string(payload, "goal_state")),
        challenge_kind=_string(payload, "challenge_kind"),
        action_required=_bool(payload, "action_required"),
        confidence=_confidence(payload),
        reason_code=_string(payload, "reason_code"),
        model=selected_model,
        provider=provider or configured_semantic_identity().provider_id,
        identity=SemanticIdentity(
            provider or configured_semantic_identity().provider_id,
            selected_model,
        ),
    )


def parse_action_decision(
    content: str,
    *,
    scene: Scene,
    candidate_ids: Iterable[str],
    model: str | None = None,
    provider: str = "",
    expected_model: str | None = None,
) -> ActionDecision:
    """Parse and bind an action response to the current neutral scene."""
    payload = _load_exact_object(content, _EXACT_ACTION_KEYS)
    selected_model = model or _default_model_id()
    _require_expected_model(selected_model, expected_model)
    decision = ActionDecision(
        schema_version=_int(payload, "schema_version"),
        scene_version=_int(payload, "scene_version"),
        action=DecisionAction(_string(payload, "action")),
        target_id=_optional_string(payload, "target_id"),
        value=_optional_string(payload, "value"),
        key=_optional_string(payload, "key"),
        goal_state=GoalState(_string(payload, "goal_state")),
        confidence=_confidence(payload),
        reason_code=_string(payload, "reason_code"),
        expected_postcondition=_string(payload, "expected_postcondition"),
        model=selected_model,
        provider=provider or configured_semantic_identity().provider_id,
        identity=SemanticIdentity(
            provider or configured_semantic_identity().provider_id,
            selected_model,
        ),
    )
    decision.validate_against(scene, candidate_ids)
    return decision


def parse_reanalysis(
    content: str,
    *,
    scene_version: int,
    model: str | None = None,
    provider: str = "",
    expected_model: str | None = None,
) -> ReanalysisDecision:
    """Parse a strict post-action/recovery response."""
    payload = _load_exact_object(content, _EXACT_REANALYSIS_KEYS)
    selected_model = model or _default_model_id()
    _require_expected_model(selected_model, expected_model)
    parsed_scene_version = _int(payload, "scene_version")
    if parsed_scene_version != scene_version:
        raise ValueError("stale reanalysis decision")
    return ReanalysisDecision(
        schema_version=_int(payload, "schema_version"),
        scene_version=parsed_scene_version,
        goal_state=GoalState(_string(payload, "goal_state")),
        challenge_kind=_string(payload, "challenge_kind"),
        confidence=_confidence(payload),
        reason_code=_string(payload, "reason_code"),
        model=selected_model,
        provider=provider or configured_semantic_identity().provider_id,
        identity=SemanticIdentity(
            provider or configured_semantic_identity().provider_id,
            selected_model,
        ),
    )


def _validate_common(
    schema_version: int, scene_version: int, confidence: float
) -> None:
    if schema_version != DECISION_SCHEMA_VERSION:
        raise ValueError(f"unsupported decision schema: {schema_version!r}")
    if scene_version < 0:
        raise ValueError("scene_version must be non-negative")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")


def _extract_json_object(content: str) -> Any:
    """Extract the first JSON object from a provider response.

    Tries in order:
    1. Whole content as JSON.
    2. First ```json ``` fenced block.
    3. First ``` ``` fenced block.
    4. Bracket-counted extraction from first { to matching }.
    """
    # 1. direct parse
    try:
        return json.loads(content)
    except (TypeError, json.JSONDecodeError):
        pass

    # 2. markdown json fence
    match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 3. any markdown fence
    match = re.search(r"```\s*(\{.*?\})\s*```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 4. bracket-counted extraction
    start = content.find("{")
    if start == -1:
        raise ValueError("no JSON object found in response")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(content)):
        ch = content[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(content[start : i + 1])

    raise ValueError("unbalanced braces in response")


def _load_exact_object(content: str, keys: frozenset[str]) -> dict[str, Any]:
    preview = content[:200].replace("\n", " ")
    try:
        payload = _extract_json_object(content)
    except ValueError as error:
        raise ValueError(
            f"{error} [raw_preview: {preview}]"
        ) from error
    if not isinstance(payload, dict) or keys - set(payload):
        raise ValueError(
            f"response does not match the strict decision schema"
            f" [raw_preview: {preview}]"
        )
    return payload


def _require_expected_model(model: str, expected_model: str | None) -> None:
    if not model.strip():
        raise ValueError("semantic response model identity is required")
    configured_model = expected_model or configured_semantic_identity().model_id
    if model != configured_model:
        raise ValueError(
            "semantic response model "
            f"{model!r} does not match configured {configured_model!r}"
        )


def _int(payload: dict[str, Any], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _bool(payload: dict[str, Any], key: str) -> bool:
    value = payload[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload[key]
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _confidence(payload: dict[str, Any]) -> float:
    value = payload["confidence"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence must be numeric")
    return float(value)
