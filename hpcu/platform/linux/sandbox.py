"""Grok Linux sandbox adapter — talks to grokbot-gateway-sandbox HTTP API.

The sandbox exposes:

- GET  /health
- POST /screenshot  -> {mimeType, data: base64 png}
- POST /exec        -> {cmd, args, timeout} run inside DISPLAY=:99

Common runtime never imports X11. This plugin only uses HTTP.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shlex
import struct
import time
from typing import Any, Optional
from urllib.parse import urljoin

from hpcu.capture.backend import CaptureBackend, CaptureCapabilities
from hpcu.capture.frame_store import FrameStore
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.input.keys import (
    ENTER,
    ESCAPE,
    FOCUS_LOCATION,
    NEW_TAB,
    PAGE_DOWN,
    SELECT_ALL,
)
from hpcu.lifecycle.launcher import (
    ApplicationCandidate,
    ApplicationDiscoveryResult,
    ApplicationLaunchCapabilities,
    ApplicationLauncher,
    ApplicationLaunchResult,
)
from hpcu.observation.structure_observer import (
    StructureCapabilities,
    StructureObserver,
)
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
_DEFAULT_BROWSER_SCRIPT = r"""
if command -v xdg-mime >/dev/null 2>&1; then
    for mime in x-scheme-handler/http text/html; do
        desktop=$(xdg-mime query default "$mime" 2>/dev/null || true)
        if [ -n "$desktop" ]; then
            printf '%s\n' "$desktop"
            exit 0
        fi
    done
