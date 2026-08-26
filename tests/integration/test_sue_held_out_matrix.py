"""Deterministic 36-case SUE held-out grounding matrix."""

from __future__ import annotations

import pytest

from hpcu.grounder.grounder import Grounder
from hpcu.qualification.sue import (
    SUE_REQUIRED_FAMILIES,
    SUEQualificationCase,
    SUEQualificationThresholds,
    qualify_sue,
)
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import ElementRelations, ElementSource, UIElement

pytestmark = pytest.mark.integration


def test_sue_36_case_held_out_matrix_matches_reference_and_safe_abstention():
    grounder = Grounder(confidence_threshold=0.55, min_margin=0.08, config={})
    families = sorted(SUE_REQUIRED_FAMILIES)
    evidence: list[SUEQualificationCase] = []

    for index in range(36):
        family = families[index % len(families)]
        version = index + 10
        korean = family == "korean-english" and index % 2 == 0
        text = f"확인 {index}" if korean else f"Target {index}"
        role = "unknown" if family in {"structure-poor", "pixels-only"} else "button"
        source_type = _source_type(family)
        scale = 1.5 if family == "viewport-scale" else 1.0
        target = _element(
            f"target-{index}",
            version,
            text,
            role,
            source_type,
            x=100 * scale,
            y=80 * scale,
            parent="modal-root" if family == "modal" else None,
        )
        distractor = _element(
            f"other-{index}",
            version,
            f"Other {index}",
            role,
            source_type,
            x=300 * scale,
            y=80 * scale,
            parent="modal-root" if family == "modal" else None,
        )
        elements = {target.id: target, distractor.id: distractor}

        ambiguity_expected = family in {"duplicate-labels", "slow-loading"}
        if ambiguity_expected:
            duplicate = _element(
                f"duplicate-{index}",
                version,
                text,
                role,
                source_type,
                x=500 * scale,
                y=80 * scale,
            )
            elements[duplicate.id] = duplicate
        if family == "stale-frame":
            stale = _element(
                f"stale-{index}",
                version - 1,
                text,
                role,
                source_type,
                x=500,
                y=80,
            )
            elements[stale.id] = stale

        scene = Scene(version=version, elements=elements)
        query = {"text": text}
        if role != "unknown":
            query["role"] = role

        indexed = grounder.resolve(query, scene)
        reference = grounder.resolve_reference(query, scene)
        assert indexed.element_id == reference.element_id
        assert indexed.failure_code == reference.failure_code

        if ambiguity_expected:
            assert indexed.element_id is None
            correct = False
            executed = False
            ambiguity_detected = True
        else:
            assert indexed.element_id == target.id
            correct = True
            executed = True
            ambiguity_detected = False

        evidence.append(
            SUEQualificationCase(
                case_id=f"held-out-{index:02d}",
                family=family,
                expected_resolvable=not ambiguity_expected,
                resolved=indexed.element_id is not None,
                correct_target=correct,
                action_executed=executed,
                ambiguity_expected=ambiguity_expected,
                ambiguity_detected=ambiguity_detected,
                local_model_calls=0,
                coordinate_replay_executions=0,
                drift_safe=True,
            )
        )

    report = qualify_sue(
        evidence,
        SUEQualificationThresholds(
            require_performance_baseline=False,
            require_processed_pixel_reduction=False,
            require_full_ocr_reduction=False,
        ),
    )
    assert report.passed is True
    assert report.case_count == 36
    assert report.missing_families == ()
    assert report.top1_precision == 1.0
    assert report.ambiguity_detection_rate == 1.0
    assert report.false_executable_targets == 0


def _source_type(family: str) -> str:
    if family in {"pixels-only", "structure-poor", "korean-english"}:
        return "ocr"
    if family == "terminal":
        return "atspi"
    if family == "desktop":
        return "uia"
    return "dom"


def _element(
    element_id: str,
    scene_version: int,
    text: str,
    role: str,
    source_type: str,
    *,
    x: float,
    y: float,
    parent: str | None = None,
) -> UIElement:
    return UIElement(
        id=element_id,
        scene_version=scene_version,
        role=role,
        name=text,
        text=text,
        bbox=BoundingBox(
            space=CoordinateSpace.SCREEN_PHYSICAL_PX,
            x=x,
            y=y,
            width=120,
            height=40,
        ),
        relations=ElementRelations(parent=parent),
        sources=(ElementSource(type=source_type, confidence=1.0, text=text),),
        stable_frames=4,
    )
