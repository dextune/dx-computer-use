"""Unit tests for AlternateModeSelector — mode selection."""

import pytest

from hpcu.recovery.alternate_modes import (
    MODE_ORDER,
    AlternateModeSelector,
)


@pytest.mark.unit
def test_modes_expose_expected_order():
    """The selector exposes the canonical ordered mode list."""
    # Given
    selector = AlternateModeSelector()

    # When
    modes = selector.modes

    # Then
    assert modes == ("semantic", "click", "hotkey", "scroll_and_retry", "reexplore")


@pytest.mark.unit
def test_next_mode_advances_one_step_on_first_failure():
    """A single failure advances from semantic to click."""
    # Given
    selector = AlternateModeSelector()

    # When
    result = selector.next_mode("semantic", failure_count=1)

    # Then
    assert result == "click"


@pytest.mark.unit
def test_next_mode_escalates_faster_after_many_failures():
    """High failure counts jump two modes forward at once."""
    # Given
    selector = AlternateModeSelector(escalate_failure_count=3)

    # When
    result = selector.next_mode("click", failure_count=5)

    # Then — two steps forward: click -> scroll_and_retry
    assert result == "scroll_and_retry"


@pytest.mark.unit
def test_next_mode_clamps_at_last_mode():
    """The last mode is retained once reached (recovery exhausted)."""
    # Given
    selector = AlternateModeSelector()

    # When
    result = selector.next_mode("scroll_and_retry", failure_count=1)

    # Then
    assert result == "reexplore"
    assert selector.next_mode("reexplore", failure_count=1) == "reexplore"


@pytest.mark.unit
def test_next_mode_unknown_mode_returns_first():
    """An unknown current mode falls back to the first/cheapest mode."""
    # Given
    selector = AlternateModeSelector()

    # When
    result = selector.next_mode("bogus", failure_count=1)

    # Then
    assert result == MODE_ORDER[0]


@pytest.mark.unit
def test_next_mode_walks_whole_sequence():
    """Repeated single failures walk the full ordered sequence."""
    # Given
    selector = AlternateModeSelector(escalate_failure_count=999)

    # When
    sequence = []
    current = "semantic"
    for failure in range(1, 5):
        current = selector.next_mode(current, failure)
        sequence.append(current)

    # Then
    assert sequence == ["click", "hotkey", "scroll_and_retry", "reexplore"]
