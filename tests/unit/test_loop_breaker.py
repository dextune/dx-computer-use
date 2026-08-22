"""Unit tests for LoopBreaker — loop detection and recovery."""

import pytest

from hpcu.recovery.loop_breaker import (
    LoopBreaker,
    LoopTriggerType,
    RecoveryAction,
)
from hpcu.schemas.action import (
    Action,
    ActionOp,
    ActionTarget,
    Postcondition,
    PostconditionKind,
)
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement


def _button_scene(version: int, element_ids: tuple[str, ...]) -> Scene:
    elements = {
        element_id: UIElement(
            id=element_id,
            scene_version=version,
            role="button",
            name=f"Button {element_id}",
            fingerprint=f"fp-{element_id}-v{version}",
        )
        for element_id in element_ids
    }
    return Scene(version=version, elements=elements)


def _click(action_id: str, element_id: str) -> Action:
    return Action(
        id=action_id,
        op=ActionOp.CLICK,
        target=ActionTarget(element_id=element_id),
    )


def _type(action_id: str, value: str) -> Action:
    return Action(
        id=action_id,
        op=ActionOp.TYPE,
        target=ActionTarget(element_id="input_1"),
        value=value,
        postconditions=(
            Postcondition(
                kind=PostconditionKind.TEXT_EQUALS,
                target="input_1",
                value=value,
            ),
        ),
    )


@pytest.mark.unit
def test_detect_repeated_action_flags_same_action_same_scene():
    """An unchanged scene with the same action repeated is a REPEATED_ACTION."""
    # Given — three identical clicks against an identical scene
    actions = [_click(f"a{i}", "btn_1") for i in range(3)]
    scenes = [_button_scene(1, ("btn_1",)) for _ in range(3)]

    # When
    detection = LoopBreaker().detect(actions, scenes)

    # Then
    assert detection is not None
    assert detection.trigger is LoopTriggerType.REPEATED_ACTION
    assert detection.sequence_span == 3
    assert len(detection.repeated_actions) == 3


@pytest.mark.unit
def test_detect_postcondition_failure_when_screen_stalled():
    """Different actions with postconditions on a frozen scene signal failure."""
    # Given — distinct actions, each declaring a postcondition, frozen screen
    actions = [_type(f"t{i}", f"value_{i}") for i in range(3)]
    scenes = [_button_scene(1, ("btn_1",)) for _ in range(3)]

    # When
    detection = LoopBreaker().detect(actions, scenes)

    # Then
    assert detection is not None
    assert detection.trigger is LoopTriggerType.POSTCONDITION_FAILURE


@pytest.mark.unit
def test_detect_a_b_a_pattern():
    """A->B->A->B oscillation between two distinct actions is a loop."""
    # Given — alternating clicks on two different targets
    actions = [
        _click("m0", "btn_a"),
        _click("m1", "btn_b"),
        _click("m2", "btn_a"),
        _click("m3", "btn_b"),
    ]
    scenes = [_button_scene(1, ("btn_a", "btn_b")) for _ in range(4)]

    # When
    detection = LoopBreaker().detect(actions, scenes)

    # Then
    assert detection is not None
    assert detection.trigger is LoopTriggerType.A_B_A_PATTERN


@pytest.mark.unit
def test_detect_popup_loop_when_overlay_reappears():
    """A dialog that disappears and reappears is a POPUP_LOOP."""
    # Given — the dialog is present, then dismissed, then present again
    dialog = UIElement(
        id="dlg", scene_version=1, role="dialog", name="Confirm", fingerprint="fp-dlg"
    )
    button = UIElement(
        id="btn_1", scene_version=1, role="button", name="Go", fingerprint="fp-btn"
    )
    scenes = [
        Scene(version=1, elements={"btn_1": button, "dlg": dialog}),
        Scene(version=1, elements={"btn_1": button}),
        Scene(version=1, elements={"btn_1": button, "dlg": dialog}),
        Scene(version=1, elements={"btn_1": button, "dlg": dialog}),
    ]
    actions = [_click(f"p{i}", "btn_1") for i in range(4)]

    # When
    detection = LoopBreaker().detect(actions, scenes)

    # Then
    assert detection is not None
    assert detection.trigger is LoopTriggerType.POPUP_LOOP


