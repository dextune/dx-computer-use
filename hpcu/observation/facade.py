"""CompositeObserver — facade composing CaptureBackend + StructureObserver.

Produces a SceneDelta (added/removed/modified) against the previous
structure snapshot. Cross-source observations are fused before temporal
ID remapping and SceneDelta construction.
"""

import asyncio
from dataclasses import dataclass, field, replace
from typing import Optional

from hpcu.capture.backend import CaptureBackend
from hpcu.capture.frame_store import FrameStore
from hpcu.observation.base import Observer
from hpcu.observation.structure_observer import StructureObserver
from hpcu.perception.engine import ScreenPerception
from hpcu.perception.fusion import FusionEngine
from hpcu.perception.window_chrome import content_roi, primary_window
from hpcu.scene_graph.tracker import ElementTracker
from hpcu.schemas.scene import FrameHandle, Scene, SceneDelta
from hpcu.schemas.ui_element import UIElement


class ObservationTransactionTimeout(TimeoutError):
    """The observer's own transaction deadline expired."""


@dataclass
class _Metric:
    count: int = 0
    total_latency_ms: int = 0
    error_count: int = 0
    timeout_count: int = 0


@dataclass
class PerformanceSnapshot:
    capture: _Metric = field(default_factory=_Metric)
    structure: _Metric = field(default_factory=_Metric)
    capture_structure_transaction: _Metric = field(default_factory=_Metric)

    def as_dict(self) -> dict:
        return {
            "capture": {
                "count": self.capture.count,
                "total_latency_ms": self.capture.total_latency_ms,
                "error_count": self.capture.error_count,
                "timeout_count": self.capture.timeout_count,
            },
            "structure": {
                "count": self.structure.count,
                "total_latency_ms": self.structure.total_latency_ms,
                "error_count": self.structure.error_count,
                "timeout_count": self.structure.timeout_count,
            },
            "capture_structure_transaction": {
                "count": self.capture_structure_transaction.count,
                "total_latency_ms": (
                    self.capture_structure_transaction.total_latency_ms
                ),
                "error_count": self.capture_structure_transaction.error_count,
                "timeout_count": (
                    self.capture_structure_transaction.timeout_count
                ),
            },
        }


