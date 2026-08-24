"""Data-case adapter over the single command-to-plan product runtime."""

from __future__ import annotations

import hashlib
import time
from dataclasses import replace

from hpcu.cases.specs import CaseSpec
from hpcu.cases.stats import ActionRecord, CaseStats, EvidenceRecord
from hpcu.compiler.targeting_compiler import TargetingCompilationError
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
_APPLICATION_SELECTION_PURPOSE = "application_selection"
_GROUNDING_PURPOSES = ("grounding", "action_decision")
_REANALYSIS_PURPOSES = ("post_action_reanalysis", "recovery_reanalysis")


class CaseRunner:
    """Translate CaseSpec to CommandRequest and terminal runtime statistics.

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
        self._task_budget_template = runtime.task_budget_template
        self._monotonic = monotonic if monotonic is not None else time.monotonic

    async def run_case(self, spec: CaseSpec) -> CaseStats:
        started = self._monotonic()
        stats = CaseStats(
            case_id=spec.id,
            outcome="running",
            max_attempts=spec.max_attempts,
        )
        task_budget = self._case_task_budget(spec)
        request = CommandRequest(
            instruction=spec.goal,
            capability=self._capability,
            task_budget=task_budget,
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
        except TargetingCompilationError as error:
            self._fail_before_runtime(
                stats,
                self._targeting_failure_code(error),
                error.diagnostic,
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
        stats.application_selection_call_count = purpose_counts.get(
            _APPLICATION_SELECTION_PURPOSE,
            0,
        )
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
        stats.evidence_bindings = [
            self._evidence_record(binding) for binding in task.evidence_bindings
        ]
        stats.evidence_tokens = [
            binding.requirement for binding in task.evidence_bindings
        ]
        if task.evidence_bindings:
            latest = max(
                task.evidence_bindings,
                key=lambda binding: binding.scene_version,
            )
            stats.evidence_scene_version = latest.scene_version
            stats.evidence_frame_id = latest.frame_id

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

    def _case_task_budget(self, spec: CaseSpec) -> TaskBudgetSpec:
        """Keep deployment resource limits while applying the case call limit.

        Cases with their own low call ceiling cannot reserve a subset of that
        same small budget for recovery without preventing plan compilation.
        """
        template = self._task_budget_template
        max_calls = spec.max_model_calls
        return replace(
            template,
            max_model_calls=max_calls,
            planning_call_ceiling=min(
                template.planning_call_ceiling
                if template.planning_call_ceiling is not None
                else max_calls,
                max_calls,
            ),
            recovery_call_reserve=0,
        )

    @staticmethod
    def _targeting_failure_code(
        error: TargetingCompilationError,
    ) -> FailureCode:
        try:
            return FailureCode(error.failure_code)
        except ValueError:
            return FailureCode.MODEL_FAILED

    @staticmethod
    def _evidence_record(binding) -> EvidenceRecord:
        observed = binding.observed_text
        return EvidenceRecord(
            requirement=binding.requirement,
            node_id=binding.node_id,
            element_id=binding.element_id,
            scene_version=binding.scene_version,
            frame_id=binding.frame_id,
            role=binding.role,
            fingerprint=binding.fingerprint,
            observed_text_sha256=hashlib.sha256(
                observed.encode("utf-8")
            ).hexdigest(),
            observed_text_length=len(observed),
            visible=binding.visible,
            enabled=binding.enabled,
            selected=binding.selected,
        )

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
