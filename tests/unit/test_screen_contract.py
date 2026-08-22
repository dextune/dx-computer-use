"""Common browser/terminal physical-screen contract tests."""

import pytest

from hpcu.cases.screen_contract import (
    MAX_CASE_ATTEMPTS,
    CaseOutcome,
    ChallengePolicy,
    EntryContract,
    EntryKind,
    ScreenCaseSpec,
    SurfaceKind,
)

pytestmark = pytest.mark.unit


def test_browser_and_terminal_share_screen_case_shape():
    browser = ScreenCaseSpec(
        id="browser-screen",
        surface=SurfaceKind.BROWSER,
        goal="observe a page",
        entry=EntryContract(EntryKind.URL, "https://example.invalid"),
    )
    terminal = ScreenCaseSpec(
        id="terminal-screen",
        surface=SurfaceKind.TERMINAL,
        goal="run a visible command",
        entry=EntryContract(EntryKind.TERMINAL_COMMAND, "printf ready"),
    )
    assert browser.entry.kind is EntryKind.URL
    assert terminal.entry.kind is EntryKind.TERMINAL_COMMAND
    assert browser.challenge_policy.outcome is CaseOutcome.HUMAN_HANDOFF


def test_existing_screen_has_no_hidden_setup_command():
    case = ScreenCaseSpec(
        id="fixture",
        surface=SurfaceKind.CHALLENGE,
        goal="stop at the access control screen",
        entry=EntryContract(EntryKind.EXISTING_SCREEN),
    )
    assert case.entry.value == ""


def test_challenge_policy_rejects_bypass_flags():
    with pytest.raises(ValueError):
        ChallengePolicy(allow_credential_input=True)
    with pytest.raises(ValueError):
        ChallengePolicy(allow_refresh_or_alternate_route=True)


def test_entry_contract_rejects_blank_terminal_command():
    with pytest.raises(ValueError):
        EntryContract(EntryKind.TERMINAL_COMMAND, " ")


def test_case_attempt_budget_is_bounded_to_five():
    allowed = ScreenCaseSpec(
        id="bounded",
        surface=SurfaceKind.TERMINAL,
        goal="bounded attempts",
        entry=EntryContract(EntryKind.EXISTING_SCREEN),
        max_attempts=MAX_CASE_ATTEMPTS,
    )
    assert allowed.max_attempts == MAX_CASE_ATTEMPTS
    with pytest.raises(ValueError, match="max_attempts"):
        ScreenCaseSpec(
            id="too-many",
            surface=SurfaceKind.TERMINAL,
            goal="too many attempts",
            entry=EntryContract(EntryKind.EXISTING_SCREEN),
            max_attempts=MAX_CASE_ATTEMPTS + 1,
        )


def test_surface_rejects_wrong_entry_kind():
    with pytest.raises(ValueError):
        ScreenCaseSpec(
            id="wrong",
            surface=SurfaceKind.TERMINAL,
            goal="no browser entry",
            entry=EntryContract(EntryKind.URL, "https://example.invalid"),
        )
