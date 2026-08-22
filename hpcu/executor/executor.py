"""Deterministic Action execution with fail-closed command handling."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Optional

from hpcu.input.injector import ExecutionResult, InputInjector
from hpcu.runtime_config import load_runtime_config
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.coordinates import ScreenPoint
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
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

_NO_INPUT_OPS = frozenset(
    {
        ActionOp.WAIT_UNTIL,
        ActionOp.ASSERT,
        ActionOp.READ,
        ActionOp.CHECKPOINT,
    }
)


def _command_for(op: ActionOp) -> str | None:
    if op in _NO_INPUT_OPS:
        return op.value
    return _OP_COMMAND.get(op)


@dataclass(frozen=True)
class PreparedAction:
    """A resolved action bound to the Scene used for grounding."""

    action: Action
    element_id: Optional[str]
    command: str | None
    element: Optional[UIElement] = None
    physical_point: Optional[ScreenPoint] = None
    source_scene_version: int | None = None
    source_frame_id: str | None = None
    source_target_fingerprint: str | None = None


class Executor:
    """Injector-only executor; unsupported operations never become clicks."""

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
        source_scene: Scene | None = None,
    ) -> PreparedAction:
        """Bind an Action to an already resolved target without executing it."""
        frame_id = None
        scene_version = None
        if source_scene is not None:
            scene_version = source_scene.version
            frame_id = source_scene.frame.shm_id if source_scene.frame else None
        return PreparedAction(
            action=action,
            element_id=element_id,
            command=_command_for(action.op),
            element=element,
            physical_point=physical_point,
            source_scene_version=scene_version,
            source_frame_id=frame_id,
            source_target_fingerprint=element.fingerprint if element else None,
        )

    async def execute(
        self,
        prepared: PreparedAction,
        *,
        current_scene: Scene | None = None,
    ) -> ExecutionResult:
        """Execute a prepared action after validating its Scene binding."""
        stale = self._stale_failure(prepared, current_scene)
        if stale is not None:
            return stale

        if prepared.action.op in _NO_INPUT_OPS:
            return ExecutionResult(success=True, mode="none")
        if prepared.command is None:
            return ExecutionResult(
                success=False,
                mode="none",
                failure_code=FailureCode.ACTION_UNSUPPORTED.value,
            )

        if prepared.element is not None:
            semantic_result = await self._injector.semantic(
                prepared.element,
                prepared.command,
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
            mode="none",
            failure_code=FailureCode.INPUT_PHYSICAL_UNSUPPORTED.value,
        )

    @staticmethod
    def _stale_failure(
        prepared: PreparedAction,
        current_scene: Scene | None,
    ) -> ExecutionResult | None:
        if current_scene is None or prepared.source_scene_version is None:
            return None
        if current_scene.version != prepared.source_scene_version:
            return ExecutionResult(
                success=False,
                mode="none",
                failure_code=FailureCode.STALE_DECISION.value,
            )
        if prepared.element_id is None:
            return None
        current = current_scene.get(prepared.element_id)
        if current is None or current.scene_version != current_scene.version:
            return ExecutionResult(
                success=False,
                mode="none",
                failure_code=FailureCode.STALE_DECISION.value,
            )
        expected = prepared.source_target_fingerprint
        if expected and current.fingerprint != expected:
            return ExecutionResult(
                success=False,
                mode="none",
                failure_code=FailureCode.STALE_DECISION.value,
            )
        return None

    async def inject_physical(
        self,
        point: ScreenPoint,
        command: str,
        text: str | None = None,
    ) -> ExecutionResult:
        """Execute a known physical command; reject unknown tokens."""
        op = _COMMAND_TO_OP.get(command)
        if op is None:
            return ExecutionResult(
                success=False,
                mode="none",
                failure_code=FailureCode.ACTION_UNSUPPORTED.value,
            )
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
        """Poll until a predicate holds for the requested stable samples."""
        if timeout_ms < 0:
            raise ValueError("timeout_ms must be non-negative")
        needed = required_stable_polls if required_stable_polls is not None else 1
        if needed <= 0:
            raise ValueError("required_stable_polls must be positive")
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
        self,
        predicate: Callable[[], bool],
        timeout_ms: int,
    ) -> bool:
        return await self.wait_until(
            predicate,
            timeout_ms,
            required_stable_polls=self._required_stable_polls,
        )


def is_valid_command(command: str) -> bool:
    """Return True only for a command implemented by the common executor."""
    return command in _COMMAND_TO_OP
