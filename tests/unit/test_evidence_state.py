"""Structured evidence must remain separate from input success."""

import pytest

from hpcu.schemas.evidence import EvidenceCondition, EvidenceContract, EvidenceKind
from hpcu.schemas.evidence_state import EvidenceStatus
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement
from hpcu.verifier.verifier import Verifier

pytestmark = pytest.mark.unit


def test_verify_state_records_unsatisfied_screen_evidence():
    scene = Scene(
        version=7,
        elements={
            "status": UIElement(
                id="status", scene_version=7, text="working"
            )
        },
    )
    contract = EvidenceContract(
        all=(
            EvidenceCondition(
                kind=EvidenceKind.TEXT_EQUALS,
                target="status",
                value="done",
            ),
        )
    )
    state = Verifier().verify_state(contract, scene)
    assert state.status is EvidenceStatus.UNSATISFIED
    assert state.scene_version == 7
    assert state.observations[0].status is EvidenceStatus.UNSATISFIED


def test_verify_state_satisfied_is_independent_of_action_result():
    scene = Scene(
        version=8,
        elements={
            "status": UIElement(id="status", scene_version=8, text="done")
        },
    )
    contract = EvidenceContract(
        all=(
            EvidenceCondition(
                kind=EvidenceKind.TEXT_EQUALS,
                target="status",
                value="done",
            ),
        )
    )
    state = Verifier().verify_state(contract, scene)
    assert state.satisfied is True
    assert state.status is EvidenceStatus.SATISFIED
