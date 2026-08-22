"""Test harness dataset capture — collects per-test evidence.

Writes a run-dataset.json to {SCRATCH} after each test session.
Each entry contains: case_id, success, actions, model_call_count,
model_error_count, model_tokens, provider, model, compile_call_count,
grounding_call_count, failure, and aggregated totals.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

SCRATCH = Path(
    os.environ.get("HPCU_SCRATCH", "/tmp/grok-goal-f73a6e2ef612/implementer")
)


class DatasetCollector:
    """Collects per-case stats across the test session."""

    def __init__(self):
        self.entries: list[dict[str, Any]] = []

    def record_case(self, stats: dict[str, Any]) -> None:
        required = {
            "case_id": str(stats.get("case_id", "unknown")),
            "success": bool(stats.get("success", False)),
            "actions": stats.get("actions", []),
            "provider": str(stats.get("provider", "")),
            "model_call_count": int(stats.get("model_call_count", 0)),
            "model_error_count": int(stats.get("model_error_count", 0)),
            "model_tokens": int(stats.get("model_tokens", 0)),
            "model": str(stats.get("model", "")),
            "compile_call_count": int(stats.get("compile_call_count", 0)),
            "grounding_call_count": int(stats.get("grounding_call_count", 0)),
            "failure": str(stats.get("failure", "")),
            "runner_success": bool(stats.get("runner_success", False)),
            "verified_success": bool(stats.get("verified_success", False)),
            "human_confirmed": bool(stats.get("human_confirmed", False)),
            "outcome": str(stats.get("outcome", "")),
            "failure_code": str(stats.get("failure_code", "")),
            "challenge_kind": str(stats.get("challenge_kind", "")),
            "handoff_required": bool(stats.get("handoff_required", False)),
            "final_scene_version": int(stats.get("final_scene_version", 0)),
            "final_frame_id": str(stats.get("final_frame_id", "")),
            "evidence_scene_version": int(stats.get("evidence_scene_version", 0)),
            "evidence_frame_id": str(stats.get("evidence_frame_id", "")),
            "evidence_tokens": stats.get("evidence_tokens", []),
            "evidence_element_ids": stats.get("evidence_element_ids", []),
            "decision_diagnostics": stats.get("decision_diagnostics", []),
            "attempts": int(stats.get("attempts", 0)),
            "max_attempts": int(stats.get("max_attempts", 0)),
            "elapsed_ms": int(stats.get("elapsed_ms", 0)),
            "attempts_detail": stats.get("attempts_detail", []),
            "ai_calls": stats.get("ai_calls", []),
            "evidence_status": str(stats.get("evidence_status", "not_evaluated")),
        }
        self.entries.append(required)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        totals = {
            "cases": len(self.entries),
            "successes": sum(1 for e in self.entries if e["success"]),
            "action_count": sum(len(e["actions"]) for e in self.entries),
            "model_call_count": sum(e["model_call_count"] for e in self.entries),
            "model_error_count": sum(e["model_error_count"] for e in self.entries),
            "model_tokens": sum(e["model_tokens"] for e in self.entries),
            "compile_call_count": sum(e["compile_call_count"] for e in self.entries),
            "grounding_call_count": sum(
                e["grounding_call_count"] for e in self.entries
            ),
        }
        payload = {
            "provider": self.entries[0].get("provider", "") if self.entries else "",
            "model": self.entries[0].get("model", "") if self.entries else "",
            "cases": self.entries,
            "totals": totals,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


_collector = DatasetCollector()


@pytest.fixture(scope="session")
def dataset_collector() -> DatasetCollector:
    return _collector


@pytest.fixture(scope="session", autouse=True)
def _write_dataset(request):
    """Write run-dataset.json after the session ends."""
    yield
    dataset_path = SCRATCH / "run-dataset.json"
    _collector.write(dataset_path)
