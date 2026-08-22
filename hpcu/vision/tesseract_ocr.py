"""Tesseract CLI OCR over PNG bytes — OS-agnostic, no platform imports."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.vision.ocr import TextRegion


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def detect_png(
    png_bytes: bytes,
    *,
    languages: str = "kor+eng",
    psm: int = 6,
) -> list[TextRegion]:
    """Run tesseract TSV on a PNG and return word boxes.

    Falls back to `eng` when `languages` is unavailable.
    """
    if not tesseract_available():
        return []
    with tempfile.TemporaryDirectory() as tmp:
        image_path = Path(tmp) / "frame.png"
        image_path.write_bytes(png_bytes)
        raw = _run_tesseract(image_path, languages, psm)
        if raw is None and languages != "eng":
            raw = _run_tesseract(image_path, "eng", psm)
        if not raw:
            return []
        return _parse_tsv(raw)


def _run_tesseract(image_path: Path, languages: str, psm: int) -> str | None:
    try:
        completed = subprocess.run(
            [
                "tesseract",
                str(image_path),
                "stdout",
                "-l",
                languages,
                "--psm",
                str(psm),
                "tsv",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _parse_tsv(tsv: str) -> list[TextRegion]:
    regions: list[TextRegion] = []
    lines = tsv.splitlines()
    if not lines:
        return regions
    header = lines[0].split("\t")
    try:
        i_left = header.index("left")
        i_top = header.index("top")
        i_width = header.index("width")
        i_height = header.index("height")
        i_conf = header.index("conf")
        i_text = header.index("text")
        i_level = header.index("level")
    except ValueError:
        return regions
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) <= max(i_text, i_level):
            continue
        if cols[i_level] != "5":
            continue
        text = (cols[i_text] or "").strip()
        if not text:
            continue
        try:
            conf = float(cols[i_conf])
        except ValueError:
            continue
        if conf < 0:
            continue
        regions.append(
            TextRegion(
                text=text,
                bbox=BoundingBox(
                    space=CoordinateSpace.SCREEN_PHYSICAL_PX,
                    x=float(cols[i_left]),
                    y=float(cols[i_top]),
                    width=float(cols[i_width]),
                    height=float(cols[i_height]),
                ),
                confidence=max(0.0, min(1.0, conf / 100.0)),
                language="kor",
            )
        )
    return regions
