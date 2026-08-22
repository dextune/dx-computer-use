"""Test harness dataset capture — collects per-test evidence.

Writes a run-dataset.json to {SCRATCH} after each test session.
Each entry contains: case_id, success, actions, minimax_call_count,
minimax_error_count, minimax_tokens, model, compile_call_count,
grounding_call_count, failure, and aggregated totals.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

SCRATCH = Path(os.environ.get("HPCU_SCRATCH", "/tmp/grok-goal-f73a6e2ef612/implementer"))


class DatasetCollector:
    """Collects per-case stats across the test session."""

    def __init__(self):
        self.entries: list[dict[str, Any]] = []

    def record_case(self, stats: dict[str, Any]) -> None:
        required = {
            "case_id": str(stats.get("case_id", "unknown")),
            "success": bool(stats.get("success", False)),
            "actions": stats.get("actions", []),
            "minimax_call_count": int(stats.get("minimax_call_count", 0)),
            "minimax_error_count": int(stats.get("minimax_error_count", 0)),
            "minimax_tokens": int(stats.get("minimax_tokens", 0)),
            "model": str(stats.get("model", "MiniMax-M3")),
            "compile_call_count": int(stats.get("compile_call_count", 0)),
            "grounding_call_count": int(stats.get("grounding_call_count", 0)),
            "failure": str(stats.get("failure", "")),
        }
        self.entries.append(required)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        totals = {
            "cases": len(self.entries),
            "successes": sum(1 for e in self.entries if e["success"]),
            "action_count": sum(len(e["actions"]) for e in self.entries),
            "minimax_call_count": sum(e["minimax_call_count"] for e in self.entries),
            "minimax_error_count": sum(e["minimax_error_count"] for e in self.entries),
            "minimax_tokens": sum(e["minimax_tokens"] for e in self.entries),
            "compile_call_count": sum(e["compile_call_count"] for e in self.entries),
            "grounding_call_count": sum(e["grounding_call_count"] for e in self.entries),
        }
        payload = {
            "model": "MiniMax-M3",
            "cases": self.entries,
            "totals": totals,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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