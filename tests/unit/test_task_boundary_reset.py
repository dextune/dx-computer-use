"""Task boundaries must not leak loop state into the next command."""

import pytest

from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.scene import Scene

pytestmark = pytest.mark.unit


class _Breaker:
    window_size = 4

    def __init__(self) -> None:
        self.reset_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1


def test_begin_task_clears_histories_and_loop_attempt_counters():
    breaker = _Breaker()
    loop = ControlLoop(
        observer=object(),
        grounder=object(),
        executor=object(),
        verifier=object(),
        loop_breaker=breaker,
    )
    loop._action_history.append(Action(id="old", op=ActionOp.CLICK))
    loop._scene_history.append(Scene(version=1))

    loop.begin_task()

    assert loop.action_history == ()
    assert loop.scene_history == ()
    assert breaker.reset_calls == 1
