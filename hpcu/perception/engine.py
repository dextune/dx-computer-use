"""CPU-first OCR fusion with bounded workers and incremental reuse."""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Optional

from hpcu.capture.frame_store import FrameStore
from hpcu.runtime_config import load_runtime_config
from hpcu.runtime_core.performance import PerformanceSnapshot, PerformanceTrace
from hpcu.runtime_core.resource_policy import LocalResourcePolicy
from hpcu.schemas.coordinates import BoundingBox
from hpcu.schemas.scene import FrameHandle
from hpcu.schemas.ui_element import ElementSource, UIElement
from hpcu.vision.ocr import TextRegion
from hpcu.vision.tesseract_ocr import detect_png

_PRICE_RE = re.compile(
    r"(?:\d{1,3}(?:,\d{3})+\s*원|\d+\s*원|₩\s*\d|KRW\s*\d|\$\s*\d)",
    re.IGNORECASE,
)
_HANGUL_SPACE = re.compile(r"(?<=[가-힣])\s+(?=[가-힣])")


class PerceptionQueueFull(RuntimeError):
    """The bounded local perception queue cannot accept more work."""


def normalize_ocr_text(text: str) -> str:
    """Drop inter-Hangul spaces Tesseract often inserts."""
    return _HANGUL_SPACE.sub("", text or "")


def looks_like_product(text: str) -> bool:
    """Deterministic product-like text: a price token, not a model opinion."""
    if not text:
        return False
    compact = normalize_ocr_text(text).replace(" ", "")
    if "회원가입" in compact or "원문" in compact:
        compact = compact.replace("회원가입", "").replace("원문", "")
    if re.search(r"\d{1,3}(?:,\d{3})+원", compact):
        return True
    if re.search(r"\d{3,}원", compact):
        return True
    if _PRICE_RE.search(text) or _PRICE_RE.search(compact):
        if re.search(r"\d{3,}", compact):
            return True
    if "₩" in compact and any(ch.isdigit() for ch in compact):
        return True
    return False


def looks_like_offer(text: str) -> bool:
    """Price token only; target semantics stay in the TargetingPack."""
    return looks_like_product(text)


def crop_png(png: bytes, roi: BoundingBox) -> tuple[bytes, float, float]:
    """Crop PNG to `roi`. Returns bytes plus the screen-space origin."""
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:
        return png, 0.0, 0.0
    image = Image.open(BytesIO(png)).convert("RGB")
    x0 = max(0, int(roi.x))
    y0 = max(0, int(roi.y))
    x1 = min(image.width, int(roi.x + roi.width))
    y1 = min(image.height, int(roi.y + roi.height))
    if x1 - x0 < 20 or y1 - y0 < 20:
        return png, 0.0, 0.0
    buffer = BytesIO()
    image.crop((x0, y0, x1, y1)).save(buffer, format="PNG")
    return buffer.getvalue(), float(x0), float(y0)


def _offset_regions(
    regions: list[TextRegion],
    origin_x: float,
    origin_y: float,
) -> list[TextRegion]:
    if origin_x == 0.0 and origin_y == 0.0:
        return regions
    shifted: list[TextRegion] = []
    for region in regions:
        box = region.bbox
        shifted.append(
            TextRegion(
                text=region.text,
                bbox=BoundingBox(
                    space=box.space,
                    x=box.x + origin_x,
                    y=box.y + origin_y,
                    width=box.width,
                    height=box.height,
                ),
                confidence=region.confidence,
                language=region.language,
            )
        )
    return shifted


