"""Unit tests for workflow compiler."""

import pytest

pytestmark = pytest.mark.unit

from hpcu.compiler.compiler import (
    DriftCondition,
    DriftDetector,
    PreconditionInferrer,
    QualificationReport,
    ShadowReplayEngine,
    TrajectoryParameterizer,
    Workflow,
    WorkflowStep,
    WorkflowStatus,
    WorkflowVersions,
)
from hpcu.schemas.action import ActionOp
from hpcu.schemas.trace import TraceEventType, TraceRecord


# ---- TrajectoryParameterizer ----

def test_parameterize_action_records():
    records = (
        TraceRecord(seq=1, event_type=TraceEventType.CAPTURE, timestamp_ns=1000, scene_version=1),
        TraceRecord(seq=2, event_type=TraceEventType.ACTION, timestamp_ns=2000, scene_version=1,
                    element_id="btn_1", latency_us=50000, payload={"op": "click"}),
        TraceRecord(seq=3, event_type=TraceEventType.ACTION, timestamp_ns=3000, scene_version=1,
                    element_id="btn_2", latency_us=30000, payload={"op": "click"}),
    )
    p = TrajectoryParameterizer()
    steps = p.parameterize(records)
    assert len(steps) == 2
    assert steps[0].target_element_id == "btn_1"
    assert steps[1].target_element_id == "btn_2"
    assert steps[0].op == ActionOp.CLICK


def test_parameterize_preserves_type_op():
    records = (
        TraceRecord(
            seq=1, event_type=TraceEventType.ACTION, timestamp_ns=1, scene_version=1,
            element_id="field", payload={"op": "type"},
        ),
    )
    steps = TrajectoryParameterizer().parameterize(records)
    assert len(steps) == 1
    assert steps[0].op == ActionOp.TYPE
    assert steps[0].op is not ActionOp.CLICK


def test_parameterize_skips_action_without_op():
    records = (
        TraceRecord(seq=1, event_type=TraceEventType.ACTION, timestamp_ns=1, scene_version=1,
                    element_id="btn"),
    )
    steps = TrajectoryParameterizer().parameterize(records)
    assert steps == []


def test_parameterize_no_actions():
    records = (
        TraceRecord(seq=1, event_type=TraceEventType.CAPTURE, timestamp_ns=1000, scene_version=1),
    )
    p = TrajectoryParameterizer()
    steps = p.parameterize(records)
    assert len(steps) == 0


# ---- PreconditionInferrer ----

def test_infer_preconditions():
    steps = [
        WorkflowStep(id="s1", op=ActionOp.CLICK, target_element_id="el_a"),
        WorkflowStep(id="s2", op=ActionOp.INVOKE, target_element_id="el_b"),
    ]
    inferrer = PreconditionInferrer()
    result = inferrer.infer_preconditions(steps)
    assert len(result[0].preconditions) == 1
    assert result[0].preconditions[0].target == "el_a"
    assert len(result[1].preconditions) == 1


def test_infer_postconditions():
    steps = [
        WorkflowStep(id="s1", op=ActionOp.CLICK, target_element_id="el_a"),
    ]
    inferrer = PreconditionInferrer()
    result = inferrer.infer_postconditions(steps)
    assert len(result[0].postconditions) == 1
    assert result[0].postconditions[0].target == "el_a"


# ---- ShadowReplayEngine ----

def test_shadow_replay_match():
    workflow = Workflow(id="wf_1", name="test")
    workflow.steps = [
        WorkflowStep(id="s1", op=ActionOp.CLICK, target_element_id="btn_a"),
        WorkflowStep(id="s2", op=ActionOp.CLICK, target_element_id="btn_b"),
    ]
    trace = (
        TraceRecord(seq=1, event_type=TraceEventType.ACTION, timestamp_ns=1000, scene_version=1,
                    element_id="btn_a"),
        TraceRecord(seq=2, event_type=TraceEventType.ACTION, timestamp_ns=2000, scene_version=1,
                    element_id="btn_b"),
    )
    engine = ShadowReplayEngine()
    success, mismatches = engine.replay(workflow, trace)
    assert success is True
    assert mismatches == 0


