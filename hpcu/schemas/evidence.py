"""Evidence contract schema — task completion is proven, not claimed."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EvidenceKind(str, Enum):
    CART_CONTAINS = "cart_contains"
    ELEMENT_VISIBLE = "element_visible"
    ELEMENT_ABSENT = "element_absent"
    TEXT_EQUALS = "text_equals"
    STATE_MATCHES = "state_matches"
    QUANTITY_AT_LEAST = "quantity_at_least"
    FILE_EXISTS = "file_exists"
    FILE_MTIME_AFTER = "file_mtime_after"
    API_RESPONSE_OK = "api_response_ok"
    BADGE_VALUE = "badge_value"
    NOTIFICATION_SHOWN = "notification_shown"


@dataclass(frozen=True)
class EvidenceCondition:
    kind: EvidenceKind
    target: Optional[str] = None  # element_id or path
    product_match: Optional[str] = None
    value: Optional[int] = None


@dataclass(frozen=True)
class EvidenceContract:
    """Declarative completion criteria.

    A task is complete only when ALL conditions in `all` are satisfied,
    or ANY condition in `any` is satisfied.  At least one array must be
    non-empty.
    """

    all: tuple[EvidenceCondition, ...] = ()
    any: tuple[EvidenceCondition, ...] = ()

    def __post_init__(self):
        if not self.all and not self.any:
            raise ValueError(
                "EvidenceContract must have at least one condition in 'all' or 'any'"
            )