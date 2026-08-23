"""Unit tests for common perception and geometry-only browser chrome."""

import pytest

from hpcu.perception.consent import consent_elements, is_blocked_scene
from hpcu.perception.engine import (
    crop_png,
    elements_from_ocr,
    looks_like_offer,
    looks_like_product,
    merge_into_lines,
)
from hpcu.perception.window_chrome import (
    content_roi,
    is_in_chrome,
    omnibox_point,
    primary_window,
)
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.scene import Scene
from hpcu.schemas.targeting import TargetingPack
from hpcu.schemas.ui_element import UIElement
from hpcu.vision.ocr import TextRegion

SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


def _bbox(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(space=SPACE, x=x, y=y, width=width, height=height)


def _region(text: str, x: float, y: float, width: float = 40.0) -> TextRegion:
    return TextRegion(
        text=text,
        bbox=_bbox(x, y, width, 14.0),
        confidence=0.9,
        language="kor",
    )


@pytest.mark.unit
def test_looks_like_product_requires_price():
    assert looks_like_product("12,900원 생수") is True
    assert looks_like_product("₩15000") is True
    assert looks_like_product("쿠팡 장바구니") is False
    assert looks_like_product("") is False
    assert looks_like_product("원문 2026-08-21") is False
    assert looks_like_product("1 2 , 9 0 0 원") is True
    assert looks_like_offer("구매하기") is False
    assert looks_like_offer("12,900원") is True
    assert looks_like_offer("결제하기") is False


@pytest.mark.unit
def test_merge_into_lines_joins_same_baseline():
    regions = [
        _region("12,900", 100, 200, 50),
        _region("원", 155, 201, 16),
        _region("생수", 180, 200, 40),
        _region("다음줄", 100, 240, 50),
    ]
    lines = merge_into_lines(regions, y_tolerance=8.0)
    assert len(lines) == 2
    assert lines[0].text == "12,900 원 생수"
    assert looks_like_product(lines[0].text) is True
    assert lines[1].text == "다음줄"


@pytest.mark.unit
def test_elements_from_ocr_tags_product_lines():
    regions = [_region("12,900", 10, 200, 40), _region("원", 55, 200, 16)]
    elements = elements_from_ocr(regions, scene_version=3)
    assert len(elements) == 1
    assert elements[0].id.startswith("ocr_line_")
    assert elements[0].fingerprint.startswith("ocr:")
    assert elements[0].role == "product"
    assert elements[0].scene_version == 3


@pytest.mark.unit
def test_primary_window_prefers_largest():
    small = UIElement(
        id="term",
        scene_version=1,
        name="Terminal",
        bbox=_bbox(0, 0, 300, 200),
    )
    chrome = UIElement(
        id="web",
        scene_version=1,
        name="Chromium",
        bbox=_bbox(0, 0, 1280, 800),
    )
    scene = Scene(version=1, elements={"term": small, "web": chrome})
    window = primary_window(scene)
    assert window is not None
    assert window.id == "web"
    point = omnibox_point(window)
    assert point is not None
    assert 0 < point.x < 1280
    assert 0 < point.y < 80


@pytest.mark.unit
def test_is_in_chrome_skips_toolbar_band():
    window = UIElement(
        id="web",
        scene_version=1,
        name="Chromium",
        bbox=_bbox(0, 0, 1280, 800),
    )
    toolbar = UIElement(
        id="ocr_line_0",
        scene_version=1,
        text="12,900원",
        bbox=_bbox(20, 10, 80, 14),
    )
    content = UIElement(
        id="ocr_line_1",
        scene_version=1,
        text="12,900원",
        bbox=_bbox(20, 200, 80, 14),
    )
    assert is_in_chrome(toolbar, window) is True
    assert is_in_chrome(content, window) is False
    roi = content_roi(window)
    assert roi is not None
    assert roi.y > window.bbox.y
    assert roi.height < window.bbox.height
    assert content_roi(None) is None


@pytest.mark.unit
def test_primary_window_empty_scene():
    scene = Scene(version=1, elements={})
    assert primary_window(scene) is None
    tiny = UIElement(id="x", scene_version=1, bbox=_bbox(0, 0, 10, 10))
    assert primary_window(Scene(version=1, elements={"x": tiny})) is tiny
    assert omnibox_point(UIElement(id="n", scene_version=1)) is None
    assert is_in_chrome(UIElement(id="n", scene_version=1), None) is False
    assert is_in_chrome(tiny, None) is True


@pytest.mark.unit
def test_crop_png_shifts_origin():
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (100, 80), color=(255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    cropped, origin_x, origin_y = crop_png(
        buffer.getvalue(),
        _bbox(10, 20, 40, 30),
    )
    assert origin_x == 10.0
    assert origin_y == 20.0
    out = Image.open(BytesIO(cropped))
    assert out.size == (40, 30)


@pytest.mark.unit
def test_consent_elements_skip_forbid():
    pack = TargetingPack(
        goal_id="test",
        dismiss_any=("동의", "확인"),
        forbid_any=("결제",),
    )
    accept = UIElement(
        id="a",
        scene_version=1,
        text="동의하고 계속",
        bbox=_bbox(10, 10, 80, 20),
    )
    pay = UIElement(
        id="b",
        scene_version=1,
        text="결제하기",
        bbox=_bbox(10, 40, 80, 20),
    )
    scene = Scene(version=1, elements={"a": accept, "b": pay})
    matches = consent_elements(scene, pack)
    assert [item.id for item in matches] == ["a"]


@pytest.mark.unit
def test_is_blocked_scene_detects_anti_bot_copy():
    pack = TargetingPack(
        goal_id="test",
        blocked_any=("비정상적인",),
    )
    wall = UIElement(
        id="ocr_line_0",
        scene_version=1,
        text="네이버는 안정적인 쇼핑 서비스 비정상적인 접근이 감지될 경우",
        bbox=_bbox(10, 200, 400, 40),
    )
    scene = Scene(version=1, elements={"ocr_line_0": wall})
    assert is_blocked_scene(scene, pack) is True
    clean_pack = TargetingPack(goal_id="test", blocked_any=())
    assert is_blocked_scene(scene, clean_pack) is False
