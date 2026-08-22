"""TaskRuntime terminal evidence and transition contracts."""

import pytest

from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.observation.base import Observer
from hpcu.runtime_core import ControlLoop, TaskRuntime
from hpcu.schemas.action import Action, ActionOp, ActionTarget
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.evidence import EvidenceCondition, EvidenceContract, EvidenceKind
from hpcu.schemas.planning import GoalEnvelope, GoalIntent, PlanIR, PlanNode, StrategyPlan
from hpcu.schemas.scene import SceneDelta
from hpcu.schemas.ui_element import ElementSource, UIElement
from hpcu.verifier.verifier import Verifier


class _Injector(InputInjector):
    def __init__(self):
        super().__init__("session")
        self.calls: list[tuple[str, str]] = []

    async def semantic(self, element, action: str) -> ExecutionResult:
        self.calls.append((element.id, action))
        return ExecutionResult(success=True, mode="semantic")

    async def physical(self, point, action: str, text: str | None = None):
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(semantic_invoke=Capability.SUPPORTED)


class _Observer(Observer):
    def __init__(self, snapshots: list[tuple[UIElement, ...]]):
        super().__init__("session")
        self.snapshots = snapshots
        self.index = 0
        self.previous: dict[str, UIElement] = {}

    async def observe(self) -> SceneDelta:
        snapshot = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        current = {element.id: element for element in snapshot}
        base = self.index
        self.index += 1
        delta = SceneDelta(
            base_version=base,
            new_version=base + 1,
            added=tuple(
                value for key, value in current.items() if key not in self.previous
            ),
            removed=tuple(key for key in self.previous if key not in current),
            modified=tuple(
                value for key, value in current.items() if key in self.previous
            ),
        )
        self.previous = current
        return delta


def _element(element_id: str, text: str, role: str = "button") -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=1,
        role=role,
        name=text,
        text=text,
        bbox=BoundingBox(
            space=CoordinateSpace.SCREEN_PHYSICAL_PX,
            x=20,
            y=20,
            width=120,
            height=30,
        ),
        sources=(ElementSource(type="dom", confidence=1.0),),
    )


def _plan(node: PlanNode) -> PlanIR:
    goal = GoalEnvelope(
        id="goal",
        raw_instruction="Open and verify",
        intent=GoalIntent.SELECT,
    )
    strategy = StrategyPlan(
        id="strategy",
        goal_id=goal.id,
        execution_mode=goal.execution_mode,
        surface="browser",
    )
    return PlanIR(
        id="plan",
        goal=goal,
        strategy=strategy,
        entry_node_id=node.id,
        nodes=(node,),
    )


def _runtime(snapshots: list[tuple[UIElement, ...]]):
    injector = _Injector()
    loop = ControlLoop(
        observer=_Observer(snapshots),
        grounder=Grounder(confidence_threshold=0.5, min_margin=0.0),
        executor=Executor(injector),
        verifier=Verifier(),
    )
    return TaskRuntime(loop), injector


@pytest.mark.unit
async def test_terminal_click_requires_fresh_evidence():
    button = _element("open", "Open")
    done = _element("done", "Opened", role="status")
    evidence = EvidenceContract(
        all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="done"),)
    )
    node = PlanNode(
        id="open-node",
        action=Action(
            id="open-node",
            op=ActionOp.CLICK,
            target=ActionTarget(element_id="open"),
        ),
        evidence=evidence,
        terminal=True,
    )
    runtime, injector = _runtime([(button,), (button, done)])

    result = await runtime.run(_plan(node))

    assert result.success is True
    assert result.completed_node_ids == ("open-node",)
    assert result.records[0].verified is True
    assert result.records[0].scene_changed is True
    assert injector.calls == [("open", "click")]


@pytest.mark.unit
async def test_terminal_mutation_rejects_preexisting_unchanged_evidence():
    button = _element("open", "Open")
    done = _element("done", "Opened", role="status")
    evidence = EvidenceContract(
        all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="done"),)
    )
    node = PlanNode(
        id="open-node",
        action=Action(
            id="open-node",
            op=ActionOp.CLICK,
            target=ActionTarget(element_id="open"),
        ),
        evidence=evidence,
        terminal=True,
    )
    runtime, _injector = _runtime([(button, done), (button, done)])

    result = await runtime.run(_plan(node))

    assert result.success is False
    assert result.failure_code == "verification_failed"
    assert result.records[0].verified is False
    assert result.records[0].scene_changed is False


@pytest.mark.unit
async def test_read_node_can_accept_preexisting_evidence_without_input():
    result_element = _element("result", "42", role="text")
    evidence = EvidenceContract(
        all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="result"),)
    )
    node = PlanNode(
        id="read-node",
        action=Action(id="read-node", op=ActionOp.READ),
        evidence=evidence,
        terminal=True,
        allow_preexisting_success=True,
        require_scene_change=False,
    )
    runtime, injector = _runtime([(result_element,), (result_element,)])

    result = await runtime.run(_plan(node))

    assert result.success is True
    assert injector.calls == []


@pytest.mark.unit
async def test_nonterminal_node_without_contract_does_not_advance():
    result_element = _element("result", "42", role="text")
    terminal_evidence = EvidenceContract(
        all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="result"),)
    )
    first = PlanNode(
        id="first",
        action=Action(id="first", op=ActionOp.READ),
        success_next="terminal",
    )
    terminal = PlanNode(
        id="terminal",
        action=Action(id="terminal", op=ActionOp.READ),
        evidence=terminal_evidence,
        terminal=True,
        allow_preexisting_success=True,
        require_scene_change=False,
    )
    goal = GoalEnvelope(
        id="goal",
        raw_instruction="read",
        intent=GoalIntent.READ,
    )
    strategy = StrategyPlan(
        id="strategy",
        goal_id=goal.id,
        execution_mode=goal.execution_mode,
        surface="browser",
    )
    plan = PlanIR(
        id="plan",
        goal=goal,
        strategy=strategy,
        entry_node_id="first",
        nodes=(first, terminal),
    )
    runtime, _injector = _runtime([(result_element,), (result_element,)])

    result = await runtime.run(plan)

    assert result.success is False
    assert result.failed_node_id == "first"
    assert result.completed_node_ids == ()
