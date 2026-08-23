"""Regression tests for target-requirement decisions in ControlLoop."""

import pytest

from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.schemas.action import Action, ActionOp, ActionTarget

pytestmark = pytest.mark.unit


def test_query_requires_grounding_even_for_local_verification_ops():
    query = {"text": "result"}

    for op in (ActionOp.ASSERT, ActionOp.READ, ActionOp.CHECKPOINT):
        action = Action(id=op.value, op=op)
        assert ControlLoop._requires_target(query, action) is True


def test_focus_window_query_requires_grounding():
    action = Action(id="focus", op=ActionOp.FOCUS_WINDOW)

    assert ControlLoop._requires_target({"role": "window"}, action) is True


def test_targetless_focus_window_without_query_remains_valid():
    action = Action(id="focus", op=ActionOp.FOCUS_WINDOW)

    assert ControlLoop._requires_target(None, action) is False


def test_explicit_element_binding_requires_target_without_query():
    action = Action(
        id="click",
        op=ActionOp.CLICK,
        target=ActionTarget(element_id="button"),
    )

    assert ControlLoop._requires_target(None, action) is True


@pytest.mark.parametrize(
    "op",
    (
        ActionOp.INVOKE,
        ActionOp.NAVIGATE,
        ActionOp.CLICK,
        ActionOp.DOUBLE_CLICK,
        ActionOp.RIGHT_CLICK,
        ActionOp.TYPE,
        ActionOp.REPLACE_TEXT,
        ActionOp.SELECT,
        ActionOp.TOGGLE,
        ActionOp.DRAG,
    ),
)
def test_intrinsically_targeted_actions_fail_closed_without_binding(op):
    action = Action(id=op.value, op=op, value="value")

    assert ControlLoop._requires_target(None, action) is True
