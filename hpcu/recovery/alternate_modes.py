"""Alternate interaction-mode selection after a failure.

When an interaction mode fails the runtime should not repeat it blindly.
AlternateModeSelector walks an ordered list of interaction modes, choosing
the next one to try and escalating faster as the failure count grows.
"""

# The ordered interaction modes, from least to most expensive.
MODE_ORDER = ("semantic", "click", "hotkey", "scroll_and_retry", "reexplore")

# After this many failures, jump further forward instead of one step at a time.
ESCALATE_FAILURE_COUNT = 3


class AlternateModeSelector:
    """Pick the next interaction mode given the current mode and failures."""

    def __init__(
        self,
        mode_order: tuple[str, ...] = MODE_ORDER,
        escalate_failure_count: int = ESCALATE_FAILURE_COUNT,
    ) -> None:
        self._mode_order = mode_order
        self._escalate_failure_count = escalate_failure_count

    @property
    def modes(self) -> tuple[str, ...]:
        """The ordered mode list this selector is allowed to choose from."""
        return self._mode_order

    def next_mode(self, current_mode: str, failure_count: int) -> str:
        """Return the next interaction mode to try after a failure.

        Unknown modes fall back to the first (cheapest) mode.  Once the last
        mode is reached it is kept — the recovery path is effectively exhausted.
        """
        if current_mode not in self._mode_order:
            return self._mode_order[0]
        current_index = self._mode_order.index(current_mode)
        step = 2 if failure_count >= self._escalate_failure_count else 1
        next_index = min(current_index + step, len(self._mode_order) - 1)
        return self._mode_order[next_index]
