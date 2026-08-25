import pytest

from hpcu.runtime_core.performance import PerformanceTrace

pytestmark = pytest.mark.unit


def test_workload_counters_are_aggregated_without_changing_stage_metrics():
    trace = PerformanceTrace()
    trace.record("ocr_total", 10, 4)
    trace.increment_counter("pixels_processed", 100)
    trace.increment_counter("pixels_processed", 40)
    trace.increment_counter("full_frame_ocr_calls")

    snapshot = trace.snapshot()
    assert snapshot.as_dict()["ocr_total"]["count"] == 1
    assert snapshot.counters_as_dict() == {
        "full_frame_ocr_calls": 1,
        "pixels_processed": 140,
    }


def test_counter_validation_and_reset():
    trace = PerformanceTrace()
    with pytest.raises(ValueError, match="non-empty"):
        trace.increment_counter(" ")
    with pytest.raises(ValueError, match=">= 0"):
        trace.increment_counter("pixels", -1)
    trace.increment_counter("pixels", 5)
    trace.reset()
    assert trace.snapshot().counters == ()
