"""Low-overhead task-local performance tracing for hot-path decisions."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(frozen=True)
class StageSample:
    name: str
    wall_us: int
    cpu_us: int
    queue_depth: int = 0
    items: int = 0
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PerformanceSnapshot:
    samples: tuple[StageSample, ...]
    process_cpu_count: int
    max_queue_depth: int

    def total_wall_us(self, name: str) -> int:
        return sum(sample.wall_us for sample in self.samples if sample.name == name)

    def total_cpu_us(self, name: str) -> int:
        return sum(sample.cpu_us for sample in self.samples if sample.name == name)


@dataclass
class PerformanceTrace:
    """Thread-safe collector used by observer, perception and runtime layers."""

    _samples: list[StageSample] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(
        self,
        name: str,
        *,
        wall_us: int,
        cpu_us: int = 0,
        queue_depth: int = 0,
        items: int = 0,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if not name:
            raise ValueError("performance stage name must be non-empty")
        normalized = tuple(
            sorted((str(key), str(value)) for key, value in (metadata or {}).items())
        )
        sample = StageSample(
            name=name,
            wall_us=max(0, int(wall_us)),
            cpu_us=max(0, int(cpu_us)),
            queue_depth=max(0, int(queue_depth)),
            items=max(0, int(items)),
            metadata=normalized,
        )
        with self._lock:
            self._samples.append(sample)

    @contextmanager
    def stage(
        self,
        name: str,
        *,
        queue_depth: int = 0,
        items: int = 0,
        metadata: dict[str, object] | None = None,
    ) -> Iterator[None]:
        wall_started = time.perf_counter_ns()
        cpu_started = time.process_time_ns()
        try:
            yield
        finally:
            self.record(
                name,
                wall_us=(time.perf_counter_ns() - wall_started) // 1000,
                cpu_us=(time.process_time_ns() - cpu_started) // 1000,
                queue_depth=queue_depth,
                items=items,
                metadata=metadata,
            )

    def snapshot(self) -> PerformanceSnapshot:
        with self._lock:
            samples = tuple(self._samples)
        return PerformanceSnapshot(
            samples=samples,
            process_cpu_count=os.cpu_count() or 1,
            max_queue_depth=max((sample.queue_depth for sample in samples), default=0),
        )

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
