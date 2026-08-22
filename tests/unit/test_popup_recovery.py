"""Unit tests for PopupRecovery — detection and dismissal."""

import pytest

from hpcu.recovery.popup_recovery import PopupRecovery, is_popup_element
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import ElementRelations, UIElement


def _element(
    element_id: str,
    role: str = "button",
    name: str | None = None,
    text: str | None = None,
    relations: ElementRelations | None = None,
) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=1,
        role=role,
        name=name,
        text=text,
        relations=relations or ElementRelations(),
    )


@pytest.mark.unit
def test_detect_finds_role_based_popup():
    """A dialog role element is detected as a popup."""
    # Given
    scene = Scene(
        version=1,
        elements={
            "dlg": _element("dlg", role="dialog", name="Confirm"),
            "btn": _element("btn", name="OK"),
        },
    )
    recovery = PopupRecovery()

    # When
    popups = recovery.detect(scene)

    # Then
    assert [element.id for element in popups] == ["dlg"]


@pytest.mark.unit
def test_detect_finds_overlay_relation():
    """An element that overlays others is detected as a popup."""
    # Given — a close-button genuinely overlaying the content
    scene = Scene(
        version=1,
        elements={
            "overlay": _element(
                "overlay",
                role="div",
                relations=ElementRelations(overlays=("content",)),
            ),
            "content": _element("content", role="div"),
        },
    )
    recovery = PopupRecovery()

    # When
    popups = recovery.detect(scene)

    # Then
    assert [element.id for element in popups] == ["overlay"]


@pytest.mark.unit
def test_detect_orders_top_most_first():
    """The element covering the most is ordered first."""
    # Given
    scene = Scene(
        version=1,
        elements={
            "small": _element(
                "small", relations=ElementRelations(overlays=("a",))
            ),
            "big": _element(
                "big", relations=ElementRelations(overlays=("a", "b", "c"))
            ),
        },
    )
    recovery = PopupRecovery()

    # When
    popups = recovery.detect(scene)

    # Then
    assert popups[0].id == "big"


@pytest.mark.unit
def test_detect_empty_scene_returns_empty_list():
    """An empty scene has no popups."""
    # Given
    scene = Scene(version=1, elements={})
    recovery = PopupRecovery()

    # When
    popups = recovery.detect(scene)

    # Then
    assert popups == []


@pytest.mark.unit
def test_is_popup_element_rejects_plain_button():
    """A plain button is not a popup element."""
    # Given
    button = _element("btn", name="Go")

    # When
    result = is_popup_element(button)

    # Then
    assert result is False


@pytest.mark.unit
def test_dismiss_returns_false_when_no_popup():
    """Dismissing a scene with no popups is a no-op returning False."""
    # Given
    scene = Scene(version=1, elements={"btn": _element("btn", name="Go")})
    recovery = PopupRecovery()

    # When
    dismissed = recovery.dismiss(scene, object())

    # Then
    assert dismissed is False


class _FakeExecutor:
    def __init__(self) -> None:
        self.dismissed: list[str] = []
        self.clicks: list[str] = []

    def dismiss(self, element_id: str) -> bool:
        self.dismissed.append(element_id)
        return True

    def click(self, element_id: str) -> bool:
        self.clicks.append(element_id)
        return True


@pytest.mark.unit
def test_dismiss_dispatches_executor_dismiss():
    """Dismiss calls executor.dismiss with the popup element id."""
    # Given
    scene = Scene(
        version=1,
        elements={
            "dlg": _element("dlg", role="dialog", name="Confirm"),
            "close": _element("close", name="Close"),
        },
    )
    executor = _FakeExecutor()
    recovery = PopupRecovery()

    # When
    dismissed = recovery.dismiss(scene, executor)

    # Then
    assert dismissed is True
    assert executor.dismissed == ["close"]


class _ClickOnlyExecutor:
    def __init__(self) -> None:
        self.clicks: list[str] = []

    def click(self, element_id: str) -> bool:
        self.clicks.append(element_id)
        return True


@pytest.mark.unit
def test_dismiss_falls_back_to_click():
    """An executor without dismiss() is dismissed via click()."""
    # Given
    scene = Scene(
        version=1,
        elements={"dlg": _element("dlg", role="dialog", name="Confirm")},
    )
    executor = _ClickOnlyExecutor()
    recovery = PopupRecovery()

    # When
    dismissed = recovery.dismiss(scene, executor)

    # Then
    assert dismissed is True
    assert executor.clicks == ["dlg"]


class _NoMethodExecutor:
    pass


@pytest.mark.unit
def test_dismiss_lacking_method_raises_value_error():
    """An executor with neither dismiss() nor click() raises ValueError."""
    # Given
    scene = Scene(
        version=1,
        elements={"dlg": _element("dlg", role="dialog", name="Confirm")},
    )
    recovery = PopupRecovery()

    # When / Then
    with pytest.raises(ValueError):
        recovery.dismiss(scene, _NoMethodExecutor())
