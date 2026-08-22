"""Unit tests for platform stubs — all capabilities must be UNSUPPORTED."""

import pytest

pytestmark = pytest.mark.unit

from hpcu.platform.windows.backend import (
    WindowsDxgiCapture, WindowsUiaObserver, WindowsInputInjector, probe as win_probe,
)
from hpcu.platform.linux.backend import (
    LinuxX11Capture, LinuxPipewireCapture, LinuxAtSpiObserver,
    LinuxXtestInjector, LinuxPortalInjector, probe as linux_probe,
)
from hpcu.platform.macos.backend import (
    MacosScreenCaptureKitCapture, MacosAxObserver, MacosCgEventInjector,
    probe as macos_probe,
)
from hpcu.platform.remote.backend import (
    RemoteVncCapture, RemoteVncInjector, probe as remote_probe,
)
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import CoordinateSpace, ScreenPoint
from hpcu.schemas.ui_element import UIElement


# ---- Windows ----

def test_windows_dxgi_capabilities_unsupported():
    cap = WindowsDxgiCapture(session_id="s")
    caps = cap.capabilities()
    assert caps.pixel_grab == Capability.UNSUPPORTED
    assert caps.dirty_rects == Capability.UNSUPPORTED


def test_windows_uia_unsupported():
    obs = WindowsUiaObserver()
    caps = obs.capabilities()
    assert caps["structure_tree"] == Capability.UNSUPPORTED


@pytest.mark.asyncio
async def test_windows_input_semantic_unsupported():
    inj = WindowsInputInjector(session_id="s")
    el = UIElement(id="btn_1", scene_version=1)
    result = await inj.semantic(el, "invoke")
    assert result.success is False
    assert result.failure_code == "input_semantic_unsupported"


@pytest.mark.asyncio
async def test_windows_input_physical_unsupported():
    inj = WindowsInputInjector(session_id="s")
    point = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=0, y=0)
    result = await inj.physical(point, "click")
    assert result.success is False


def test_windows_probe_returns_false():
    assert win_probe() is False


# ---- Linux ----

def test_linux_x11_capabilities_unsupported():
    cap = LinuxX11Capture(session_id="s")
    caps = cap.capabilities()
    assert caps.pixel_grab == Capability.UNSUPPORTED


def test_linux_pipewire_capabilities_unsupported():
    cap = LinuxPipewireCapture(session_id="s")
    caps = cap.capabilities()
    assert caps.pixel_grab == Capability.UNSUPPORTED


def test_linux_atspi_unsupported():
    obs = LinuxAtSpiObserver()
    caps = obs.capabilities()
    assert caps["structure_tree"] == Capability.UNSUPPORTED


@pytest.mark.asyncio
async def test_linux_xtest_unsupported():
    inj = LinuxXtestInjector(session_id="s")
    el = UIElement(id="btn_1", scene_version=1)
    result = await inj.semantic(el, "invoke")
    assert result.success is False


@pytest.mark.asyncio
async def test_linux_portal_unsupported():
    inj = LinuxPortalInjector(session_id="s")
    el = UIElement(id="btn_1", scene_version=1)
    result = await inj.semantic(el, "invoke")
    assert result.success is False


def test_linux_probe_returns_false():
    assert linux_probe() is False


# ---- macOS ----

def test_macos_sckit_capabilities_unsupported():
    cap = MacosScreenCaptureKitCapture(session_id="s")
    caps = cap.capabilities()
    assert caps.pixel_grab == Capability.UNSUPPORTED


def test_macos_ax_unsupported():
    obs = MacosAxObserver()
    caps = obs.capabilities()
    assert caps["structure_tree"] == Capability.UNSUPPORTED


@pytest.mark.asyncio
async def test_macos_cgevent_unsupported():
    inj = MacosCgEventInjector(session_id="s")
    el = UIElement(id="btn_1", scene_version=1)
    result = await inj.semantic(el, "invoke")
    assert result.success is False


def test_macos_probe_returns_false():
    assert macos_probe() is False


# ---- Remote ----

def test_remote_vnc_capabilities_unsupported():
    cap = RemoteVncCapture(session_id="s")
    caps = cap.capabilities()
    assert caps.pixel_grab == Capability.UNSUPPORTED


@pytest.mark.asyncio
async def test_remote_vnc_input_unsupported():
    inj = RemoteVncInjector(session_id="s")
    el = UIElement(id="btn_1", scene_version=1)
    result = await inj.semantic(el, "invoke")
    assert result.success is False


def test_remote_probe_returns_false():
    assert remote_probe() is False