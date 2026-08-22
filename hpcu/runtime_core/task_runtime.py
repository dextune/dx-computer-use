"""PlanIR task runtime over the single deterministic ControlLoop."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum

from hpcu.recovery.loop_breaker import RecoveryAction, action_fingerprint, scene_hash
from hpcu.runtime_config import load_runtime_config
from hpcu.runtime_core.control_loop import ControlLoop, StepResult
from hpcu.runtime_core.task_budget import TaskBudgetLedger
from hpcu.schemas.action import ActionOp
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.goal import IntentKind
from hpcu.schemas.plan import (
    PlanIR,
    PlanNode,
    PlanPatch,
    ReplanRequest,
    TaskBudgetSnapshot,
)
from hpcu.schemas.scene import Scene
from hpcu.schemas.trace import TraceEventType

_WHITESPACE_RE = re.compile(r"\s+")


class TaskStatus(str, Enum):
    RUNNING = "running"
    VERIFIED_SUCCESS = "verified_success"
    HUMAN_HANDOFF = "human_handoff"
    FAILED = "failed"


@dataclass(frozen=True)
class EvidenceBinding:
    """One independently observed requirement bound to a concrete scene."""

    requirement: str
    node_id: str
    element_id: str
    scene_version: int
    frame_id: str
    observed_text: str
    role: str
    fingerprint: str
    visible: bool
    enabled: bool
    selected: bool

    def __post_init__(self) -> None:
        if not self.requirement or not self.node_id or not self.element_id:
            raise ValueError("evidence binding identifiers must be non-empty")
        if self.scene_version < 0:
            raise ValueError("evidence scene_version must be non-negative")


@dataclass(frozen=True)
class TaskRunResult:
    status: TaskStatus
    node_id: str
    steps: int
    last_step: StepResult | None = None
    failure_code: str | None = None
    final_scene_version: int = 0
    final_frame_id: str = ""
    terminal_evidence_ids: tuple[str, ...] = ()
    evidence_bindings: tuple[EvidenceBinding, ...] = ()
    completed_node_ids: tuple[str, ...] = ()
    plan_hash: str = ""
    patch_lineage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is TaskStatus.VERIFIED_SUCCESS and self.failure_code is not None:
            raise ValueError("verified success cannot retain a failure code")
        if self.status is TaskStatus.RUNNING:
            raise ValueError("TaskRunResult must represent a terminal state")
        if self.status is not TaskStatus.VERIFIED_SUCCESS and self.terminal_evidence_ids:
            raise ValueError("non-success terminal state cannot publish evidence ids")

    @property
    def success(self) -> bool:
        return self.status is TaskStatus.VERIFIED_SUCCESS


Repairer = Callable[
    [ReplanRequest, PlanIR],
    PlanPatch | None | Awaitable[PlanPatch | None],
]

_HANDOFF_FAILURES = frozenset(
    {
        FailureCode.CAPTCHA_DETECTED,
        FailureCode.LOGIN_REQUIRED,
        FailureCode.SECURITY_CHECK_REQUIRED,
        FailureCode.HUMAN_HANDOFF_REQUIRED,
        FailureCode.ACCESS_CONTROL_BLOCKED,
        FailureCode.ACTION_REJECTED_BY_POLICY,
    }
)


class TaskRuntime:
    """Own graph progression, bounded repair, evidence, and terminal commit."""

    def __init__(
        self,
        control_loop: ControlLoop,
        budget_ledger: TaskBudgetLedger | None = None,
        *,
        local_repairer: Repairer | None = None,
        semantic_replanner: Repairer | None = None,
        max_local_repairs_per_node: int | None = None,
        max_replans_per_task: int | None = None,
        config: dict | None = None,
    ) -> None:
        runtime = config if config is not None else load_runtime_config()
        recovery = runtime.get("recovery", {})
        local_limit = (
            int(recovery.get("max_local_repairs_per_node", 1))
            if max_local_repairs_per_node is None
            else max_local_repairs_per_node
        )
        replan_limit = (
            int(recovery.get("max_replans_per_task", 2))
            if max_replans_per_task is None
            else max_replans_per_task
        )
        if local_limit < 0 or replan_limit < 0:
            raise ValueError("repair and replan limits must be non-negative")
        self.control_loop = control_loop
        self._budget_ledger = budget_ledger
        self._local_repairer = local_repairer
        self._semantic_replanner = semantic_replanner
        self._max_local_repairs_per_node = local_limit
        self._max_replans_per_task = replan_limit
        self._terminal_committed = False

    @property
    def budget_ledger(self) -> TaskBudgetLedger | None:
        return self._budget_ledger

    async def run(
        self,
        plan: PlanIR,
        *,
        max_steps: int | None = None,
    ) -> TaskRunResult:
        self._terminal_committed = False
        self._validate_plan_evidence(plan)
        if self._budget_ledger is None:
            self._budget_ledger = TaskBudgetLedger(plan.task_budget)
        elif self._budget_ledger.spec != plan.task_budget:
            raise ValueError("task budget ledger does not match PlanIR budget")

        limit = max_steps if max_steps is not None else max(1, len(plan.nodes) * 6)
        if limit <= 0:
            raise ValueError("max_steps must be positive")

        current_plan = plan
        node_id = plan.entry_node_id
        last: StepResult | None = None
        completed: list[str] = []
        completed_set: set[str] = set()
        evidence: dict[str, EvidenceBinding] = {}
        failure_signatures: set[tuple[str, str, str]] = set()
        local_repairs: dict[str, int] = {}
        replan_count = 0

        for step_number in range(1, limit + 1):
            node = current_plan.nodes[node_id]
            last = await self._run_node(node)

            if last.success:
                bindings = self._bind_node_evidence(node, last)
                if len(bindings) != len(node.evidence_requirements):
                    return self._commit_terminal(
                        status=TaskStatus.FAILED,
                        node_id=node_id,
                        steps=step_number,
                        last_step=last,
                        failure_code=FailureCode.VERIFICATION_FAILED.value,
                        completed=completed,
                        evidence=evidence,
                        plan=current_plan,
                    )
                for binding in bindings:
                    evidence[binding.requirement] = binding
                if node_id not in completed_set:
                    completed.append(node_id)
                    completed_set.add(node_id)
                if node.success_edge is None:
                    if not self._terminal_evidence_valid(current_plan, evidence):
                        return self._commit_terminal(
                            status=TaskStatus.FAILED,
                            node_id=node_id,
                            steps=step_number,
                            last_step=last,
                            failure_code=FailureCode.VERIFICATION_FAILED.value,
                            completed=completed,
                            evidence=evidence,
                            plan=current_plan,
                        )
                    return self._commit_terminal(
                        status=TaskStatus.VERIFIED_SUCCESS,
                        node_id=node_id,
                        steps=step_number,
                        last_step=last,
                        failure_code=None,
                        completed=completed,
                        evidence=evidence,
                        plan=current_plan,
                    )
                node_id = node.success_edge
                continue

            failure = self._failure_code(last.failure_code)
            if failure in _HANDOFF_FAILURES:
                return self._commit_terminal(
                    status=TaskStatus.HUMAN_HANDOFF,
                    node_id=node_id,
                    steps=step_number,
                    last_step=last,
                    failure_code=failure.value,
                    completed=completed,
                    evidence=evidence,
                    plan=current_plan,
                )
            if last.recovery_action is RecoveryAction.HALT:
                return self._commit_terminal(
                    status=TaskStatus.FAILED,
                    node_id=node_id,
                    steps=step_number,
                    last_step=last,
                    failure_code=FailureCode.LOOP_DETECTED.value,
                    completed=completed,
                    evidence=evidence,
                    plan=current_plan,
                )
            if self._irreversible_effect_is_uncertain(node, last):
                return self._commit_terminal(
                    status=TaskStatus.HUMAN_HANDOFF,
                    node_id=node_id,
                    steps=step_number,
                    last_step=last,
                    failure_code=failure.value,
                    completed=completed,
                    evidence=evidence,
                    plan=current_plan,
                )

            signature = (
                node_id,
                action_fingerprint(node.action),
                scene_hash(last.scene),
            )
            repeated_failure = signature in failure_signatures
            failure_signatures.add(signature)

            recovery_edge = node.failure_edges.get(failure.value)
            if recovery_edge is None:
                recovery_edge = node.failure_edges.get("*")
            if recovery_edge is not None and not repeated_failure:
                node_id = recovery_edge
                continue

            request = self._replan_request(
                failure=failure,
                node_id=node_id,
                step=last,
                plan=current_plan,
                completed=completed,
            )

            local_count = local_repairs.get(node_id, 0)
            if (
                self._local_repairer is not None
                and local_count < self._max_local_repairs_per_node
                and not repeated_failure
            ):
                patch = await self._invoke_repairer(
                    self._local_repairer,
                    request,
                    current_plan,
                )
                local_repairs[node_id] = local_count + 1
                if patch is not None:
                    self._validate_patch_reason(patch, request)
                    current_plan = self._apply_patch(
                        current_plan,
                        patch,
                        completed,
                    )
                    self._validate_plan_evidence(current_plan)
                    node_id = patch.resume_node_id
                    self._record_patch(last, patch, semantic=False)
                    continue

            wants_semantic = last.recovery_action in (
                RecoveryAction.ESCALATE,
                RecoveryAction.SWITCH_MODE,
            ) or failure in {
                FailureCode.GROUNDING_AMBIGUOUS,
                FailureCode.GROUNDING_NO_CANDIDATES,
                FailureCode.GROUNDING_CONFIDENCE_LOW,
                FailureCode.STALE_DECISION,
                FailureCode.POSTCONDITION_UNMET,
                FailureCode.VERIFICATION_FAILED,
            }
            if (
                wants_semantic
                and self._semantic_replanner is not None
                and replan_count < self._max_replans_per_task
                and self._budget_ledger.remaining_calls > 0
            ):
                patch = await self._invoke_repairer(
                    self._semantic_replanner,
                    request,
                    current_plan,
                )
                replan_count += 1
                if patch is not None:
                    self._validate_patch_reason(patch, request)
                    current_plan = self._apply_patch(
                        current_plan,
                        patch,
                        completed,
                    )
                    self._validate_plan_evidence(current_plan)
                    node_id = patch.resume_node_id
                    self._record_patch(last, patch, semantic=True)
                    continue

            if last.recovery_action is RecoveryAction.ESCALATE:
                return self._commit_terminal(
                    status=TaskStatus.HUMAN_HANDOFF,
                    node_id=node_id,
                    steps=step_number,
                    last_step=last,
                    failure_code=failure.value,
                    completed=completed,
                    evidence=evidence,
                    plan=current_plan,
                )

            return self._commit_terminal(
                status=TaskStatus.FAILED,
                node_id=node_id,
                steps=step_number,
                last_step=last,
                failure_code=(
                    FailureCode.LOOP_DETECTED.value
                    if repeated_failure
                    else failure.value
                ),
                completed=completed,
                evidence=evidence,
                plan=current_plan,
            )

        return self._commit_terminal(
            status=TaskStatus.FAILED,
            node_id=node_id,
            steps=limit,
            last_step=last,
            failure_code=FailureCode.LOOP_DETECTED.value,
            completed=completed,
            evidence=evidence,
            plan=current_plan,
        )

    async def _run_node(self, node: PlanNode) -> StepResult:
        query = node.target_query.as_dict() if node.target_query is not None else None
        verification_query = (
            node.verification_query.as_dict()
            if node.verification_query is not None
            else None
        )
        return await self.control_loop.step(
            query,
            node.action,
            verification_query=verification_query,
        )

    def _replan_request(
        self,
        *,
        failure: FailureCode,
        node_id: str,
        step: StepResult,
        plan: PlanIR,
        completed: list[str],
    ) -> ReplanRequest:
        ledger = self._budget_ledger
        assert ledger is not None
        return ReplanRequest(
            reason=failure,
            failed_node_id=node_id,
            scene_version=step.scene.version,
            unresolved_slots=plan.goal.ambiguity_slots,
            completed_nodes=tuple(completed),
            remaining_budget=TaskBudgetSnapshot(
                remaining_calls=ledger.remaining_calls,
                remaining_tokens=ledger.remaining_tokens,
                remaining_latency_ms=max(
                    0,
                    ledger.spec.max_model_latency_ms - ledger.latency_ms,
                ),
            ),
        )

    @staticmethod
    async def _invoke_repairer(
        repairer: Repairer,
        request: ReplanRequest,
        plan: PlanIR,
    ) -> PlanPatch | None:
        result = repairer(request, plan)
        if inspect.isawaitable(result):
            result = await result
        if result is not None and not isinstance(result, PlanPatch):
            raise TypeError("repairer must return PlanPatch or None")
        return result

    @staticmethod
    def _validate_patch_reason(
        patch: PlanPatch,
        request: ReplanRequest,
    ) -> None:
        if patch.reason is not request.reason:
            raise ValueError("plan patch reason does not match the replan request")

    @staticmethod
    def _apply_patch(
        plan: PlanIR,
        patch: PlanPatch,
        completed: list[str],
    ) -> PlanIR:
        return plan.apply_patch(
            patch,
            completed_node_ids=tuple(completed),
        )

    @staticmethod
    def _validate_plan_evidence(plan: PlanIR) -> None:
        assigned = tuple(
            requirement
            for node in plan.nodes.values()
            for requirement in node.evidence_requirements
        )
        if len(assigned) != len(set(assigned)):
            raise ValueError("PlanIR contains duplicate evidence requirements")
        if set(assigned) != set(plan.goal.evidence_requirements):
            raise ValueError("PlanIR evidence requirements do not match its goal")

    @staticmethod
    def _bind_node_evidence(
        node: PlanNode,
        step: StepResult,
    ) -> tuple[EvidenceBinding, ...]:
        if not node.evidence_requirements:
            return ()
        candidates: list[str] = []
        for grounding in (step.verification_grounding, step.grounding):
            if grounding is None or not grounding.confident:
                continue
            element_id = grounding.element_id
            element = step.scene.get(element_id) if element_id else None
            if element is None or element.scene_version != step.scene.version:
                continue
            if element_id not in candidates:
                candidates.append(element_id)
        if len(candidates) < len(node.evidence_requirements):
            return ()

        frame_id = step.scene.frame.shm_id if step.scene.frame is not None else ""
        bindings: list[EvidenceBinding] = []
        for requirement, element_id in zip(
            node.evidence_requirements,
            candidates,
            strict=True,
        ):
            element = step.scene.get(element_id)
            assert element is not None
            bindings.append(
                EvidenceBinding(
                    requirement=requirement,
                    node_id=node.id,
                    element_id=element_id,
                    scene_version=step.scene.version,
                    frame_id=frame_id,
                    observed_text=str(
                        element.text
                        if element.text is not None
                        else element.name or ""
                    ).strip(),
                    role=element.role,
                    fingerprint=element.fingerprint or "",
                    visible=element.state.visible,
                    enabled=element.state.enabled,
                    selected=element.state.selected,
                )
            )
        return tuple(bindings)

    @classmethod
    def _terminal_evidence_valid(
        cls,
        plan: PlanIR,
        evidence: Mapping[str, EvidenceBinding],
    ) -> bool:
        requirements = plan.goal.evidence_requirements
        if set(evidence) != set(requirements):
            return False
        bindings = {name: evidence[name] for name in requirements}
        if any(not binding.visible for binding in bindings.values()):
            return False

        intent = plan.goal.intent
        if intent is IntentKind.NAVIGATE:
            return cls._has_identity(bindings["surface_identity"])
        if intent is IntentKind.SEARCH:
            query = plan.goal.entity_values("search_query")
            if len(query) != 1:
                return False
            query_binding = bindings["query_echo"]
            result_binding = bindings["result_candidate"]
            return bool(
                query_binding.element_id != result_binding.element_id
                and cls._normalize(query[0])
                == cls._normalize(query_binding.observed_text)
                and cls._has_identity(result_binding)
            )
        if intent is IntentKind.SELECT:
            return bindings["selected_state"].selected
        if intent is IntentKind.COMPARE:
            first = bindings["candidate_a"]
            second = bindings["candidate_b"]
            return bool(
                first.element_id != second.element_id
                and cls._has_identity(first)
                and cls._has_identity(second)
            )
        if intent is IntentKind.EDIT:
            old = bindings["old_value"]
            new = bindings["edited_value"]
            requested = cls._requested_edit_value(plan)
            return bool(
                requested
                and cls._normalize(old.observed_text)
                != cls._normalize(new.observed_text)
                and cls._normalize(new.observed_text) == cls._normalize(requested)
            )
        if intent is IntentKind.SUBMIT:
            return cls._has_identity(bindings["submission_confirmation"])
        return all(cls._has_identity(binding) for binding in bindings.values())

    @staticmethod
    def _requested_edit_value(plan: PlanIR) -> str:
        values = [
            node.action.value or ""
            for node in plan.nodes.values()
            if node.action.op is ActionOp.REPLACE_TEXT
        ]
        unique = {value for value in values if value}
        return next(iter(unique)) if len(unique) == 1 else ""

    @staticmethod
    def _has_identity(binding: EvidenceBinding) -> bool:
        return bool(
            binding.observed_text
            or binding.fingerprint
            or binding.role not in ("", "unknown")
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return _WHITESPACE_RE.sub(" ", value).strip().casefold()

    @staticmethod
    def _irreversible_effect_is_uncertain(
        node: PlanNode,
        step: StepResult,
    ) -> bool:
        return bool(
            node.irreversible
            and step.execution is not None
            and step.execution.success
        )

    @staticmethod
    def _failure_code(value: str | None) -> FailureCode:
        if value is None:
            return FailureCode.UNKNOWN
        try:
            return FailureCode(value)
        except ValueError:
            return FailureCode.UNKNOWN

    def _record_patch(
        self,
        step: StepResult,
        patch: PlanPatch,
        *,
        semantic: bool,
    ) -> None:
        recorder = getattr(self.control_loop, "recorder", None)
        if recorder is None:
            return
        recorder.append(
            TraceEventType.RECOVERY,
            step.scene.version,
            failure_code=patch.reason.value,
            payload={
                "kind": "semantic_replan" if semantic else "local_repair",
                "parent_plan_hash": patch.parent_plan_hash,
                "patch_hash": patch.patch_hash,
                "resume_node_id": patch.resume_node_id,
            },
        )

    def _commit_terminal(
        self,
        *,
        status: TaskStatus,
        node_id: str,
        steps: int,
        last_step: StepResult | None,
        failure_code: str | None,
        completed: list[str],
        evidence: Mapping[str, EvidenceBinding],
        plan: PlanIR,
    ) -> TaskRunResult:
        if self._terminal_committed:
            raise RuntimeError("task terminal state was already committed")
        self._terminal_committed = True
        scene = (
            last_step.scene
            if last_step is not None
            else getattr(self.control_loop, "scene", Scene(version=0))
        )
        frame_id = scene.frame.shm_id if scene.frame is not None else ""
        normalized_failure = (
            None if status is TaskStatus.VERIFIED_SUCCESS else failure_code
        )
        bindings = tuple(
            evidence[name]
            for name in plan.goal.evidence_requirements
            if name in evidence
        )
        terminal_ids: tuple[str, ...] = ()
        if status is TaskStatus.VERIFIED_SUCCESS:
            terminal_ids = tuple(
                dict.fromkeys(binding.element_id for binding in bindings)
            )
        recorder = getattr(self.control_loop, "recorder", None)
        if recorder is not None:
            recorder.append(
                TraceEventType.COMPLETION,
                scene.version,
                failure_code=normalized_failure,
                payload={
                    "status": status.value,
                    "node_id": node_id,
                    "plan_hash": plan.plan_hash,
                    "evidence": [
                        {
                            "requirement": binding.requirement,
                            "node_id": binding.node_id,
                            "element_id": binding.element_id,
                            "scene_version": binding.scene_version,
                            "frame_id": binding.frame_id,
                        }
                        for binding in bindings
                    ],
                },
            )
        return TaskRunResult(
            status=status,
            node_id=node_id,
            steps=steps,
            last_step=last_step,
            failure_code=normalized_failure,
            final_scene_version=scene.version,
            final_frame_id=frame_id,
            terminal_evidence_ids=terminal_ids,
            evidence_bindings=bindings,
            completed_node_ids=tuple(completed),
            plan_hash=plan.plan_hash,
            patch_lineage=plan.patch_lineage,
        )
