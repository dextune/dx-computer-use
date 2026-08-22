"""Workflow compiler — trajectory to reusable workflow.

Converts successful execution traces into deterministic, replayable
workflows that can be executed without model calls.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from hpcu.schemas.action import ActionOp, Postcondition, Precondition, PreconditionKind, PostconditionKind, RetryPolicy
from hpcu.schemas.trace import TraceEventType, TraceRecord


class WorkflowStatus(str, Enum):
    EXPERIMENTAL = "experimental"
    QUALIFIED = "qualified"
    DEPRECATED = "deprecated"


class DriftCondition(str, Enum):
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"
    STRUCTURE_CHANGED = "structure_changed"
    ELEMENT_MISSING = "element_missing"
    TEXT_CHANGED = "text_changed"


@dataclass(frozen=True)
class SelectorStrategy:
    type: str  # "playwright_role", "accessibility", "scene_query", "visual_anchor"
    role: Optional[str] = None
    name: Optional[str] = None
    text: Optional[str] = None
    within_relation: Optional[str] = None
    template_id: Optional[str] = None


@dataclass(frozen=True)
class SelectorEnsemble:
    strategies: tuple[SelectorStrategy, ...] = ()
    primary: Optional[str] = None  # strategy type to try first


@dataclass
class WorkflowStep:
    id: str
    op: ActionOp
    target_element_id: Optional[str] = None
    selector_ensemble: Optional[SelectorEnsemble] = None
    preconditions: tuple[Precondition, ...] = ()
    postconditions: tuple[Postcondition, ...] = ()
    timeout_ms: int = 5000
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    value: Optional[str] = None
    variables: dict[str, str] = field(default_factory=dict)


@dataclass
class Workflow:
    id: str
    name: str
    version: int = 1
    status: WorkflowStatus = WorkflowStatus.EXPERIMENTAL
    steps: list[WorkflowStep] = field(default_factory=list)
    expected_evidence: Optional[dict] = None
    parent_workflow_id: Optional[str] = None
    model_call_count: int = 0


class TrajectoryParameterizer:
    """Extract parameters from a trace trajectory for workflow reuse."""

    def parameterize(self, records: tuple[TraceRecord, ...]) -> list[WorkflowStep]:
        """Convert trace records into parameterized workflow steps.

        Extracts action steps from the trace, separating fixed values
        from user-specific variables.
        """
        steps: list[WorkflowStep] = []
        for rec in records:
            if rec.event_type != TraceEventType.ACTION:
                continue
            op_raw = rec.payload.get("op")
            if not op_raw:
                continue
            try:
                op = ActionOp(op_raw)
            except ValueError:
                continue
            step = WorkflowStep(
                id=f"step_{rec.seq}",
                op=op,
                target_element_id=rec.element_id,
                timeout_ms=rec.latency_us // 1000 if rec.latency_us else 5000,
            )
            steps.append(step)
        return steps


class PreconditionInferrer:
    """Infer pre/postconditions from successful trajectories."""

    def infer_preconditions(self, steps: list[WorkflowStep]) -> list[WorkflowStep]:
        """Add inferred preconditions to each step."""
        for step in steps:
            if step.target_element_id:
                step.preconditions = (
                    Precondition(
                        kind=PreconditionKind.ELEMENT_VISIBLE,
                        target=step.target_element_id,
                    ),
                )
        return steps

    def infer_postconditions(self, steps: list[WorkflowStep]) -> list[WorkflowStep]:
        """Add inferred postconditions to each step."""
        for step in steps:
            if step.target_element_id:
                step.postconditions = (
                    Postcondition(
                        kind=PostconditionKind.ELEMENT_VISIBLE,
                        target=step.target_element_id,
                    ),
                )
        return steps


class ShadowReplayEngine:
    """Replay a workflow in shadow mode to verify correctness."""

    def replay(self, workflow: Workflow, trace: tuple[TraceRecord, ...]) -> tuple[bool, int]:
        """Shadow replay a workflow against a stored trace.

        Returns (success, mismatch_count).
        """
        mismatches = 0
        trace_actions = [
            r for r in trace if r.event_type == TraceEventType.ACTION
        ]
        for i, step in enumerate(workflow.steps):
            if i >= len(trace_actions):
                mismatches += 1
                continue
            trace_rec = trace_actions[i]
            if trace_rec.element_id != step.target_element_id:
                mismatches += 1
        return (mismatches == 0, mismatches)


class WorkflowVersions:
    """Store workflow versions and rollback."""

    def __init__(self):
        self._versions: dict[str, list[Workflow]] = {}

    def store(self, workflow: Workflow) -> None:
        key = workflow.id
        if key not in self._versions:
            self._versions[key] = []
        self._versions[key].append(workflow)

    def latest(self, workflow_id: str) -> Optional[Workflow]:
        versions = self._versions.get(workflow_id, [])
        return versions[-1] if versions else None

    def rollback(self, workflow_id: str) -> Optional[Workflow]:
        versions = self._versions.get(workflow_id, [])
        if len(versions) >= 2:
            versions.pop()
            return versions[-1]
        return None

    def list_versions(self, workflow_id: str) -> list[int]:
        versions = self._versions.get(workflow_id, [])
        return [w.version for w in versions]


class DriftDetector:
    """Detect when a workflow has drifted from the current UI."""

    def detect(
        self,
        workflow: Workflow,
        current_element_ids: set[str],
    ) -> list[DriftCondition]:
        """Check each workflow step for drift against the current scene.

        Returns a list of drift conditions found.
        """
        drift: list[DriftCondition] = []
        for step in workflow.steps:
            if step.target_element_id is None:
                continue
            if step.target_element_id not in current_element_ids:
                drift.append(DriftCondition.ELEMENT_MISSING)
        return drift


class QualificationReport:
    """Report on workflow qualification status."""

    def __init__(self, workflow: Workflow):
        self.workflow_id = workflow.id
        self.version = workflow.version
        self.steps_count = len(workflow.steps)
        self.model_call_count = workflow.model_call_count
        self.shadow_replay_passed = False
        self.drift_detected: list[DriftCondition] = []
        self.qualified = False

    def qualify(self) -> bool:
        self.qualified = (
            self.shadow_replay_passed
            and len(self.drift_detected) == 0
            and self.model_call_count == 0
        )
        return self.qualified