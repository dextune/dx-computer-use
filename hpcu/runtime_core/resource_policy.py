"""Configuration-driven local CPU resource allocation."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalResourcePolicy:
    """Bound local workers while reserving capacity for capture and input."""

    cpu_count: int
    reserve_cores: int
    perception_workers: int
    perception_queue_depth: int

    @classmethod
    def from_config(
        cls,
        config: dict | None = None,
        *,
        cpu_count: int | None = None,
    ) -> "LocalResourcePolicy":
        runtime = config or {}
        performance = runtime.get("performance", {})
        detected = int(cpu_count if cpu_count is not None else os.cpu_count() or 1)
        detected = max(1, detected)
        reserve = max(0, int(performance.get("local_reserve_cores", 1)))
        available = max(1, detected - min(reserve, detected - 1))
        configured_max = max(
            1,
            int(performance.get("max_perception_workers", 4)),
        )
        workers = min(available, configured_max)
        queue_multiplier = max(
            1,
            int(performance.get("perception_queue_multiplier", 2)),
        )
        configured_queue = int(
            performance.get("max_perception_queue_depth", 0)
        )
        queue_depth = (
            max(workers, configured_queue)
            if configured_queue > 0
            else workers * queue_multiplier
        )
        return cls(
            cpu_count=detected,
            reserve_cores=min(reserve, detected - 1),
            perception_workers=workers,
            perception_queue_depth=max(workers, queue_depth),
        )
