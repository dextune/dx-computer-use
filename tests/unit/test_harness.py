"""Unit tests for benchmark harness."""

import json

import pytest

from benchmarks.harness import BenchmarkHarness, capture_dataset

pytestmark = pytest.mark.unit


def _fake_time_counter(start: int = 0, step: int = 100_000_000):
    """Return a callable that advances by `step` ns each call (100 ms default)."""
    def _counter():
        nonlocal start
        start += step
        return start
    return _counter


def test_harness_start_stop():
    """start→stop should produce non-zero elapsed time."""
    t = _fake_time_counter(0, 10_000_000)  # 10 ms per call
    h = BenchmarkHarness(time_ns=t)
    h.start()
    m = h.stop()
    assert m.total_time_us > 0


def test_harness_records_model_calls():
    h = BenchmarkHarness(time_ns=lambda: 0)
    h.start()
    h.record_model_call()
    h.record_model_call()
    m = h.stop()
    assert m.model_call_count == 2


def test_harness_records_actions():
    h = BenchmarkHarness(time_ns=lambda: 0)
    h.start()
    h.record_action()
    h.record_action()
    h.record_action()
    m = h.stop()
    assert m.action_count == 3


def test_harness_step_latency():
    """Step latency is computed from time_ns deltas."""
    t = _fake_time_counter(0, 1_000_000)  # 1 ms per call
    h = BenchmarkHarness(time_ns=t)
    h.start()
    # step_start: t=1, step_end: t=2 → latency = 1000 us
    h.step_start()
    h.step_end()
    # step_start: t=3, step_end: t=4 → latency = 1000 us
    h.step_start()
    h.step_end()
    m = h.stop()
    assert len(m.step_latencies_us) == 2
    assert m.step_latencies_us[0] == 1000
    assert m.step_latencies_us[1] == 1000


def test_harness_stale_rejections():
    h = BenchmarkHarness(time_ns=lambda: 0)
    h.start()
    h.record_stale_rejection()
    m = h.stop()
    assert m.stale_rejections == 1


def test_harness_reset():
    h = BenchmarkHarness(time_ns=lambda: 0)
    h.start()
    h.record_model_call()
    h.record_action()
    h.stop()
    h.reset()
    h.start()
    m = h.stop()
    assert m.model_call_count == 0
    assert m.action_count == 0


def test_harness_success_flag():
    h = BenchmarkHarness(time_ns=lambda: 0)
    h.start()
    h.mark_success()
    m = h.stop()
    assert m.success is True


def test_harness_not_success_by_default():
    h = BenchmarkHarness(time_ns=lambda: 0)
    h.start()
    m = h.stop()
    assert m.success is False


def test_capture_dataset_preserves_bounded_attempt_evidence(tmp_path):
    output = tmp_path / "run-dataset.json"
    capture_dataset(
        [
            {
                "case_id": "bounded-failure",
                "success": False,
                "attempts": 5,
                "max_attempts": 5,
                "outcome": "verification_failed",
                "evidence_status": "unsatisfied",
                "attempts_detail": [{"attempt": number} for number in range(1, 6)],
                "provider": "configured-provider",
                "model": "configured-model",
            }
        ],
        output,
        provider="configured-provider",
        model="configured-model",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    case = payload["cases"][0]
    assert case["attempts"] == 5
    assert case["max_attempts"] == 5
    assert [row["attempt"] for row in case["attempts_detail"]] == [1, 2, 3, 4, 5]
    assert case["evidence_status"] == "unsatisfied"
    assert payload["provider"] == "configured-provider"
