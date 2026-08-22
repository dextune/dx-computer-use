"""Unit tests for schema DTOs."""

import pytest

from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace, ScreenPoint
from hpcu.schemas.ui_element import UIElement, ElementState, ElementRelations, ElementSource
from hpcu.schemas.scene import Scene, SceneDelta, FrameHandle
from hpcu.schemas.action import (
    Action, ActionOp, ActionTarget, Precondition, Postcondition,
    PreconditionKind, PostconditionKind, RetryPolicy,
)
from hpcu.schemas.evidence import EvidenceContract, EvidenceCondition, EvidenceKind
from hpcu.schemas.trace import TraceRecord, TraceEventType

pytestmark = pytest.mark.unit


# ---- failure_codes ----

def test_failure_code_values_are_unique():
    codes = list(FailureCode)
    values = [c.value for c in codes]
    assert len(values) == len(set(values))


def test_capture_permission_denied_exists():
    assert FailureCode.CAPTURE_PERMISSION_DENIED == "capture_permission_denied"


# ---- capability ----

def test_capability_enum_values():
    assert Capability.SUPPORTED == "supported"
    assert Capability.DEGRADED == "degraded"
    assert Capability.UNSUPPORTED == "unsupported"


# ---- coordinates ----

def test_bounding_box_center():
    bbox = BoundingBox(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=0, y=0, width=100, height=50)
    cx, cy = bbox.center
    assert cx == 50.0
    assert cy == 25.0


def test_bounding_box_contains():
    bbox = BoundingBox(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=10, y=10, width=100, height=100)
    assert bbox.contains(50, 50)
    assert not bbox.contains(5, 50)
    assert not bbox.contains(200, 50)


def test_bounding_box_area():
    bbox = BoundingBox(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=0, y=0, width=300, height=200)
    assert bbox.area == 60000.0


def test_screen_point_immutable():
    p = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=1.0, y=2.0)
    with pytest.raises(Exception):
        p.x = 3.0  # type: ignore


# ---- ui_element ----

def test_ui_element_defaults():
    el = UIElement(id="elem_1", scene_version=1)
    assert el.id == "elem_1"
    assert el.role == "unknown"
    assert el.state.visible is True
    assert el.state.enabled is True


def test_ui_element_with_sources():
    el = UIElement(
        id="btn_1",
        scene_version=5,
        role="button",
        name="Submit",
        sources=(
            ElementSource(type="dom", ref="locator:role=button", confidence=0.99),
            ElementSource(type="ocr", text="Submit", confidence=0.96),
        ),
    )
    assert len(el.sources) == 2
    assert el.sources[0].type == "dom"


# ---- scene ----

def test_scene_delta_monotonic():
    d = SceneDelta(base_version=1, new_version=2)
    assert d.new_version == 2


def test_scene_delta_non_monotonic_raises():
    with pytest.raises(ValueError):
        SceneDelta(base_version=3, new_version=2)


def test_scene_get():
    el = UIElement(id="a", scene_version=1)
    scene = Scene(version=1, elements={"a": el})
    assert scene.get("a") is el
    assert scene.get("b") is None


def test_scene_contains():
    scene = Scene(version=1, elements={"a": UIElement(id="a", scene_version=1)})
    assert "a" in scene
    assert "b" not in scene


def test_frame_handle_no_bytes():
    fh = FrameHandle(
        shm_id="shm_1",
        width=1920, height=1080, stride=7680,
        pixel_format="BGRA", timestamp_ns=1, space="screen_physical_px",
    )
    assert fh.shm_id == "shm_1"
    assert fh.space is CoordinateSpace.SCREEN_PHYSICAL_PX
    assert not hasattr(fh, "data")
    assert not hasattr(fh, "bytes")


def test_frame_handle_rejects_unknown_space():
    with pytest.raises(ValueError):
        FrameHandle(
            shm_id="shm_1",
            width=1, height=1, stride=4,
            pixel_format="BGRA", timestamp_ns=1, space="oops",
        )


# ---- action ----

def test_action_immutable():
    a = Action(id="s1", op=ActionOp.CLICK, target=ActionTarget(element_id="btn_1"))
    with pytest.raises(Exception):
        a.op = ActionOp.INVOKE  # type: ignore


def test_action_defaults():
    a = Action(id="s1", op=ActionOp.CLICK)
    assert a.timeout_ms == 5000
    assert a.retry.max_attempts == 2


def test_precondition_factory():
    pc = Precondition(kind=PreconditionKind.ELEMENT_VISIBLE, target="el_1")
    assert pc.kind == PreconditionKind.ELEMENT_VISIBLE


# ---- evidence ----

def test_evidence_contract_requires_condition():
    with pytest.raises(ValueError):
        EvidenceContract()


def test_evidence_contract_valid():
    ec = EvidenceContract(
        all=(EvidenceCondition(kind=EvidenceKind.ELEMENT_VISIBLE, target="btn_ok"),)
    )
    assert len(ec.all) == 1


# ---- trace ----

def test_trace_record_immutable():
    r = TraceRecord(seq=1, event_type=TraceEventType.CAPTURE, timestamp_ns=1000, scene_version=1)
    assert r.seq == 1
    with pytest.raises(Exception):
        r.seq = 2  # type: ignore