class CompositeObserver(Observer):
    """Combines capture, structure and local perception into one SceneDelta."""

    def __init__(
        self,
        session_id: str,
        capture_backend: Optional[CaptureBackend] = None,
        structure_observer: Optional[StructureObserver] = None,
        tracker: Optional[ElementTracker] = None,
        perception: Optional[ScreenPerception] = None,
        config: Optional[dict] = None,
        fusion_engine: Optional[FusionEngine] = None,
    ):
        super().__init__(session_id)
        self._validate_backend_session(capture_backend, "capture")
        self._validate_backend_session(structure_observer, "structure")
        self._capture_backend = capture_backend
        self._structure_observer = structure_observer
        self._perception = (
            perception
            if perception is not None
            else _auto_perception(capture_backend)
        )
        self._fusion_engine = (
            fusion_engine if fusion_engine is not None else FusionEngine()
        )
        self._tracker = tracker if tracker is not None else ElementTracker()
        self._config = config
        self._version = 0
        self._capture_started = False
        self._previous: dict[str, UIElement] = {}
        self._observe_lock = asyncio.Lock()
        self.performance_snapshot = PerformanceSnapshot()

    @property
    def capture_backend(self) -> Optional[CaptureBackend]:
        return self._capture_backend

    @property
    def structure_observer(self) -> Optional[StructureObserver]:
        return self._structure_observer

    async def observe(self) -> SceneDelta:
        """Run one stateful observation transaction at a time."""
        async with self._observe_lock:
            return await self._observe_serialized()

    async def _observe_serialized(self) -> SceneDelta:
        timeout_ms = None
        if self._config is not None:
            performance = self._config.get("performance", {})
            if not isinstance(performance, dict):
                performance = {}
            timeout_ms = performance.get("observe_timeout_ms")

        if timeout_ms is not None:
            if not isinstance(timeout_ms, (int, float)):
                timeout_ms = None
            elif timeout_ms <= 0:
                raise ValueError("observe_timeout_ms must be positive")

        metric = self.performance_snapshot.capture_structure_transaction
        t0 = asyncio.get_running_loop().time() * 1000
        transaction = asyncio.create_task(self._observe_transaction())
        try:
            if timeout_ms is None:
                result = await transaction
            else:
                done, _pending = await asyncio.wait(
                    {transaction},
                    timeout=timeout_ms / 1000.0,
                )
                if transaction not in done:
                    await self._cancel_and_wait(transaction)
                    metric.timeout_count += 1
                    raise ObservationTransactionTimeout(
                        "observation transaction timed out"
                    )
                result = transaction.result()
        except ObservationTransactionTimeout:
            raise
        except asyncio.CancelledError:
            await self._cancel_and_wait(transaction)
            raise
        except Exception:
            metric.error_count += 1
            await self._cancel_and_wait(transaction)
            raise
        else:
            elapsed = int(
                asyncio.get_running_loop().time() * 1000 - t0
            )
            metric.total_latency_ms += elapsed
        return result

    @staticmethod
    async def _cancel_and_wait(task: asyncio.Task[SceneDelta]) -> None:
        if task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _observe_transaction(self) -> SceneDelta:
        """Grab frame and read structure in parallel, then build delta."""
        frame_task = asyncio.create_task(self._grab_frame())
        structure_task = asyncio.create_task(self._read_structure())
        tasks = (frame_task, structure_task)
        try:
            frame, current = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        self.performance_snapshot.capture_structure_transaction.count += 1

        if self._perception is not None and frame is not None:
            roi = content_roi(
                primary_window(Scene(version=0, elements=current))
            )
            perceived = await self._perception.elements_from_frame_async(
                frame,
                self._version + 1,
                roi=roi,
            )
            if not perceived and roi is not None:
                perceived = await self._perception.elements_from_frame_async(
                    frame,
                    self._version + 1,
                    roi=None,
                )
            for element in perceived:
                current[element.id] = element

        if current:
            fused = self._fusion_engine.fuse(current.values())
            current = {element.id: element for element in fused.elements}

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
        metric = self.performance_snapshot.capture
        t0 = asyncio.get_running_loop().time() * 1000
        try:
            if self._capture_backend is None:
                result = None
            else:
                if not self._capture_started:
                    await self._capture_backend.start()
                    self._capture_started = True
                result = await self._capture_backend.grab()
        except TimeoutError:
            metric.timeout_count += 1
            raise
        except Exception:
            metric.error_count += 1
            raise
        else:
            metric.count += 1
            metric.total_latency_ms += int(
                asyncio.get_running_loop().time() * 1000 - t0
            )
            return result

    async def _read_structure(self) -> dict[str, UIElement]:
        metric = self.performance_snapshot.structure
        t0 = asyncio.get_running_loop().time() * 1000
        try:
            if self._structure_observer is None:
                result: dict[str, UIElement] = {}
            else:
                snapshot = (
                    await self._structure_observer.observe_structure()
                )
                result = {element.id: element for element in snapshot}
        except TimeoutError:
            metric.timeout_count += 1
            raise
        except Exception:
            metric.error_count += 1
            raise
        else:
            metric.count += 1
            metric.total_latency_ms += int(
                asyncio.get_running_loop().time() * 1000 - t0
            )
            return result

    def _remap_ids(
        self,
        current: dict[str, UIElement],
    ) -> dict[str, UIElement]:
        if not self._previous:
            return dict(current)
        fingerprint_map = self._tracker.match_by_fingerprint(
            current,
            self._previous,
        )
        remapped: dict[str, UIElement] = {}
        for current_id, element in current.items():
            previous_id = fingerprint_map.get(current_id, current_id)
            if previous_id != current_id:
                remapped[previous_id] = replace(
                    element,
                    id=previous_id,
                )
            else:
                remapped[current_id] = element
        return remapped

    @staticmethod
    def _diff(
        previous: dict[str, UIElement],
        current: dict[str, UIElement],
    ) -> tuple[
        tuple[UIElement, ...],
        tuple[str, ...],
        tuple[UIElement, ...],
    ]:
        added = tuple(
            current[key] for key in current if key not in previous
        )
        removed = tuple(
            key for key in previous if key not in current
        )
        modified = tuple(
            current[key]
            for key in current
            if key in previous and current[key] != previous[key]
        )
        return added, removed, modified

    def _validate_backend_session(
        self,
        backend: object,
        label: str,
    ) -> None:
        backend_session = getattr(backend, "session_id", None)
        if (
            backend_session is not None
            and backend_session != self._session_id
        ):
            raise ValueError(
                "CompositeObserver session mismatch: "
                f"{label} backend is bound to {backend_session!r}, "
                f"observer to {self._session_id!r}"
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
