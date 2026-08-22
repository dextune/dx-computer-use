"""ControlLoop — one observe→ground→policy→execute→verify cycle.

No platform imports. Model is not called in this module (unresolved
grounding returns without escalation).
"""

from dataclasses import dataclass, replace
from typing import Optional

from hpcu.coordinates.spaces import compute_safe_click_point
from hpcu.executor.executor import Executor
from hpcu.grounder.grounder import Grounder, GroundingResult
from hpcu.input.injector import ExecutionResult
from hpcu.observation.base import Observer
from hpcu.policy.approval import ApprovalGate
from hpcu.policy.risk_engine import RiskEngine
from hpcu.recovery.loop_breaker import LoopBreaker
from hpcu.scene_graph.builder import SceneBuilder
from hpcu.schemas.action import Action, ActionTarget
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.trace import TraceEventType
from hpcu.trace.recorder import TraceRecorder
from hpcu.verifier.verifier import Verifier


@dataclass(frozen=True)
class StepResult:
    """Outcome of a single control-loop cycle."""

    success: bool
    scene: Scene
    failure_code: Optional[str] = None
    skipped: bool = False
    grounding: Optional[GroundingResult] = None
    execution: Optional[ExecutionResult] = None


class ControlLoop:
    """Constructor-injected control loop. Platform-agnostic."""

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

    async def step(self, query: dict, action: Action) -> StepResult:
        """Run one cycle. Does not call a model."""
        delta = await self._observer.observe()
        scene = self._builder.update(self._scene, delta)
        self._scene = scene
        self._record(TraceEventType.SCENE_UPDATE, scene.version)

        grounding = self._grounder.resolve(query, scene)
        if not grounding.confident:
            return StepResult(
                success=False,
                scene=scene,
                failure_code=FailureCode.GROUNDING_NO_CANDIDATES.value,
                skipped=True,
                grounding=grounding,
            )

        targeted = replace(
            action,
            target=ActionTarget(element_id=grounding.element_id),
        )
        if not self._risk_engine.allow(targeted, scene):
            if self._approval_gate is not None:
                self._approval_gate.request_approval(
                    targeted,
                    self._risk_engine.assess(targeted, scene),
                    "policy.allow denied",
                )
            return StepResult(
                success=False,
                scene=scene,
                failure_code=FailureCode.ACTION_REJECTED_BY_POLICY.value,
                skipped=True,
                grounding=grounding,
            )

        element = scene.get(grounding.element_id) if grounding.element_id else None
        physical_point = None
        if element is not None and element.bbox is not None:
            physical_point = compute_safe_click_point(element.bbox)
        prepared = self._executor.prepare(
            targeted,
            grounding.element_id,
            element=element,
            physical_point=physical_point,
        )
        execution = await self._executor.execute(prepared)
        self._record(
            TraceEventType.ACTION,
            scene.version,
            element_id=grounding.element_id,
            failure_code=execution.failure_code,
            payload={"op": targeted.op.value},
        )
        if not execution.success:
            return StepResult(
                success=False,
                scene=scene,
                failure_code=execution.failure_code,
                grounding=grounding,
                execution=execution,
            )

        verified = self._verifier.verify_postconditions(
            targeted.postconditions, scene, last_action=targeted
        )
        self._record(TraceEventType.VERIFICATION, scene.version)
        if not verified:
            if self._loop_breaker is not None:
                self._loop_breaker.detect((), ())
            return StepResult(
                success=False,
                scene=scene,
                failure_code=FailureCode.POSTCONDITION_UNMET.value,
                grounding=grounding,
                execution=execution,
            )
        return StepResult(
            success=True,
            scene=scene,
            grounding=grounding,
            execution=execution,
        )

    async def run(self, query: dict, action: Action, max_steps: int = 1) -> StepResult:
        """Repeat step up to max_steps until success or a non-retry failure."""
        last = StepResult(success=False, scene=self._scene)
        for _ in range(max_steps):
            last = await self.step(query, action)
            if last.success or last.skipped:
                return last
        return last

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