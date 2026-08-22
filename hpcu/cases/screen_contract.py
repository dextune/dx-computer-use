"""Common physical-screen case contracts for browser, terminal, and challenges."""

from dataclasses import dataclass
from enum import Enum


class SurfaceKind(str, Enum):
    BROWSER = "browser"
    TERMINAL = "terminal"
    CHALLENGE = "challenge"


class EntryKind(str, Enum):
    URL = "url"
    TERMINAL_COMMAND = "terminal_command"
    EXISTING_SCREEN = "existing_screen"


MAX_CASE_ATTEMPTS = 5


class CaseOutcome(str, Enum):
    RUNNING = "running"
    VERIFIED_SUCCESS = "verified_success"
    BLOCKED = "blocked"
    HUMAN_HANDOFF = "human_handoff"
    UNSUPPORTED = "unsupported"
    ENVIRONMENT_FAILED = "environment_failed"
    MODEL_FAILED = "model_failed"
    VERIFICATION_FAILED = "verification_failed"
    TIMEOUT = "timeout"
    POLICY_DENIED = "policy_denied"
    CANCELLED = "cancelled"


class ChallengeKind(str, Enum):
    NONE = "none"
    CAPTCHA = "captcha"
    LOGIN_REQUIRED = "login_required"
    SECURITY_CHECK = "security_check"
    UNKNOWN_ACCESS_CONTROL = "unknown_access_control"


@dataclass(frozen=True)
class EntryContract:
    kind: EntryKind
    value: str = ""

    def __post_init__(self) -> None:
        if self.kind is EntryKind.EXISTING_SCREEN and self.value:
            raise ValueError("existing_screen cannot carry an entry value")
        if self.kind is not EntryKind.EXISTING_SCREEN and not self.value.strip():
            raise ValueError("physical URL/terminal entry requires a value")


@dataclass(frozen=True)
class ChallengePolicy:
    """Access-control challenges always stop and request a human."""

    outcome: CaseOutcome = CaseOutcome.HUMAN_HANDOFF
    allow_credential_input: bool = False
    allow_refresh_or_alternate_route: bool = False

    def __post_init__(self) -> None:
        if self.outcome not in (CaseOutcome.BLOCKED, CaseOutcome.HUMAN_HANDOFF):
            raise ValueError("challenge outcome must be blocked or human_handoff")
        if self.allow_credential_input or self.allow_refresh_or_alternate_route:
            raise ValueError("challenge bypass is not allowed")


@dataclass(frozen=True)
class ScreenCaseSpec:
    id: str
    surface: SurfaceKind
    goal: str
    entry: EntryContract
    challenge_policy: ChallengePolicy = ChallengePolicy()
    max_attempts: int = MAX_CASE_ATTEMPTS
    max_model_calls: int = 2

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.goal.strip():
            raise ValueError("screen case id and goal are required")
        if not 1 <= self.max_attempts <= MAX_CASE_ATTEMPTS:
            raise ValueError(
                f"screen case max_attempts must be between 1 and {MAX_CASE_ATTEMPTS}"
            )
        if self.max_model_calls < 0:
            raise ValueError("screen case model-call budget must be non-negative")
        if self.surface is SurfaceKind.BROWSER and self.entry.kind not in (
            EntryKind.URL,
            EntryKind.EXISTING_SCREEN,
        ):
            raise ValueError("browser cases require URL or existing_screen entry")
        if self.surface is SurfaceKind.TERMINAL and self.entry.kind not in (
            EntryKind.TERMINAL_COMMAND,
            EntryKind.EXISTING_SCREEN,
        ):
            raise ValueError("terminal cases require command or existing_screen entry")
        if self.surface is SurfaceKind.CHALLENGE and self.entry.kind is EntryKind.URL:
            raise ValueError(
                "challenge fixtures must not navigate through a browser URL"
            )
