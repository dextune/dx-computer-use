"""Fuse OCR word boxes and optional window regions into UIElements.

This is the common screen-understanding path. Adapters only supply a
FrameHandle (pixels in FrameStore) and optional window rectangles.
"""

from __future__ import annotations

import re
from typing import Optional

from hpcu.capture.frame_store import FrameStore
from hpcu.runtime_config import load_runtime_config
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
    """Price token only — no hardcoded CTA markers.

    CTA detection is now done by the TargetingPack's pick_query.
    """
    return looks_like_product(text)


def crop_png(png: bytes, roi: BoundingBox) -> tuple[bytes, float, float]:
    """Crop PNG to `roi`. Returns (png, origin_x, origin_y)."""
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
    regions: list[TextRegion], origin_x: float, origin_y: float
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
    """Cluster word boxes that share a baseline into line-level regions."""
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
            region.bbox.y < anchor.y + height and region.bbox.y + region.bbox.height > anchor.y
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


def elements_from_ocr(
    regions: list[TextRegion],
    scene_version: int,
    *,
    y_tolerance: float = 8.0,
) -> list[UIElement]:
    """Build UIElements from OCR words, using merged lines as the primary nodes."""
    lines = merge_into_lines(regions, y_tolerance=y_tolerance)
    source_regions = lines if lines else regions
    elements: list[UIElement] = []
    for index, region in enumerate(source_regions):
        product = looks_like_offer(region.text)
        elements.append(
            UIElement(
                id=f"ocr_line_{index}",
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
            )
        )
    return elements


class ScreenPerception:
    """Read pixels from FrameStore and emit OCR elements."""

    def __init__(
        self,
        store: FrameStore,
        *,
        config: Optional[dict] = None,
    ):
        runtime = config if config is not None else load_runtime_config()
        perception = runtime.get("perception", {})
        self._store = store
        self._languages = str(perception.get("ocr_languages", "kor+eng"))
        self._psm = int(perception.get("ocr_psm", 6))
        self._sparse_psm = int(perception.get("ocr_sparse_psm", 11))
        self._min_regions = int(perception.get("ocr_min_regions_for_dense", 8))
        self._y_tolerance = float(perception.get("line_y_tolerance_px", 8))

    def elements_from_frame(
        self,
        frame: FrameHandle,
        scene_version: int,
        roi: Optional[BoundingBox] = None,
    ) -> tuple[UIElement, ...]:
        if frame.shm_id not in self._store:
            return ()
        png = self._store.get(frame.shm_id)
        origin_x = origin_y = 0.0
        if roi is not None:
            png, origin_x, origin_y = crop_png(png, roi)
        regions = detect_png(png, languages=self._languages, psm=self._psm)
        if len(regions) < self._min_regions:
            sparse = detect_png(
                png, languages=self._languages, psm=self._sparse_psm
            )
            if len(sparse) > len(regions):
                regions = sparse
        regions = _offset_regions(regions, origin_x, origin_y)
        return tuple(
            elements_from_ocr(
                regions, scene_version, y_tolerance=self._y_tolerance
            )
        )