@pytest.mark.unit
def test_detect_scroll_no_change():
    """Scroll actions that never move the scene signal SCROLL_NO_CHANGE."""
    # Given — repeated scrolls against an unchanged scene
    actions = [
        Action(id=f"s{i}", op=ActionOp.SCROLL, dy=100.0) for i in range(3)
    ]
    scenes = [_button_scene(1, ("btn_1",)) for _ in range(3)]

    # When
    detection = LoopBreaker().detect(actions, scenes)

    # Then
    assert detection is not None
    assert detection.trigger is LoopTriggerType.SCROLL_NO_CHANGE


@pytest.mark.unit
def test_detect_no_loop_on_normal_progress():
    """A changing scene with distinct actions produces no detection."""
    # Given — scene advances across steps, actions are all different
    actions = [
        _click("n0", "btn_1"),
        _click("n1", "btn_2"),
        Action(id="n2", op=ActionOp.SCROLL, dy=-50.0),
    ]
    scenes = [
        _button_scene(1, ("btn_1",)),
        _button_scene(2, ("btn_1", "btn_2")),
        _button_scene(3, ("btn_1", "btn_2", "btn_3")),
    ]

    # When
    detection = LoopBreaker().detect(actions, scenes)

    # Then
    assert detection is None


@pytest.mark.unit
def test_detect_empty_history_returns_none():
    """Empty histories produce no detection."""
    # Given
    breaker = LoopBreaker()

    # When
    detection = breaker.detect([], [])

    # Then
    assert detection is None


@pytest.mark.unit
def test_detect_mismatched_lengths_raises():
    """Misaligned histories are rejected."""
    # Given
    breaker = LoopBreaker()

    # When / Then
    with pytest.raises(ValueError):
        breaker.detect([_click("a0", "btn_1")], [])


@pytest.mark.unit
def test_recover_escalates_and_halts_on_repeat():
    """Repeated recovery attempts escalate and finally halt."""
    # Given — a repeated-action detection and a fresh breaker
    breaker = LoopBreaker(escalate_threshold=2, halt_threshold=4)
    actions = [_click(f"a{i}", "btn_1") for i in range(3)]
    scenes = [_button_scene(1, ("btn_1",)) for _ in range(3)]
    detection = breaker.detect(actions, scenes)
    scene = scenes[0]

    # When — recover called repeatedly for the same detection
    first = breaker.recover(detection, scene, None)
    second = breaker.recover(detection, scene, None)
    third = breaker.recover(detection, scene, None)
    fourth = breaker.recover(detection, scene, None)

    # Then
    assert first is RecoveryAction.REEXPLORE
    assert second is RecoveryAction.ESCALATE
    assert third is RecoveryAction.ESCALATE
    assert fourth is RecoveryAction.HALT
    assert breaker.recovery_count == 4


@pytest.mark.unit
def test_recover_a_b_a_escalates_immediately():
    """A-B-A oscillation maps straight to ESCALATE on the first attempt."""
    # Given
    breaker = LoopBreaker()
    actions = [
        _click("m0", "btn_a"),
        _click("m1", "btn_b"),
        _click("m2", "btn_a"),
        _click("m3", "btn_b"),
    ]
    scenes = [_button_scene(1, ("btn_a", "btn_b")) for _ in range(4)]
    detection = breaker.detect(actions, scenes)

    # When
    response = breaker.recover(detection, scenes[0], None)

    # Then
    assert response is RecoveryAction.ESCALATE


@pytest.mark.unit
def test_reset_clears_recovery_state():
    """reset() clears attempt counters and keeps detection working."""
    # Given
    breaker = LoopBreaker(escalate_threshold=1, halt_threshold=2)
    actions = [_click(f"a{i}", "btn_1") for i in range(3)]
    scenes = [_button_scene(1, ("btn_1",)) for _ in range(3)]
    detection = breaker.detect(actions, scenes)
    breaker.recover(detection, scenes[0], None)

    # When
    breaker.reset()
    result = breaker.detect(actions, scenes)

    # Then
    assert breaker.recovery_count == 0
    assert result is not None
