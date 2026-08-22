"""Unit tests for the Grounder — natural-language query resolution.

Pure scene matching; no model or platform is involved.
"""

import pytest

from hpcu.grounder.grounder import Grounder, GroundingCandidate, GroundingResult
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement


def make_element(
    element_id: str,
    *,
    role: str = "button",
    name: str | None = None,
    text: str | None = None,
) -> UIElement:
    return UIElement(id=element_id, scene_version=1, role=role, name=name, text=text)


def make_scene(*elements: UIElement) -> Scene:
    return Scene(version=1, elements={el.id: el for el in elements})


@pytest.mark.unit
def test_resolve_exact_text_match_wins():
    # Given a scene with two buttons, one an exact text match and one partial
    grounder = Grounder(confidence_threshold=0.5, min_margin=0.0)
    scene = make_scene(
        make_element("login", name="로그인", role="button"),
        make_element("logout", name="로그아웃", role="button"),
    )

    # When resolving an exact text/name query
    result = grounder.resolve({"text": "로그인"}, scene)

    # Then the exact match is grounded
    assert result.is_resolved is True
    assert result.element_id == "login"


@pytest.mark.unit
def test_resolve_exact_text_beats_partial_substring():
    # Given an exact "로그인" and a partial "로그아웃" both matching "로그인" prefix
    grounder = Grounder(confidence_threshold=0.4, min_margin=0.0)
    scene = make_scene(
        make_element("login", name="로그인 카드"),
        make_element("login_btn", name="로그인"),
    )

    # When resolving "로그인"
    result = grounder.resolve({"text": "로그인"}, scene)

    # Then the exact-value element ranks above the substring match
    assert result.element_id == "login_btn"
    assert result.candidates[0].element_id == "login_btn"


@pytest.mark.unit
def test_resolve_role_filter():
    # Given a scene with a button and an input
    grounder = Grounder(confidence_threshold=0.4, min_margin=0.0)
    scene = make_scene(
        make_element("btn", role="button", name="필터"),
        make_element("field", role="textbox", name="필터"),
    )

    # When resolving by role textbox
    result = grounder.resolve({"role": "textbox", "text": "필터"}, scene)

    # Then the textbox ranks first among matches
    assert result.element_id == "field"


@pytest.mark.unit
def test_resolve_no_candidates_returns_unresolved():
    # Given a scene with no element matching the query
    grounder = Grounder()
    scene = make_scene(make_element("login", name="로그인"))

    # When resolving an unrelated text
    result = grounder.resolve({"text": "결제"}, scene)

    # Then the result is unresolved with an empty candidate list
    assert result.is_resolved is False
    assert result.element_id is None


@pytest.mark.unit
def test_resolve_candidate_list_is_sorted_by_confidence():
    # Given a scene where several elements match with mixed strength
    grounder = Grounder(confidence_threshold=0.3, min_margin=0.0)
    scene = make_scene(
        make_element("a", name="고주파 수신부"),
        make_element("b", name="주파수"),
        make_element("c", name="고주파"),
    )

    # When resolving "주파수"
    result = grounder.resolve({"text": "주파수"}, scene)

    # Then returned candidates are ordered best-first
    confidences = [c.confidence for c in result.candidates]
    assert confidences == sorted(confidences, reverse=True)


@pytest.mark.unit
def test_score_candidates_sorts_descending():
    # Given an unordered candidate list
    grounder = Grounder()
    candidates = [
        GroundingCandidate(element_id="low", confidence=0.3),
        GroundingCandidate(element_id="high", confidence=0.9),
        GroundingCandidate(element_id="mid", confidence=0.5),
    ]

    # When score_candidates is applied
    scored = grounder.score_candidates(candidates)

    # Then the list is ordered by confidence descending
    assert [c.id for c in scored] == ["high", "mid", "low"]
    assert all(
        scored[i].confidence >= scored[i + 1].confidence
        for i in range(len(scored) - 1)
    )


@pytest.mark.unit
def test_score_candidates_empty_returns_empty():
    # Given no candidates
    grounder = Grounder()

    # When score_candidates receives an empty list
    scored = grounder.score_candidates([])

    # Then an empty list is returned
    assert scored == []


@pytest.mark.unit
def test_score_candidates_threshold_filters_low_confidence():
    # Given candidates both above and below a threshold
    grounder = Grounder()
    candidates = [
        GroundingCandidate(element_id="a", confidence=0.1),
        GroundingCandidate(element_id="b", confidence=0.8),
    ]

    # When filtered with a threshold
    scored = grounder.score_candidates(
        candidates, confidence_threshold=0.5
    )

    # Then low-confidence candidates are removed
    assert [c.id for c in scored] == ["b"]


@pytest.mark.unit
def test_score_candidates_query_overrides_threshold():
    # Given candidates and a query that raises the threshold
    grounder = Grounder()
    candidates = [
        GroundingCandidate(element_id="a", confidence=0.4),
        GroundingCandidate(element_id="b", confidence=0.9),
    ]

    # When the query supplies a higher confidence_threshold
    scored = grounder.score_candidates(candidates, query={"confidence_threshold": 0.6})

    # Then the weaker candidate is dropped by the query override
    assert [c.id for c in scored] == ["b"]


@pytest.mark.unit
def test_resolve_threshold_drops_all_candidates():
    # Given a strict threshold on the grounder and only weak substring matches
    grounder = Grounder(confidence_threshold=0.95)
    scene = make_scene(make_element("pay", name="결제 카드 최종 확인"))

    # When resolving a clause that yields only sub-threshold (0.8) matches
    result = grounder.resolve({"text": "카드"}, scene)

    # Then nothing clears the threshold and the result is unresolved
    assert result.is_resolved is False
    assert result.element_id is None


@pytest.mark.unit
def test_resolve_ambiguous_when_top_two_close():
    # Given two equally-strong candidates and a strict margin
    grounder = Grounder(min_margin=0.3)
    scene = make_scene(
        make_element("dup_a", name="결제"),
        make_element("dup_b", name="결제"),
    )

    # When resolving a query matching both equally
    result = grounder.resolve({"text": "결제"}, scene)

    # Then the top two are separated by less than the margin -> unresolved
    assert result.element_id is None
    assert len(result.candidates) == 2


@pytest.mark.unit
def test_resolve_case_insensitive():
    # Given an element captioned in mixed case
    grounder = Grounder(confidence_threshold=0.5, min_margin=0.0)
    scene = make_scene(make_element("sign", name="Sign Out"))

    # When resolving with a lowercase query
    result = grounder.resolve({"text": "sign out"}, scene)

    # Then matching is case-insensitive
    assert result.element_id == "sign"


@pytest.mark.unit
@pytest.mark.unit
def test_grounder_uses_injected_config_threshold():
    config = {"confidence": {"local_execute_threshold": 0.99, "local_margin_min": 0.0}}
    grounder = Grounder(config=config)
    scene = make_scene(make_element("login", name="로그인", role="button"))
    result = grounder.resolve({"text": "로그인"}, scene)
    assert result.element_id is None


def test_grounding_result_is_resolved_property():
    # Given an unresolved and a resolved result
    # When the is_resolved property is read
    # Then it reflects a grounded element id with positive confidence
    assert GroundingResult(element_id=None, confidence=0.0, candidates=()).is_resolved is False
    assert GroundingResult(element_id="x", confidence=0.9, candidates=()).is_resolved is True