def merge_into_lines(
    regions: list[TextRegion],
    y_tolerance: float = 8.0,
) -> list[TextRegion]:
    """Cluster word boxes sharing a baseline into line-level regions."""
    if not regions:
        return []
    ordered = sorted(regions, key=lambda region: (region.bbox.y, region.bbox.x))
    clusters: list[list[TextRegion]] = []
    for region in ordered:
        if not clusters:
            clusters.append([region])
            continue
        current = clusters[-1]
        anchor = current[0].bbox
        height = max(item.bbox.height for item in current)
        same_line = abs(region.bbox.y - anchor.y) <= y_tolerance or (
            region.bbox.y < anchor.y + height
            and region.bbox.y + region.bbox.height > anchor.y
        )
        if same_line:
            current.append(region)
        else:
            clusters.append([region])
    lines: list[TextRegion] = []
    for group in clusters:
        group.sort(key=lambda region: region.bbox.x)
        text = " ".join(region.text for region in group)
        x0 = min(region.bbox.x for region in group)
        y0 = min(region.bbox.y for region in group)
        x1 = max(region.bbox.x + region.bbox.width for region in group)
        y1 = max(region.bbox.y + region.bbox.height for region in group)
        confidence = sum(region.confidence for region in group) / len(group)
        lines.append(
            TextRegion(
                text=text,
                bbox=BoundingBox(
                    space=group[0].bbox.space,
                    x=x0,
                    y=y0,
                    width=x1 - x0,
                    height=y1 - y0,
                ),
                confidence=confidence,
                language=group[0].language,
            )
        )
    return lines


def _ocr_identity(region: TextRegion) -> str:
    box = region.bbox
    normalized = " ".join(normalize_ocr_text(region.text).casefold().split())
    geometry = (
        round(box.x / 8),
        round(box.y / 8),
        round(box.width / 8),
        round(box.height / 8),
    )
    payload = f"{normalized}|{geometry}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def elements_from_ocr(
    regions: list[TextRegion],
    scene_version: int,
    *,
    y_tolerance: float = 8.0,
) -> list[UIElement]:
    """Build stable OCR elements from merged line regions."""
    lines = merge_into_lines(regions, y_tolerance=y_tolerance)
    source_regions = lines if lines else regions
    elements: list[UIElement] = []
    for region in source_regions:
        product = looks_like_offer(region.text)
        identity = _ocr_identity(region)
        elements.append(
            UIElement(
                id=f"ocr_line_{identity}",
                scene_version=scene_version,
                role="product" if product else "text",
                name=region.text,
                text=region.text,
                bbox=region.bbox,
                semantic_tags=("product",) if product else (),
                sources=(
                    ElementSource(
                        type="ocr",
                        text=region.text,
                        confidence=region.confidence,
                    ),
                ),
                fingerprint=f"ocr:{identity}",
            )
        )
    return elements


def _roi_signature(roi: BoundingBox | None) -> tuple[object, ...]:
    if roi is None:
        return ()
    return (roi.space.value, roi.x, roi.y, roi.width, roi.height)


def _intersection(
    first: BoundingBox,
    second: BoundingBox,
) -> BoundingBox | None:
    if first.space is not second.space:
        return None
    x0 = max(first.x, second.x)
    y0 = max(first.y, second.y)
    x1 = min(first.x + first.width, second.x + second.width)
    y1 = min(first.y + first.height, second.y + second.height)
    if x1 <= x0 or y1 <= y0:
        return None
    return BoundingBox(first.space, x0, y0, x1 - x0, y1 - y0)


def _union(rects: tuple[BoundingBox, ...]) -> BoundingBox | None:
    if not rects:
        return None
    space = rects[0].space
    compatible = tuple(rect for rect in rects if rect.space is space)
    if not compatible:
        return None
    x0 = min(rect.x for rect in compatible)
    y0 = min(rect.y for rect in compatible)
    x1 = max(rect.x + rect.width for rect in compatible)
    y1 = max(rect.y + rect.height for rect in compatible)
    return BoundingBox(space, x0, y0, x1 - x0, y1 - y0)


def _overlaps(element: UIElement, roi: BoundingBox) -> bool:
    box = element.bbox
    return box is not None and _intersection(box, roi) is not None


