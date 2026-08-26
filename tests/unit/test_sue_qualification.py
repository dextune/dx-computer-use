from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.sue_artifacts import write_sue_qualification_artifacts
from hpcu.qualification import QualificationCase
from hpcu.qualification.sue import (
    SUE_REQUIRED_FAMILIES,
    SUEQualificationCase,
    qualify_product_sue,
    qualify_sue,
)

pytestmark = pytest.mark.unit


def _passing_cases():
    families = sorted(SUE_REQUIRED_FAMILIES)
    cases = []
    for index in range(36):
        family = families[index % len(families)]
        ambiguity = family in {"duplicate-labels", "slow-loading"}
        cases.append(
            SUEQualificationCase(
                case_id=f"sue-held-out-{index:02d}",
                family=family,
                expected_resolvable=not ambiguity,
                resolved=not ambiguity,
                correct_target=not ambiguity,
                action_executed=not ambiguity,
                ambiguity_expected=ambiguity,
                ambiguity_detected=ambiguity,
                local_model_calls=0,
                processed_pixels=40,
                full_ocr_passes=0,
                first_actionable_us=80,
                grounding_us=20,
                baseline_processed_pixels=100,
                baseline_full_ocr_passes=1,
                baseline_first_actionable_us=100,
                baseline_grounding_us=25,
            )
        )
    return cases


def test_sue_gate_accepts_30_plus_diverse_safe_ab_evidence():
    report = qualify_sue(_passing_cases())
    assert report.passed is True
    assert report.case_count == 36
    assert report.family_count == len(SUE_REQUIRED_FAMILIES)
    assert report.top1_precision == 1.0
    assert report.ambiguity_detection_rate == 1.0
    assert report.coordinate_replay_executions == 0
    assert report.processed_pixels < report.baseline_processed_pixels
    assert report.full_ocr_passes < report.baseline_full_ocr_passes
    assert report.reasons == ()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("stale_action_executions", 1, "sue_stale_action_execution_detected"),
        ("false_completions", 1, "sue_false_completion_detected"),
        ("false_merge_executions", 1, "sue_false_merge_execution_detected"),
        ("coordinate_replay_executions", 1, "coordinate_replay_detected"),
        ("drift_safe", False, "unsafe_drift_handling_detected"),
    ],
)
def test_sue_gate_rejects_safety_and_replay_violations(field, value, reason):
    cases = _passing_cases()
    cases[0] = replace(cases[0], **{field: value})
    report = qualify_sue(cases)
    assert report.passed is False
    assert reason in report.reasons


def test_sue_gate_rejects_wrong_executable_target():
    cases = _passing_cases()
    cases[0] = replace(cases[0], correct_target=False, action_executed=True)
    report = qualify_sue(cases)
    assert "sue_false_executable_target_detected" in report.reasons


def test_sue_gate_rejects_p95_or_work_reduction_regression():
    cases = _passing_cases()
    cases[0] = replace(cases[0], first_actionable_us=1000)
    cases[1] = replace(cases[1], first_actionable_us=1000)
    report = qualify_sue(cases)
    assert "first_actionable_p95_regression" in report.reasons

    cases = [
        replace(case, processed_pixels=case.baseline_processed_pixels)
        for case in _passing_cases()
    ]
    report = qualify_sue(cases)
    assert "processed_pixels_not_reduced" in report.reasons


def test_sue_artifact_writer_emits_mandatory_files(tmp_path: Path):
    write_sue_qualification_artifacts(_passing_cases(), tmp_path)
    assert {path.name for path in tmp_path.iterdir()} == {
        "summary.json",
        "stage-metrics.json",
        "qualification.json",
        "fixture-list.txt",
        "reference-hardware.json",
    }


def test_sue_gate_requires_real_performance_baseline_by_default():
    cases = [
        replace(
            case,
            baseline_processed_pixels=0,
            baseline_full_ocr_passes=0,
            baseline_first_actionable_us=0,
            baseline_grounding_us=0,
        )
        for case in _passing_cases()
    ]
    report = qualify_sue(cases)
    assert report.passed is False
    assert "missing_sue_performance_baseline" in report.reasons


def test_combined_product_and_sue_gate_requires_both_reports_to_pass():
    product_cases = [
        QualificationCase(
            case_id=f"product-{index:02d}",
            success=True,
            verified_success=True,
            model_calls=index % 2,
            actions=2,
            chaos_injected=index < 5,
        )
        for index in range(30)
    ]
    report = qualify_product_sue(product_cases, _passing_cases())
    assert report.passed is True
    assert report.product.passed is True
    assert report.sue.passed is True

    product_cases[0] = replace(
        product_cases[0],
        verified_success=False,
    )
    failed = qualify_product_sue(product_cases, _passing_cases())
    assert failed.passed is False
    assert failed.product.passed is False
