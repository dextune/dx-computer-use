"""Execute PlanIR nodes through the single ControlLoop path."""

from __future__ import annotations

from dataclasses import dataclass

from hpcu.runtime_core.control_loop import ControlLoop, StepResult
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.planning import PlanIR, PlanNode
from hpcu.schemas.scene import Scene


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

    async def run(self, plan: PlanIR, *, max_steps: int | None = None) -> TaskRunResult:
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

            step = await self._control_loop.step(dict(node.target_query), node.action)
            node_verified = self._node_verified(node, step)
            records.append(
                NodeRunRecord(
                    node_id=node.id,
                    success=step.success,
                    verified=node_verified,
                    pre_scene_version=step.pre_scene.version,
                    post_scene_version=step.scene.version,
                    scene_changed=step.scene_changed,
                    failure_code=step.failure_code or "",
                )
            )

            if not step.success:
                if node.failure_next is not None:
                    current_id = node.failure_next
                    continue
                return self._failure(
                    node,
                    step.failure_code or FailureCode.UNKNOWN.value,
                    completed,
                    records,
                )

            completed.append(node.id)
            if node.terminal:
                if not node_verified:
                    return self._failure(
                        node,
                        FailureCode.VERIFICATION_FAILED.value,
                        completed,
                        records,
                    )
                if bool(node.require_scene_change) and not step.scene_changed:
                    pre_satisfied = self._evidence_satisfied(node, step.pre_scene)
                    if not (node.allow_preexisting_success and pre_satisfied):
                        return self._failure(
                            node,
                            FailureCode.VERIFICATION_FAILED.value,
                            completed,
                            records,
                        )
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

        final_node = plan.node(current_id)
        return self._failure(
            final_node,
            FailureCode.RECOVERY_EXHAUSTED.value,
            completed,
            records,
        )

    def _node_verified(self, node: PlanNode, step: StepResult) -> bool:
        if not step.success:
            return False
        evidence_ok = self._evidence_satisfied(node, step.scene)
        postconditions_ok = not node.action.postconditions or step.verified
        return evidence_ok and postconditions_ok

    def _evidence_satisfied(self, node: PlanNode, scene: Scene) -> bool:
        if node.evidence is None:
            return bool(node.action.postconditions)
        return self._control_loop.verifier.verify(node.evidence, scene)

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
