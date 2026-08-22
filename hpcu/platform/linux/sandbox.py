"""Grok Linux sandbox adapter — talks to grokbot-gateway-sandbox HTTP API.

The sandbox exposes:

- GET  /health
- POST /screenshot  -> {mimeType, data: base64 png}
- POST /exec        -> {cmd, args, timeout} run inside DISPLAY=:99

Common runtime never imports X11. This plugin only uses HTTP.
"""

from __future__ import annotations

import base64
import struct
import time
from typing import Any, Optional
from urllib.parse import urljoin

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.capture.frame_store import FrameStore
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.input.keys import ENTER, ESCAPE, FOCUS_LOCATION, NEW_TAB, PAGE_DOWN, SELECT_ALL
from hpcu.observation.structure_observer import StructureCapabilities, StructureObserver
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace, ScreenPoint
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import FrameHandle
from hpcu.schemas.ui_element import ElementSource, UIElement

_DEFAULT_BASE_URL = "http://127.0.0.1:1337"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PORTABLE_KEYS = {
    ENTER: "Return",
    "Return": "Return",
    SELECT_ALL: "ctrl+a",
    FOCUS_LOCATION: "ctrl+l",
    ESCAPE: "Escape",
    PAGE_DOWN: "Page_Down",
    NEW_TAB: "ctrl+t",
}


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Read width/height from a PNG IHDR without decoding pixels."""
    if len(data) < 24 or data[:8] != _PNG_MAGIC:
        raise ValueError("screenshot is not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


class SandboxHttpClient:
    """Thin HTTP wrapper; `http` is any object with get/post like httpx."""

    def __init__(self, base_url: str = _DEFAULT_BASE_URL, http: Optional[Any] = None):
        self.base_url = base_url.rstrip("/") + "/"
        self._http = http

    def _client(self) -> Any:
        if self._http is not None:
            return self._http
        import httpx

        self._http = httpx.Client(timeout=20.0)
        return self._http

    def health(self) -> dict:
        response = self._client().get(urljoin(self.base_url, "health"))
        response.raise_for_status()
        return response.json()

    def screenshot_png(self) -> bytes:
        response = self._client().post(urljoin(self.base_url, "screenshot"))
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if not data:
            raise RuntimeError("sandbox screenshot returned no data")
        return base64.b64decode(data)

    def exec(self, cmd: str, args: list[str], timeout_ms: int = 10_000) -> dict:
        response = self._client().post(
            urljoin(self.base_url, "exec"),
            json={"cmd": cmd, "args": args, "timeout": timeout_ms},
        )
        response.raise_for_status()
        return response.json()


def probe(base_url: str = _DEFAULT_BASE_URL, http: Optional[Any] = None) -> bool:
    """True when the grok sandbox health endpoint answers ok."""
    try:
        payload = SandboxHttpClient(base_url, http=http).health()
    except Exception:
        return False
    return bool(payload.get("ok"))


class GrokSandboxCapture(CaptureBackend):
    """Capture Xvfb via sandbox POST /screenshot."""

    def __init__(
        self,
        session_id: str,
        *,
        client: Optional[SandboxHttpClient] = None,
        store: Optional[FrameStore] = None,
    ):
        super().__init__(session_id)
        self._client = client or SandboxHttpClient()
        self._store = store if store is not None else FrameStore()
        self._started = False

    @property
    def store(self) -> FrameStore:
        return self._store

    async def start(self) -> None:
        health = self._client.health()
        if not health.get("ok"):
            raise RuntimeError("grok sandbox health is not ok")
        self._started = True

    async def grab(self) -> FrameHandle:
        if not self._started:
            raise RuntimeError("CaptureBackend not started")
        png = self._client.screenshot_png()
        width, height = png_dimensions(png)
        shm_id = f"sandbox-{self.session_id}-{time.time_ns()}"
        self._store.put(shm_id, png)
        return FrameHandle(
            shm_id=shm_id,
            width=width,
            height=height,
            stride=width * 4,
            pixel_format="BGRA",
            timestamp_ns=time.time_ns(),
            space=CoordinateSpace.SCREEN_PHYSICAL_PX,
            source="x11",
        )

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(
            pixel_grab=Capability.SUPPORTED,
            dirty_rects=Capability.UNSUPPORTED,
            max_fps=2,
        )


class GrokSandboxInjector(InputInjector):
    """Physical pointer/keyboard via xdotool inside the sandbox."""

    def __init__(
        self,
        session_id: str,
        *,
        client: Optional[SandboxHttpClient] = None,
    ):
        super().__init__(session_id)
        self._client = client or SandboxHttpClient()

    async def semantic(self, element: UIElement, action: str) -> ExecutionResult:
        if action in ("focus", "invoke") and element.sources:
            ref = element.sources[0].ref
            if ref:
                activated = self._client.exec("xdotool", ["windowactivate", ref])
                self._client.exec("xdotool", ["windowraise", ref])
                ok = bool(activated.get("ok")) or activated.get("code") in (0, "0", None)
                return ExecutionResult(
                    success=ok,
                    mode="semantic",
                    failure_code=None if ok else FailureCode.INPUT_SEMANTIC_UNSUPPORTED.value,
                )
        return ExecutionResult(
            success=False,
            mode="semantic",
            failure_code=FailureCode.INPUT_SEMANTIC_UNSUPPORTED.value,
        )

    async def physical(
        self, point: ScreenPoint, action: str, text: str | None = None
    ) -> ExecutionResult:
        x = int(round(point.x))
        y = int(round(point.y))
        if action in ("click", "invoke"):
            result = self._client.exec(
                "xdotool", ["mousemove", str(x), str(y), "click", "1"]
            )
        elif action in ("double_click",):
            result = self._client.exec(
                "xdotool", ["mousemove", str(x), str(y), "click", "--repeat", "2", "1"]
            )
        elif action in ("right_click",):
            result = self._client.exec(
                "xdotool", ["mousemove", str(x), str(y), "click", "3"]
            )
        elif action in ("type", "replace_text"):
            result = self._client.exec(
                "xdotool",
                ["type", "--delay", "0", "--clearmodifiers", "--", text or ""],
            )
        elif action in ("key", "hotkey"):
            mapped = _PORTABLE_KEYS.get(text or ENTER, text or "Return")
            result = self._client.exec("xdotool", ["key", mapped])
        else:
            result = self._client.exec(
                "xdotool", ["mousemove", str(x), str(y)]
            )
        ok = bool(result.get("ok")) or result.get("code") in (0, "0", None)
        return ExecutionResult(
            success=ok,
            mode="physical",
            failure_code=None if ok else FailureCode.INPUT_PHYSICAL_UNSUPPORTED.value,
        )

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(
            semantic_invoke=Capability.UNSUPPORTED,
            physical_pointer=Capability.SUPPORTED,
            physical_keyboard=Capability.SUPPORTED,
        )


class GrokSandboxStructure(StructureObserver):
    """Visible X11 windows via xdotool search/getwindowgeometry."""

    def __init__(
        self,
        session_id: str,
        *,
        client: Optional[SandboxHttpClient] = None,
    ):
        super().__init__(session_id)
        self._client = client or SandboxHttpClient()

    async def observe_structure(self) -> tuple[UIElement, ...]:
        listing = self._client.exec(
            "sh",
            [
                "-c",
                "xdotool search --onlyvisible --name . 2>/dev/null",
            ],
        )
        if not listing.get("ok"):
            return ()
        window_ids = [line.strip() for line in listing.get("stdout", "").splitlines() if line.strip()]
        elements: list[UIElement] = []
        for window_id in window_ids:
            name_result = self._client.exec("xdotool", ["getwindowname", window_id])
            geo_result = self._client.exec("xdotool", ["getwindowgeometry", window_id])
            name = (name_result.get("stdout") or "").strip() or window_id
            bbox = _parse_geometry(geo_result.get("stdout") or "")
            elements.append(
                UIElement(
                    id=f"x11:{window_id}",
                    scene_version=0,
                    role="window",
                    name=name,
                    text=name,
                    bbox=bbox,
                    sources=(ElementSource(type="atspi", ref=window_id, confidence=0.7),),
                )
            )
        return tuple(elements)

    def capabilities(self) -> StructureCapabilities:
        return StructureCapabilities(tree=Capability.DEGRADED)


def _parse_geometry(text: str) -> Optional[BoundingBox]:
    """Parse `Position: X,Y` and `Geometry: WxH` from xdotool output."""
    pos_x = pos_y = width = height = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("Position:"):
            body = line.split(":", 1)[1].strip()
            xy = body.split()[0]
            parts = xy.split(",")
            if len(parts) == 2:
                pos_x, pos_y = int(parts[0]), int(parts[1])
        elif line.startswith("Geometry:"):
            body = line.split(":", 1)[1].strip().split()[0]
            if "x" in body:
                w_text, h_text = body.split("x", 1)
                width, height = int(w_text), int(h_text)
    if None in (pos_x, pos_y, width, height):
        return None
    return BoundingBox(
        space=CoordinateSpace.SCREEN_PHYSICAL_PX,
        x=float(pos_x),
        y=float(pos_y),
        width=float(width),
        height=float(height),
    )


def create_sandbox_backends(
    session_id: str,
    *,
    base_url: str = _DEFAULT_BASE_URL,
    http: Optional[Any] = None,
) -> tuple[GrokSandboxCapture, GrokSandboxStructure, GrokSandboxInjector]:
    """Same-session capture, structure, and input bound to one sandbox."""
    client = SandboxHttpClient(base_url, http=http)
    capture = GrokSandboxCapture(session_id, client=client)
    structure = GrokSandboxStructure(session_id, client=client)
    injector = GrokSandboxInjector(session_id, client=client)
    return capture, structure, injector