def test_shadow_replay_mismatch():
    workflow = Workflow(id="wf_1", name="test")
    workflow.steps = [
        WorkflowStep(id="s1", op=ActionOp.CLICK, target_element_id="btn_a"),
    ]
    trace = (
        TraceRecord(seq=1, event_type=TraceEventType.ACTION, timestamp_ns=1000, scene_version=1,
                    element_id="btn_diff"),
    )
    engine = ShadowReplayEngine()
    success, mismatches = engine.replay(workflow, trace)
    assert success is False
    assert mismatches == 1


def test_shadow_replay_length_mismatch():
    workflow = Workflow(id="wf_1", name="test")
    workflow.steps = [
        WorkflowStep(id="s1", op=ActionOp.CLICK, target_element_id="btn_a"),
        WorkflowStep(id="s2", op=ActionOp.CLICK, target_element_id="btn_b"),
    ]
    trace = (
        TraceRecord(seq=1, event_type=TraceEventType.ACTION, timestamp_ns=1000, scene_version=1,
                    element_id="btn_a"),
    )
    engine = ShadowReplayEngine()
    success, mismatches = engine.replay(workflow, trace)
    assert success is False
    assert mismatches == 1


# ---- WorkflowVersionManager ----

def test_version_manager_store_and_latest():
    m = WorkflowVersions()
    wf = Workflow(id="wf_1", name="test", version=1)
    m.store(wf)
    assert m.latest("wf_1") is wf


def test_version_manager_rollback():
    m = WorkflowVersions()
    wf1 = Workflow(id="wf_1", name="test", version=1)
    wf2 = Workflow(id="wf_1", name="test", version=2)
    m.store(wf1)
    m.store(wf2)
    rolled = m.rollback("wf_1")
    assert rolled is wf1
    assert m.latest("wf_1") is wf1


def test_version_manager_list_versions():
    m = WorkflowVersions()
    m.store(Workflow(id="wf_1", name="test", version=1))
    m.store(Workflow(id="wf_1", name="test", version=2))
    m.store(Workflow(id="wf_1", name="test", version=3))
    versions = m.list_versions("wf_1")
    assert versions == [1, 2, 3]


# ---- DriftDetector ----

def test_drift_detector_no_drift():
    detector = DriftDetector()
    workflow = Workflow(id="wf_1", name="test")
    workflow.steps = [
        WorkflowStep(id="s1", op=ActionOp.CLICK, target_element_id="el_a"),
        WorkflowStep(id="s2", op=ActionOp.CLICK, target_element_id="el_b"),
    ]
    current = {"el_a", "el_b", "el_c"}
    drift = detector.detect(workflow, current)
    assert len(drift) == 0


def test_drift_detector_element_missing():
    detector = DriftDetector()
    workflow = Workflow(id="wf_1", name="test")
    workflow.steps = [
        WorkflowStep(id="s1", op=ActionOp.CLICK, target_element_id="el_a"),
        WorkflowStep(id="s2", op=ActionOp.CLICK, target_element_id="el_missing"),
    ]
    current = {"el_a", "el_c"}
    drift = detector.detect(workflow, current)
    assert DriftCondition.ELEMENT_MISSING in drift


# ---- QualificationReport ----

def test_qualification_report_passes():
    workflow = Workflow(id="wf_1", name="test", model_call_count=0)
    report = QualificationReport(workflow)
    report.shadow_replay_passed = True
    report.drift_detected = []
    assert report.qualify() is True
    assert report.qualified is True


def test_qualification_report_fails_on_model_calls():
    workflow = Workflow(id="wf_1", name="test", model_call_count=3)
    report = QualificationReport(workflow)
    report.shadow_replay_passed = True
    report.drift_detected = []
    assert report.qualify() is False


def test_qualification_report_fails_on_drift():
    workflow = Workflow(id="wf_1", name="test", model_call_count=0)
    report = QualificationReport(workflow)
    report.shadow_replay_passed = True
    report.drift_detected = [DriftCondition.ELEMENT_MISSING]
    assert report.qualify() is False


# ---- Workflow fields ----

def test_workflow_defaults():
    wf = Workflow(id="wf_1", name="test workflow")
    assert wf.version == 1
    assert wf.status == WorkflowStatus.EXPERIMENTAL
    assert len(wf.steps) == 0
    assert wf.model_call_count == 0