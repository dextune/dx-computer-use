"""Unit tests for ApprovalGate — human approval flow."""

import pytest

from hpcu.policy.approval import (
    ApprovalAlreadyDecidedError,
    ApprovalGate,
    ApprovalNotFoundError,
    ApprovalStatus,
)
from hpcu.policy.risk_engine import RiskLevel
from hpcu.schemas.action import Action, ActionOp, ActionTarget


def _action() -> Action:
    return Action(
        id="pay",
        op=ActionOp.CLICK,
        target=ActionTarget(element_id="btn_buy"),
    )


@pytest.mark.unit
def test_request_approval_creates_pending_request():
    """A request starts PENDING with an incrementing unique id."""
    # Given
    manager = ApprovalGate()

    # When
    first = manager.request_approval(_action(), RiskLevel.CRITICAL, "purchase")
    second = manager.request_approval(_action(), RiskLevel.HIGH, "delete")

    # Then
    assert first.status is ApprovalStatus.PENDING
    assert first.risk_level is RiskLevel.CRITICAL
    assert first.reason == "purchase"
    assert first.id == "approval_1"
    assert second.id == "approval_2"


@pytest.mark.unit
def test_request_approval_keeps_request_immutable():
    """The returned request stays unchanged when the manager mutates state."""
    # Given
    manager = ApprovalGate()
    request = manager.request_approval(_action(), RiskLevel.HIGH, "danger")

    # When
    approved = manager.approve(request.id)

    # Then
    assert request.status is ApprovalStatus.PENDING  # original untouched
    assert approved.status is ApprovalStatus.APPROVED


@pytest.mark.unit
def test_approve_marks_request_approved():
    """Approve transitions a pending request to APPROVED."""
    # Given
    manager = ApprovalGate()
    request = manager.request_approval(_action(), RiskLevel.HIGH, "delete")

    # When
    approved = manager.approve(request.id)

    # Then
    assert approved.status is ApprovalStatus.APPROVED
    assert approved.decided_at_ns is not None
    assert manager.get(request.id).status is ApprovalStatus.APPROVED


@pytest.mark.unit
def test_deny_marks_request_denied():
    """Deny transitions a pending request to DENIED."""
    # Given
    manager = ApprovalGate()
    request = manager.request_approval(_action(), RiskLevel.CRITICAL, "purchase")

    # When
    denied = manager.deny(request.id)

    # Then
    assert denied.status is ApprovalStatus.DENIED


@pytest.mark.unit
def test_decide_unknown_request_id_raises():
    """Deciding an unknown request raises ApprovalNotFoundError."""
    # Given
    manager = ApprovalGate()

    # When / Then
    with pytest.raises(ApprovalNotFoundError):
        manager.approve("approval_999")


@pytest.mark.unit
def test_approve_twice_raises_already_decided():
    """A second approve on an already-decided request is rejected."""
    # Given
    manager = ApprovalGate()
    request = manager.request_approval(_action(), RiskLevel.HIGH, "delete")
    manager.approve(request.id)

    # When / Then
    with pytest.raises(ApprovalAlreadyDecidedError):
        manager.approve(request.id)


@pytest.mark.unit
def test_pending_requests_only_lists_pending():
    """pending_requests() excludes already-decided requests."""
    # Given
    manager = ApprovalGate()
    open_request = manager.request_approval(_action(), RiskLevel.HIGH, "delete")
    done_request = manager.request_approval(_action(), RiskLevel.CRITICAL, "buy")
    manager.deny(done_request.id)

    # When
    pending = manager.pending_requests()

    # Then
    assert [request.id for request in pending] == [open_request.id]
