"""Unit tests for hpcu.router.candidate_scoring."""


import pytest

from hpcu.router.candidate_scoring import (
    ScoredCandidate,
    TargetQuery,
    score_candidates,
)
from hpcu.schemas.ui_element import ElementSource, ElementState, UIElement


def _element(
    element_id: str,
    *,
    text: str = "",
    role: str = "unknown",
    source_type: str = "dom",
    source_confidence: float = 1.0,
    visible: bool = True,
    enabled: bool = True,
    occluded: bool = False,
) -> UIElement:
    sources = (ElementSource(type=source_type, confidence=source_confidence),)
    state = ElementState(visible=visible, enabled=enabled, occluded=occluded)
    return UIElement(
        id=element_id,
        scene_version=1,
        role=role,
        text=text,
        state=state,
        sources=sources,
    )


@pytest.mark.unit
def test_score_candidates_sorts_by_score_descending():
    """Given mixed candidates, scoring returns best-matched first."""
    # Given
    candidates = [
        _element("btn_help", text="Help", role="button"),
        _element("btn_login", text="Login", role="button"),
        _element("input_email", text="Email", role="textbox"),
    ]
    # When
    scored = score_candidates(candidates, TargetQuery(text="Login", role="button"))
    # Then
    assert len(scored) == 3
    assert scored[0].element_id == "btn_login"
    assert all(
        scored[i].score >= scored[i + 1].score for i in range(len(scored) - 1)
    )


@pytest.mark.unit
def test_score_candidates_strong_match_confident():
    """Given an exact text+role match from DOM, the candidate is confident."""
    # Given
    candidates = [
        _element("btn_login", text="Login", role="button", source_type="dom"),
    ]
    # When
    scored = score_candidates(candidates, TargetQuery(text="Login", role="button"))
    # Then
    assert scored[0].confidence > 0.8
    assert scored[0].confidence <= 1.0
    assert "text=" in scored[0].reason


@pytest.mark.unit
def test_score_candidates_role_mismatch_lowers_score():
    """Given a matching text but wrong role, the score is lower than a full match."""
    # Given
    wrong_role = _element("lbl_login", text="Login", role="text")
    right_role = _element("btn_login", text="Login", role="button")
    # When
    scored = score_candidates(
        [wrong_role, right_role], TargetQuery(text="Login", role="button")
    )
    # Then
    by_id = {s.element_id: s for s in scored}
    assert by_id["btn_login"].score > by_id["lbl_login"].score


@pytest.mark.unit
def test_score_candidates_empty_list_returns_empty():
    """Given no candidates, scoring returns an empty list."""
    # Given
    candidates = []
    # When
    scored = score_candidates(candidates, TargetQuery(text="Login"))
    # Then
    assert scored == []


@pytest.mark.unit
def test_score_candidates_min_score_filters_low_scorers():
    """Given a min_score, lower-scoring candidates are excluded."""
    # Given
    candidates = [
        _element("btn_login", text="Login", role="button"),
        _element("input_email", text="Email", role="textbox"),
    ]
    # When
    scored = score_candidates(
        candidates,
        TargetQuery(text="Login", role="button"),
        min_score=0.6,
    )
    # Then
    assert [s.element_id for s in scored] == ["btn_login"]


@pytest.mark.unit
def test_score_candidates_dom_beats_ocr_source():
    """Given identical elements, a DOM source is more reliable than OCR."""
    # Given
    dom = _element("btn_a", text="Submit", role="button", source_type="dom")
    ocr = _element("btn_b", text="Submit", role="button", source_type="ocr")
    # When
    scored = score_candidates([dom, ocr], TargetQuery(text="Submit", role="button"))
    # Then
    by_id = {s.element_id: s for s in scored}
    assert by_id["btn_a"].score > by_id["btn_b"].score


@pytest.mark.unit
def test_score_candidates_promotes_plain_string_query():
    """Given a plain string target_query, it is treated as text-only."""
    # Given
    candidates = [
        _element("btn_login", text="Login", role="button"),
    ]
    # When
    scored = score_candidates(candidates, "Login")
    # Then
    assert scored[0].element_id == "btn_login"
    assert isinstance(scored[0], ScoredCandidate)


@pytest.mark.unit
def test_score_candidates_completely_unrelated_is_not_confident():
    """Given a totally unrelated element, confidence stays low."""
    # Given
    candidates = [
        _element("img_logo", text="Company Logo", role="image", source_type="ocr"),
    ]
    # When
    scored = score_candidates(candidates, TargetQuery(text="Confirm Purchase"))
    # Then
    assert scored[0].confidence < 0.5
