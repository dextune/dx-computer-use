"""Executor — prepare and run deterministic actions.

The executor never touches OS input directly.  It delegates to an
InputInjector: Call `semantic()` first, and fall back to `physical()`
only when semantic is unsupported or fails.  If neither path is possible
the executor returns an honest `ExecutionResult` with a failure code
instead of pretending to succeed.
"""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

from hpcu.input.injector import ExecutionResult, InputInjector
from hpcu.runtime_config import load_runtime_config
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.coordinates import ScreenPoint
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.ui_element import UIElement

_COMMAND_TO_OP = {
    "invoke": ActionOp.INVOKE,
    "click": ActionOp.CLICK,
    "double_click": ActionOp.DOUBLE_CLICK,
    "right_click": ActionOp.RIGHT_CLICK,
    "type": ActionOp.TYPE,
    "replace_text": ActionOp.REPLACE_TEXT,
    "key": ActionOp.HOTKEY,
    "hotkey": ActionOp.HOTKEY,
    "select": ActionOp.SELECT,
    "toggle": ActionOp.TOGGLE,
    "scroll": ActionOp.SCROLL,
    "drag": ActionOp.DRAG,
    "focus": ActionOp.FOCUS_WINDOW,
}


# Op -> injector command name.  The value is what InputInjector.semantic()
# and InputInjector.physical() understand.  Ops that are postcondition-only
# (wait_until / assert / checkpoint) are still mapped so prepare() is total.
_OP_COMMAND = {
    ActionOp.INVOKE: "invoke",
    ActionOp.NAVIGATE: "invoke",
    ActionOp.CLICK: "click",
    ActionOp.DOUBLE_CLICK: "double_click",
    ActionOp.RIGHT_CLICK: "right_click",
    ActionOp.TYPE: "type",
    ActionOp.REPLACE_TEXT: "replace_text",
    ActionOp.HOTKEY: "key",
    ActionOp.SELECT: "select",
    ActionOp.TOGGLE: "toggle",
    ActionOp.SCROLL: "scroll",
    ActionOp.DRAG: "drag",
    ActionOp.FOCUS_WINDOW: "focus",
}


def _command_for(op: ActionOp) -> str:
    """Map an ActionOp to the command string the injector understands."""
    return _OP_COMMAND.get(op, op.value)


@dataclass(frozen=True)
class PreparedAction:
    """A resolved action ready for execution.

    `command` is the string handed to the injector.  `element` and
    `physical_point` carry the already-resolved target (filled by the
    caller/control loop) so execute() can dispatch without a scene.
    """

    action: Action
    element_id: Optional[str]
    command: str
    element: Optional[UIElement] = None
    physical_point: Optional[ScreenPoint] = None


class Executor:
    """Action executor bound to a single InputInjector.

    The executor is injector-only: it never imports a platform package and
    never resolves coordinates itself.
    """

    def __init__(
        self,
        injector: InputInjector,
        poll_interval_ms: Optional[int] = None,
        required_stable_polls: Optional[int] = None,
        sleep=None,
        monotonic=None,
        config: Optional[dict] = None,
    ):
        runtime = config if config is not None else load_runtime_config()
        performance = runtime.get("performance", {})
        self._injector = injector
        self._poll_interval_ms = (
            poll_interval_ms
            if poll_interval_ms is not None
            else int(performance.get("settle_poll_interval_ms", 5))
        )
        self._required_stable_polls = (
            required_stable_polls
            if required_stable_polls is not None
            else int(performance.get("settle_stable_polls", 2))
        )
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._monotonic = monotonic if monotonic is not None else time.monotonic

    @property
    def injector(self) -> InputInjector:
        return self._injector

    def prepare(
        self,
        action: Action,
        element_id: Optional[str],
        *,
        element: Optional[UIElement] = None,
        physical_point: Optional[ScreenPoint] = None,
    ) -> PreparedAction:
        """Resolve an Action into a PreparedAction without executing it.

        prepare() is deterministic and pure: it derives the injector
        command from the action op and keeps the caller's resolved target.
        """
        return PreparedAction(
            action=action,
            element_id=element_id,
            command=_command_for(action.op),
            element=element,
            physical_point=physical_point,
        )

    async def execute(self, prepared: PreparedAction) -> ExecutionResult:
        """Run a prepared action: semantic first, physical fallback.

        When a resolved element is present the semantic path is tried
        first.  If it is unsupported or fails, physical pointer/key input
        is attempted when a point is available.  If neither path can run,
        an unsupported result is returned with INPUT_PHYSICAL_UNSUPPORTED.
        """
        if prepared.element is not None:
            semantic_result = await self._injector.semantic(
                prepared.element, prepared.command
            )
            if semantic_result.success:
                return semantic_result

        if prepared.physical_point is not None:
            typed = prepared.action.value or prepared.action.key
            return await self._injector.physical(
                prepared.physical_point,
                prepared.command,
                text=typed,
            )

        return ExecutionResult(
            success=False,
            mode="physical",
            failure_code=FailureCode.INPUT_PHYSICAL_UNSUPPORTED.value,
        )

    async def inject_physical(
        self,
        point: ScreenPoint,
        command: str,
        text: str | None = None,
    ) -> ExecutionResult:
        """Run a physical injector command through the Action DSL.

        Case loops and recovery use this instead of talking to an OS
        injector. Adapters still only see (point, command, text).
        """
        op = _COMMAND_TO_OP.get(command, ActionOp.CLICK)
        action = Action(
            id=f"physical-{command}",
            op=op,
            value=text,
            key=text if op is ActionOp.HOTKEY else None,
        )
        prepared = self.prepare(action, None, physical_point=point)
        return await self.execute(prepared)

    async def wait_until(
        self,
        predicate: Callable[[], bool],
        timeout_ms: int,
        *,
        required_stable_polls: Optional[int] = None,
    ) -> bool:
        """Poll `predicate` until it holds stably or `timeout_ms` elapses.

        `wait_until` defaults to one true poll. `settle_detector` requires
        consecutive true polls from config. Sleep is injected (`self._sleep`)
        so tests never wait on a real clock.
        """
        needed = (
            required_stable_polls
            if required_stable_polls is not None
            else 1
        )
        deadline_at = self._monotonic() + (timeout_ms / 1000.0)
        stable = 0
        while True:
            if predicate():
                stable += 1
                if stable >= needed:
                    return True
            else:
                stable = 0
            if self._monotonic() >= deadline_at:
                return False
            await self._sleep(self._poll_interval_ms / 1000.0)

    async def settle_detector(
        self, predicate: Callable[[], bool], timeout_ms: int
    ) -> bool:
        """Wait until `predicate` holds for N consecutive polls (config)."""
        return await self.wait_until(
            predicate,
            timeout_ms,
            required_stable_polls=self._required_stable_polls,
        )


# Kept for explicitness in trace/reporting: a command string should be a
# simple snake_case token with no whitespace.
_COMMAND_RE = re.compile(r"^[a-z_]+$")


def is_valid_command(command: str) -> bool:
    """Whether a command string is a well-formed injector token."""
    return bool(_COMMAND_RE.match(command))
