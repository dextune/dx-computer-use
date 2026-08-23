"""Tesseract CLI OCR over PNG bytes through stdin."""

from __future__ import annotations

import shutil
import subprocess

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
    """Run Tesseract TSV without creating a temporary image file."""
    if not tesseract_available():
        return []
    raw = _run_tesseract(png_bytes, languages, psm)
    used_language = languages
    if raw is None and languages != "eng":
        raw = _run_tesseract(png_bytes, "eng", psm)
        used_language = "eng"
    if not raw:
        return []
    return _parse_tsv(raw, used_language)


def _run_tesseract(png_bytes: bytes, languages: str, psm: int) -> str | None:
    try:
        completed = subprocess.run(
            [
                "tesseract",
                "stdin",
                "stdout",
                "-l",
                languages,
                "--psm",
                str(psm),
                "tsv",
            ],
            input=png_bytes,
            check=False,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace")


def _parse_tsv(tsv: str, language: str = "") -> list[TextRegion]:
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
                language=language,
            )
        )
    return regions
