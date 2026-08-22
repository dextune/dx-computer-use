"""One authoritative observe→ground→policy→execute→fresh-verify cycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from hpcu.coordinates.spaces import compute_safe_click_point
from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder, GroundingCandidate, GroundingResult
from hpcu.input.injector import ExecutionResult
from hpcu.observation.base import Observer
from hpcu.policy.approval import ApprovalGate
from hpcu.policy.risk_engine import RiskEngine
from hpcu.recovery.loop_breaker import LoopBreaker
from hpcu.scene_graph.builder import SceneBuilder
from hpcu.schemas.action import Action, ActionOp, ActionTarget, PreconditionKind
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.trace import TraceEventType
from hpcu.schemas.ui_element import UIElement
from hpcu.trace.recorder import TraceRecorder
from hpcu.verifier.verifier import Verifier


@dataclass(frozen=True)
class StepResult:
    """Action-level outcome; terminal task success is decided by TaskRuntime."""

    success: bool
    scene: Scene
    pre_scene: Scene
    failure_code: Optional[str] = None
    skipped: bool = False
    verified: bool = False
    scene_changed: bool = False
    grounding: Optional[GroundingResult] = None
    execution: Optional[ExecutionResult] = None


class ControlLoop:
    """Platform-neutral control loop used by task, case and replay paths."""

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
    ):
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
        self._action_history: list[Action] = []
        self._scene_history: list[Scene] = []

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

    async def observe(self) -> Scene:
        """Capture one fresh Scene and make it the current runtime state."""
        delta = await self._observer.observe()
        scene = self._builder.update(self._scene, delta)
        self._scene = scene
        self._record(
            TraceEventType.SCENE_UPDATE,
            scene.version,
            payload={
                "frame_id": scene.frame.shm_id if scene.frame else None,
                "added": len(delta.added),
                "removed": len(delta.removed),
                "modified": len(delta.modified),
            },
        )
        return scene

    async def step(self, query: dict, action: Action) -> StepResult:
        """Execute one action and verify it only against a post-action Scene."""
        pre_scene = await self.observe()

        if not _preconditions_hold(action, pre_scene):
            return StepResult(
                success=False,
                scene=pre_scene,
                pre_scene=pre_scene,
                skipped=True,
                failure_code=FailureCode.PRECONDITION_UNMET.value,
            )

        grounding = self._resolve(action, query, pre_scene)
        if not grounding.confident:
            return StepResult(
                success=False,
                scene=pre_scene,
                pre_scene=pre_scene,
                failure_code=(
                    FailureCode.GROUNDING_NO_CANDIDATES.value
                    if not grounding.candidates
                    else FailureCode.GROUNDING_CONFIDENCE_LOW.value
                ),
                skipped=True,
                grounding=grounding,
            )

        element = pre_scene.get(grounding.element_id or "")
        if element is not None and not _actionable(element, pre_scene.version):
            return StepResult(
                success=False,
                scene=pre_scene,
                pre_scene=pre_scene,
                failure_code=(
                    FailureCode.TARGET_OCCLUDED.value
                    if element.state.occluded
                    else FailureCode.STALE_DECISION.value
                ),
                skipped=True,
                grounding=grounding,
            )

        targeted = replace(
            action,
            target=ActionTarget(
                element_id=grounding.element_id,
                locator=action.target.locator,
            ),
        )
        risk = self._risk_engine.assess(targeted, pre_scene)
        if self._risk_engine.require_approval(risk):
            if self._approval_gate is not None:
                self._approval_gate.request_approval(
                    targeted,
                    risk,
                    "policy denied execution without explicit approval",
                )
            return StepResult(
                success=False,
                scene=pre_scene,
                pre_scene=pre_scene,
                failure_code=FailureCode.ACTION_REJECTED_BY_POLICY.value,
                skipped=True,
                grounding=grounding,
            )

        point = None
        if element is not None and element.bbox is not None:
            point = compute_safe_click_point(element.bbox)
        prepared = self._executor.prepare(
            targeted,
            grounding.element_id,
            element=element,
            physical_point=point,
            source_scene=pre_scene,
        )
        execution = await self._executor.execute(prepared, current_scene=pre_scene)
        self._record(
            TraceEventType.ACTION,
            pre_scene.version,
            element_id=grounding.element_id,
            failure_code=execution.failure_code,
            payload={
                "op": targeted.op.value,
                "source_frame_id": prepared.source_frame_id,
            },
        )
        if not execution.success:
            return StepResult(
                success=False,
                scene=pre_scene,
                pre_scene=pre_scene,
                failure_code=execution.failure_code,
                grounding=grounding,
                execution=execution,
            )

        # This is the critical freshness boundary. Never verify against the
        # Scene that was used to choose the action.
        post_scene = await self.observe()
        changed = _scene_state(pre_scene) != _scene_state(post_scene)
        verified = bool(targeted.postconditions) and self._verifier.verify_postconditions(
            targeted.postconditions,
            post_scene,
            last_action=targeted,
        )
        self._record(
            TraceEventType.VERIFICATION,
            post_scene.version,
            element_id=grounding.element_id,
            failure_code=(
                None
                if (verified or not targeted.postconditions)
                else FailureCode.POSTCONDITION_UNMET.value
            ),
            payload={
                "pre_scene_version": pre_scene.version,
                "post_scene_version": post_scene.version,
                "scene_changed": changed,
                "postconditions_declared": bool(targeted.postconditions),
                "verified": verified,
            },
        )

        self._action_history.append(targeted)
        self._scene_history.append(post_scene)
        if targeted.postconditions and not verified:
            if self._loop_breaker is not None:
                self._loop_breaker.detect(
                    tuple(self._action_history),
                    tuple(self._scene_history),
                )
            return StepResult(
                success=False,
                scene=post_scene,
                pre_scene=pre_scene,
                failure_code=FailureCode.POSTCONDITION_UNMET.value,
                verified=False,
                scene_changed=changed,
                grounding=grounding,
                execution=execution,
            )

        return StepResult(
            success=True,
            scene=post_scene,
            pre_scene=pre_scene,
            verified=verified,
            scene_changed=changed,
            grounding=grounding,
            execution=execution,
        )

    async def run(self, query: dict, action: Action, max_steps: int = 1) -> StepResult:
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        last = StepResult(success=False, scene=self._scene, pre_scene=self._scene)
        for _ in range(max_steps):
            last = await self.step(query, action)
            if last.success or last.skipped:
                return last
        return last

    def _resolve(self, action: Action, query: dict, scene: Scene) -> GroundingResult:
        explicit_id = action.target.element_id
        if explicit_id:
            element = scene.get(explicit_id)
            if element is None:
                return GroundingResult(None, 0.0, ())
            candidate = GroundingCandidate(
                element_id=explicit_id,
                confidence=1.0,
                source="explicit_current_target",
            )
            return GroundingResult(explicit_id, 1.0, (candidate,))
        if action.op in {
            ActionOp.WAIT_UNTIL,
            ActionOp.ASSERT,
            ActionOp.READ,
            ActionOp.CHECKPOINT,
        } and not query:
            candidate = GroundingCandidate(
                element_id="",
                confidence=1.0,
                source="targetless_action",
            )
            return GroundingResult("", 1.0, (candidate,))
        return self._grounder.resolve(query, scene)

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


def _actionable(element: UIElement, scene_version: int) -> bool:
    return bool(
        element.scene_version == scene_version
        and element.state.visible
        and element.state.enabled
        and not element.state.occluded
    )


def _preconditions_hold(action: Action, scene: Scene) -> bool:
    for condition in action.preconditions:
        target = scene.get(condition.target or "")
        if condition.kind is PreconditionKind.ELEMENT_VISIBLE:
            if target is None or not target.state.visible:
                return False
        elif condition.kind is PreconditionKind.ELEMENT_FOCUSED:
            if target is None or not target.state.selected:
                return False
        elif condition.kind is PreconditionKind.ELEMENT_ENABLED:
            if target is None or not target.state.enabled:
                return False
        elif condition.kind is PreconditionKind.TEXT_EQUALS:
            text = "" if target is None else (target.text or target.name or "")
            if text != str(condition.value or ""):
                return False
        elif condition.kind is PreconditionKind.STATE_MATCHES:
            expected = str(condition.value or "").casefold() in {
                "1",
                "true",
                "yes",
                "selected",
            }
            if target is None or target.state.selected is not expected:
                return False
        else:
            return False
    return True


def _scene_state(scene: Scene) -> tuple[tuple[object, ...], ...]:
    """Semantic state used for transition checks; versions alone do not count."""
    return tuple(
        sorted(
            (
                element.id,
                element.role,
                element.name,
                element.text,
                element.fingerprint,
                element.state.visible,
                element.state.enabled,
                element.state.selected,
                element.state.occluded,
                (
                    None
                    if element.bbox is None
                    else (
                        element.bbox.space.value,
                        element.bbox.x,
                        element.bbox.y,
                        element.bbox.width,
                        element.bbox.height,
                    )
                ),
            )
            for element in scene.elements.values()
        )
    )
