"""Regression tests for schema-directed model response framing."""

import json

import pytest

from hpcu.gateway.json_response import extract_json_objects, select_json_object
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.decision import (
    DecisionAction,
    GoalState,
    parse_action_decision,
    parse_reanalysis,
)
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import ElementState, UIElement

pytestmark = pytest.mark.unit


def _scene() -> Scene:
    return Scene(
        version=7,
        elements={
            "target": UIElement(
                id="target",
                scene_version=7,
                role="button",
                text="Continue",
                state=ElementState(visible=True, enabled=True),
                bbox=BoundingBox(
                    space=CoordinateSpace.SCREEN_PHYSICAL_PX,
                    x=10,
                    y=10,
                    width=120,
                    height=32,
                ),
            )
        },
    )


def _action_payload(target_id: str = "target") -> dict:
    return {
        "schema_version": 1,
        "scene_version": 7,
        "action": "click",
        "target_id": target_id,
        "value": None,
        "key": None,
        "goal_state": "in_progress",
        "confidence": 0.91,
        "reason_code": "goal_progress",
        "expected_postcondition": "new_screen",
    }


def test_extract_json_objects_handles_prose_fences_and_wrappers():
    content = (
        "analysis metadata: {\"thought\": \"brace } inside string\"}\n"
        "```json\n"
        + json.dumps({"response": _action_payload()})
        + "\n```"
    )
    objects = extract_json_objects(content)
    assert {"thought": "brace } inside string"} in objects
    assert _action_payload() in objects


def test_action_parser_selects_only_schema_matching_object():
    content = (
        '{"diagnostic":"draft"}\n'
        + json.dumps(_action_payload())
        + "\nprovider footer"
    )
    decision = parse_action_decision(
        content,
        scene=_scene(),
        candidate_ids=("target",),
    )
    assert decision.action is DecisionAction.CLICK
    assert decision.target_id == "target"


def test_reanalysis_parser_accepts_nested_provider_envelope():
    content = json.dumps(
        {
            "response": {
                "schema_version": 1,
                "scene_version": 8,
                "goal_state": "success",
                "challenge_kind": "none",
                "confidence": 0.96,
                "reason_code": "fresh_scene",
            }
        }
    )
    result = parse_reanalysis(content, scene_version=8, provider="minimax")
    assert result.goal_state is GoalState.SUCCESS


def test_conflicting_schema_matching_objects_fail_closed():
    first = _action_payload("target")
    second = _action_payload("target")
    second["confidence"] = 0.6
    content = json.dumps(first) + "\n" + json.dumps(second)
    with pytest.raises(ValueError, match="ambiguous action decision"):
        parse_action_decision(
            content,
            scene=_scene(),
            candidate_ids=("target",),
        )


def test_identical_duplicate_objects_are_transport_noise():
    payload = _action_payload()
    content = json.dumps(payload) + "\n" + json.dumps(payload)
    selected = select_json_object(
        content,
        required_keys=payload.keys(),
        schema_name="action decision",
    )
    assert selected == payload


def test_nonmatching_objects_report_observed_keys_without_raw_dump():
    with pytest.raises(ValueError, match="required=.*scene_version"):
        select_json_object(
            '{"diagnostic":"only"}',
            required_keys={"schema_version", "scene_version"},
            schema_name="reanalysis decision",
        )
