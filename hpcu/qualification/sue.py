"""Screen Understanding Engine product qualification gates.

This module turns per-task SUE evidence into deterministic correctness,
local-first, replay-safety, and A/B performance release decisions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from hpcu.qualification.gate import (
    QualificationCase,
    QualificationReport,
    QualificationThresholds,
    qualify,
)

SUE_REQUIRED_FAMILIES = frozenset(
    {
        "browser",
        "terminal",
        "desktop",
        "structure-rich",
        "structure-poor",
        "pixels-only",
        "duplicate-labels",
        "modal",
        "slow-loading",
        "stale-frame",
        "viewport-scale",
        "korean-english",
    }
)


@dataclass(frozen=True)
class SUEQualificationCase:
    """Held-out SUE evidence for one task/fixture."""

    case_id: str
    family: str
    expected_resolvable: bool
    resolved: bool
    correct_target: bool
    action_executed: bool = False
    ambiguity_expected: bool = False
    ambiguity_detected: bool = False
    local_model_calls: int = 0
    stale_action_executions: int = 0
    false_completions: int = 0
    false_merge_executions: int = 0
    coordinate_replay_executions: int = 0
    drift_safe: bool = True
    processed_pixels: int = 0
    full_ocr_passes: int = 0
    first_actionable_us: int = 0
    grounding_us: int = 0
    baseline_processed_pixels: int = 0
    baseline_full_ocr_passes: int = 0
    baseline_first_actionable_us: int = 0
    baseline_grounding_us: int = 0

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("SUE case_id must be non-empty")
        if not self.family.strip():
            raise ValueError("SUE family must be non-empty")
        for name in (
            "local_model_calls",
            "stale_action_executions",
            "false_completions",
            "false_merge_executions",
            "coordinate_replay_executions",
            "processed_pixels",
            "full_ocr_passes",
            "first_actionable_us",
            "grounding_us",
            "baseline_processed_pixels",
            "baseline_full_ocr_passes",
            "baseline_first_actionable_us",
            "baseline_grounding_us",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class SUEQualificationThresholds:
    minimum_cases: int = 30
    required_families: frozenset[str] = SUE_REQUIRED_FAMILIES
    minimum_top1_precision: float = 1.0
    minimum_confident_local_precision: float = 1.0
    minimum_ambiguity_detection_rate: float = 1.0
    maximum_unnecessary_semantic_escalation_rate: float = 0.0
    maximum_stale_action_executions: int = 0
    maximum_false_completions: int = 0
    maximum_false_merge_executions: int = 0
    maximum_coordinate_replay_executions: int = 0
    maximum_p95_regression_ratio: float = 1.0
    require_performance_baseline: bool = True
    require_processed_pixel_reduction: bool = True
    require_full_ocr_reduction: bool = True

    def __post_init__(self) -> None:
        if self.minimum_cases <= 0:
            raise ValueError("minimum_cases must be positive")
        for name in (
            "minimum_top1_precision",
            "minimum_confident_local_precision",
            "minimum_ambiguity_detection_rate",
            "maximum_unnecessary_semantic_escalation_rate",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.maximum_p95_regression_ratio < 1.0:
            raise ValueError("maximum_p95_regression_ratio must be >= 1.0")


@dataclass(frozen=True)
class ProductSUEQualificationReport:
    """Final product decision; both the generic and SUE gates must pass."""

    passed: bool
    product: QualificationReport
    sue: "SUEQualificationReport"


@dataclass(frozen=True)
class SUEQualificationReport:
    passed: bool
    case_count: int
    family_count: int
    missing_families: tuple[str, ...]
    top1_precision: float
    confident_local_precision: float
    ambiguity_detection_rate: float
    local_resolution_rate: float
    unnecessary_semantic_escalation_rate: float
    stale_action_executions: int
    false_completions: int
    false_executable_targets: int
    false_merge_executions: int
    coordinate_replay_executions: int
    drift_failures: int
    processed_pixels: int
    baseline_processed_pixels: int
    full_ocr_passes: int
    baseline_full_ocr_passes: int
    first_actionable_p95_us: int
    baseline_first_actionable_p95_us: int
    grounding_p95_us: int
    baseline_grounding_p95_us: int
    reasons: tuple[str, ...]


def qualify_sue(
    cases: tuple[SUEQualificationCase, ...] | list[SUEQualificationCase],
    thresholds: SUEQualificationThresholds = SUEQualificationThresholds(),
) -> SUEQualificationReport:
    """Evaluate the final SUE Q1-Q5 product gates."""
    items = tuple(cases)
    families = {item.family for item in items}
    missing_families = tuple(sorted(thresholds.required_families - families))

    resolved_items = tuple(item for item in items if item.resolved)
    correct_resolved = sum(1 for item in resolved_items if item.correct_target)
    top1_precision = _rate(correct_resolved, len(resolved_items), empty=1.0)

    confident_local = tuple(
        item
        for item in items
        if item.action_executed and item.local_model_calls == 0
    )
    correct_confident = sum(1 for item in confident_local if item.correct_target)
    confident_precision = _rate(
        correct_confident, len(confident_local), empty=1.0
    )

    ambiguous = tuple(item for item in items if item.ambiguity_expected)
    ambiguity_detected = sum(1 for item in ambiguous if item.ambiguity_detected)
    ambiguity_rate = _rate(ambiguity_detected, len(ambiguous), empty=1.0)

    deterministic = tuple(
        item
        for item in items
        if item.expected_resolvable and not item.ambiguity_expected
    )
    local_resolved = sum(
        1
        for item in deterministic
        if item.correct_target and item.local_model_calls == 0
    )
    local_resolution_rate = _rate(local_resolved, len(deterministic), empty=1.0)
    unnecessary_escalations = sum(
        1 for item in deterministic if item.local_model_calls > 0
    )
    escalation_rate = _rate(
        unnecessary_escalations, len(deterministic), empty=0.0
    )

    stale_actions = sum(item.stale_action_executions for item in items)
    false_completions = sum(item.false_completions for item in items)
    false_executable = sum(
        1 for item in items if item.action_executed and not item.correct_target
    )
    false_merges = sum(item.false_merge_executions for item in items)
    coordinate_replays = sum(item.coordinate_replay_executions for item in items)
    drift_failures = sum(1 for item in items if not item.drift_safe)

    perf_items = tuple(
        item
        for item in items
        if item.baseline_first_actionable_us > 0
        or item.baseline_grounding_us > 0
        or item.baseline_processed_pixels > 0
        or item.baseline_full_ocr_passes > 0
    )
    processed_pixels = sum(item.processed_pixels for item in perf_items)
    baseline_pixels = sum(item.baseline_processed_pixels for item in perf_items)
    full_ocr = sum(item.full_ocr_passes for item in perf_items)
    baseline_full_ocr = sum(item.baseline_full_ocr_passes for item in perf_items)
    first_p95 = _p95([item.first_actionable_us for item in perf_items])
    baseline_first_p95 = _p95(
        [item.baseline_first_actionable_us for item in perf_items]
    )
    grounding_p95 = _p95([item.grounding_us for item in perf_items])
    baseline_grounding_p95 = _p95(
        [item.baseline_grounding_us for item in perf_items]
    )

    reasons: list[str] = []
    if len(items) < thresholds.minimum_cases:
        reasons.append("insufficient_sue_held_out_cases")
    if missing_families:
        reasons.append("missing_sue_fixture_families")
    if thresholds.require_performance_baseline and not perf_items:
        reasons.append("missing_sue_performance_baseline")
    if top1_precision < thresholds.minimum_top1_precision:
        reasons.append("top1_precision_below_threshold")
    if confident_precision < thresholds.minimum_confident_local_precision:
        reasons.append("confident_local_precision_below_threshold")
    if ambiguity_rate < thresholds.minimum_ambiguity_detection_rate:
        reasons.append("ambiguity_detection_below_threshold")
    if escalation_rate > thresholds.maximum_unnecessary_semantic_escalation_rate:
        reasons.append("unnecessary_semantic_escalation_detected")
    if stale_actions > thresholds.maximum_stale_action_executions:
        reasons.append("sue_stale_action_execution_detected")
    if false_completions > thresholds.maximum_false_completions:
        reasons.append("sue_false_completion_detected")
    if false_executable:
        reasons.append("sue_false_executable_target_detected")
    if false_merges > thresholds.maximum_false_merge_executions:
        reasons.append("sue_false_merge_execution_detected")
    if coordinate_replays > thresholds.maximum_coordinate_replay_executions:
        reasons.append("coordinate_replay_detected")
    if drift_failures:
        reasons.append("unsafe_drift_handling_detected")

    p95_limit = thresholds.maximum_p95_regression_ratio
    if baseline_first_p95 and first_p95 > baseline_first_p95 * p95_limit:
        reasons.append("first_actionable_p95_regression")
    if baseline_grounding_p95 and grounding_p95 > baseline_grounding_p95 * p95_limit:
        reasons.append("grounding_p95_regression")
    if (
        thresholds.require_processed_pixel_reduction
        and baseline_pixels
        and processed_pixels >= baseline_pixels
    ):
        reasons.append("processed_pixels_not_reduced")
    if (
        thresholds.require_full_ocr_reduction
        and baseline_full_ocr
        and full_ocr >= baseline_full_ocr
    ):
        reasons.append("full_ocr_not_reduced")

    return SUEQualificationReport(
        passed=not reasons,
        case_count=len(items),
        family_count=len(families),
        missing_families=missing_families,
        top1_precision=top1_precision,
        confident_local_precision=confident_precision,
        ambiguity_detection_rate=ambiguity_rate,
        local_resolution_rate=local_resolution_rate,
        unnecessary_semantic_escalation_rate=escalation_rate,
        stale_action_executions=stale_actions,
        false_completions=false_completions,
        false_executable_targets=false_executable,
        false_merge_executions=false_merges,
        coordinate_replay_executions=coordinate_replays,
        drift_failures=drift_failures,
        processed_pixels=processed_pixels,
        baseline_processed_pixels=baseline_pixels,
        full_ocr_passes=full_ocr,
        baseline_full_ocr_passes=baseline_full_ocr,
        first_actionable_p95_us=first_p95,
        baseline_first_actionable_p95_us=baseline_first_p95,
        grounding_p95_us=grounding_p95,
        baseline_grounding_p95_us=baseline_grounding_p95,
        reasons=tuple(reasons),
    )


def _rate(numerator: int, denominator: int, *, empty: float) -> float:
    return numerator / denominator if denominator else empty


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def qualify_product_sue(
    product_cases: tuple[QualificationCase, ...] | list[QualificationCase],
    sue_cases: tuple[SUEQualificationCase, ...] | list[SUEQualificationCase],
    *,
    product_thresholds: QualificationThresholds = QualificationThresholds(),
    sue_thresholds: SUEQualificationThresholds = SUEQualificationThresholds(),
) -> ProductSUEQualificationReport:
    """Require both the generic product release gate and SUE Q1-Q5 gate."""
    product_report = qualify(product_cases, product_thresholds)
    sue_report = qualify_sue(sue_cases, sue_thresholds)
    return ProductSUEQualificationReport(
        passed=product_report.passed and sue_report.passed,
        product=product_report,
        sue=sue_report,
    )
