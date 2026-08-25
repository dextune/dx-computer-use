"""Low-overhead stage profiling for local runtime hot paths."""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class StageMetric:
    """Aggregated wall/CPU measurements for one named runtime stage."""

    name: str
    count: int
    total_us: int
    cpu_us: int
    max_us: int
    max_queue_depth: int = 0

    @property
    def average_us(self) -> int:
        return self.total_us // self.count if self.count else 0


@dataclass(frozen=True)
class CounterMetric:
    """Aggregated non-time workload count, such as processed pixels."""

    name: str
    value: int


@dataclass(frozen=True)
class PerformanceSnapshot:
    """Immutable deterministic snapshot of measured stages and workload counters."""

    stages: tuple[StageMetric, ...]
    counters: tuple[CounterMetric, ...] = ()

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {
            stage.name: {
                "count": stage.count,
                "total_us": stage.total_us,
                "cpu_us": stage.cpu_us,
                "max_us": stage.max_us,
                "average_us": stage.average_us,
                "max_queue_depth": stage.max_queue_depth,
            }
            for stage in self.stages
        }

    def counters_as_dict(self) -> dict[str, int]:
        return {counter.name: counter.value for counter in self.counters}


@dataclass
class _MutableStage:
    count: int = 0
    total_us: int = 0
    cpu_us: int = 0
    max_us: int = 0
    max_queue_depth: int = 0


class PerformanceTrace:
    """Thread-safe profiler shared by async orchestration and CPU workers."""

    def __init__(
        self,
        *,
        wall_time_ns=None,
        cpu_time_ns=None,
    ) -> None:
        self._wall_time_ns = wall_time_ns or time.perf_counter_ns
        self._cpu_time_ns = cpu_time_ns or time.process_time_ns
        self._lock = threading.Lock()
        self._stages: dict[str, _MutableStage] = {}
        self._counters: dict[str, int] = {}

    def record(
        self,
        name: str,
        elapsed_us: int,
        cpu_us: int = 0,
        *,
        queue_depth: int = 0,
    ) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("performance stage name must be non-empty")
        elapsed = max(0, int(elapsed_us))
        cpu = max(0, int(cpu_us))
        depth = max(0, int(queue_depth))
        with self._lock:
            stage = self._stages.setdefault(normalized, _MutableStage())
            stage.count += 1
            stage.total_us += elapsed
            stage.cpu_us += cpu
            stage.max_us = max(stage.max_us, elapsed)
            stage.max_queue_depth = max(stage.max_queue_depth, depth)

    def increment_counter(self, name: str, value: int = 1) -> None:
        """Increment a deterministic non-time workload counter."""

        normalized = name.strip()
        if not normalized:
            raise ValueError("performance counter name must be non-empty")
        increment = int(value)
        if increment < 0:
            raise ValueError("performance counter increment must be >= 0")
        with self._lock:
            self._counters[normalized] = self._counters.get(normalized, 0) + increment

    @contextmanager
    def measure(
        self,
        name: str,
        *,
        queue_depth: int = 0,
    ) -> Iterator[None]:
        wall_started = self._wall_time_ns()
        cpu_started = self._cpu_time_ns()
        try:
            yield
        finally:
            self.record(
                name,
                (self._wall_time_ns() - wall_started) // 1000,
                (self._cpu_time_ns() - cpu_started) // 1000,
                queue_depth=queue_depth,
            )

    @asynccontextmanager
    async def measure_async(
        self,
        name: str,
        *,
        queue_depth: int = 0,
    ) -> AsyncIterator[None]:
        wall_started = self._wall_time_ns()
        cpu_started = self._cpu_time_ns()
        try:
            yield
        finally:
            self.record(
                name,
                (self._wall_time_ns() - wall_started) // 1000,
                (self._cpu_time_ns() - cpu_started) // 1000,
                queue_depth=queue_depth,
            )

    def snapshot(self) -> PerformanceSnapshot:
        with self._lock:
            stages = tuple(
                StageMetric(
                    name=name,
                    count=value.count,
                    total_us=value.total_us,
                    cpu_us=value.cpu_us,
                    max_us=value.max_us,
                    max_queue_depth=value.max_queue_depth,
                )
                for name, value in sorted(self._stages.items())
            )
            counters = tuple(
                CounterMetric(name=name, value=value)
                for name, value in sorted(self._counters.items())
            )
        return PerformanceSnapshot(stages=stages, counters=counters)

    def reset(self) -> None:
        with self._lock:
            self._stages.clear()
            self._counters.clear()
