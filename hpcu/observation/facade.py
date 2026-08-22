"""CompositeObserver — facade composing CaptureBackend + StructureObserver.

Produces a SceneDelta (added/removed/modified) against the previous
structure snapshot. Fingerprint matches keep the previous element id.
"""

from dataclasses import replace
from typing import Optional

from hpcu.capture.backend import CaptureBackend
from hpcu.observation.base import Observer
from hpcu.observation.structure_observer import StructureObserver
from hpcu.scene_graph.tracker import ElementTracker
from hpcu.schemas.scene import FrameHandle, SceneDelta
from hpcu.schemas.ui_element import UIElement


class CompositeObserver(Observer):
    """Combines capture (pixels) and structure (tree) into one SceneDelta."""

    def __init__(
        self,
        session_id: str,
        capture_backend: Optional[CaptureBackend] = None,
        structure_observer: Optional[StructureObserver] = None,
        tracker: Optional[ElementTracker] = None,
    ):
        super().__init__(session_id)
        self._validate_backend_session(capture_backend, "capture")
        self._validate_backend_session(structure_observer, "structure")
        self._capture_backend = capture_backend
        self._structure_observer = structure_observer
        self._tracker = tracker if tracker is not None else ElementTracker()
        self._version = 0
        self._capture_started = False
        self._previous: dict[str, UIElement] = {}

    @property
    def capture_backend(self) -> Optional[CaptureBackend]:
        return self._capture_backend

    @property
    def structure_observer(self) -> Optional[StructureObserver]:
        return self._structure_observer

    async def observe(self) -> SceneDelta:
        frame = await self._grab_frame()
        current = await self._read_structure()
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