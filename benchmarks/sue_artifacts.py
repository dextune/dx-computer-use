"""Deterministic SUE qualification artifact writer."""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict
from pathlib import Path

from hpcu.qualification.sue import SUEQualificationCase, qualify_sue


def write_sue_qualification_artifacts(
    cases: list[SUEQualificationCase] | tuple[SUEQualificationCase, ...],
    output_dir: Path,
) -> None:
    """Write the five mandatory SUE-8 benchmark artifacts."""
    items = tuple(cases)
    report = qualify_sue(items)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "case_count": report.case_count,
        "family_count": report.family_count,
        "passed": report.passed,
        "top1_precision": report.top1_precision,
        "confident_local_precision": report.confident_local_precision,
        "ambiguity_detection_rate": report.ambiguity_detection_rate,
        "local_resolution_rate": report.local_resolution_rate,
        "unnecessary_semantic_escalation_rate": (
            report.unnecessary_semantic_escalation_rate
        ),
        "safety": {
            "stale_action_executions": report.stale_action_executions,
            "false_completions": report.false_completions,
            "false_executable_targets": report.false_executable_targets,
            "false_merge_executions": report.false_merge_executions,
            "coordinate_replay_executions": report.coordinate_replay_executions,
            "drift_failures": report.drift_failures,
        },
    }
    stage_metrics = {
        "candidate": {
            "processed_pixels": report.processed_pixels,
            "full_ocr_passes": report.full_ocr_passes,
            "first_actionable_p95_us": report.first_actionable_p95_us,
            "grounding_p95_us": report.grounding_p95_us,
        },
        "baseline": {
            "processed_pixels": report.baseline_processed_pixels,
            "full_ocr_passes": report.baseline_full_ocr_passes,
            "first_actionable_p95_us": report.baseline_first_actionable_p95_us,
            "grounding_p95_us": report.baseline_grounding_p95_us,
        },
    }
    hardware = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "logical_cpu_count": os.cpu_count(),
    }

    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "stage-metrics.json", stage_metrics)
    _write_json(output_dir / "qualification.json", asdict(report))
    _write_json(output_dir / "reference-hardware.json", hardware)
    (output_dir / "fixture-list.txt").write_text(
        "".join(f"{item.case_id}\t{item.family}\n" for item in items),
        encoding="utf-8",
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
