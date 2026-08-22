"""PlanIR runtime — node/edge state machine over the single ControlLoop."""

from dataclasses import dataclass
from enum import Enum

from hpcu.runtime_core.control_loop import ControlLoop, StepResult
from hpcu.runtime_core.task_budget import TaskBudgetLedger
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.plan import PlanIR, PlanNode


class TaskStatus(str, Enum):
    RUNNING = "running"
    VERIFIED_SUCCESS = "verified_success"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskRunResult:
    status: TaskStatus
    node_id: str
    steps: int
    last_step: StepResult | None = None
    failure_code: str | None = None

    @property
    def success(self) -> bool:
        return self.status is TaskStatus.VERIFIED_SUCCESS


class TaskRuntime:
    """Own plan progression; ControlLoop remains the only action cycle."""

    def __init__(
        self,
        control_loop: ControlLoop,
        budget_ledger: TaskBudgetLedger | None = None,
    ):
        self.control_loop = control_loop
        self._budget_ledger = budget_ledger

    @property
    def budget_ledger(self) -> TaskBudgetLedger | None:
        return self._budget_ledger

    async def run(self, plan: PlanIR, *, max_steps: int | None = None) -> TaskRunResult:
        # One immutable budget spec -> one mutable ledger for the whole task.
        # A ledger already used during intent/plan semantic interrupts may be
        # injected and continues here; it is never reset on node transitions.
        if self._budget_ledger is None:
            self._budget_ledger = TaskBudgetLedger(plan.task_budget)
        elif self._budget_ledger.spec != plan.task_budget:
            raise ValueError("task budget ledger does not match PlanIR budget")
        limit = max_steps if max_steps is not None else max(1, len(plan.nodes) * 4)
        if limit <= 0:
            raise ValueError("max_steps must be positive")
        node_id = plan.entry_node_id
        last: StepResult | None = None
        for step_number in range(1, limit + 1):
            node = plan.nodes[node_id]
            last = await self._run_node(node)
            if last.success:
                if node.success_edge is None:
                    return TaskRunResult(
                        status=TaskStatus.VERIFIED_SUCCESS,
                        node_id=node_id,
                        steps=step_number,
                        last_step=last,
                    )
                node_id = node.success_edge
                continue
            failure = last.failure_code or FailureCode.UNKNOWN.value
            recovery = node.failure_edges.get(failure) or node.failure_edges.get("*")
            if recovery is None:
                return TaskRunResult(
                    status=TaskStatus.FAILED,
                    node_id=node_id,
                    steps=step_number,
                    last_step=last,
                    failure_code=failure,
                )
            node_id = recovery
        return TaskRunResult(
            status=TaskStatus.FAILED,
            node_id=node_id,
            steps=limit,
            last_step=last,
            failure_code=FailureCode.LOOP_DETECTED.value,
        )

    async def _run_node(self, node: PlanNode) -> StepResult:
        query = node.target_query.as_dict() if node.target_query is not None else None
        verification_query = (
            node.verification_query.as_dict()
            if node.verification_query is not None
            else None
        )
        return await self.control_loop.step(
            query, node.action, verification_query=verification_query
        )
