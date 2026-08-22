"""Unit tests for the Verifier evidence evaluation.

Pure scene checks only — no model, no platform, no timers.
"""

import pytest

from hpcu.schemas.action import Postcondition, PostconditionKind
from hpcu.schemas.evidence import EvidenceCondition, EvidenceContract, EvidenceKind
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import ElementSource, ElementState, UIElement
from hpcu.verifier.verifier import Verifier


def make_element(
    element_id: str,
    *,
    role: str = "button",
    name: str | None = None,
    text: str | None = None,
    visible: bool = True,
    enabled: bool = True,
    selected: bool = False,
    source_type: str = "dom",
) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=1,
        role=role,
        name=name,
        text=text,
        state=ElementState(
            visible=visible, enabled=enabled, selected=selected, occluded=False
        ),
        sources=(ElementSource(type=source_type),) if source_type else (),
    )


def make_scene(*elements: UIElement) -> Scene:
    return Scene(version=1, elements={el.id: el for el in elements})


@pytest.mark.unit
def test_verify_all_conditions_single_scene_ok():
    # Given a scene with a visible button whose text is the cart badge
    verifier = Verifier()
    scene = make_scene(make_element("btn_ok", name="확인", text="2"))
    contract = EvidenceContract(
        all=(
            EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="btn_ok"),
            EvidenceCondition(
                kind=EvidenceKind.BADGE_VALUE, target="btn_ok", value=2
            ),
        )
    )

    # When the contract is verified
    result = verifier.verify(contract, scene)

    # Then it is fully satisfied
    assert result is True


@pytest.mark.unit
def test_verify_all_condition_fails_when_any_breaks():
    # Given a contract whose second condition is violated
    verifier = Verifier()
    scene = make_scene(make_element("btn_ok", text="1"))
    contract = EvidenceContract(
        all=(
            EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="btn_ok"),
            EvidenceCondition(
                kind=EvidenceKind.BADGE_VALUE, target="btn_ok", value=5
            ),
        )
    )

    # When the contract is verified
    result = verifier.verify(contract, scene)

    # Then it is not satisfied (all must hold)
    assert result is False


@pytest.mark.unit
def test_verify_any_condition_satisfied():
    # Given a contract satisfied by any single branch (first fails, second holds)
    verifier = Verifier()
    scene = make_scene(make_element("btn_ok", text="3"))
    contract = EvidenceContract(
        any=(
            EvidenceCondition(kind=EvidenceKind.ELEMENT_ABSENT, target="btn_ok"),
            EvidenceCondition(
                kind=EvidenceKind.QUANTITY_AT_LEAST, target="btn_ok", value=3
            ),
        )
    )

    # When the contract is verified
    result = verifier.verify(contract, scene)

    # Then it is satisfied via the second branch
    assert result is True


@pytest.mark.unit
def test_verify_any_condition_all_fail():
    # Given a contract where no branch holds
    verifier = Verifier()
    scene = make_scene(make_element("btn_read", role="link", name="Read", text="2"))
    contract = EvidenceContract(
        any=(
            EvidenceCondition(kind=EvidenceKind.ELEMENT_ABSENT, target="btn_read"),
            EvidenceCondition(
                kind=EvidenceKind.QUANTITY_AT_LEAST, target="btn_read", value=9
            ),
        )
    )

    # When the contract is verified
    result = verifier.verify(contract, scene)

    # Then it is not satisfied
    assert result is False


@pytest.mark.unit
def test_verify_element_visible_matches_state():
    # Given a scene with a hidden element
    verifier = Verifier()
    scene = make_scene(
        make_element("btn_hidden", visible=False),
        make_element("btn_shown", visible=True),
    )

    # When ELEMENT_VISIBLE is checked against each
    # Then visible state drives the verdict
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="btn_hidden"),)
            ),
            scene,
        )
        is False
    )
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="btn_shown"),)
            ),
            scene,
        )
        is True
    )


@pytest.mark.unit
def test_verify_element_absent_is_true_only_when_missing():
    # Given a scene without the target element
    verifier = Verifier()
    scene = make_scene(make_element("present"))

    # When ELEMENT_ABSENT is checked
    # Then a missing id is absent and a present id is not
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_ABSENT, target="missing"),)
            ),
            scene,
        )
        is True
    )
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_ABSENT, target="present"),)
            ),
            scene,
        )
        is False
    )


@pytest.mark.unit
def test_verify_text_equals():
    # Given elements holding "123" and "로그인"
    verifier = Verifier()
    scene = make_scene(make_element("btn_title", text="로그인"))

    # When TEXT_EQUALS is checked with matching and non-matching values
    # Then only the exact text value passes
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.TEXT_EQUALS, target="btn_title", value=123),)
            ),
            make_scene(make_element("btn_title", text="123")),
        )
        is True
    )
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.TEXT_EQUALS, target="btn_title", value=456),)
            ),
            scene,
        )
        is False
    )


@pytest.mark.unit
def test_verify_state_matches_selected():
    # Given a selected and an unselected element
    verifier = Verifier()
    scene = make_scene(
        make_element("sel", selected=True),
        make_element("unsel", selected=False),
    )

    # When STATE_MATCHES interprets value truthiness as the selected state
    # Then selected matches value=1 and unselected matches value=0
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.STATE_MATCHES, target="sel", value=1),)
            ),
            scene,
        )
        is True
    )
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.STATE_MATCHES, target="unsel", value=0),)
            ),
            scene,
        )
        is True
    )
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.STATE_MATCHES, target="sel", value=0),)
            ),
            scene,
        )
        is False
    )


