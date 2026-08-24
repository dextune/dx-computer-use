"""CommandRuntime composition for OS-first, model-last browser bootstrap."""

import pytest

from hpcu.cases.planning import CasePlanCompiler
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.lifecycle.launcher import (
    ApplicationCandidate,
    ApplicationDiscoveryResult,
    ApplicationLaunchCapabilities,
    ApplicationLauncher,
    ApplicationLaunchResult,
)
from hpcu.runtime_core.control_loop import StepResult
from hpcu.runtime_core.product_runtime import CommandRequest, CommandRuntime
from hpcu.schemas.action import ActionOp
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.capability import Capability
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import GoalEntity, GoalEnvelope, IntentKind
from hpcu.schemas.plan import PlanningContext, TargetQuerySpec
from hpcu.schemas.scene import Scene
from hpcu.schemas.strategy import CapabilitySnapshot, StrategyPlan
from hpcu.schemas.surface import ExecutionMode, SurfaceKind

pytestmark = pytest.mark.integration


class _Gateway(Gateway):
    def __init__(self) -> None:
        self.calls: list[ModelCallPurpose] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return "fake-model"

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
        if purpose is not ModelCallPurpose.APPLICATION_SELECTION:
            raise AssertionError(f"unexpected semantic purpose: {purpose.value}")
        return GatewayResponse(
            content='{"candidate_id":"second.desktop"}',
            model=self.model_id,
            provider=self.provider_id,
            tokens_used=3,
            latency_ms=1,
        )


class _Launcher(ApplicationLauncher):
    def __init__(self) -> None:
        super().__init__("session")
        self.launch_calls = 0

    async def discover(self, application: str) -> ApplicationDiscoveryResult:
        return ApplicationDiscoveryResult(
            application=application,
            candidates=(
                ApplicationCandidate(id="first.desktop", label="First"),
                ApplicationCandidate(id="second.desktop", label="Second"),
            ),
        )

    async def launch(
        self,
        application: str,
        *,
        candidate_id: str | None = None,
        timeout_ms: int,
        poll_interval_ms: int,
    ) -> ApplicationLaunchResult:
        del application, candidate_id, timeout_ms, poll_interval_ms
        self.launch_calls += 1
        raise AssertionError("integration control loop must not perform OS side effect")

    def capabilities(self) -> ApplicationLaunchCapabilities:
        return ApplicationLaunchCapabilities(
            launch=Capability.SUPPORTED,
            discovery=Capability.SUPPORTED,
        )


class _ExecutorFacade:
    def __init__(self, launcher: ApplicationLauncher) -> None:
        self.application_launcher = launcher


class _ControlLoop:
    def __init__(self, launcher: ApplicationLauncher) -> None:
        self.executor = _ExecutorFacade(launcher)
        self.scene = Scene(version=0)

    def begin_task(self) -> None:
        return None

    async def step(self, query, action, *, verification_query=None) -> StepResult:
        del query, action, verification_query
        self.scene = Scene(version=1)
        return StepResult(
            success=False,
            scene=self.scene,
            failure_code=FailureCode.ACTION_UNSUPPORTED.value,
            skipped=True,
        )


class _GoalInterpreter:
    def interpret(self, instruction: str, **kwargs) -> GoalEnvelope:
        del kwargs
        return GoalEnvelope(
            raw_instruction=instruction,
            intent=IntentKind.SEARCH,
            terminal_state="search result visible",
            entities=(GoalEntity(kind="search_query", value="노트북"),),
            evidence_requirements=("query_echo", "result_candidate"),
            latency_budget_ms=10_000,
            model_call_budget=4,
        )


class _StrategyPlanner:
    def select(self, goal, capability) -> StrategyPlan:
        del goal, capability
        return StrategyPlan(
            id="screen",
            surface=SurfaceKind.BROWSER,
            execution_mode=ExecutionMode.SCREEN_STRICT,
            route="screen",
            expected_steps=4,
            evidence_strength=0.8,
            expected_reliability=0.8,
            expected_latency_ms=10.0,
        )


def _capability() -> CapabilitySnapshot:
    return CapabilitySnapshot(
        active_surface=SurfaceKind.BROWSER,
        execution_mode=ExecutionMode.SCREEN_STRICT,
        capture=Capability.SUPPORTED,
        structure=Capability.SUPPORTED,
        physical_input=Capability.SUPPORTED,
        application_launch=Capability.SUPPORTED,
    )


def _context() -> PlanningContext:
    return PlanningContext(
        target_queries={
            "surface": TargetQuerySpec(role="window"),
            "entry_ready": TargetQuerySpec(text="ready"),
            "search_field": TargetQuerySpec(text="search", role="textbox"),
            "search_result": TargetQuerySpec(text="노트북"),
        },
        values={"entry_url": "https://example.com"},
        allowed_ops=frozenset(
            {
                ActionOp.LAUNCH_APPLICATION,
                ActionOp.NAVIGATE,
                ActionOp.CLICK,
                ActionOp.REPLACE_TEXT,
                ActionOp.HOTKEY,
                ActionOp.ASSERT,
            }
        ),
    )


@pytest.mark.asyncio
async def test_command_runtime_binds_os_candidate_before_task_execution():
    launcher = _Launcher()
    gateway = _Gateway()
    control_loop = _ControlLoop(launcher)
    config = {
        "semantic": {
            "default_provider": "fake",
            "default_model": "fake-model",
            "request_limits": {
                "application_selection_max_tokens": 64,
                "application_selection_retry_attempts": 0,
                "retry_base_delay_ms": 0,
                "retry_max_delay_ms": 0,
            },
        },
        "performance": {"model_call_timeout_ms": 1000},
        "tier_budget": {
            "max_model_calls_per_task": 4,
            "max_model_tokens_per_task": 1024,
            "max_model_latency_ms_per_task": 10_000,
            "planning_call_ceiling": 4,
            "recovery_call_reserve": 0,
        },
    }
    runtime = CommandRuntime(
        lambda: control_loop,
        provider_gateway=gateway,
        goal_interpreter=_GoalInterpreter(),
        strategy_planner=_StrategyPlanner(),
        plan_compiler=CasePlanCompiler(),
        config=config,
    )

    result = await runtime.run(
        CommandRequest(
            instruction="노트북을 검색해줘",
            capability=_capability(),
            planning_context=_context(),
            task_budget=TaskBudgetSpec(
                max_model_calls=4,
                max_model_tokens=1024,
                max_model_latency_ms=10_000,
                planning_call_ceiling=4,
            ),
            max_steps=1,
        )
    )

    launch = result.plan.nodes["launch-case-browser"].action
    assert launch.target.locator == "second.desktop"
    assert result.model_calls == 1
    assert result.model_calls_by_purpose == (("application_selection", 1),)
    assert gateway.calls == [ModelCallPurpose.APPLICATION_SELECTION]
    assert launcher.launch_calls == 0
    assert result.task.success is False
