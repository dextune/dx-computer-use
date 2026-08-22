"""ControlLoop — one observe→ground→policy→execute→fresh-observe→verify cycle."""

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
from hpcu.schemas.action import Action, ActionOp, ActionTarget
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.trace import TraceEventType
from hpcu.trace.recorder import TraceRecorder
from hpcu.verifier.verifier import Verifier

_LOCAL_VERIFY_OPS = frozenset(
    {ActionOp.ASSERT, ActionOp.READ, ActionOp.WAIT_UNTIL, ActionOp.CHECKPOINT}
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

    async def step(
        self,
        query: dict | None,
        action: Action,
        *,
        verification_query: dict | None = None,
    ) -> StepResult:
        """Run one deterministic action cycle without invoking a model."""
        pre_scene = await self._observe_scene()
        grounding = self._resolve_target(query, action, pre_scene)
        if self._requires_target(query, action) and (
            grounding is None or not grounding.confident
        ):
            return StepResult(
                success=False,
                scene=pre_scene,
                failure_code=FailureCode.GROUNDING_NO_CANDIDATES.value,
                skipped=True,
                grounding=grounding,
                pre_scene_version=pre_scene.version,
            )

        element_id = (
            grounding.element_id
            if grounding is not None
            else action.target.element_id
        )
        targeted = self._bind_target(action, element_id)
        verification_grounding = None

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
            return StepResult(
                success=False,
                scene=pre_scene,
                failure_code=execution.failure_code,
                grounding=grounding,
                execution=execution,
                verification_grounding=verification_grounding,
                pre_scene_version=pre_scene.version,
            )

        # A side effect is never verified against the scene that preceded it.
        post_scene = await self._observe_scene()
        verified_action = targeted
        if verification_query:
            verification_grounding = self._grounder.resolve(
                verification_query, post_scene
            )
            if not verification_grounding.confident:
                self._record(TraceEventType.VERIFICATION, post_scene.version)
                return StepResult(
                    success=False,
                    scene=post_scene,
                    failure_code=FailureCode.VERIFICATION_FAILED.value,
                    grounding=grounding,
                    verification_grounding=verification_grounding,
                    execution=execution,
                    pre_scene_version=pre_scene.version,
                    post_scene_version=post_scene.version,
                )
            verified_action = self._bind_verification_target(
                targeted, verification_grounding.element_id
            )
        verified = self._verifier.verify_transition(
            verified_action, pre_scene, post_scene
        )
        self._record(TraceEventType.VERIFICATION, post_scene.version)
        if not verified:
            if self._loop_breaker is not None:
                self._loop_breaker.detect((), ())
            return StepResult(
                success=False,
                scene=post_scene,
                failure_code=FailureCode.POSTCONDITION_UNMET.value,
                grounding=grounding,
                execution=execution,
                verification_grounding=verification_grounding,
                pre_scene_version=pre_scene.version,
                post_scene_version=post_scene.version,
            )
        return StepResult(
            success=True,
            scene=post_scene,
            grounding=grounding,
            execution=execution,
            verification_grounding=verification_grounding,
            pre_scene_version=pre_scene.version,
            post_scene_version=post_scene.version,
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

    async def _observe_scene(self) -> Scene:
        delta = await self._observer.observe()
        scene = self._builder.update(self._scene, delta)
        self._scene = scene
        self._record(TraceEventType.SCENE_UPDATE, scene.version)
        return scene

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
                candidates=(GroundingCandidate(element_id=element_id, confidence=1.0),),
            )
        return None

    @staticmethod
    def _requires_target(query: dict | None, action: Action) -> bool:
        if query:
            return True
        return action.target.element_id is not None

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
            target=ActionTarget(element_id=element_id, locator=action.target.locator),
            postconditions=postconditions,
        )

    @staticmethod
    def _bind_verification_target(action: Action, element_id: str | None) -> Action:
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
