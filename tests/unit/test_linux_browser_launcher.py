"""Linux sandbox default-browser discovery and launch regression tests."""

import pytest

from hpcu.platform.linux.sandbox import (
    GrokSandboxApplicationLauncher,
    SandboxHttpClient,
)
from hpcu.schemas.capability import Capability
from hpcu.schemas.failure_codes import FailureCode

pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _BrowserHttp:
    def __init__(
        self,
        *,
        default_desktop: str = "browser.desktop",
        scanned_entries: str = "",
        already_running: bool = False,
        launch_ok: bool = True,
    ) -> None:
        self.default_desktop = default_desktop
        self.scanned_entries = scanned_entries
        self.already_running = already_running
        self.launch_ok = launch_ok
        self.launched = False
        self.launch_calls = 0
        self.launched_desktop = ""

    def get(self, url: str):
        del url
        return _Response({"ok": True})

    def post(self, url: str, json: dict | None = None):
        del url
        payload = json or {}
        cmd = payload.get("cmd")
        args = payload.get("args") or []
        if cmd == "xdotool" and args == ["getactivewindow"]:
            if self.already_running:
                return _Response({"ok": True, "stdout": "7\n", "code": 0})
            if self.launched:
                return _Response({"ok": True, "stdout": "99\n", "code": 0})
            return _Response({"ok": True, "stdout": "", "code": 0})
        if cmd != "sh" or len(args) < 2 or args[0] != "-c":
            return _Response({"ok": True, "stdout": "", "code": 0})

        script = args[1]
        if "xdg-mime query default" in script:
            return _Response(
                {
                    "ok": True,
                    "stdout": (
                        f"{self.default_desktop}\n"
                        if self.default_desktop
                        else ""
                    ),
                    "code": 0,
                }
            )
        if "Categories=.*WebBrowser" in script:
            return _Response(
                {"ok": True, "stdout": self.scanned_entries, "code": 0}
            )
        if "StartupWMClass" in script:
            desktop_id = args[-1] if args else "browser.desktop"
            stem = str(desktop_id).removesuffix(".desktop")
            return _Response(
                {
                    "ok": True,
                    "stdout": (
                        f"{stem.title()}\n"
                        f"{stem.title()}Class\n"
                        f"/usr/bin/{stem}-bin %U\n"
                    ),
                    "code": 0,
                }
            )
        if "getwindowclassname" in script:
            if self.already_running:
                stdout = "7\tBrowserClass\tbrowser-bin\n"
            elif self.launched:
                stem = self.launched_desktop.removesuffix(".desktop") or "browser"
                stdout = f"99\t{stem.title()}Class\t{stem}-bin\n"
            else:
                stdout = ""
            return _Response({"ok": True, "stdout": stdout, "code": 0})
        if "gtk-launch" in script:
            self.launch_calls += 1
            self.launched_desktop = str(args[-1]) if args else ""
            if not self.launch_ok:
                return _Response({"ok": False, "stdout": ""})
            self.launched = True
            return _Response({"ok": True, "stdout": "", "code": 0})
        return _Response({"ok": True, "stdout": "", "code": 0})


@pytest.mark.asyncio
async def test_discovery_prefers_os_default_without_semantic_choice():
    http = _BrowserHttp(default_desktop="browser.desktop")
    launcher = GrokSandboxApplicationLauncher(
        "session",
        client=SandboxHttpClient("http://sandbox.test", http=http),
    )

    discovery = await launcher.discover("browser")

    assert [candidate.id for candidate in discovery.candidates] == [
        "browser.desktop"
    ]
    assert discovery.preferred_candidate_id == "browser.desktop"
    assert discovery.candidates[0].label == "Browser"