@pytest.mark.unit
def test_verify_cart_contains_by_product():
    # Given a cart element whose text mentions the product
    verifier = Verifier()
    scene = make_scene(
        make_element("cart", name="Cart", text="커피 2잔, 주문 합계 4000원")
    )

    # When CART_CONTAINS is checked for a present vs absent product
    # Then present product returns True, absent product returns False
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.CART_CONTAINS, product_match="커피"),)
            ),
            scene,
        )
        is True
    )
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.CART_CONTAINS, product_match="라떼"),)
            ),
            scene,
        )
        is False
    )


@pytest.mark.unit
def test_verify_quantity_at_least_parses_badge():
    # Given an element with a numeric badge in its text
    verifier = Verifier()
    scene = make_scene(make_element("badge", text="알림 7건"))

    # When QUANTITY_AT_LEAST is compared against the parsed integer
    # Then a lower or equal value holds and a higher one fails
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.QUANTITY_AT_LEAST, target="badge", value=7),)
            ),
            scene,
        )
        is True
    )
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.QUANTITY_AT_LEAST, target="badge", value=8),)
            ),
            scene,
        )
        is False
    )


@pytest.mark.unit
def test_verify_badge_value_matches():
    # Given a scene with a quantity badge of 3
    verifier = Verifier()
    scene = make_scene(make_element("qty", text="3"))

    # When BADGE_VALUE is compared
    # Then it holds at the exact value and fails above it
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.BADGE_VALUE, target="qty", value=3),)
            ),
            scene,
        )
        is True
    )
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.BADGE_VALUE, target="qty", value=4),)
            ),
            scene,
        )
        is False
    )


@pytest.mark.unit
def test_verify_notification_shown_requires_visible():
    # Given a hidden notification element
    verifier = Verifier()
    scene = make_scene(make_element("toast", visible=False, name="toast"))

    # When NOTIFICATION_SHOWN is checked
    # Then a hidden element does not count as shown
    assert (
        verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=EvidenceKind.NOTIFICATION_SHOWN, target="toast"),)
            ),
            scene,
        )
        is False
    )


@pytest.mark.unit
def test_verify_file_and_api_evidence_are_not_provable_from_scene():
    # Given a scene that carries no file/API state
    verifier = Verifier()
    scene = make_scene(make_element("x"))

    # When non-scene evidence kinds are evaluated
    # Then they return False (completion is not claimed without proof)
    for kind in (EvidenceKind.FILE_EXISTS, EvidenceKind.FILE_MTIME_AFTER,
                 EvidenceKind.API_RESPONSE_OK):
        result = verifier.verify(
            EvidenceContract(
                all=(EvidenceCondition(kind=kind, target="whatever"),)
            ),
            scene,
        )
        assert result is False


@pytest.mark.unit
def test_verify_postconditions_all_satisfied():
    # Given a scene satisfying every requested postcondition
    verifier = Verifier()
    scene = make_scene(
        make_element("dialog", role="window", selected=True, text="완료"),
        make_element("row", role="listitem"),
    )
    postconditions = (
        Postcondition(kind=PostconditionKind.ELEMENT_VISIBLE, target="dialog"),
        Postcondition(kind=PostconditionKind.ELEMENT_FOCUSED, target="dialog"),
        Postcondition(kind=PostconditionKind.TEXT_EQUALS, target="dialog", value="완료"),
        Postcondition(kind=PostconditionKind.ELEMENT_COUNT_AT_LEAST, target="listitem", value=1),
    )

    # When verify_postconditions is run
    result = verifier.verify_postconditions(postconditions, scene, last_action=None)

    # Then all postconditions hold
    assert result is True


@pytest.mark.unit
def test_verify_postcondition_absent_hides_element():
    # Given a postcondition that the element be absent, but it is present
    verifier = Verifier()
    scene = make_scene(make_element("modal", role="window"))
    postconditions = (
        Postcondition(kind=PostconditionKind.ELEMENT_ABSENT, target="modal"),
    )

    # When verify_postconditions is run
    result = verifier.verify_postconditions(postconditions, scene, None)

    # Then the present element fails the absent postcondition
    assert result is False


@pytest.mark.unit
def test_verify_postconditions_empty_is_vacuously_true():
    # Given no postconditions
    verifier = Verifier()
    scene = Scene(version=1)

    # When verify_postconditions is run with none
    result = verifier.verify_postconditions((), scene, None)

    # Then nothing must be proven and it returns True
    assert result is True


@pytest.mark.unit
def test_verify_postcondition_enabled():
    # Given an enabled and a disabled control
    verifier = Verifier()
    scene = make_scene(
        make_element("ok", enabled=True),
        make_element("cancel", enabled=False),
    )

    # When ELEMENT_ENABLED postconditions are checked
    # Then the enabled control passes and the disabled one fails
    assert verifier.verify_postconditions(
        (Postcondition(kind=PostconditionKind.ELEMENT_ENABLED, target="ok"),), scene, None
    ) is True
    assert verifier.verify_postconditions(
        (Postcondition(kind=PostconditionKind.ELEMENT_ENABLED, target="cancel"),), scene, None
    ) is False


@pytest.mark.unit
def test_verify_postcondition_file_api_not_provable():
    # Given a scene snapshot
    verifier = Verifier()
    scene = make_scene()

    # When a file/API postcondition is requested
    result = verifier.verify_postconditions(
        (Postcondition(kind=PostconditionKind.FILE_EXISTS, target="x"),), scene, None
    )

    # Then it is not provable from the scene and returns False
    assert result is False


@pytest.mark.unit
def test_verify_contract_with_no_conditions_is_rejected_at_construction():
    # Given that an empty contract cannot be built by the schema
    # When an empty EvidenceContract is attempted
    # Then construction raises because at least one branch is required
    with pytest.raises(ValueError):
        EvidenceContract()
