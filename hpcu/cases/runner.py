"""Data-case adapter over the single command-to-plan product runtime."""

from __future__ import annotations

import time

from hpcu.cases.specs import CaseSpec
from hpcu.cases.stats import ActionRecord, CaseStats
from hpcu.runtime_core.product_runtime import (
    CommandRequest,
    CommandRuntime,
    UnresolvedGoalError,
)
from hpcu.runtime_core.task_runtime import TaskStatus
from hpcu.schemas.action import ActionOp
from hpcu.schemas.budget import TaskBudgetSpec
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.strategy import CapabilitySnapshot

_LOCAL_ONLY_OPS = frozenset(
    {
        ActionOp.ASSERT,
        ActionOp.READ,
        ActionOp.WAIT_UNTIL,
        ActionOp.CHECKPOINT,
    }
)
_PLAN_COMPILE_PURPOSE = "plan_compile"
_GROUNDING_PURPOSES = ("grounding", "action_decision")
_REANALYSIS_PURPOSES = ("post_action_reanalysis", "recovery_reanalysis")


class CaseRunner:
    """Translate CaseSpec to CommandRequest and translate terminal statistics.

    This adapter owns no gateway, parser, observer, executor, input injector,
    scene loop, or retry budget. All execution passes through CommandRuntime.
    """

    def __init__(
        self,
        runtime: CommandRuntime,
        capability: CapabilitySnapshot,
        *,
        monotonic=None,
    ) -> None:
        self._runtime = runtime
        self._capability = capability
        self._monotonic = monotonic if monotonic is not None else time.monotonic

    async def run_case(self, spec: CaseSpec) -> CaseStats:
        started = self._monotonic()
        stats = CaseStats(
            case_id=spec.id,
            outcome="running",
            max_attempts=spec.max_attempts,
        )
        request = CommandRequest(
            instruction=spec.goal,
            capability=self._capability,
            task_budget=TaskBudgetSpec(max_model_calls=spec.max_model_calls),
            max_steps=max(1, spec.max_attempts * 8),
            context_metadata={
                "case_id": spec.id,
                "start_url": spec.start_url,
            },
        )
        try:
            result = await self._runtime.run(request)
        except UnresolvedGoalError:
            self._fail_before_runtime(
                stats,
                FailureCode.DECISION_REQUIRED,
                "unresolved_goal",
            )
            stats.elapsed_ms = int((self._monotonic() - started) * 1000)
            return stats
        except ValueError as error:
            self._fail_before_runtime(
                stats,
                FailureCode.CAPABILITY_MISSING,
                type(error).__name__,
            )
            stats.elapsed_ms = int((self._monotonic() - started) * 1000)
            return stats

        task = result.task
        purpose_counts = dict(result.model_calls_by_purpose)
        stats.success = task.success
        stats.runner_success = task.success
        stats.verified_success = task.success
        stats.outcome = task.status.value
        stats.failure_code = task.failure_code or ""
        stats.failure = "" if task.success else stats.failure_code
        stats.handoff_required = task.status is TaskStatus.HUMAN_HANDOFF
        stats.final_scene_version = task.final_scene_version
        stats.final_frame_id = task.final_frame_id
        stats.attempts = task.steps
        stats.model_call_count = result.model_calls
        stats.model_tokens = result.model_tokens
        stats.compile_call_count = purpose_counts.get(_PLAN_COMPILE_PURPOSE, 0)
        stats.grounding_call_count = sum(
            purpose_counts.get(purpose, 0) for purpose in _GROUNDING_PURPOSES
        )
        stats.reanalysis_count = sum(
            purpose_counts.get(purpose, 0) for purpose in _REANALYSIS_PURPOSES
        )
        stats.provider = result.provider_id or stats.provider
        stats.model = result.model_id or stats.model
        stats.evidence_status = "satisfied" if task.success else "unsatisfied"
        stats.evidence_scene_version = task.final_scene_version
        stats.evidence_frame_id = task.final_frame_id
        stats.evidence_element_ids = list(task.terminal_evidence_ids)

        for node_id in task.completed_node_ids:
            node = result.plan.nodes[node_id]
            stats.actions.append(
                ActionRecord(
                    kind=node.action.op.value,
                    detail=node_id,
                    ok=True,
                )
            )
            if node.action.op not in _LOCAL_ONLY_OPS:
                stats.action_count += 1
        if not task.success and task.node_id in result.plan.nodes:
            node = result.plan.nodes[task.node_id]
            stats.actions.append(
                ActionRecord(
                    kind=node.action.op.value,
                    detail=task.failure_code or "failed",
                    ok=False,
                )
            )
        stats.elapsed_ms = int((self._monotonic() - started) * 1000)
        return stats

    @staticmethod
    def _fail_before_runtime(
        stats: CaseStats,
        code: FailureCode,
        detail: str,
    ) -> None:
        stats.success = False
        stats.runner_success = False
        stats.verified_success = False
        stats.outcome = TaskStatus.FAILED.value
        stats.failure_code = code.value
        stats.failure = detail