@pytest.mark.asyncio
async def test_discovery_exposes_ambiguous_os_candidates_without_launching():
    http = _BrowserHttp(
        default_desktop="",
        scanned_entries="first.desktop\nsecond.desktop\n",
    )
    launcher = GrokSandboxApplicationLauncher(
        "session",
        client=SandboxHttpClient("http://sandbox.test", http=http),
    )

    discovery = await launcher.discover("browser")

    assert [candidate.id for candidate in discovery.candidates] == [
        "first.desktop",
        "second.desktop",
    ]
    assert discovery.preferred_candidate_id is None
    assert http.launch_calls == 0


@pytest.mark.asyncio
async def test_launcher_discovers_default_browser_and_returns_window_evidence():
    http = _BrowserHttp()
    launcher = GrokSandboxApplicationLauncher(
        "session",
        client=SandboxHttpClient("http://sandbox.test", http=http),
    )

    result = await launcher.launch(
        "browser",
        timeout_ms=100,
        poll_interval_ms=1,
    )

    assert result.success is True
    assert result.evidence_element_id == "x11:99"
    assert http.launch_calls == 1
    assert http.launched_desktop == "browser.desktop"
    assert launcher.capabilities().launch is Capability.SUPPORTED


@pytest.mark.asyncio
async def test_launcher_reuses_observed_default_browser_window():
    http = _BrowserHttp(already_running=True)
    launcher = GrokSandboxApplicationLauncher(
        "session",
        client=SandboxHttpClient("http://sandbox.test", http=http),
    )

    result = await launcher.launch(
        "browser",
        timeout_ms=100,
        poll_interval_ms=1,
    )

    assert result.success is True
    assert result.evidence_element_id == "x11:7"
    assert http.launch_calls == 0


@pytest.mark.asyncio
async def test_launcher_fails_closed_when_browser_discovery_is_ambiguous():
    http = _BrowserHttp(
        default_desktop="",
        scanned_entries="first.desktop\nsecond.desktop\n",
    )
    launcher = GrokSandboxApplicationLauncher(
        "session",
        client=SandboxHttpClient("http://sandbox.test", http=http),
    )

    result = await launcher.launch(
        "browser",
        timeout_ms=100,
        poll_interval_ms=1,
    )

    assert result.success is False
    assert result.failure_code == FailureCode.DECISION_REQUIRED.value
    assert http.launch_calls == 0


@pytest.mark.asyncio
async def test_launcher_executes_only_explicit_bound_candidate():
    http = _BrowserHttp(
        default_desktop="",
        scanned_entries="first.desktop\nsecond.desktop\n",
    )
    launcher = GrokSandboxApplicationLauncher(
        "session",
        client=SandboxHttpClient("http://sandbox.test", http=http),
    )

    result = await launcher.launch(
        "browser",
        candidate_id="second.desktop",
        timeout_ms=100,
        poll_interval_ms=1,
    )

    assert result.success is True
    assert result.evidence_element_id == "x11:99"
    assert http.launch_calls == 1
    assert http.launched_desktop == "second.desktop"


@pytest.mark.asyncio
async def test_launcher_reports_missing_browser_without_executable_guessing():
    http = _BrowserHttp(default_desktop="", scanned_entries="")
    launcher = GrokSandboxApplicationLauncher(
        "session",
        client=SandboxHttpClient("http://sandbox.test", http=http),
    )

    result = await launcher.launch(
        "browser",
        timeout_ms=100,
        poll_interval_ms=1,
    )

    assert result.success is False
    assert result.failure_code == FailureCode.APPLICATION_NOT_FOUND.value
    assert http.launch_calls == 0


@pytest.mark.asyncio
async def test_launcher_rejects_explicit_failed_exec_without_exit_code():
    http = _BrowserHttp(launch_ok=False)
    launcher = GrokSandboxApplicationLauncher(
        "session",
        client=SandboxHttpClient("http://sandbox.test", http=http),
    )

    result = await launcher.launch(
        "browser",
        timeout_ms=100,
        poll_interval_ms=1,
    )

    assert result.success is False
    assert result.failure_code == FailureCode.APPLICATION_LAUNCH_FAILED.value
    assert http.launch_calls == 1