fi
exit 0
""".strip()
_BROWSER_ENTRY_SCAN_SCRIPT = r"""
{
    for dir in \
        "${XDG_DATA_HOME:-$HOME/.local/share}/applications" \
        "$HOME/.local/share/applications" \
        /usr/local/share/applications \
        /usr/share/applications
    do
        [ -d "$dir" ] || continue
        for file in "$dir"/*.desktop; do
            [ -f "$file" ] || continue
            if grep -q '^Categories=.*WebBrowser' "$file" 2>/dev/null; then
                basename "$file"
            fi
        done
    done
} | sort -u
""".strip()
_DESKTOP_ENTRY_METADATA_SCRIPT = r"""
desktop=$1
for dir in \
    "${XDG_DATA_HOME:-$HOME/.local/share}/applications" \
    "$HOME/.local/share/applications" \
    /usr/local/share/applications \
    /usr/share/applications
do
    file="$dir/$desktop"
    [ -f "$file" ] || continue
    name=$(sed -n 's/^Name=//p' "$file" | head -n 1)
    wm_class=$(sed -n 's/^StartupWMClass=//p' "$file" | head -n 1)
    exec_line=$(sed -n 's/^Exec=//p' "$file" | head -n 1)
    printf '%s\n%s\n%s\n' "$name" "$wm_class" "$exec_line"
    exit 0
done
exit 0
""".strip()
_WINDOW_INVENTORY_SCRIPT = r"""
command -v xdotool >/dev/null 2>&1 || exit 0
for window_id in $(xdotool search --onlyvisible --name . 2>/dev/null); do
    class_name=$(xdotool getwindowclassname "$window_id" 2>/dev/null || true)
    pid=$(xdotool getwindowpid "$window_id" 2>/dev/null || true)
    process_name=""
    if [ -n "$pid" ]; then
        process_name=$(ps -p "$pid" -o comm= 2>/dev/null | head -n 1 | tr -d ' ')
    fi
    printf '%s\t%s\t%s\n' "$window_id" "$class_name" "$process_name"
done
""".strip()
_LAUNCH_DEFAULT_BROWSER_SCRIPT = r"""
desktop=$1
application_id=${desktop%.desktop}
if [ -n "$application_id" ] && command -v gtk-launch >/dev/null 2>&1; then
    nohup gtk-launch "$application_id" >/dev/null 2>&1 &
    exit 0
fi
if command -v xdg-open >/dev/null 2>&1; then
    nohup xdg-open http://127.0.0.1/ >/dev/null 2>&1 &
    exit 0
fi
exit 127
""".strip()


def _exec_ok(result: dict) -> bool:
    """Accept only an explicit successful sandbox execution result."""
    if "ok" in result:
        return result.get("ok") is True
    return result.get("code") in (0, "0")


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Read width/height from a PNG IHDR without decoding pixels."""
    if len(data) < 24 or data[:8] != _PNG_MAGIC:
        raise ValueError("screenshot is not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


class SandboxHttpClient:
    """Thin HTTP wrapper; `http` is any object with get/post like httpx."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        http: Optional[Any] = None,
    ):
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
                raised = self._client.exec("xdotool", ["windowraise", ref])
                ok = _exec_ok(activated) and _exec_ok(raised)
                return ExecutionResult(
                    success=ok,
                    mode="semantic",
                    failure_code=(
                        None
                        if ok
                        else FailureCode.INPUT_SEMANTIC_UNSUPPORTED.value
                    ),
                )
        return ExecutionResult(
            success=False,
            mode="semantic",
            failure_code=FailureCode.INPUT_SEMANTIC_UNSUPPORTED.value,
        )

    async def physical(
        self,
        point: ScreenPoint,
        action: str,
        text: str | None = None,
    ) -> ExecutionResult:
        x = int(round(point.x))
        y = int(round(point.y))
        if action in ("click", "invoke"):
            result = self._client.exec(
                "xdotool",
                ["mousemove", str(x), str(y), "click", "1"],
            )
        elif action == "double_click":
            result = self._client.exec(
                "xdotool",
                [
                    "mousemove",
                    str(x),
                    str(y),
                    "click",
                    "--repeat",
                    "2",
                    "1",
                ],
            )
        elif action == "right_click":
            result = self._client.exec(
                "xdotool",
                ["mousemove", str(x), str(y), "click", "3"],
            )
        elif action in ("type", "replace_text"):
            result = self._client.exec(
                "xdotool",
                [
                    "type",
                    "--delay",
                    "0",
                    "--clearmodifiers",
                    "--",
                    text or "",
                ],
            )
        elif action in ("key", "hotkey"):
            mapped = _PORTABLE_KEYS.get(text or ENTER, text or "Return")
            result = self._client.exec("xdotool", ["key", mapped])
        else:
            result = self._client.exec(
                "xdotool",
                ["mousemove", str(x), str(y)],
            )
        ok = _exec_ok(result)
        return ExecutionResult(
            success=ok,
            mode="physical",
            failure_code=(
                None if ok else FailureCode.INPUT_PHYSICAL_UNSUPPORTED.value
            ),
        )

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(
            semantic_invoke=Capability.UNSUPPORTED,
            physical_pointer=Capability.SUPPORTED,
            physical_keyboard=Capability.SUPPORTED,
        )


class GrokSandboxApplicationLauncher(ApplicationLauncher):
    """Discover and launch the Linux default browser without app-name tables."""

    def __init__(
        self,
        session_id: str,
        *,
        client: Optional[SandboxHttpClient] = None,
        sleep=None,
        monotonic=None,
    ) -> None:
        super().__init__(session_id)
        self._client = client or SandboxHttpClient()
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._monotonic = monotonic if monotonic is not None else time.monotonic

    async def discover(self, application: str) -> ApplicationDiscoveryResult:
        if application.strip().casefold() != "browser":
            return ApplicationDiscoveryResult(
                application=application,
                failure_code=FailureCode.APPLICATION_LAUNCH_UNSUPPORTED.value,
            )

        default_id = self._default_browser_desktop_id()
        if default_id:
            candidate = self._candidate(default_id)
            return ApplicationDiscoveryResult(
                application=application,
                candidates=(candidate,),
                preferred_candidate_id=candidate.id,
            )

        entries = self._scan_browser_desktop_ids()
        if not entries:
            return ApplicationDiscoveryResult(
                application=application,
                failure_code=FailureCode.APPLICATION_NOT_FOUND.value,
            )
        candidates = tuple(self._candidate(desktop_id) for desktop_id in entries)
        return ApplicationDiscoveryResult(
            application=application,
            candidates=candidates,
            preferred_candidate_id=(candidates[0].id if len(candidates) == 1 else None),
        )

    async def launch(
        self,
        application: str,
        *,
        candidate_id: str | None = None,
        timeout_ms: int,
        poll_interval_ms: int,
    ) -> ApplicationLaunchResult:
        discovery = await self.discover(application)
        if not discovery.candidates:
            return ApplicationLaunchResult(
                success=False,
                application=application,
                failure_code=(
                    discovery.failure_code
                    or FailureCode.APPLICATION_NOT_FOUND.value
                ),
            )

        candidate = self._select_candidate(discovery, candidate_id)
        if candidate is None:
            return ApplicationLaunchResult(
                success=False,
                application=application,
                failure_code=FailureCode.DECISION_REQUIRED.value,
            )

        baseline = self._window_inventory()
        baseline_ids = {window_id for window_id, _, _ in baseline}
        matching = self._matching_windows(
            baseline,
            startup_class=candidate.window_class,
            executable=candidate.executable,
        )
        active = self._active_window()
        existing = self._choose_window(matching, active)
        if existing is not None:
            return self._success(application, existing)

        launched = self._client.exec(
            "sh",
            [
                "-c",
                _LAUNCH_DEFAULT_BROWSER_SCRIPT,
                "hpcu-launch",
                candidate.id,
            ],
            timeout_ms=min(max(timeout_ms, 1), 10_000),
        )
        if not _exec_ok(launched):
            return ApplicationLaunchResult(
                success=False,
                application=application,
                failure_code=FailureCode.APPLICATION_LAUNCH_FAILED.value,
            )

        deadline = self._monotonic() + max(timeout_ms, 1) / 1000.0
        interval = max(poll_interval_ms, 1) / 1000.0
        while True:
            inventory = self._window_inventory()
            matching = self._matching_windows(
                inventory,
                startup_class=candidate.window_class,
                executable=candidate.executable,
            )
            active = self._active_window()
            chosen = self._choose_window(matching, active)
            if chosen is None:
                new_ids = [
                    window_id
                    for window_id, _, _ in inventory
                    if window_id not in baseline_ids
                ]
                if active in new_ids:
                    chosen = active
                elif len(new_ids) == 1:
                    chosen = new_ids[0]
            if chosen is not None:
                return self._success(application, chosen)
            if self._monotonic() >= deadline:
                return ApplicationLaunchResult(
                    success=False,
                    application=application,
                    failure_code=FailureCode.APPLICATION_NOT_OBSERVED.value,
                )
            await self._sleep(interval)

    def capabilities(self) -> ApplicationLaunchCapabilities:
        return ApplicationLaunchCapabilities(
            launch=Capability.SUPPORTED,
            discovery=Capability.SUPPORTED,
        )

    def _default_browser_desktop_id(self) -> str:
        result = self._client.exec("sh", ["-c", _DEFAULT_BROWSER_SCRIPT])
        if not _exec_ok(result):
            return ""
        return self._first_line(result.get("stdout", ""))

    def _scan_browser_desktop_ids(self) -> tuple[str, ...]:
        result = self._client.exec("sh", ["-c", _BROWSER_ENTRY_SCAN_SCRIPT])
        if not _exec_ok(result):
            return ()
        return tuple(
            dict.fromkeys(
                line.strip()
                for line in str(result.get("stdout", "")).splitlines()
                if line.strip()
            )
        )

    def _candidate(self, desktop_id: str) -> ApplicationCandidate:
        label, window_class, executable = self._desktop_metadata(desktop_id)
        return ApplicationCandidate(
            id=desktop_id,
            label=label or desktop_id,
            window_class=window_class,
            executable=executable,
        )

    def _desktop_metadata(self, desktop_id: str) -> tuple[str, str, str]:
        result = self._client.exec(
            "sh",
            [
                "-c",
                _DESKTOP_ENTRY_METADATA_SCRIPT,
                "hpcu-desktop-entry",
                desktop_id,
            ],
        )
        if not _exec_ok(result):
            return desktop_id, "", ""
        lines = str(result.get("stdout", "")).splitlines()
        label = lines[0].strip() if lines else desktop_id
        window_class = lines[1].strip() if len(lines) > 1 else ""
        exec_line = lines[2].strip() if len(lines) > 2 else ""
        return label, window_class, self._executable_name(exec_line)

    def _window_inventory(self) -> tuple[tuple[str, str, str], ...]:
        result = self._client.exec("sh", ["-c", _WINDOW_INVENTORY_SCRIPT])
        if not _exec_ok(result):
            return ()
        windows: list[tuple[str, str, str]] = []
        for line in str(result.get("stdout", "")).splitlines():
            parts = line.split("\t")
            if not parts or not parts[0].strip():
                continue
            window_id = parts[0].strip()
            class_name = parts[1].strip() if len(parts) > 1 else ""
            process_name = parts[2].strip() if len(parts) > 2 else ""
            windows.append((window_id, class_name, process_name))
        return tuple(windows)

    def _active_window(self) -> str:
        result = self._client.exec("xdotool", ["getactivewindow"])
        if not _exec_ok(result):
            return ""
        return self._first_line(result.get("stdout", ""))

    @staticmethod
    def _select_candidate(
        discovery: ApplicationDiscoveryResult,
        candidate_id: str | None,
    ) -> ApplicationCandidate | None:
        if candidate_id is not None:
            return next(
                (
                    candidate
                    for candidate in discovery.candidates
                    if candidate.id == candidate_id
                ),
                None,
            )
        preferred = discovery.preferred_candidate_id
        if preferred is not None:
            return next(
                candidate
                for candidate in discovery.candidates
                if candidate.id == preferred
            )
        if len(discovery.candidates) == 1:
            return discovery.candidates[0]
        return None

    @staticmethod
    def _matching_windows(
        inventory: tuple[tuple[str, str, str], ...],
        *,
        startup_class: str,
        executable: str,
    ) -> tuple[str, ...]:
        needles = tuple(
            value.casefold()
            for value in (startup_class.strip(), executable.strip())
            if value.strip()
        )
        if not needles:
            return ()
        matches = []
        for window_id, class_name, process_name in inventory:
            haystacks = (class_name.casefold(), process_name.casefold())
            if any(
                needle == haystack or needle in haystack
                for needle in needles
                for haystack in haystacks
                if haystack
            ):
                matches.append(window_id)
        return tuple(matches)

    @staticmethod
    def _choose_window(candidates: tuple[str, ...], active: str) -> str | None:
        if active and active in candidates:
            return active
        if len(candidates) == 1:
            return candidates[0]
        return None

    @staticmethod
    def _success(application: str, window_id: str) -> ApplicationLaunchResult:
        return ApplicationLaunchResult(
            success=True,
            application=application,
            evidence_element_id=f"x11:{window_id}",
        )

    @staticmethod
    def _first_line(value: object) -> str:
        for line in str(value or "").splitlines():
            if line.strip():
                return line.strip()
        return ""

    @staticmethod
    def _executable_name(exec_line: str) -> str:
        if not exec_line.strip():
            return ""
        try:
            parts = shlex.split(exec_line)
        except ValueError:
            return ""
        if not parts:
            return ""
        index = 0
        if os.path.basename(parts[0]) == "env":
            index = 1
            while index < len(parts) and "=" in parts[index]:
                index += 1
        if index >= len(parts):
            return ""
        return os.path.basename(parts[index])


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
        if not _exec_ok(listing):
            return ()
        window_ids = [
            line.strip()
            for line in listing.get("stdout", "").splitlines()
            if line.strip()
        ]
        elements: list[UIElement] = []
        for window_id in window_ids:
            name_result = self._client.exec("xdotool", ["getwindowname", window_id])
            geo_result = self._client.exec(
                "xdotool",
                ["getwindowgeometry", window_id],
            )
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
                    sources=(
                        ElementSource(
                            type="atspi",
                            ref=window_id,
                            confidence=0.7,
                        ),
                    ),
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


def create_sandbox_launcher(
    session_id: str,
    *,
    base_url: str = _DEFAULT_BASE_URL,
    http: Optional[Any] = None,
) -> GrokSandboxApplicationLauncher:
    """Create an application launcher bound to the same sandbox session."""
    return GrokSandboxApplicationLauncher(
        session_id,
        client=SandboxHttpClient(base_url, http=http),
    )
