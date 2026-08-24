"""Action DSL schema — the deterministic action language.

All operations are defined here.  AI models produce Action objects,
the Executor consumes them.  Coordinates are resolved at runtime
from the latest element bounding box.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ActionOp(str, Enum):
    LAUNCH_APPLICATION = "launch_application"
    FOCUS_WINDOW = "focus_window"
    NAVIGATE = "navigate"
    INVOKE = "invoke"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE = "type"
    REPLACE_TEXT = "replace_text"
    HOTKEY = "hotkey"
    SELECT = "select"
    TOGGLE = "toggle"
    SCROLL = "scroll"
    DRAG = "drag"
    WAIT_UNTIL = "wait_until"
    ASSERT = "assert"
    READ = "read"
    CALL_TOOL = "call_tool"
    CHECKPOINT = "checkpoint"
    REQUEST_APPROVAL = "request_approval"


class PreconditionKind(str, Enum):
    ELEMENT_VISIBLE = "element_visible"
    ELEMENT_FOCUSED = "element_focused"
    ELEMENT_ENABLED = "element_enabled"
    TEXT_EQUALS = "text_equals"
    STATE_MATCHES = "state_matches"


class PostconditionKind(str, Enum):
    ELEMENT_VISIBLE = "element_visible"
    ELEMENT_FOCUSED = "element_focused"
    ELEMENT_ENABLED = "element_enabled"
    TEXT_EQUALS = "text_equals"
    STATE_MATCHES = "state_matches"
    ELEMENT_ABSENT = "element_absent"
    ELEMENT_COUNT_AT_LEAST = "element_count_at_least"
    FILE_EXISTS = "file_exists"
    API_RESPONSE_OK = "api_response_ok"


@dataclass(frozen=True)
class Precondition:
    kind: PreconditionKind
    target: Optional[str] = None  # element_id
    value: Optional[str] = None


@dataclass(frozen=True)
class Postcondition:
    kind: PostconditionKind
    target: Optional[str] = None  # element_id
    value_ref: Optional[str] = None  # variable reference
    value: Optional[str] = None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    alternate_modes: tuple[str, ...] = ()  # "semantic" | "click" | "hotkey"


@dataclass(frozen=True)
class ActionTarget:
    element_id: Optional[str] = None
    locator: Optional[str] = None  # Playwright / accessibility ref


@dataclass(frozen=True)
class Action:
    """A single action in the deterministic DSL.

    Actions are idempotent where possible.  If the current state
    already satisfies the postconditions the action is a no-op.
    """

    id: str
    op: ActionOp
    target: ActionTarget = field(default_factory=ActionTarget)
    preconditions: tuple[Precondition, ...] = ()
    postconditions: tuple[Postcondition, ...] = ()
    timeout_ms: int = 5000
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    value: Optional[str] = None
    value_ref: Optional[str] = None  # reference to a variable
    key: Optional[str] = None  # for hotkey
    modifiers: tuple[str, ...] = ()  # for hotkey
    dx: float = 0.0  # for scroll/drag
    dy: float = 0.0
    application_candidate_id: Optional[str] = None
