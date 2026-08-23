"""Regression tests for executable candidate visibility contracts."""

import pytest

from hpcu.router.candidate_scoring import TargetQuery, score_candidates
from hpcu.schemas.ui_element import ElementSource, ElementState, UIElement

pytestmark = pytest.mark.unit


def _candidate(element_id: str, *, visible: bool = True, occluded: bool = False):
    return UIElement(
        id=element_id,
        scene_version=1,
        role="button",
        text="Login",
        state=ElementState(visible=visible, enabled=True, occluded=occluded),
        sources=(ElementSource(type="dom", confidence=1.0),),
    )


def test_hidden_exact_dom_match_is_not_a_candidate():
    scored = score_candidates(
        [_candidate("hidden", visible=False)],
        TargetQuery(text="Login", role="button"),
    )

    assert scored == []


def test_occluded_exact_dom_match_is_not_a_candidate():
    scored = score_candidates(
        [_candidate("covered", occluded=True)],
        TargetQuery(text="Login", role="button"),
    )

    assert scored == []


def test_visible_candidate_remains_rankable():
    scored = score_candidates(
        [_candidate("visible")],
        TargetQuery(text="Login", role="button"),
    )

    assert [candidate.element_id for candidate in scored] == ["visible"]
