"""Human-in-the-loop approval flow for high-risk actions.

ApprovalGate issues immutable ApprovalRequest objects and records the
human decision.  Requests are immutable: decision transitions produce a new
instance (via dataclasses.replace) so the boundary object stays frozen.
"""

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional

from hpcu.policy.risk_engine import RiskLevel
from hpcu.schemas.action import Action


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


@dataclass(frozen=True)
class ApprovalRequest:
    """An immutable human-approval request for a high-risk action."""

    id: str
    action: Action
    risk_level: RiskLevel
    reason: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at_ns: int = field(default_factory=time.monotonic_ns)
    decided_at_ns: Optional[int] = None


class ApprovalNotFoundError(Exception):
    """Raised when a request id is unknown to the ApprovalGate."""


class ApprovalAlreadyDecidedError(Exception):
    """Raised when an already-decided request is decided again."""


class ApprovalGate:
    """Create and decide human-approval requests."""

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self._seq = 0

    def request_approval(
        self, action: Action, risk_level: RiskLevel, reason: str
    ) -> ApprovalRequest:
        """Create a new PENDING approval request and return it."""
        self._seq += 1
        request = ApprovalRequest(
            id=f"approval_{self._seq}",
            action=action,
            risk_level=risk_level,
            reason=reason,
        )
        self._requests[request.id] = request
        return request

    def approve(self, request_id: str) -> ApprovalRequest:
        """Mark a pending request APPROVED and return the updated request."""
        return self._decide(request_id, ApprovalStatus.APPROVED)

    def deny(self, request_id: str) -> ApprovalRequest:
        """Mark a pending request DENIED and return the updated request."""
        return self._decide(request_id, ApprovalStatus.DENIED)

    def get(self, request_id: str) -> Optional[ApprovalRequest]:
        """Look up a request by id; None when unknown."""
        return self._requests.get(request_id)

    def pending_requests(self) -> tuple[ApprovalRequest, ...]:
        """All requests that are still awaiting a human decision."""
        return tuple(
            request
            for request in self._requests.values()
            if request.status is ApprovalStatus.PENDING
        )

    def _decide(self, request_id: str, status: ApprovalStatus) -> ApprovalRequest:
        current = self._requests.get(request_id)
        if current is None:
            raise ApprovalNotFoundError(f"unknown approval request id: {request_id}")
        if current.status is not ApprovalStatus.PENDING:
            raise ApprovalAlreadyDecidedError(
                f"approval request {request_id} already {current.status.value}"
            )
        decided = replace(
            current, status=status, decided_at_ns=time.monotonic_ns()
        )
        self._requests[request_id] = decided
        return decided
