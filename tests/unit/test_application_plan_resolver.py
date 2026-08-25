"""Model-last application candidate binding contracts."""

import pytest

from hpcu.executor.executor import Executor
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.lifecycle.launcher import (
    ApplicationCandidate,
    ApplicationDiscoveryResult,
    ApplicationLaunchCapabilities,
    ApplicationLauncher,
    ApplicationLaunchResult,
)
from hpcu.lifecycle.resolver import ApplicationPlanResolver
from hpcu.runtime_core.task_budget import BudgetedGateway, TaskBudgetLedger
from hpcu.schemas.action import Action, ActionOp, ActionTarget
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import ScreenPoint
from hpcu.schemas.goal import GoalEnvelope, IntentKind
from hpcu.schemas.plan import PlanIR, PlanNode
from hpcu.schemas.surface import SurfaceKind

pytestmark = pytest.mark.unit


class _Launcher(ApplicationLauncher):
    def __init__(
        self,
        candidates: tuple[ApplicationCandidate, ...],
        *,
        preferred_candidate_id: str | None = None,
    ) -> None:
        super().__init__("session")
        self._candidates = candidates
        self._preferred = preferred_candidate_id
        self.discover_calls = 0
        self.launch_calls = 0
        self.candidate_ids: list[str | None] = []

    async def discover(self, application: str) -> ApplicationDiscoveryResult:
        self.discover_calls += 1
        return ApplicationDiscoveryResult(
            application=application,
            candidates=self._candidates,
            preferred_candidate_id=self._preferred,
        )

    async def launch(
        self,
        application: str,
        *,
        candidate_id: str | None = None,
        timeout_ms: int,
        poll_interval_ms: int,
    ) -> ApplicationLaunchResult:
        del timeout_ms, poll_interval_ms
        self.launch_calls += 1
        self.candidate_ids.append(candidate_id)
        return ApplicationLaunchResult(success=True, application=application)

    def capabilities(self) -> ApplicationLaunchCapabilities:
        return ApplicationLaunchCapabilities(
            launch=Capability.SUPPORTED,
            discovery=Capability.SUPPORTED,
        )


class _NoInput(InputInjector):
    def __init__(self) -> None:
        super().__init__("session")

    async def semantic(self, element, action: str) -> ExecutionResult:
        del element, action
        return ExecutionResult(success=False, mode="semantic")

    async def physical(
        self,
        point: ScreenPoint,
        action: str,
        text: str | None = None,
    ) -> ExecutionResult:
        del point, action, text
        return ExecutionResult(success=False, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities()


class _Gateway(Gateway):
    def __init__(self, candidate_id: str):
        self.candidate_id = candidate_id
        self.calls: list[ModelCallPurpose] = []

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        del prompt, system_prompt, max_tokens
        self.calls.append(purpose)
        return GatewayResponse(
            content=f'{{"candidate_id":"{self.candidate_id}"}}',
            model="fake",
            tokens_used=1,
            latency_ms=1,
        )


def _candidate(candidate_id: str) -> ApplicationCandidate:
    stem = candidate_id.removesuffix(".desktop")
    return ApplicationCandidate(
        id=candidate_id,
        label=stem.title(),
        window_class=f"{stem.title()}Class",
        executable=f"{stem}-bin",
    )


def _plan() -> PlanIR:
    goal = GoalEnvelope(
        raw_instruction="브라우저를 열어줘",
        intent=IntentKind.NAVIGATE,
        terminal_state="browser visible",
        evidence_requirements=("surface_identity",),
    )
    node = PlanNode(
        id="launch",
        action=Action(
            id="launch",
            op=ActionOp.LAUNCH_APPLICATION,
            value="browser",
        ),
        surface=SurfaceKind.BROWSER,
    )
    return PlanIR(
        goal=goal,
        strategy_id="screen",
        entry_node_id=node.id,
        nodes={node.id: node},
        task_budget=TaskBudgetSpec(
            max_model_calls=4,
            max_model_tokens=1024,
            max_model_latency_ms=10_000,
            planning_call_ceiling=4,
        ),
    )


def _resolver() -> ApplicationPlanResolver:
    return ApplicationPlanResolver(
        config={
            "semantic": {
                "request_limits": {
                    "application_selection_max_tokens": 64,
                }
            },
            "performance": {"model_call_timeout_ms": 1000},
        }
    )


@pytest.mark.asyncio
async def test_preferred_os_candidate_binds_without_model_call():
    launcher = _Launcher(
        (_candidate("browser.desktop"),),
        preferred_candidate_id="browser.desktop",
    )
    gateway = _Gateway("unused.desktop")

    resolved = await _resolver().resolve(_plan(), launcher, gateway)
    action = resolved.nodes["launch"].action

    assert action.application_candidate_id == "browser.desktop"
    assert action.target.locator is None
    assert gateway.calls == []
    assert launcher.discover_calls == 1
    assert launcher.launch_calls == 0


@pytest.mark.asyncio
async def test_ambiguous_os_candidates_use_budgeted_semantic_selection_once():
    candidates = (_candidate("first.desktop"), _candidate("second.desktop"))
    launcher = _Launcher(candidates)
    ledger = TaskBudgetLedger(_plan().task_budget)
    inner = _Gateway("second.desktop")
    gateway = BudgetedGateway(inner, ledger)

    resolved = await _resolver().resolve(_plan(), launcher, gateway)
    action = resolved.nodes["launch"].action

    assert action.application_candidate_id == "second.desktop"
    assert action.target.locator is None
    assert inner.calls == [ModelCallPurpose.APPLICATION_SELECTION]
    assert ledger.calls_by_purpose == {ModelCallPurpose.APPLICATION_SELECTION: 1}
    assert launcher.launch_calls == 0


@pytest.mark.asyncio
async def test_model_cannot_invent_application_candidate():
    candidates = (_candidate("first.desktop"), _candidate("second.desktop"))
    launcher = _Launcher(candidates)
    gateway = _Gateway("invented.desktop")

    resolved = await _resolver().resolve(_plan(), launcher, gateway)
    action = resolved.nodes["launch"].action

    assert action.application_candidate_id is None
    assert action.target.locator is None
    assert gateway.calls == [ModelCallPurpose.APPLICATION_SELECTION]
    assert launcher.launch_calls == 0


@pytest.mark.asyncio
async def test_ambiguous_candidates_without_gateway_remain_unbound():
    candidates = (_candidate("first.desktop"), _candidate("second.desktop"))
    launcher = _Launcher(candidates)

    resolved = await _resolver().resolve(_plan(), launcher, None)
    action = resolved.nodes["launch"].action

    assert action.application_candidate_id is None
    assert action.target.locator is None
    assert launcher.launch_calls == 0


@pytest.mark.asyncio
async def test_executor_uses_candidate_field_instead_of_ui_locator():
    launcher = _Launcher((_candidate("browser.desktop"),))
    executor = Executor(_NoInput(), application_launcher=launcher)
    action = Action(
        id="launch",
        op=ActionOp.LAUNCH_APPLICATION,
        value="browser",
        target=ActionTarget(locator="legacy-ui-locator"),
        application_candidate_id="browser.desktop",
    )

    result = await executor.execute(executor.prepare(action, None))

    assert result.success is True
    assert launcher.candidate_ids == ["browser.desktop"]
