"""CompositeObserver — facade composing CaptureBackend + StructureObserver.

Produces a SceneDelta (added/removed/modified) against the previous
structure snapshot. Fingerprint matches keep the previous element id.
"""

import asyncio
from dataclasses import dataclass, field, replace
from typing import Optional

from hpcu.capture.backend import CaptureBackend
from hpcu.capture.frame_store import FrameStore
from hpcu.observation.base import Observer
from hpcu.observation.structure_observer import StructureObserver
from hpcu.perception.engine import ScreenPerception
from hpcu.perception.window_chrome import content_roi, primary_window
from hpcu.scene_graph.tracker import ElementTracker
from hpcu.schemas.scene import FrameHandle, Scene, SceneDelta
from hpcu.schemas.ui_element import UIElement


@dataclass
class _Metric:
    count: int = 0


@dataclass
class PerformanceSnapshot:
    capture: _Metric = field(default_factory=_Metric)
    structure: _Metric = field(default_factory=_Metric)
    capture_structure_transaction: _Metric = field(default_factory=_Metric)

    def as_dict(self) -> dict:
        return {
            "capture": {"count": self.capture.count},
            "structure": {"count": self.structure.count},
            "capture_structure_transaction": {
                "count": self.capture_structure_transaction.count,
            },
        }


class CompositeObserver(Observer):
    """Combines capture (pixels) and structure (tree) into one SceneDelta."""

    def __init__(
        self,
        session_id: str,
        capture_backend: Optional[CaptureBackend] = None,
        structure_observer: Optional[StructureObserver] = None,
        tracker: Optional[ElementTracker] = None,
        perception: Optional[ScreenPerception] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(session_id)
        self._validate_backend_session(capture_backend, "capture")
        self._validate_backend_session(structure_observer, "structure")
        self._capture_backend = capture_backend
        self._structure_observer = structure_observer
        self._perception = perception if perception is not None else _auto_perception(
            capture_backend
        )
        self._tracker = tracker if tracker is not None else ElementTracker()
        self._config = config
        self._version = 0
        self._capture_started = False
        self._previous: dict[str, UIElement] = {}
        self.performance_snapshot = PerformanceSnapshot()

    @property
    def capture_backend(self) -> Optional[CaptureBackend]:
        return self._capture_backend

    @property
    def structure_observer(self) -> Optional[StructureObserver]:
        return self._structure_observer

    async def observe(self) -> SceneDelta:
        timeout_ms = None
        if self._config is not None:
            performance = self._config.get("performance", {})
            timeout_ms = performance.get("observe_timeout_ms")

        if timeout_ms is not None:
            try:
                result = await asyncio.wait_for(
                    self._observe_transaction(),
                    timeout=timeout_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                raise TimeoutError("observation transaction timed out") from None
        else:
            result = await self._observe_transaction()
        return result

    async def _observe_transaction(self) -> SceneDelta:
        """Grab frame and read structure in parallel, then build delta."""
        frame_task = asyncio.create_task(self._grab_frame())
        structure_task = asyncio.create_task(self._read_structure())

        frame, current = await asyncio.gather(frame_task, structure_task)

        self.performance_snapshot.capture.count += 1
        self.performance_snapshot.structure.count += 1
        self.performance_snapshot.capture_structure_transaction.count += 1

        if self._perception is not None and frame is not None:
            roi = content_roi(primary_window(Scene(version=0, elements=current)))
            perceived = self._perception.elements_from_frame(
                frame, self._version + 1, roi=roi
            )
            if not perceived and roi is not None:
                perceived = self._perception.elements_from_frame(
                    frame, self._version + 1, roi=None
                )
            for element in perceived:
                current[element.id] = element
        remapped = self._remap_ids(current)
        base_version = self._version
        self._version += 1
        added, removed, modified = self._diff(self._previous, remapped)
        self._previous = remapped
        return SceneDelta(
            base_version=base_version,
            new_version=self._version,
            added=added,
            removed=removed,
            modified=modified,
            frame=frame,
        )

    async def _grab_frame(self) -> Optional[FrameHandle]:
        if self._capture_backend is None:
            return None
        if not self._capture_started:
            await self._capture_backend.start()
            self._capture_started = True
        return await self._capture_backend.grab()

    async def _read_structure(self) -> dict[str, UIElement]:
        if self._structure_observer is None:
            return {}
        snapshot = await self._structure_observer.observe_structure()
        return {element.id: element for element in snapshot}

    def _remap_ids(self, current: dict[str, UIElement]) -> dict[str, UIElement]:
        if not self._previous:
            return dict(current)
        fingerprint_map = self._tracker.match_by_fingerprint(current, self._previous)
        remapped: dict[str, UIElement] = {}
        for current_id, element in current.items():
            previous_id = fingerprint_map.get(current_id, current_id)
            if previous_id != current_id:
                remapped[previous_id] = replace(element, id=previous_id)
            else:
                remapped[current_id] = element
        return remapped

    @staticmethod
    def _diff(
        previous: dict[str, UIElement],
        current: dict[str, UIElement],
    ) -> tuple[tuple[UIElement, ...], tuple[str, ...], tuple[UIElement, ...]]:
        added = tuple(current[key] for key in current if key not in previous)
        removed = tuple(key for key in previous if key not in current)
        modified = tuple(
            current[key]
            for key in current
            if key in previous and current[key] != previous[key]
        )
        return added, removed, modified

    def _validate_backend_session(self, backend: object, label: str) -> None:
        backend_session = getattr(backend, "session_id", None)
        if backend_session is not None and backend_session != self._session_id:
            raise ValueError(
                f"CompositeObserver session mismatch: {label} backend is bound to "
                f"{backend_session!r}, observer to {self._session_id!r}"
            )


def _auto_perception(
    capture_backend: Optional[CaptureBackend],
) -> Optional[ScreenPerception]:
    """Wire OCR when the capture backend already owns a FrameStore.

    Other environments get perception for free: put PNG bytes in the
    store; this facade never needs an OS-specific OCR path.
    """
    if capture_backend is None:
        return None
    store = getattr(capture_backend, "store", None)
    if not isinstance(store, FrameStore):
        return None
    return ScreenPerception(store)