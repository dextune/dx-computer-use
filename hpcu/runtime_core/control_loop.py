"""Single observe→ground→policy→execute→fresh-observe→verify cycle."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Optional

from hpcu.coordinates.spaces import compute_safe_click_point
from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder, GroundingCandidate, GroundingResult
from hpcu.input.injector import ExecutionResult
from hpcu.observation.base import Observer
from hpcu.policy.approval import ApprovalGate
from hpcu.policy.risk_engine import RiskEngine
from hpcu.recovery.loop_breaker import (
    LoopBreaker,
    LoopDetection,
    RecoveryAction,
)
from hpcu.runtime_config import load_runtime_config
from hpcu.scene_graph.builder import SceneBuilder
from hpcu.schemas.action import Action, ActionOp, ActionTarget
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.trace import TraceEventType
from hpcu.trace.recorder import TraceRecorder
from hpcu.verifier.verifier import Verifier

_LOCAL_VERIFY_OPS = frozenset(
    {ActionOp.ASSERT, ActionOp.READ, ActionOp.CHECKPOINT}
)
_INTRINSIC_TARGET_OPS = frozenset(
    {
        ActionOp.INVOKE,
        ActionOp.NAVIGATE,
        ActionOp.CLICK,
        ActionOp.DOUBLE_CLICK,
        ActionOp.RIGHT_CLICK,
        ActionOp.TYPE,
        ActionOp.REPLACE_TEXT,
        ActionOp.SELECT,
        ActionOp.TOGGLE,
        ActionOp.DRAG,
    }
)
_TARGET_PLACEHOLDER = "$target"


@dataclass(frozen=True)
class StepResult:
    success: bool
    scene: Scene
    failure_code: Optional[str] = None
    skipped: bool = False
    grounding: Optional[GroundingResult] = None
    execution: Optional[ExecutionResult] = None
    verification_grounding: Optional[GroundingResult] = None
    pre_scene_version: int | None = None
    post_scene_version: int | None = None
    loop_detection: LoopDetection | None = None
    recovery_action: RecoveryAction | None = None


class ControlLoop:
    def __init__(
        self,
        observer: Observer,
        grounder: Grounder,
        executor: Executor,
        verifier: Verifier,
        builder: Optional[SceneBuilder] = None,
        risk_engine: Optional[RiskEngine] = None,
        approval_gate: Optional[ApprovalGate] = None,
        recorder: Optional[TraceRecorder] = None,
        loop_breaker: Optional[LoopBreaker] = None,
        scene: Optional[Scene] = None,
        *,
        config: Optional[dict] = None,
        sleep=None,
        monotonic=None,
    ):
        runtime = config if config is not None else load_runtime_config()
        performance = runtime.get("performance", {})
        self._observer = observer
        self._grounder = grounder
        self._executor = executor
        self._verifier = verifier
        self._builder = builder if builder is not None else SceneBuilder()
        self._risk_engine = risk_engine if risk_engine is not None else RiskEngine()
        self._approval_gate = approval_gate
        self._recorder = recorder
        self._loop_breaker = loop_breaker
        self._scene = scene if scene is not None else Scene(version=0)
        self._wait_poll_interval_ms = max(
            1, int(performance.get("settle_poll_interval_ms", 5))
        )
        self._wait_stable_polls = max(
            1, int(performance.get("settle_stable_polls", 2))
        )
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._action_history: list[Action] = []
        self._scene_history: list[Scene] = []
        self._history_limit = max(
            1,
            loop_breaker.window_size if loop_breaker is not None else 16,
        )

    @property
    def observer(self) -> Observer:
        return self._observer

    @property
    def grounder(self) -> Grounder:
        return self._grounder

    @property
    def executor(self) -> Executor:
        return self._executor

    @property
    def verifier(self) -> Verifier:
        return self._verifier

    @property
    def recorder(self) -> Optional[TraceRecorder]:
        return self._recorder

    @property
    def scene(self) -> Scene:
        return self._scene

    @property
    def action_history(self) -> tuple[Action, ...]:
        return tuple(self._action_history)

    @property
    def scene_history(self) -> tuple[Scene, ...]:
        return tuple(self._scene_history)

    def begin_task(self) -> None:
        """Reset task-local recovery state while preserving the live scene."""
        self._action_history.clear()
        self._scene_history.clear()
        if self._loop_breaker is not None:
            self._loop_breaker.reset()

    async def step(
        self,
        query: dict | None,
        action: Action,
        *,
        verification_query: dict | None = None,
    ) -> StepResult:
        """Run one deterministic cycle without invoking a semantic model."""
        pre_scene = await self._observe_scene()

        if action.op is ActionOp.WAIT_UNTIL:
            return await self._wait_until(query, action, pre_scene)

        grounding = self._resolve_target(query, action, pre_scene)
        if self._requires_target(query, action) and (
            grounding is None or not grounding.confident
        ):
            return StepResult(
                success=False,
                scene=pre_scene,
                failure_code=self._grounding_failure_code(grounding).value,
                skipped=True,
                grounding=grounding,
                pre_scene_version=pre_scene.version,
            )

        element_id = (
            grounding.element_id if grounding is not None else action.target.element_id
        )
        targeted = self._bind_target(action, element_id)

        if targeted.op in _LOCAL_VERIFY_OPS:
            verified = self._verifier.verify_postconditions(
                targeted.postconditions, pre_scene
            )
            self._record(TraceEventType.VERIFICATION, pre_scene.version)
            return StepResult(
                success=verified,
                scene=pre_scene,
                failure_code=(
                    None if verified else FailureCode.POSTCONDITION_UNMET.value
                ),
                skipped=True,
                grounding=grounding,
                pre_scene_version=pre_scene.version,
                post_scene_version=pre_scene.version,
            )

        if not self._risk_engine.allow(targeted, pre_scene):
            if self._approval_gate is not None:
                self._approval_gate.request_approval(
                    targeted,
                    self._risk_engine.assess(targeted, pre_scene),
                    "policy.allow denied",
                )
            return StepResult(
                success=False,
                scene=pre_scene,
                failure_code=FailureCode.ACTION_REJECTED_BY_POLICY.value,
                skipped=True,
                grounding=grounding,
                pre_scene_version=pre_scene.version,
            )

        element = pre_scene.get(element_id) if element_id else None
        physical_point = None
        if element is not None and element.bbox is not None:
            physical_point = compute_safe_click_point(element.bbox)
        prepared = self._executor.prepare(
            targeted,
            element_id,
            element=element,
            physical_point=physical_point,
            source_scene=pre_scene,
        )
        execution = await self._executor.execute(prepared, current_scene=pre_scene)
        self._record(
            TraceEventType.ACTION,
            pre_scene.version,
            element_id=element_id,
            failure_code=execution.failure_code,
            payload={"op": targeted.op.value},
        )
        if not execution.success:
            detection, recovery = self._remember_attempt(targeted, pre_scene)
            return StepResult(
                success=False,
                scene=pre_scene,
                failure_code=self._failure_after_recovery(
                    execution.failure_code, recovery
                ),
                grounding=grounding,
                execution=execution,
                pre_scene_version=pre_scene.version,
                loop_detection=detection,
                recovery_action=recovery,
            )

        post_scene = await self._observe_scene()
        verified_action = targeted
        verification_grounding = None
        if verification_query:
            verification_grounding = self._grounder.resolve(
                verification_query, post_scene
            )
            if not verification_grounding.confident:
                detection, recovery = self._remember_attempt(targeted, post_scene)
                self._record(TraceEventType.VERIFICATION, post_scene.version)
                return StepResult(
                    success=False,
                    scene=post_scene,
                    failure_code=self._failure_after_recovery(
                        self._grounding_failure_code(
                            verification_grounding
                        ).value,
                        recovery,
                    ),
                    grounding=grounding,
                    verification_grounding=verification_grounding,
                    execution=execution,
                    pre_scene_version=pre_scene.version,
                    post_scene_version=post_scene.version,
                    loop_detection=detection,
                    recovery_action=recovery,
                )
            verified_action = self._bind_verification_target(
                targeted, verification_grounding.element_id
            )

        verified = self._verifier.verify_transition(
            verified_action, pre_scene, post_scene
        )
        self._record(TraceEventType.VERIFICATION, post_scene.version)
        detection, recovery = self._remember_attempt(targeted, post_scene)
        if not verified:
            return StepResult(
                success=False,
                scene=post_scene,
                failure_code=self._failure_after_recovery(
                    FailureCode.POSTCONDITION_UNMET.value, recovery
                ),
                grounding=grounding,
                execution=execution,
                verification_grounding=verification_grounding,
                pre_scene_version=pre_scene.version,
                post_scene_version=post_scene.version,
                loop_detection=detection,
                recovery_action=recovery,
            )
        return StepResult(
            success=True,
            scene=post_scene,
            grounding=grounding,
            execution=execution,
            verification_grounding=verification_grounding,
            pre_scene_version=pre_scene.version,
            post_scene_version=post_scene.version,
            loop_detection=detection,
            recovery_action=recovery,
        )

    async def run(
        self, query: dict | None, action: Action, max_steps: int = 1
    ) -> StepResult:
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        last = StepResult(success=False, scene=self._scene)
        for _ in range(max_steps):
            last = await self.step(query, action)
            if last.success or last.skipped:
                return last
        return last

    async def _wait_until(
        self,
        query: dict | None,
        action: Action,
        initial_scene: Scene,
    ) -> StepResult:
        deadline = self._monotonic() + action.timeout_ms / 1000.0
        scene = initial_scene
        stable = 0
        last_grounding: GroundingResult | None = None
        while True:
            last_grounding = self._resolve_target(query, action, scene)
            element_id = (
                last_grounding.element_id
                if last_grounding is not None and last_grounding.confident
                else action.target.element_id
            )
            targeted = self._bind_target(action, element_id)
            holds = (
                (not self._requires_target(query, action) or element_id is not None)
                and self._verifier.verify_postconditions(
                    targeted.postconditions, scene
                )
            )
            stable = stable + 1 if holds else 0
            if stable >= self._wait_stable_polls:
                self._record(TraceEventType.VERIFICATION, scene.version)
                return StepResult(
                    success=True,
                    scene=scene,
                    skipped=True,
                    grounding=last_grounding,
                    pre_scene_version=initial_scene.version,
                    post_scene_version=scene.version,
                )
            if self._monotonic() >= deadline:
                self._record(
                    TraceEventType.VERIFICATION,
                    scene.version,
                    failure_code=FailureCode.ACTION_TIMEOUT.value,
                )
                return StepResult(
                    success=False,
                    scene=scene,
                    failure_code=FailureCode.ACTION_TIMEOUT.value,
                    skipped=True,
                    grounding=last_grounding,
                    pre_scene_version=initial_scene.version,
                    post_scene_version=scene.version,
                )
            await self._sleep(self._wait_poll_interval_ms / 1000.0)
            scene = await self._observe_scene()

    async def _observe_scene(self) -> Scene:
        delta = await self._observer.observe()
        scene = self._builder.update(self._scene, delta)
        self._scene = scene
        self._record(TraceEventType.SCENE_UPDATE, scene.version)
        return scene

    def _remember_attempt(
        self,
        action: Action,
        resulting_scene: Scene,
    ) -> tuple[LoopDetection | None, RecoveryAction | None]:
        self._action_history.append(action)
        self._scene_history.append(resulting_scene)
        del self._action_history[:-self._history_limit]
        del self._scene_history[:-self._history_limit]
        if self._loop_breaker is None:
            return None, None
        detection = self._loop_breaker.detect(
            self._action_history,
            self._scene_history,
        )
        if detection is None:
            return None, None
        recovery = self._loop_breaker.recover(detection, resulting_scene, action)
        self._record(
            TraceEventType.RECOVERY,
            resulting_scene.version,
            failure_code=FailureCode.LOOP_DETECTED.value,
            payload={
                "trigger": detection.trigger.value,
                "recovery": recovery.value,
            },
        )
        return detection, recovery

    @staticmethod
    def _failure_after_recovery(
        failure_code: str | None,
        recovery: RecoveryAction | None,
    ) -> str:
        if recovery is RecoveryAction.HALT:
            return FailureCode.LOOP_DETECTED.value
        return failure_code or FailureCode.UNKNOWN.value

    @staticmethod
    def _grounding_failure_code(
        grounding: GroundingResult | None,
    ) -> FailureCode:
        if grounding is not None and grounding.failure_code is not None:
            return grounding.failure_code
        return FailureCode.GROUNDING_NO_CANDIDATES

    def _resolve_target(
        self, query: dict | None, action: Action, scene: Scene
    ) -> GroundingResult | None:
        if query:
            return self._grounder.resolve(query, scene)
        element_id = action.target.element_id
        if element_id and scene.get(element_id) is not None:
            return GroundingResult(
                element_id=element_id,
                confidence=1.0,
                candidates=(
                    GroundingCandidate(element_id=element_id, confidence=1.0),
                ),
            )
        return None

    @staticmethod
    def _requires_target(query: dict | None, action: Action) -> bool:
        # A supplied query is an explicit request to ground an abstract target;
        # intrinsic pointer/text operations also fail before the executor when
        # no target binding exists. FOCUS_WINDOW and global verification ops may
        # remain targetless when no query/element binding was requested.
        return bool(
            query
            or action.target.element_id is not None
            or action.op in _INTRINSIC_TARGET_OPS
        )

    @staticmethod
    def _bind_target(action: Action, element_id: str | None) -> Action:
        postconditions = tuple(
            replace(condition, target=element_id)
            if condition.target == _TARGET_PLACEHOLDER
            else condition
            for condition in action.postconditions
        )
        return replace(
            action,
            target=ActionTarget(
                element_id=element_id,
                locator=action.target.locator,
            ),
            postconditions=postconditions,
        )

    @staticmethod
    def _bind_verification_target(
        action: Action, element_id: str | None
    ) -> Action:
        postconditions = tuple(
            replace(condition, target=element_id)
            if condition.target == "$verify"
            else condition
            for condition in action.postconditions
        )
        return replace(action, postconditions=postconditions)

    def _record(
        self,
        event_type: TraceEventType,
        scene_version: int,
        *,
        element_id: Optional[str] = None,
        failure_code: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        if self._recorder is None:
            return
        self._recorder.append(
            event_type,
            scene_version,
            element_id=element_id,
            failure_code=failure_code,
            payload=payload or {},
        )