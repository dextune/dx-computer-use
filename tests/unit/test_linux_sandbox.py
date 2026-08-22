"""Unit tests for the grok Linux sandbox adapter (fake HTTP, no docker)."""

import base64
import struct

import pytest

from hpcu.platform.linux.sandbox import (
    FrameStore,
    GrokSandboxCapture,
    GrokSandboxInjector,
    GrokSandboxStructure,
    SandboxHttpClient,
    create_sandbox_backends,
    png_dimensions,
    probe,
)
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import CoordinateSpace, ScreenPoint
from hpcu.schemas.ui_element import UIElement


def _png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict:
        return self._payload


class FakeHttp:
    def __init__(self, *, health_ok: bool = True):
        png = _png(1280, 800)
        self._health_ok = health_ok
        self.posts: list[tuple[str, dict | None]] = []
        self._png_b64 = base64.b64encode(png).decode("ascii")

    def get(self, url: str):
        if not self._health_ok:
            return FakeResponse({"ok": False}, status_code=503)
        return FakeResponse({"ok": True, "service": "sandbox-desktop"})

    def post(self, url: str, json: dict | None = None):
        self.posts.append((url, json))
        if url.endswith("screenshot"):
            return FakeResponse({"mimeType": "image/png", "data": self._png_b64})
        payload = json or {}
        cmd = payload.get("cmd")
        args = payload.get("args") or []
        if cmd == "sh":
            return FakeResponse({"ok": True, "stdout": "42\n", "code": 0})
        if cmd == "xdotool" and args[:1] == ["getwindowname"]:
            return FakeResponse({"ok": True, "stdout": "Terminal\n"})
        if cmd == "xdotool" and args[:1] == ["getwindowgeometry"]:
            return FakeResponse(
                {
                    "ok": True,
                    "stdout": "Position: 10,20 (screen: 0)\nGeometry: 100x50\n",
                }
            )
        return FakeResponse({"ok": True, "stdout": "", "code": 0})


@pytest.mark.unit
def test_png_dimensions_reads_ihdr():
    width, height = png_dimensions(_png(64, 32))
    assert width == 64
    assert height == 32


@pytest.mark.unit
def test_png_dimensions_rejects_garbage():
    with pytest.raises(ValueError):
        png_dimensions(b"not-a-png")


@pytest.mark.unit
def test_probe_true_when_health_ok():
    assert probe("http://sandbox.test/", http=FakeHttp(health_ok=True)) is True


@pytest.mark.unit
def test_probe_false_when_health_fails():
    assert probe("http://sandbox.test/", http=FakeHttp(health_ok=False)) is False


@pytest.mark.unit
async def test_capture_grab_stores_png_not_on_handle():
    store = FrameStore()
    http = FakeHttp()
    client = SandboxHttpClient("http://sandbox.test", http=http)
    capture = GrokSandboxCapture("sess", client=client, store=store)
    await capture.start()
    handle = await capture.grab()
    assert handle.width == 1280
    assert handle.height == 800
    assert handle.space is CoordinateSpace.SCREEN_PHYSICAL_PX
    assert not hasattr(handle, "data")
    assert handle.shm_id in store
    assert store.get(handle.shm_id)[:8] == b"\x89PNG\r\n\x1a\n"
    assert capture.capabilities().pixel_grab is Capability.SUPPORTED


@pytest.mark.unit
async def test_capture_grab_before_start_raises():
    capture = GrokSandboxCapture(
        "sess", client=SandboxHttpClient("http://sandbox.test", http=FakeHttp())
    )
    with pytest.raises(RuntimeError):
        await capture.grab()


@pytest.mark.unit
async def test_injector_physical_click_runs_xdotool():
    http = FakeHttp()
    injector = GrokSandboxInjector(
        "sess", client=SandboxHttpClient("http://sandbox.test", http=http)
    )
    point = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=100, y=200)
    result = await injector.physical(point, "click")
    assert result.success is True
    assert result.mode == "physical"
    exec_posts = [item for item in http.posts if item[1] and item[1].get("cmd") == "xdotool"]
    assert exec_posts
    assert exec_posts[0][1]["args"] == ["mousemove", "100", "200", "click", "1"]


@pytest.mark.unit
async def test_injector_semantic_unsupported():
    injector = GrokSandboxInjector(
        "sess", client=SandboxHttpClient("http://sandbox.test", http=FakeHttp())
    )
    result = await injector.semantic(UIElement(id="w", scene_version=1), "invoke")
    assert result.success is False
    assert result.failure_code == "input_semantic_unsupported"


@pytest.mark.unit
async def test_structure_parses_visible_windows():
    structure = GrokSandboxStructure(
        "sess", client=SandboxHttpClient("http://sandbox.test", http=FakeHttp())
    )
    elements = await structure.observe_structure()
    assert len(elements) == 1
    assert elements[0].id == "x11:42"
    assert elements[0].name == "Terminal"
    assert elements[0].bbox is not None
    assert elements[0].bbox.width == 100
    assert elements[0].bbox.height == 50


@pytest.mark.unit
def test_create_sandbox_backends_share_session():
    http = FakeHttp()
    capture, structure, injector = create_sandbox_backends(
        "same-session", base_url="http://sandbox.test", http=http
    )
    assert capture.session_id == structure.session_id == injector.session_id == "same-session"
