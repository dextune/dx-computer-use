"""Unit tests for RiskEngine — action risk evaluation and approval."""

import pytest

from hpcu.policy.risk_engine import RiskEngine, RiskLevel
from hpcu.schemas.action import Action, ActionOp, ActionTarget
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement


def _element(
    element_id: str,
    name: str | None = None,
    text: str | None = None,
    semantic_tags: tuple[str, ...] = (),
) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=1,
        role="button",
        name=name,
        text=text,
        semantic_tags=semantic_tags,
    )


def _action(op: ActionOp, element_id: str | None = None) -> Action:
    target = ActionTarget(element_id=element_id) if element_id else ActionTarget()
    return Action(id="act", op=op, target=target)


@pytest.mark.unit
def test_assess_read_only_op_is_low_risk():
    """A read-only focus action is LOW risk."""
    # Given
    scene = Scene(version=1)
    engine = RiskEngine()

    # When
    level = engine.assess(_action(ActionOp.FOCUS_WINDOW), scene)

    # Then
    assert level is RiskLevel.LOW


@pytest.mark.unit
def test_assess_plain_click_is_medium_risk():
    """A plain click on a benign element is MEDIUM risk."""
    # Given
    scene = Scene(version=1, elements={"btn": _element("btn", name="Go")})
    engine = RiskEngine()

    # When
    level = engine.assess(_action(ActionOp.CLICK, "btn"), scene)

    # Then
    assert level is RiskLevel.MEDIUM


@pytest.mark.unit
def test_assess_call_tool_is_high_risk():
    """A generic tool call yields HIGH risk."""
    # Given
    scene = Scene(version=1)
    engine = RiskEngine()

    # When
    level = engine.assess(_action(ActionOp.CALL_TOOL), scene)

    # Then
    assert level is RiskLevel.HIGH


@pytest.mark.unit
def test_assess_destructive_tag_is_high_risk():
    """An element tagged destructive escalates a click to HIGH."""
    # Given
    scene = Scene(
        version=1,
        elements={"btn": _element("btn", name="Go", semantic_tags=("destructive",))},
    )
    engine = RiskEngine()

    # When
    level = engine.assess(_action(ActionOp.CLICK, "btn"), scene)

    # Then
    assert level is RiskLevel.HIGH


@pytest.mark.unit
def test_assess_payment_label_is_critical_risk():
    """An element labelled as a purchase is CRITICAL risk."""
    # Given
    scene = Scene(
        version=1,
        elements={"btn": _element("btn", name="Buy now")},
    )
    engine = RiskEngine()

    # When
    level = engine.assess(_action(ActionOp.CLICK, "btn"), scene)

    # Then
    assert level is RiskLevel.CRITICAL


@pytest.mark.unit
def test_assess_irreversible_text_is_high_risk():
    """An element labelled delete is HIGH risk."""
    # Given
    scene = Scene(
        version=1,
        elements={"btn": _element("btn", name="Delete account")},
    )
    engine = RiskEngine()

    # When
    level = engine.assess(_action(ActionOp.CLICK, "btn"), scene)

    # Then
    assert level is RiskLevel.HIGH


@pytest.mark.unit
def test_require_approval_for_high_and_critical():
    """HIGH and CRITICAL require approval; LOW and MEDIUM do not."""
    # Given
    engine = RiskEngine()

    # When / Then
    assert engine.require_approval(RiskLevel.CRITICAL) is True
    assert engine.require_approval(RiskLevel.HIGH) is True
    assert engine.require_approval(RiskLevel.MEDIUM) is False
    assert engine.require_approval(RiskLevel.LOW) is False
