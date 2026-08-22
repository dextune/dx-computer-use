"""Strict MiniMax-M3 decision and evidence-boundary tests."""

import json

import pytest

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.decision import (
    DecisionAction,
    GoalState,
    parse_action_decision,
    parse_reanalysis,
    parse_situation_analysis,
)
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import ElementState, UIElement

pytestmark = pytest.mark.unit


def _scene() -> Scene:
    return Scene(
        version=4,
        elements={
            "button-a": UIElement(
                id="button-a",
                scene_version=4,
                role="button",
                text="Continue",
                state=ElementState(visible=True, enabled=True),
                bbox=BoundingBox(
                    space=CoordinateSpace.SCREEN_PHYSICAL_PX,
                    x=10,
                    y=10,
                    width=100,
                    height=30,
                ),
            )
        },
    )


def test_action_decision_requires_exact_json_schema_and_current_candidate():
    payload = {
        "schema_version": 1,
        "scene_version": 4,
        "action": "click",
        "target_id": "button-a",
        "value": None,
        "key": None,
        "goal_state": "in_progress",
        "confidence": 0.9,
        "reason_code": "goal_progress",
        "expected_postcondition": "new_screen",
    }
    decision = parse_action_decision(
        json.dumps(payload), scene=_scene(), candidate_ids=("button-a",)
    )
    assert decision.action is DecisionAction.CLICK
    assert decision.model == "MiniMax-M3"


def test_action_decision_rejects_aliases_and_free_text():
    payload = {
        "schema_version": 1,
        "scene_version": 4,
        "action": "click",
        "id": "button-a",
        "value": None,
        "key": None,
        "goal_state": "in_progress",
        "confidence": 0.9,
        "reason_code": "goal_progress",
        "expected_postcondition": "new_screen",
    }
    with pytest.raises(ValueError):
        parse_action_decision(
            json.dumps(payload), scene=_scene(), candidate_ids=("button-a",)
        )

    with pytest.raises(ValueError):
        parse_action_decision(
            "click button-a", scene=_scene(), candidate_ids=("button-a",)
        )


def test_action_decision_rejects_stale_scene_and_invented_target():
    payload = {
        "schema_version": 1,
        "scene_version": 3,
        "action": "click",
        "target_id": "button-a",
        "value": None,
        "key": None,
        "goal_state": "in_progress",
        "confidence": 0.9,
        "reason_code": "goal_progress",
        "expected_postcondition": "new_screen",
    }
    with pytest.raises(ValueError, match="stale"):
        parse_action_decision(
            json.dumps(payload), scene=_scene(), candidate_ids=("button-a",)
        )

    payload["scene_version"] = 4
    payload["target_id"] = "invented"
    with pytest.raises(ValueError):
        parse_action_decision(
            json.dumps(payload), scene=_scene(), candidate_ids=("button-a",)
        )


def test_situation_analysis_rejects_foreign_model():
    payload = {
        "schema_version": 1,
        "scene_version": 4,
        "situation_class": "ready",
        "goal_state": "in_progress",
        "challenge_kind": "none",
        "action_required": True,
        "confidence": 0.8,
        "reason_code": "screen_observed",
    }
    with pytest.raises(ValueError):
        parse_situation_analysis(
            json.dumps(payload), scene_version=4, model="other-model"
        )


def test_reanalysis_is_bound_to_minimax_and_scene_version():
    payload = {
        "schema_version": 1,
        "scene_version": 5,
        "goal_state": "success",
        "challenge_kind": "none",
        "confidence": 0.95,
        "reason_code": "post_action_evidence",
    }
    result = parse_reanalysis(
        json.dumps(payload), scene_version=5, provider="minimax"
    )
    assert result.goal_state is GoalState.SUCCESS
    assert result.model == "MiniMax-M3"
    assert result.provider == "minimax"


def test_pointer_decision_requires_physical_bbox():
    scene = Scene(
        version=1,
        elements={
            "no-box": UIElement(
                id="no-box", scene_version=1, role="button", text="Continue"
            )
        },
    )
    payload = {
        "schema_version": 1,
        "scene_version": 1,
        "action": "click",
        "target_id": "no-box",
        "value": None,
        "key": None,
        "goal_state": "in_progress",
        "confidence": 0.9,
        "reason_code": "goal_progress",
        "expected_postcondition": "new_screen",
    }
    with pytest.raises(ValueError, match="bounding box"):
        parse_action_decision(
            json.dumps(payload), scene=scene, candidate_ids=("no-box",)
        )
