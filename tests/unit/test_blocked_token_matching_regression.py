"""Regression tests for multilingual blocked-token matching."""

import pytest

from hpcu.runtime_core.task_runtime import _blocked_token_matches, _scene_text_blob
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import ElementState, UIElement

pytestmark = pytest.mark.unit


def test_ascii_blocked_token_keeps_word_boundaries():
    assert _blocked_token_matches("bot detected", "bot") is True
    assert _blocked_token_matches("about this page", "bot") is False
    assert _blocked_token_matches("robot check", "bot") is False


def test_ascii_matching_is_case_insensitive_and_whitespace_normalized():
    assert _blocked_token_matches("CAPTCHA   detected", " captcha ") is True


def test_korean_blocked_token_matches_attached_particle():
    assert _blocked_token_matches("로그인이 필요합니다", "로그인") is True


def test_korean_blocked_token_does_not_match_inside_larger_leading_word():
    assert _blocked_token_matches("소셜로그인 옵션", "로그인") is False


def test_scene_blob_keeps_visible_text_and_accessible_name():
    scene = Scene(
        version=1,
        elements={
            "gate": UIElement(
                id="gate",
                scene_version=1,
                text="Continue",
                name="Login required",
            )
        },
    )

    blob = _scene_text_blob(scene)

    assert "Continue" in blob
    assert "Login required" in blob
    assert _blocked_token_matches(blob, "login") is True


def test_scene_blob_ignores_hidden_access_control_text():
    scene = Scene(
        version=1,
        elements={
            "hidden-gate": UIElement(
                id="hidden-gate",
                scene_version=1,
                text="captcha verification required",
                state=ElementState(visible=False),
            ),
            "visible-content": UIElement(
                id="visible-content",
                scene_version=1,
                text="Search results",
            ),
        },
    )

    blob = _scene_text_blob(scene)

    assert "captcha" not in blob
    assert "Search results" in blob
