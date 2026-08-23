"""Tests for product held-out and chaos qualification gates."""

import pytest

from hpcu.qualification import QualificationCase, qualify

pytestmark = pytest.mark.unit


def _passing_cases() -> list[QualificationCase]:
    return [
        QualificationCase(
            case_id=f"held-out-{index}",
            success=index < 27,
            verified_success=index < 27,
            model_calls=index % 3,
            actions=2,
            chaos_injected=index < 5,
            failure_code="" if index < 27 else "grounding_no_candidates",
        )
        for index in range(30)
    ]


def test_gate_accepts_sufficient_verified_held_out_evidence():
    report = qualify(_passing_cases())

    assert report.passed is True
    assert report.case_count == 30
    assert report.chaos_case_count == 5
    assert report.verified_success_rate == 0.9
    assert report.false_completions == 0
    assert report.stale_action_executions == 0
    assert report.policy_bypass_executions == 0
    assert report.reasons == ()


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("verified_success", "false_completion_detected"),
        ("stale_action_executions", "stale_action_execution_detected"),
        ("policy_bypass_executions", "policy_bypass_execution_detected"),
    ],
)
def test_gate_rejects_each_safety_violation(field, reason):
    cases = _passing_cases()
    original = cases[0]
    values = original.__dict__ | {field: False if field == "verified_success" else 1}
    cases[0] = QualificationCase(**values)

    report = qualify(cases)

    assert report.passed is False
    assert reason in report.reasons


def test_gate_rejects_too_few_cases_and_chaos_cases():
    report = qualify(_passing_cases()[:10])

    assert report.passed is False
    assert "insufficient_held_out_cases" in report.reasons
    assert "insufficient_chaos_cases" not in report.reasons


def test_case_rejects_negative_safety_counters():
    with pytest.raises(ValueError, match="non-negative"):
        QualificationCase(
            case_id="bad",
            success=False,
            verified_success=False,
            stale_action_executions=-1,
        )
