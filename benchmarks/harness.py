"""Benchmark harness for task, model, and local-stage measurements."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hpcu.runtime_core.performance import PerformanceSnapshot


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
    stage_total_us: dict[str, int] = field(default_factory=dict)
    stage_cpu_us: dict[str, int] = field(default_factory=dict)
    stage_count: dict[str, int] = field(default_factory=dict)
    stage_max_queue_depth: dict[str, int] = field(default_factory=dict)


class BenchmarkHarness:
    """Lightweight collector for runtime and local resource performance."""

    def __init__(self, time_ns: Callable[[], int] | None = None):
        self._metrics = BenchmarkMetrics()
        self._time_ns = time_ns or time.time_ns
        self._start_ns: int = 0
        self._step_start_ns: int = 0

    def start(self) -> None:
        self._start_ns = self._time_ns()
        self._metrics = BenchmarkMetrics()

    def step_start(self) -> None:
        self._step_start_ns = self._time_ns()

    def step_end(self) -> None:
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

    def record_performance(self, snapshot: PerformanceSnapshot) -> None:
        """Merge one observer/perception stage snapshot into benchmark totals."""
        for stage in snapshot.stages:
            self._metrics.stage_total_us[stage.name] = stage.total_us
            self._metrics.stage_cpu_us[stage.name] = stage.cpu_us
            self._metrics.stage_count[stage.name] = stage.count
            self._metrics.stage_max_queue_depth[stage.name] = (
                stage.max_queue_depth
            )

    def mark_success(self) -> None:
        self._metrics.success = True

    def stop(self) -> BenchmarkMetrics:
        self._metrics.total_time_us = (
            self._time_ns() - self._start_ns
        ) // 1000
        return self._metrics

    def reset(self) -> None:
        self._metrics = BenchmarkMetrics()
        self._start_ns = 0
        self._step_start_ns = 0


def capture_dataset(
    stats_list: list[dict[str, Any]],
    output_path: Path,
    *,
    provider: str = "",
    model: str = "",
) -> None:
    """Write a provider-neutral run dataset from per-case statistics."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cases = []
    for stats in stats_list:
        cases.append(
            {
                "case_id": str(stats.get("case_id", "unknown")),
                "success": bool(stats.get("success", False)),
                "runner_success": bool(stats.get("runner_success", False)),
                "verified_success": bool(
                    stats.get("verified_success", False)
                ),
                "actions": stats.get("actions", []),
                "attempts": int(stats.get("attempts", 0)),
                "max_attempts": int(stats.get("max_attempts", 0)),
                "elapsed_ms": int(stats.get("elapsed_ms", 0)),
                "final_scene_version": int(
                    stats.get("final_scene_version", 0)
                ),
                "final_frame_id": str(stats.get("final_frame_id", "")),
                "evidence_scene_version": int(
                    stats.get("evidence_scene_version", 0)
                ),
                "evidence_frame_id": str(
                    stats.get("evidence_frame_id", "")
                ),
                "evidence_tokens": stats.get("evidence_tokens", []),
                "evidence_element_ids": stats.get(
                    "evidence_element_ids", []
                ),
                "decision_diagnostics": stats.get(
                    "decision_diagnostics", []
                ),
                "attempts_detail": stats.get("attempts_detail", []),
                "outcome": str(stats.get("outcome", "")),
                "evidence_status": str(
                    stats.get("evidence_status", "not_evaluated")
                ),
                "provider": str(stats.get("provider", provider)),
                "model_call_count": int(
                    stats.get("model_call_count", 0)
                ),
                "model_error_count": int(
                    stats.get("model_error_count", 0)
                ),
                "model_tokens": int(stats.get("model_tokens", 0)),
                "model": str(stats.get("model", model)),
                "compile_call_count": int(
                    stats.get("compile_call_count", 0)
                ),
                "grounding_call_count": int(
                    stats.get("grounding_call_count", 0)
                ),
                "failure_code": str(stats.get("failure_code", "")),
                "failure": str(stats.get("failure", "")),
                "artifact_manifest": stats.get("artifact_manifest", []),
                "performance": stats.get("performance", {}),
            }
        )
    totals = {
        "cases": len(cases),
        "successes": sum(1 for case in cases if case["success"]),
        "action_count": sum(len(case["actions"]) for case in cases),
        "model_call_count": sum(
            case["model_call_count"] for case in cases
        ),
        "model_error_count": sum(
            case["model_error_count"] for case in cases
        ),
        "model_tokens": sum(case["model_tokens"] for case in cases),
        "compile_call_count": sum(
            case["compile_call_count"] for case in cases
        ),
        "grounding_call_count": sum(
            case["grounding_call_count"] for case in cases
        ),
    }
    payload = {
        "provider": provider,
        "model": model,
        "cases": cases,
        "totals": totals,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
