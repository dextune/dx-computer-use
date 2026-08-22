"""Unit tests for trace recorder, storage, and replay."""

import pytest

pytestmark = pytest.mark.unit

from hpcu.schemas.trace import TraceEventType, TraceRecord
from hpcu.trace.recorder import TraceRecorder
from hpcu.trace.storage import TraceStorage
from hpcu.trace.replay import ReplayEngine


# ---- TraceRecorder ----

def test_recorder_monotonic_seq():
    r = TraceRecorder()
    r1 = r.append(TraceEventType.CAPTURE, scene_version=1)
    r2 = r.append(TraceEventType.ACTION, scene_version=1)
    assert r1.seq == 1
    assert r2.seq == 2
    assert r.seq == 2


def test_recorder_model_call_count():
    r = TraceRecorder()
    r.append(TraceEventType.MODEL_CALL, scene_version=1, is_model_call=True)
    r.append(TraceEventType.ACTION, scene_version=1)
    r.append(TraceEventType.MODEL_CALL, scene_version=2, is_model_call=True)
    assert r.model_call_count == 2


def test_recorder_records():
    r = TraceRecorder()
    r.append(TraceEventType.CAPTURE, scene_version=1)
    r.append(TraceEventType.ACTION, scene_version=2)
    records = r.records()
    assert len(records) == 2
    assert records[0].event_type == TraceEventType.CAPTURE
    assert records[1].event_type == TraceEventType.ACTION


def test_recorder_reset():
    r = TraceRecorder()
    r.append(TraceEventType.CAPTURE, scene_version=1, is_model_call=True)
    r.reset()
    assert r.seq == 0
    assert r.model_call_count == 0
    assert len(r.records()) == 0


def test_recorder_is_stale():
    r = TraceRecorder()
    r.append(TraceEventType.CAPTURE, scene_version=3)
    assert r.is_stale(2) is True
    assert r.is_stale(3) is False
    assert r.is_stale(4) is False


def test_recorder_append_with_failure_code():
    r = TraceRecorder()
    rec = r.append(TraceEventType.ACTION, scene_version=1,
                   failure_code="stale_decision")
    assert rec.failure_code == "stale_decision"


# ---- TraceStorage ----

def test_storage_insert_and_load():
    s = TraceStorage(":memory:")
    rec = TraceRecord(seq=1, event_type=TraceEventType.CAPTURE,
                      timestamp_ns=1000, scene_version=1)
    s.insert(rec)
    records = s.load_all()
    assert len(records) == 1
    assert records[0].seq == 1
    s.close()


def test_storage_count():
    s = TraceStorage(":memory:")
    assert s.count() == 0
    s.insert(TraceRecord(seq=1, event_type=TraceEventType.CAPTURE,
                         timestamp_ns=1000, scene_version=1))
    s.insert(TraceRecord(seq=2, event_type=TraceEventType.ACTION,
                         timestamp_ns=2000, scene_version=1))
    assert s.count() == 2
    s.close()


def test_storage_load_since():
    s = TraceStorage(":memory:")
    for i in range(5):
        s.insert(TraceRecord(seq=i + 1, event_type=TraceEventType.CAPTURE,
                             timestamp_ns=i * 1000, scene_version=1))
    recent = s.load_since(2)
    assert len(recent) == 3
    assert recent[0].seq == 3
    s.close()


def test_storage_payload():
    s = TraceStorage(":memory:")
    rec = TraceRecord(seq=1, event_type=TraceEventType.ACTION,
                      timestamp_ns=1000, scene_version=1,
                      payload={"key": "value"})
    s.insert(rec)
    loaded = s.load_all()[0]
    assert loaded.payload == {"key": "value"}
    s.close()


def test_storage_element_id():
    s = TraceStorage(":memory:")
    rec = TraceRecord(seq=1, event_type=TraceEventType.ACTION,
                      timestamp_ns=1000, scene_version=1,
                      element_id="btn_1")
    s.insert(rec)
    loaded = s.load_all()[0]
    assert loaded.element_id == "btn_1"
    s.close()


# ---- ReplayEngine ----

def test_replay_next_record():
    records = (
        TraceRecord(seq=1, event_type=TraceEventType.CAPTURE, timestamp_ns=1000, scene_version=1),
        TraceRecord(seq=2, event_type=TraceEventType.ACTION, timestamp_ns=2000, scene_version=1),
    )
    engine = ReplayEngine(records)
    assert engine.next_record().seq == 1
    assert engine.next_record().seq == 2
    assert engine.next_record() is None


def test_replay_check_stale():
    records = (
        TraceRecord(seq=1, event_type=TraceEventType.ACTION, timestamp_ns=1000, scene_version=5),
    )
    engine = ReplayEngine(records)
    # current scene is behind expected
    assert engine.check_stale(3) is True
    assert engine.stale_count == 1
    # current scene matches
    assert engine.check_stale(5) is False


def test_replay_validate_trajectory_match():
    records = (
        TraceRecord(seq=1, event_type=TraceEventType.CAPTURE, timestamp_ns=1000, scene_version=1),
        TraceRecord(seq=2, event_type=TraceEventType.ACTION, timestamp_ns=2000, scene_version=1),
    )
    engine = ReplayEngine(records)
    valid, mismatches = engine.validate_trajectory(records)
    assert valid is True
    assert mismatches == 0


def test_replay_validate_trajectory_mismatch():
    original = (
        TraceRecord(seq=1, event_type=TraceEventType.CAPTURE, timestamp_ns=1000, scene_version=1),
    )
    actual = (
        TraceRecord(seq=1, event_type=TraceEventType.ACTION, timestamp_ns=1000, scene_version=1),
    )
    engine = ReplayEngine(original)
    valid, mismatches = engine.validate_trajectory(actual)
    assert valid is False
    assert mismatches > 0


def test_replay_validate_trajectory_length_mismatch():
    original = (
        TraceRecord(seq=1, event_type=TraceEventType.CAPTURE, timestamp_ns=1000, scene_version=1),
        TraceRecord(seq=2, event_type=TraceEventType.ACTION, timestamp_ns=2000, scene_version=1),
    )
    actual = (
        TraceRecord(seq=1, event_type=TraceEventType.CAPTURE, timestamp_ns=1000, scene_version=1),
    )
    engine = ReplayEngine(original)
    valid, mismatches = engine.validate_trajectory(actual)
    assert valid is False
    assert mismatches > 0