class ScreenPerception:
    """Read FrameStore pixels using bounded, cached CPU OCR workers."""

    def __init__(
        self,
        store: FrameStore,
        *,
        config: Optional[dict] = None,
        resource_policy: LocalResourcePolicy | None = None,
        performance_trace: PerformanceTrace | None = None,
    ):
        runtime = config if config is not None else load_runtime_config()
        perception = runtime.get("perception", {})
        self._store = store
        self._languages = str(perception.get("ocr_languages", "kor+eng"))
        self._psm = int(perception.get("ocr_psm", 6))
        self._sparse_psm = int(perception.get("ocr_sparse_psm", 11))
        self._min_regions = int(perception.get("ocr_min_regions_for_dense", 8))
        self._y_tolerance = float(perception.get("line_y_tolerance_px", 8))
        self._cache_entries = max(
            1,
            int(perception.get("ocr_cache_entries", 32)),
        )
        self._dirty_roi_max_ratio = float(
            perception.get("dirty_roi_max_ratio", 0.35)
        )
        if not 0.0 <= self._dirty_roi_max_ratio <= 1.0:
            raise ValueError("dirty_roi_max_ratio must be between 0 and 1")
        self._resource_policy = resource_policy or LocalResourcePolicy.from_config(
            runtime
        )
        self._performance = performance_trace or PerformanceTrace()
        self._semaphore = asyncio.Semaphore(
            self._resource_policy.perception_workers
        )
        self._queue_lock = threading.Lock()
        self._submitted = 0
        self._state_lock = threading.RLock()
        self._cache: OrderedDict[str, tuple[UIElement, ...]] = OrderedDict()
        self._last_elements: tuple[UIElement, ...] = ()
        self._last_roi: tuple[object, ...] = ()
        self._last_timestamp_ns = -1

    @property
    def resource_policy(self) -> LocalResourcePolicy:
        return self._resource_policy

    @property
    def performance_snapshot(self) -> PerformanceSnapshot:
        return self._performance.snapshot()

    async def elements_from_frame_async(
        self,
        frame: FrameHandle,
        scene_version: int,
        roi: Optional[BoundingBox] = None,
    ) -> tuple[UIElement, ...]:
        """Run OCR in a bounded worker without blocking the event loop."""
        with self._queue_lock:
            if self._submitted >= self._resource_policy.perception_queue_depth:
                raise PerceptionQueueFull("local perception queue is full")
            self._submitted += 1
            queue_depth = max(
                0,
                self._submitted - self._resource_policy.perception_workers,
            )
        wait_started = time.perf_counter_ns()
        try:
            await self._semaphore.acquire()
        except BaseException:
            self._decrement_submitted()
            raise
        self._performance.record(
            "ocr_queue_wait",
            (time.perf_counter_ns() - wait_started) // 1000,
            queue_depth=queue_depth,
        )

        worker = asyncio.create_task(
            asyncio.to_thread(
                self.elements_from_frame,
                frame,
                scene_version,
                roi,
            )
        )
        deferred_release = False
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            deferred_release = True
            worker.add_done_callback(self._release_after_worker)
            raise
        finally:
            if not deferred_release:
                self._release_worker_slot()

    def elements_from_frame(
        self,
        frame: FrameHandle,
        scene_version: int,
        roi: Optional[BoundingBox] = None,
    ) -> tuple[UIElement, ...]:
        if frame.shm_id not in self._store:
            return ()
        with self._performance.measure("ocr_total"):
            png = self._store.get(frame.shm_id)
            previous, previous_roi = self._previous_state()
            selected_roi, incremental, unchanged = self._select_roi(
                frame,
                roi,
                previous,
                previous_roi,
            )
            if unchanged:
                result = tuple(
                    replace(element, scene_version=scene_version)
                    for element in previous
                )
                self._remember(frame, roi, result)
                return result

            cache_key = self._cache_key(png, selected_roi)
            cached = self._cache_get(cache_key)
            if cached is not None:
                result = tuple(
                    replace(element, scene_version=scene_version)
                    for element in cached
                )
                self._remember(frame, roi, result)
                self._performance.record("ocr_cache_hit", 0)
                return result

            ocr_png = png
            origin_x = origin_y = 0.0
            if selected_roi is not None:
                ocr_png, origin_x, origin_y = crop_png(png, selected_roi)
            with self._performance.measure("ocr_dense"):
                regions = detect_png(
                    ocr_png,
                    languages=self._languages,
                    psm=self._psm,
                )
            if len(regions) < self._min_regions:
                with self._performance.measure("ocr_sparse"):
                    sparse = detect_png(
                        ocr_png,
                        languages=self._languages,
                        psm=self._sparse_psm,
                    )
                if len(sparse) > len(regions):
                    regions = sparse
            regions = _offset_regions(regions, origin_x, origin_y)
            detected = tuple(
                elements_from_ocr(
                    regions,
                    scene_version,
                    y_tolerance=self._y_tolerance,
                )
            )
            result = self._merge_incremental(
                previous,
                detected,
                selected_roi,
                incremental,
                scene_version,
            )
            self._cache_put(cache_key, result)
            self._remember(frame, roi, result)
            return result

    def _select_roi(
        self,
        frame: FrameHandle,
        requested: BoundingBox | None,
        previous: tuple[UIElement, ...],
        previous_roi: tuple[object, ...],
    ) -> tuple[BoundingBox | None, bool, bool]:
        if not previous or previous_roi != _roi_signature(requested):
            return requested, False, False
        dirty = _union(frame.dirty_rects)
        if dirty is None:
            return requested, False, False
        if requested is not None:
            dirty = _intersection(dirty, requested)
            if dirty is None:
                return None, True, True
            total_area = requested.area
        else:
            total_area = float(frame.width * frame.height)
        ratio = dirty.area / total_area if total_area > 0 else 1.0
        if ratio <= self._dirty_roi_max_ratio:
            return dirty, True, False
        return requested, False, False

    @staticmethod
    def _merge_incremental(
        previous: tuple[UIElement, ...],
        detected: tuple[UIElement, ...],
        dirty_roi: BoundingBox | None,
        incremental: bool,
        scene_version: int,
    ) -> tuple[UIElement, ...]:
        if not incremental or dirty_roi is None:
            return detected
        merged = {
            element.id: replace(element, scene_version=scene_version)
            for element in previous
            if not _overlaps(element, dirty_roi)
        }
        merged.update({element.id: element for element in detected})
        return tuple(
            sorted(
                merged.values(),
                key=lambda item: (
                    item.bbox.y if item.bbox is not None else 0.0,
                    item.bbox.x if item.bbox is not None else 0.0,
                    item.id,
                ),
            )
        )

    def _previous_state(
        self,
    ) -> tuple[tuple[UIElement, ...], tuple[object, ...]]:
        with self._state_lock:
            return self._last_elements, self._last_roi

    def _remember(
        self,
        frame: FrameHandle,
        roi: BoundingBox | None,
        elements: tuple[UIElement, ...],
    ) -> None:
        with self._state_lock:
            if frame.timestamp_ns < self._last_timestamp_ns:
                return
            self._last_timestamp_ns = frame.timestamp_ns
            self._last_roi = _roi_signature(roi)
            self._last_elements = elements

    def _cache_key(self, png: bytes, roi: BoundingBox | None) -> str:
        digest = hashlib.sha256(png).hexdigest()
        options = (
            self._languages,
            self._psm,
            self._sparse_psm,
            self._min_regions,
            self._y_tolerance,
            _roi_signature(roi),
        )
        return hashlib.sha256(f"{digest}|{options}".encode()).hexdigest()

    def _cache_get(self, key: str) -> tuple[UIElement, ...] | None:
        with self._state_lock:
            value = self._cache.pop(key, None)
            if value is not None:
                self._cache[key] = value
            return value

    def _cache_put(self, key: str, value: tuple[UIElement, ...]) -> None:
        with self._state_lock:
            self._cache.pop(key, None)
            self._cache[key] = value
            while len(self._cache) > self._cache_entries:
                self._cache.popitem(last=False)

    def _release_after_worker(self, worker: asyncio.Task) -> None:
        try:
            worker.exception()
        except (asyncio.CancelledError, Exception):
            pass
        self._release_worker_slot()

    def _release_worker_slot(self) -> None:
        self._semaphore.release()
        self._decrement_submitted()

    def _decrement_submitted(self) -> None:
        with self._queue_lock:
            self._submitted = max(0, self._submitted - 1)
