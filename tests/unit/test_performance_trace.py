"""Tests for deterministic local stage performance tracing."""

import pytest

from hpcu.runtime_core.performance import PerformanceTrace

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self, *values: int) -> None:
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


def test_measure_aggregates_wall_cpu_and_queue_depth():
    trace = PerformanceTrace(
        wall_time_ns=_Clock(0, 8_000, 10_000, 16_000),
        cpu_time_ns=_Clock(0, 3_000, 4_000, 6_000),
    )

    with trace.measure("ocr", queue_depth=1):
        pass
    with trace.measure("ocr", queue_depth=3):
        pass

    metric = trace.snapshot().stages[0]
    assert metric.name == "ocr"
    assert metric.count == 2
    assert metric.total_us == 14
    assert metric.cpu_us == 5
    assert metric.max_us == 8
    assert metric.average_us == 7
    assert metric.max_queue_depth == 3


@pytest.mark.asyncio
async def test_async_measure_records_cancel_safe_finally_path():
    trace = PerformanceTrace(
        wall_time_ns=_Clock(0, 5_000),
        cpu_time_ns=_Clock(0, 2_000),
    )

    async with trace.measure_async("observe"):
        pass

    assert trace.snapshot().as_dict()["observe"] == {
        "count": 1,
        "total_us": 5,
        "cpu_us": 2,
        "max_us": 5,
        "average_us": 5,
        "max_queue_depth": 0,
    }


def test_reset_and_invalid_stage_name():
    trace = PerformanceTrace()
    trace.record("capture", 10, 5)
    trace.reset()
    assert trace.snapshot().stages == ()
    with pytest.raises(ValueError, match="non-empty"):
        trace.record(" ", 1)
