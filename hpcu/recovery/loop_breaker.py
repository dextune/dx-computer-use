"""Infinite-loop detection and recovery.

A LoopBreaker inspects the recent action history and scene history and
matches one of five loop triggers:

1. ``REPEATED_ACTION``     — the same action fires against the same scene
   resulting hash over and over without progress.
2. ``POSTCONDITION_FAILURE`` — the screen stops changing (same scene hash)
   while actions that declare postconditions keep failing.
3. ``A_B_A_PATTERN``       — the agent oscillates A -> B -> A -> B between
   two distinct actions.
4. ``POPUP_LOOP``          — a popup/overlay disappears and reappears,
   i.e. it is dismissed but keeps coming back.
5. ``SCROLL_NO_CHANGE``    — a scroll action produces an unchanged scene.

All detected loops yield a :class:`RecoveryAction` via :meth:`LoopBreaker.recover`.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.scene import Scene
from hpcu.schemas.ui_element import UIElement

# Roles that identify a popup-like element in the scene graph.
POPUP_ROLES = ("dialog", "popup", "modal", "tooltip", "menu", "overlay")

# Default tuning values — overridable through the LoopBreaker constructor.
DEFAULT_WINDOW_SIZE = 8
DEFAULT_REPEAT_THRESHOLD = 3
DEFAULT_STALL_THRESHOLD = 3
DEFAULT_SCROLL_NO_CHANGE_THRESHOLD = 2
DEFAULT_ESCALATE_THRESHOLD = 2
DEFAULT_HALT_THRESHOLD = 4


class LoopTriggerType(str, Enum):
    """The five classes of infinite loop this breaker can detect."""

    REPEATED_ACTION = "repeated_action"
    POSTCONDITION_FAILURE = "postcondition_failure"
    A_B_A_PATTERN = "a_b_a_pattern"
    POPUP_LOOP = "popup_loop"
    SCROLL_NO_CHANGE = "scroll_no_change"


class RecoveryAction(str, Enum):
    """The course of action chosen after a loop is detected."""

    REEXPLORE = "reexplore"
    SWITCH_MODE = "switch_mode"
    ESCALATE = "escalate"
    HALT = "halt"


@dataclass(frozen=True)
class LoopDetection:
    """Description of a detected infinite loop."""

    trigger: LoopTriggerType
    description: str
    sequence_span: int = 0  # number of history entries involved
    repeated_actions: tuple[str, ...] = ()  # action ids that formed the loop


# Primary recovery action per trigger.
_BASE_RESPONSE = {
    LoopTriggerType.REPEATED_ACTION: RecoveryAction.REEXPLORE,
    LoopTriggerType.POSTCONDITION_FAILURE: RecoveryAction.SWITCH_MODE,
    LoopTriggerType.A_B_A_PATTERN: RecoveryAction.ESCALATE,
    LoopTriggerType.POPUP_LOOP: RecoveryAction.REEXPLORE,
    LoopTriggerType.SCROLL_NO_CHANGE: RecoveryAction.SWITCH_MODE,
}


def scene_hash(scene: Scene) -> str:
    """Return a stable content hash for a scene.

    Two scenes with identical element sets (id, role, fingerprint/name)
    hash identically regardless of ordering.
    """
    if scene is None:
        return "<none>"
    parts = sorted(
        f"{element.id}:{element.role}:{element.fingerprint or element.name or ''}"
        for element in scene.elements.values()
    )
    return __import__("hashlib").sha256(repr(parts).encode("utf-8")).hexdigest()


def action_fingerprint(action: Action) -> str:
    """Collapse an action to a stable fingerprint ignoring its id/timing."""
    target = action.target.element_id or action.target.locator or ""
    return (
        f"{action.op.value}:{target}:{action.key or ''}:"
        f"{action.value or ''}:{action.dx:.2f}:{action.dy:.2f}"
    )


def _is_popup_like(element: UIElement) -> bool:
    """True if an element looks like a popup, overlay or modal."""
    if element.role.lower() in POPUP_ROLES:
        return True
    if element.relations.overlays:
        return True
    return element.relations.modal_owner is not None


@dataclass(frozen=True)
class _Entry:
    """Internal working entry combining action and scene for a step."""

    index: int
    action: Action
    action_id: str
    fingerprint: str
    scene_hash_value: str
    scene: Scene


class LoopBreaker:
    """Detect and break infinite loops over recent action/scene history.

    Parameters are thresholds, all of which have sensible defaults.  The
    breaker keeps recovery-attempt counters per trigger so that repeated
    failure escalates and eventually halts.
    """

    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW_SIZE,
        repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD,
        stall_threshold: int = DEFAULT_STALL_THRESHOLD,
        scroll_no_change_threshold: int = DEFAULT_SCROLL_NO_CHANGE_THRESHOLD,
        escalate_threshold: int = DEFAULT_ESCALATE_THRESHOLD,
        halt_threshold: int = DEFAULT_HALT_THRESHOLD,
    ) -> None:
        self.window_size = window_size
        self.repeat_threshold = repeat_threshold
        self.stall_threshold = stall_threshold
        self.scroll_no_change_threshold = scroll_no_change_threshold
        self.escalate_threshold = escalate_threshold
        self.halt_threshold = halt_threshold
        self._attempt_counts: dict[LoopTriggerType, int] = {}
        self.recovery_count: int = 0

    def detect(
        self,
        action_history: Sequence[Action],
        scene_history: Sequence[Scene],
    ) -> Optional[LoopDetection]:
        """Return a LoopDetection if a loop is present, else None."""
        if len(action_history) != len(scene_history):
            raise ValueError(
                "action_history and scene_history must be aligned "
                f"(got {len(action_history)} vs {len(scene_history)})"
            )
        if not action_history:
            return None

        window = min(self.window_size, len(action_history))
        base = len(action_history) - window
        entries: list[_Entry] = []
        for index in range(base, len(action_history)):
            action = action_history[index]
            scene = scene_history[index]
            entries.append(
                _Entry(
                    index=index,
                    action=action,
                    action_id=action.id,
                    fingerprint=action_fingerprint(action),
                    scene_hash_value=scene_hash(scene),
                    scene=scene,
                )
            )

        detection = self._check_popup_loop(entries)
        if detection is not None:
            return detection

        detection = self._check_scroll_no_change(entries)
        if detection is not None:
            return detection

        detection = self._check_a_b_a_pattern(entries)
        if detection is not None:
            return detection

        detection = self._check_repeated_action(entries)
        if detection is not None:
            return detection

        detection = self._check_postcondition_failure(entries)
        if detection is not None:
            return detection

        return None

    def recover(
        self,
        detection: LoopDetection,
        scene: Optional[Scene],
        node: object,
    ) -> RecoveryAction:
        """Choose the recovery action for a Detection, escalating on repeat."""
        self.recovery_count += 1
        current = self._attempt_counts.get(detection.trigger, 0) + 1
        self._attempt_counts[detection.trigger] = current

        if current >= self.halt_threshold:
            return RecoveryAction.HALT

        response = _BASE_RESPONSE[detection.trigger]
        if (
            current >= self.escalate_threshold
            and response in (RecoveryAction.REEXPLORE, RecoveryAction.SWITCH_MODE)
        ):
            return RecoveryAction.ESCALATE
        return response

    def reset(self) -> None:
        """Clear recovery-attempt counters."""
        self._attempt_counts.clear()
        self.recovery_count = 0

    # ---- trigger matchers ----

    def _check_repeated_action(self, entries: list[_Entry]) -> Optional[LoopDetection]:
        if len(entries) < self.repeat_threshold:
            return None
        last = entries[-1]
        last_key = (last.fingerprint, last.scene_hash_value)
        count = 0
        for entry in reversed(entries):
            if (entry.fingerprint, entry.scene_hash_value) != last_key:
                break
            count += 1
        if count < self.repeat_threshold:
            return None
        return LoopDetection(
            trigger=LoopTriggerType.REPEATED_ACTION,
            description=(
                f"The same action ran {count} times against an unchanged scene; "
                "no progress is being made."
            ),
            sequence_span=count,
            repeated_actions=tuple(entry.action_id for entry in entries[-count:]),
        )

    def _check_postcondition_failure(
        self, entries: list[_Entry]
    ) -> Optional[LoopDetection]:
        if len(entries) < self.stall_threshold:
            return None
        last_hash = entries[-1].scene_hash_value
        count = 0
        for entry in reversed(entries):
            if entry.scene_hash_value != last_hash:
                break
            count += 1
        if count < self.stall_threshold:
            return None
        affected = entries[-count:]
        if not any(entry.action.postconditions for entry in affected):
            return None
        return LoopDetection(
            trigger=LoopTriggerType.POSTCONDITION_FAILURE,
            description=(
                f"The scene hash stayed identical for {count} steps while actions "
                "declaring postconditions ran; the postcondition keeps failing."
            ),
            sequence_span=count,
            repeated_actions=tuple(entry.action_id for entry in affected),
        )

    def _check_a_b_a_pattern(self, entries: list[_Entry]) -> Optional[LoopDetection]:
        if len(entries) < 4:
            return None
        a = entries[-1].fingerprint
        b = entries[-2].fingerprint
        c = entries[-3].fingerprint
        d = entries[-4].fingerprint
        if a != b and a == c and b == d:
            return LoopDetection(
                trigger=LoopTriggerType.A_B_A_PATTERN,
                description=(
                    "The agent oscillates between two distinct actions "
                    "(A->B->A->B)."
                ),
                sequence_span=4,
                repeated_actions=tuple(
                    entry.action_id
                    for entry in (entries[-3], entries[-2], entries[-1])
                ),
            )
        return None

    def _check_popup_loop(self, entries: list[_Entry]) -> Optional[LoopDetection]:
        presence: dict[str, list[int]] = {}
        for entry in entries:
            for element in entry.scene.elements.values():
                if not _is_popup_like(element):
                    continue
                key = element.fingerprint or element.id
                presence.setdefault(key, []).append(entry.index)
        last_index = entries[-1].index
        for key, positions in presence.items():
            if last_index not in positions:
                continue
            if len(positions) < 2:
                continue
            positions = sorted(positions)
            if any(
                positions[i] - positions[i - 1] > 1
                for i in range(1, len(positions))
            ):
                return LoopDetection(
                    trigger=LoopTriggerType.POPUP_LOOP,
                    description=(
                        "A popup/overlay keeps reappearing after being dismissed."
                    ),
                    sequence_span=len(entries),
                    repeated_actions=(key,),
                )
        return None

    def _check_scroll_no_change(
        self, entries: list[_Entry]
    ) -> Optional[LoopDetection]:
        if len(entries) < self.scroll_no_change_threshold + 1:
            return None
        tail = entries[-self.scroll_no_change_threshold - 1 :]
        if tail[-1].action.op != ActionOp.SCROLL:
            return None
        hashes = [entry.scene_hash_value for entry in tail]
        if len(set(hashes)) != 1:
            return None
        return LoopDetection(
            trigger=LoopTriggerType.SCROLL_NO_CHANGE,
            description="A scroll action ran but the scene did not change.",
            sequence_span=self.scroll_no_change_threshold + 1,
            repeated_actions=tuple(entry.action_id for entry in tail),
        )
