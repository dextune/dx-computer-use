"""ControlLoop wiring — collaborators injected, no platform imports."""

import pytest

from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder
from hpcu.observation.base import Observer
from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.schemas.scene import SceneDelta
from hpcu.trace.recorder import TraceRecorder
from hpcu.verifier.verifier import Verifier


class StubInjector:
    async def semantic(self, element, action):
        return None

    async def physical(self, point, action, text=None):
        return None

    def capabilities(self):
        return None


class StubObserver(Observer):
    def __init__(self) -> None:
        super().__init__("test-session")
        self._observed = 0

    async def observe(self) -> SceneDelta:
        self._observed += 1
        return SceneDelta(base_version=self._observed - 1, new_version=self._observed)


@pytest.mark.unit
def test_constructor_injects_and_exposes_dependencies():
    observer = StubObserver()
    trace = TraceRecorder()
    loop = ControlLoop(
        observer, Grounder(), Executor(StubInjector()), Verifier(), recorder=trace
    )
    assert loop.observer is observer
    assert isinstance(loop.grounder, Grounder)
    assert isinstance(loop.executor, Executor)
    assert isinstance(loop.verifier, Verifier)
    assert loop.recorder is trace


@pytest.mark.unit
def test_recorder_is_optional():
    observer = StubObserver()
    loop = ControlLoop(observer, Grounder(), Executor(StubInjector()), Verifier())
    assert loop.recorder is None