"""Single product entry from user command to verified terminal result."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from hpcu.gateway.gateway import Gateway, RetryableGateway
from hpcu.planning.goal_interpreter import GoalInterpreter
from hpcu.planning.plan_compiler import PlanCompiler
from hpcu.planning.semantic_interrupt import SemanticSlotFiller
from hpcu.planning.strategy_planner import StrategyPlanner
from hpcu.runtime_config import load_runtime_config
from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.runtime_core.task_budget import BudgetedGateway, TaskBudgetLedger
from hpcu.runtime_core.task_runtime import Repairer, TaskRunResult, TaskRuntime
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.goal import GoalEnvelope
from hpcu.schemas.plan import PlanIR, PlanningContext
from hpcu.schemas.strategy import CapabilitySnapshot, StrategyPlan


class UnresolvedGoalError(ValueError):
    """Raised before execution when required command slots remain unresolved."""


@dataclass(frozen=True)
class CommandRequest:
    instruction: str
    capability: CapabilitySnapshot
    planning_context: PlanningContext | None = None
    task_budget: TaskBudgetSpec = TaskBudgetSpec()
    max_steps: int | None = None
    context_metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise ValueError("command instruction must be non-empty")
        if self.max_steps is not None and self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        metadata = dict(self.context_metadata)
        if any(not key.strip() for key in metadata):
            raise ValueError("context metadata keys must be non-empty")
        object.__setattr__(self, "context_metadata", MappingProxyType(metadata))


@dataclass(frozen=True)
class CommandRunResult:
    goal: GoalEnvelope
    strategy: StrategyPlan
    plan: PlanIR
    task: TaskRunResult
    model_calls: int
    model_tokens: int
    model_latency_ms: int
    model_calls_by_purpose: tuple[tuple[str, int], ...] = ()
    provider_id: str = ""
    model_id: str = ""

    @property
    def success(self) -> bool:
        return self.task.success


RepairerFactory = Callable[[Gateway, TaskBudgetLedger], Repairer]
PlanningContextProvider = Callable[
    [
        GoalEnvelope,
        StrategyPlan,
        CommandRequest,
        Gateway | None,
        TaskBudgetLedger,
    ],
    PlanningContext,
]


class CommandRuntime:
    """Build one ledger, one semantic boundary, and one TaskRuntime per task."""

    def __init__(
        self,
        control_loop_factory: Callable[[], ControlLoop],
        *,
        provider_gateway: Gateway | None = None,
        planning_context_provider: PlanningContextProvider | None = None,
        goal_interpreter: GoalInterpreter | None = None,
        strategy_planner: StrategyPlanner | None = None,
        plan_compiler: PlanCompiler | None = None,
        local_repairer: Repairer | None = None,
        semantic_replanner_factory: RepairerFactory | None = None,
        config: dict | None = None,
    ) -> None:
        self._control_loop_factory = control_loop_factory
        self._provider_gateway = provider_gateway
        self._planning_context_provider = planning_context_provider
        self._goal_interpreter = goal_interpreter or GoalInterpreter()
        self._strategy_planner = strategy_planner or StrategyPlanner()
        self._plan_compiler = plan_compiler or PlanCompiler()
        self._local_repairer = local_repairer
        self._semantic_replanner_factory = semantic_replanner_factory
        self._config = config if config is not None else load_runtime_config()

    async def run(self, request: CommandRequest) -> CommandRunResult:
        ledger = TaskBudgetLedger(request.task_budget)
        gateway = self._semantic_gateway(ledger)
        semantic_fill = SemanticSlotFiller(gateway) if gateway is not None else None

        goal = self._goal_interpreter.interpret(
            request.instruction,
            semantic_fill=semantic_fill,
            latency_budget_ms=request.task_budget.max_model_latency_ms,
            model_call_budget=request.task_budget.max_model_calls,
        )
        if goal.ambiguity_slots:
            raise UnresolvedGoalError(
                "command has unresolved slots: "
                f"{', '.join(goal.ambiguity_slots)}"
            )

        strategy = self._strategy_planner.select(goal, request.capability)
        context = request.planning_context
        if context is None:
            if self._planning_context_provider is None:
                raise ValueError("planning context or context provider is required")
            context = self._planning_context_provider(
                goal,
                strategy,
                request,
                gateway,
                ledger,
            )
        plan = self._plan_compiler.compile(
            goal,
            strategy,
            context,
            request.task_budget,
        )
        semantic_replanner = (
            self._semantic_replanner_factory(gateway, ledger)
            if gateway is not None and self._semantic_replanner_factory is not None
            else None
        )
        runtime = TaskRuntime(
            self._control_loop_factory(),
            ledger,
            local_repairer=self._local_repairer,
            semantic_replanner=semantic_replanner,
            config=self._config,
        )
        task = await runtime.run(plan, max_steps=request.max_steps)
        calls_by_purpose = tuple(
            sorted(
                (purpose.value, count)
                for purpose, count in ledger.calls_by_purpose.items()
            )
        )
        return CommandRunResult(
            goal=goal,
            strategy=strategy,
            plan=plan,
            task=task,
            model_calls=ledger.model_calls,
            model_tokens=ledger.tokens_used,
            model_latency_ms=ledger.latency_ms,
            model_calls_by_purpose=calls_by_purpose,
            provider_id=gateway.provider_id if gateway is not None else "",
            model_id=gateway.model_id if gateway is not None else "",
        )

    def _semantic_gateway(
        self,
        ledger: TaskBudgetLedger,
    ) -> Gateway | None:
        if self._provider_gateway is None:
            return None
        limits = self._config.get("semantic", {}).get("request_limits", {})
        transport = (
            self._provider_gateway
            if isinstance(self._provider_gateway, RetryableGateway)
            else RetryableGateway(
                self._provider_gateway,
                max_retries=int(limits.get("action_decision_retry_attempts", 2)),
                base_delay_ms=int(limits.get("retry_base_delay_ms", 500)),
                max_delay_ms=int(limits.get("retry_max_delay_ms", 8000)),
            )
        )
        return BudgetedGateway(transport, ledger)
