"""Run provenance regressions for reproducible case artifacts."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hpcu.cases import __main__ as mainmod

pytestmark = pytest.mark.unit

CONFIG = {
    "semantic": {
        "default_provider": "minimax",
        "default_model": "MiniMax-M3",
    },
    "confidence": {"local_execute_threshold": 0.88},
}


def _spec(goal: str = "노트북을 검색해줘") -> SimpleNamespace:
    return SimpleNamespace(
        id="demo",
        goal=goal,
        start_url="https://example.com",
        surface=SimpleNamespace(value="browser"),
        entry=SimpleNamespace(
            kind=SimpleNamespace(value="url"),
            value="https://example.com",
        ),
        evidence=SimpleNamespace(require_pick=True),
        max_attempts=4,
        max_model_calls=1,
    )


def test_manifest_binds_revision_cases_config_and_model():
    payload = mainmod._manifest_payload(
        [_spec()],
        CONFIG,
        cases_argument=Path("cases/custom.yaml"),
        limit=1,
        source_revision="abc123",
    )
    assert payload["source_revision"] == "abc123"
    assert payload["case_ids"] == ["demo"]
    assert payload["case_count"] == 1
    assert payload["provider"] == "minimax"
    assert payload["model"] == "MiniMax-M3"
    assert len(payload["case_contract_sha256"]) == 64
    assert len(payload["runtime_config_sha256"]) == 64


def test_case_contract_hash_changes_when_effective_goal_changes():
    first = mainmod._manifest_payload(
        [_spec("first")],
        CONFIG,
        cases_argument=None,
        limit=0,
        source_revision="abc123",
    )
    second = mainmod._manifest_payload(
        [_spec("second")],
        CONFIG,
        cases_argument=None,
        limit=0,
        source_revision="abc123",
    )
    assert first["case_contract_sha256"] != second["case_contract_sha256"]


def test_write_manifest_happens_without_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "deadbeef")
    path = mainmod._write_run_manifest(
        tmp_path,
        [_spec()],
        CONFIG,
        cases_argument=None,
        limit=0,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source_revision"] == "deadbeef"
    assert payload["cases_argument"] == "<default>"
