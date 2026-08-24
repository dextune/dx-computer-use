"""Executor — prepare and run deterministic actions.

The executor never touches OS input directly. It delegates to an InputInjector
and fails closed when a command or freshness binding is invalid.
"""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

from hpcu.input.injector import ExecutionResult, InputInjector
from hpcu.input.keys import ENTER, FOCUS_LOCATION
from hpcu.lifecycle.launcher import ApplicationLauncher
from hpcu.runtime_config import load_runtime_config
from hpcu.schemas.action import Action, ActionOp
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, ScreenPoint
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
    ActionOp.NAVIGATE: "navigate",
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

_COMMAND_RE = re.compile(r"^[a-z_]+$")


def _command_for(op: ActionOp) -> str:
    return _OP_COMMAND.get(op, op.value)


def is_valid_command(command: str) -> bool:
    return bool(_COMMAND_RE.fullmatch(command))


@dataclass(frozen=True)
class PreparedAction:
    """Resolved action bound to the scene from which it was prepared."""

    action: Action
    element_id: Optional[str]
    command: str
    element: Optional[UIElement] = None
    physical_point: Optional[ScreenPoint] = None
    source_scene_version: int | None = None
    source_frame_id: str | None = None
    target_fingerprint: str | None = None
    target_bbox: BoundingBox | None = None


class Executor:
    def __init__(
        self,
        injector: InputInjector,
        poll_interval_ms: Optional[int] = None,
        required_stable_polls: Optional[int] = None,
        sleep=None,
        monotonic=None,
        config: Optional[dict] = None,
        application_launcher: ApplicationLauncher | None = None,
    ):
        runtime = config if config is not None else load_runtime_config()
        performance = runtime.get("performance", {})
        self._injector = injector
        self._application_launcher = application_launcher
        if (
            application_launcher is not None
            and application_launcher.session_id != injector.session_id
        ):
            raise ValueError("application launcher and input must share session_id")
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

    @property
    def application_launcher(self) -> ApplicationLauncher | None:
        return self._application_launcher

    def prepare(
        self,
        action: Action,
        element_id: Optional[str],
        *,
        element: Optional[UIElement] = None,
        physical_point: Optional[ScreenPoint] = None,
        source_scene: Optional[Scene] = None,
    ) -> PreparedAction:
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
            target_fingerprint=element.fingerprint if element is not None else None,
            target_bbox=element.bbox if element is not None else None,
        )

    async def execute(
        self,
        prepared: PreparedAction,
        *,
        current_scene: Scene | None = None,
    ) -> ExecutionResult:
        """Execute only when the prepared target still belongs to the same scene."""
        stale = self._freshness_failure(prepared, current_scene)
        if stale is not None:
            return stale

        if prepared.action.op is ActionOp.LAUNCH_APPLICATION:
            return await self._launch_application(prepared)

        if prepared.action.op is ActionOp.NAVIGATE:
            return await self._navigate(prepared)

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
            mode="none",
            failure_code=FailureCode.INPUT_PHYSICAL_UNSUPPORTED.value,
        )

    async def _launch_application(
        self, prepared: PreparedAction
    ) -> ExecutionResult:
        application = (prepared.action.value or "").strip()
        launcher = self._application_launcher
        if not application:
            return ExecutionResult(
                success=False,
                mode="application",
                failure_code=FailureCode.ACTION_UNSUPPORTED.value,
            )
        if (
            launcher is None
            or launcher.capabilities().launch is Capability.UNSUPPORTED
        ):
            return ExecutionResult(
                success=False,
                mode="application",
                failure_code=FailureCode.APPLICATION_LAUNCH_UNSUPPORTED.value,
            )
        result = await launcher.launch(
            application,
            candidate_id=prepared.action.target.locator,
            timeout_ms=prepared.action.timeout_ms,
            poll_interval_ms=self._poll_interval_ms,
        )
        return ExecutionResult(
            success=result.success,
            mode="application",
            failure_code=(
                result.failure_code
                if result.failure_code is not None
                else (
                    None
                    if result.success
                    else FailureCode.APPLICATION_LAUNCH_FAILED.value
                )
            ),
            evidence_element_id=result.evidence_element_id,
        )

    async def _navigate(self, prepared: PreparedAction) -> ExecutionResult:
        """Navigate through portable keys while remaining one logical action."""
        url = (prepared.action.value or "").strip()
        point = prepared.physical_point
        if not url or point is None:
            return ExecutionResult(
                success=False,
                mode="none",
                failure_code=FailureCode.ACTION_UNSUPPORTED.value,
            )
        if prepared.element is not None:
            await self._injector.semantic(prepared.element, "focus")
        sequence = (
            ("key", FOCUS_LOCATION),
            ("replace_text", url),
            ("key", ENTER),
        )
        last = ExecutionResult(success=True, mode="physical")
        for command, text in sequence:
            last = await self._injector.physical(point, command, text=text)
            if not last.success:
                return last
        return last

    def _freshness_failure(
        self,
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
        if prepared.source_frame_id is not None:
            current_frame_id = (
                current_scene.frame.shm_id if current_scene.frame else None
            )
            if current_frame_id != prepared.source_frame_id:
                return ExecutionResult(
                    success=False,
                    mode="none",
                    failure_code=FailureCode.STALE_DECISION.value,
                )
        if prepared.element_id is not None:
            current = current_scene.get(prepared.element_id)
            if current is None:
                return ExecutionResult(
                    success=False,
                    mode="none",
                    failure_code=FailureCode.STALE_DECISION.value,
                )
            if (
                prepared.target_fingerprint is not None
                and current.fingerprint != prepared.target_fingerprint
            ):
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
        """Compatibility adapter for a known physical command only."""
        if not is_valid_command(command) or command not in _COMMAND_TO_OP:
            return ExecutionResult(
                success=False,
                mode="none",
                failure_code=FailureCode.ACTION_UNSUPPORTED.value,
            )
        op = _COMMAND_TO_OP[command]
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
        needed = required_stable_polls if required_stable_polls is not None else 1
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
        return await self.wait_until(
            predicate,
            timeout_ms,
            required_stable_polls=self._required_stable_polls,
        )
