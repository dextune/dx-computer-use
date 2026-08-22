"""Benchmark harness — metrics collection stub.

Collects latency, model call count, and success rate for
benchmarking different execution strategies.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class BenchmarkMetrics:
    """Collected metrics for a single benchmark run."""

    total_time_us: int = 0
    model_call_count: int = 0
    action_count: int = 0
    success: bool = False
    stale_rejections: int = 0
    recovery_count: int = 0
    capture_count: int = 0
    scene_update_count: int = 0
    verification_count: int = 0
    step_latencies_us: list[int] = field(default_factory=list)


class BenchmarkHarness:
    """Lightweight benchmark harness for measuring runtime performance.

    Usage:
        harness = BenchmarkHarness()
        harness.start()
        # ... run the task ...
        metrics = harness.stop()

    For testing, inject a fake time_ns callable to avoid real sleeps:

        harness = BenchmarkHarness(time_ns=lambda: 0)
    """

    def __init__(self, time_ns: Callable[[], int] | None = None):
        self._metrics = BenchmarkMetrics()
        self._time_ns = time_ns or time.time_ns
        self._start_ns: int = 0
        self._step_start_ns: int = 0

    def start(self) -> None:
        """Start the benchmark timer."""
        self._start_ns = self._time_ns()
        self._metrics = BenchmarkMetrics()

    def step_start(self) -> None:
        """Mark the start of a single step."""
        self._step_start_ns = self._time_ns()

    def step_end(self) -> None:
        """Mark the end of a single step and record its latency."""
        latency_us = (self._time_ns() - self._step_start_ns) // 1000
        self._metrics.step_latencies_us.append(latency_us)

    def record_model_call(self) -> None:
        self._metrics.model_call_count += 1

    def record_action(self) -> None:
        self._metrics.action_count += 1

    def record_stale_rejection(self) -> None:
        self._metrics.stale_rejections += 1

    def record_recovery(self) -> None:
        self._metrics.recovery_count += 1

    def record_capture(self) -> None:
        self._metrics.capture_count += 1

    def record_scene_update(self) -> None:
        self._metrics.scene_update_count += 1

    def record_verification(self) -> None:
        self._metrics.verification_count += 1

    def mark_success(self) -> None:
        self._metrics.success = True

    def stop(self) -> BenchmarkMetrics:
        """Stop the benchmark and return collected metrics."""
        self._metrics.total_time_us = (self._time_ns() - self._start_ns) // 1000
        return self._metrics

    def reset(self) -> None:
        """Reset all metrics to zero."""
        self._metrics = BenchmarkMetrics()
        self._start_ns = 0
        self._step_start_ns = 0