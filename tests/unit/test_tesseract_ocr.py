"""Unit tests for Tesseract TSV parsing (no tesseract binary required)."""

import pytest

from hpcu.vision.tesseract_ocr import _parse_tsv, tesseract_available


SAMPLE = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\t"
    "top\twidth\theight\tconf\ttext\n"
    "5\t1\t1\t1\t1\t1\t10\t20\t40\t14\t95\t12,900\n"
    "5\t1\t1\t1\t1\t2\t55\t20\t16\t14\t90\t원\n"
    "5\t1\t1\t1\t1\t3\t80\t20\t30\t14\t-1\t"
    "\n"
    "4\t1\t1\t1\t1\t0\t10\t20\t100\t14\t90\tignored-line\n"
)


@pytest.mark.unit
def test_parse_tsv_skips_non_words_and_negative_confidence():
    regions = _parse_tsv(SAMPLE)
    assert [region.text for region in regions] == ["12,900", "원"]
    assert regions[0].bbox.x == 10
    assert regions[0].bbox.width == 40
    assert 0.9 <= regions[0].confidence <= 1.0
    assert regions[1].text == "원"


@pytest.mark.unit
def test_parse_tsv_empty_or_header_only():
    assert _parse_tsv("") == []
    assert _parse_tsv("level\ttext\n") == []


@pytest.mark.unit
def test_tesseract_available_is_bool():
    assert tesseract_available() in (True, False)
