"""Execute PlanIR nodes through the single :class:`ControlLoop` path."""

from __future__ import annotations

from dataclasses import dataclass

from hpcu.runtime_core.control_loop import ControlLoop, StepResult
from hpcu.schemas.action import ActionOp
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.planning import PlanIR, PlanNode
from hpcu.schemas.scene import Scene

_PREEXISTING_SAFE_OPS = frozenset(
    {
        ActionOp.WAIT_UNTIL,
        ActionOp.ASSERT,
        ActionOp.READ,
        ActionOp.CHECKPOINT,
    }
)


@dataclass(frozen=True)
class NodeRunRecord:
    node_id: str
    success: bool
    verified: bool
    pre_scene_version: int
    post_scene_version: int
    scene_changed: bool
    failure_code: str = ""


@dataclass(frozen=True)
class TaskRunResult:
    success: bool
    final_scene: Scene
    completed_node_ids: tuple[str, ...] = ()
    records: tuple[NodeRunRecord, ...] = ()
    failed_node_id: str = ""
    failure_code: str = ""


class TaskRuntime:
    """The only PlanIR executor; it never calls an injector directly."""

    def __init__(self, control_loop: ControlLoop):
        self._control_loop = control_loop

    @property
    def control_loop(self) -> ControlLoop:
        return self._control_loop

    async def run(
        self,
        plan: PlanIR,
        *,
        max_steps: int | None = None,
    ) -> TaskRunResult:
        limit = max_steps if max_steps is not None else max(1, len(plan.nodes) * 3)
        if limit <= 0:
            raise ValueError("max_steps must be positive")

        current_id = plan.entry_node_id
        completed: list[str] = []
        records: list[NodeRunRecord] = []
        visits: dict[str, int] = {}

        for _ in range(limit):
            node = plan.node(current_id)
            visits[node.id] = visits.get(node.id, 0) + 1
            if visits[node.id] > max(1, node.action.retry.max_attempts):
                return self._failure(
                    node,
                    FailureCode.RECOVERY_EXHAUSTED.value,
                    completed,
                    records,
                )

            step = await self._control_loop.step(
                dict(node.target_query),
                node.action,
            )
            pre_scene = step.pre_scene or step.scene
            contract_satisfied = step.success and self._contract_satisfied(
                node,
                step.scene,
            )
            transition_satisfied = self._transition_satisfied(
                node,
                step,
                pre_scene,
                contract_satisfied,
            )
            verified = contract_satisfied and transition_satisfied
            effective_failure = step.failure_code or (
                "" if verified else FailureCode.VERIFICATION_FAILED.value
            )
            records.append(
                NodeRunRecord(
                    node_id=node.id,
                    success=step.success,
                    verified=verified,
                    pre_scene_version=pre_scene.version,
                    post_scene_version=step.scene.version,
                    scene_changed=step.scene_changed,
                    failure_code=effective_failure,
                )
            )

            if not step.success or not verified:
                if node.failure_next is not None:
                    current_id = node.failure_next
                    continue
                return self._failure(
                    node,
                    effective_failure or FailureCode.UNKNOWN.value,
                    completed,
                    records,
                )

            completed.append(node.id)
            if node.terminal:
                return TaskRunResult(
                    success=True,
                    final_scene=step.scene,
                    completed_node_ids=tuple(completed),
                    records=tuple(records),
                )

            if node.success_next is None:
                return self._failure(
                    node,
                    FailureCode.RECOVERY_EXHAUSTED.value,
                    completed,
                    records,
                )
            current_id = node.success_next

        return self._failure(
            plan.node(current_id),
            FailureCode.RECOVERY_EXHAUSTED.value,
            completed,
            records,
        )

    def _contract_satisfied(self, node: PlanNode, scene: Scene) -> bool:
        has_contract = node.evidence is not None or bool(node.action.postconditions)
        if not has_contract:
            return False
        evidence_ok = node.evidence is None or self._control_loop.verifier.verify(
            node.evidence,
            scene,
        )
        postconditions_ok = not node.action.postconditions or (
            self._control_loop.verifier.verify_postconditions(
                node.action.postconditions,
                scene,
                last_action=node.action,
            )
        )
        return evidence_ok and postconditions_ok

    def _transition_satisfied(
        self,
        node: PlanNode,
        step: StepResult,
        pre_scene: Scene,
        contract_satisfied: bool,
    ) -> bool:
        if not bool(node.require_scene_change):
            return True
        if step.scene_changed:
            return True
        if (
            node.allow_preexisting_success
            and node.action.op in _PREEXISTING_SAFE_OPS
            and contract_satisfied
        ):
            return self._contract_satisfied(node, pre_scene)
        return False

    def _failure(
        self,
        node: PlanNode,
        failure_code: str,
        completed: list[str],
        records: list[NodeRunRecord],
    ) -> TaskRunResult:
        return TaskRunResult(
            success=False,
            final_scene=self._control_loop.scene,
            completed_node_ids=tuple(completed),
            records=tuple(records),
            failed_node_id=node.id,
            failure_code=failure_code,
        )
