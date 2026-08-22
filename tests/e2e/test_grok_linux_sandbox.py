"""Live tests against grokbot-gateway-sandbox (DISPLAY=:99 Xvfb)."""

import pytest

from hpcu.platform.linux.sandbox import (
    GrokSandboxCapture,
    GrokSandboxInjector,
    GrokSandboxStructure,
    probe,
)
from hpcu.schemas.coordinates import CoordinateSpace, ScreenPoint


@pytest.fixture
def sandbox_up():
    if not probe():
        pytest.skip("grokbot-gateway-sandbox is not reachable on :1337")


@pytest.mark.e2e
@pytest.mark.platform_linux_x11
async def test_sandbox_probe_and_screenshot(sandbox_up):
    capture = GrokSandboxCapture("e2e-sandbox")
    await capture.start()
    handle = await capture.grab()
    assert handle.width >= 640
    assert handle.height >= 480
    png = capture.store.get(handle.shm_id)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert handle.space is CoordinateSpace.SCREEN_PHYSICAL_PX


@pytest.mark.e2e
@pytest.mark.platform_linux_x11
async def test_sandbox_structure_lists_windows(sandbox_up):
    structure = GrokSandboxStructure("e2e-sandbox")
    elements = await structure.observe_structure()
    assert len(elements) >= 1
    names = {element.name for element in elements}
    assert any(names)


@pytest.mark.e2e
@pytest.mark.platform_linux_x11
async def test_sandbox_physical_mousemove(sandbox_up):
    injector = GrokSandboxInjector("e2e-sandbox")
    result = await injector.physical(
        ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=40, y=40),
        "click",
    )
    assert result.success is True
    assert result.mode == "physical"