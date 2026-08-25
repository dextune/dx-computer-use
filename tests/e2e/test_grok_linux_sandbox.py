"""Live tests against grokbot-gateway-sandbox (DISPLAY=:99 Xvfb)."""

import asyncio

import pytest

from hpcu.coordinates.spaces import compute_safe_click_point
from hpcu.executor.executor import Executor
from hpcu.platform.linux.sandbox import (
    GrokSandboxApplicationLauncher,
    GrokSandboxCapture,
    GrokSandboxInjector,
    GrokSandboxStructure,
    probe,
)
from hpcu.schemas.action import Action, ActionOp
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


@pytest.mark.e2e
@pytest.mark.platform_linux_x11
async def test_sandbox_browser_bootstrap_to_visible_page(sandbox_up):
    session_id = "e2e-browser-bootstrap"
    launcher = GrokSandboxApplicationLauncher(session_id)
    discovery = await launcher.discover("browser")
    assert discovery.candidates
    candidate_id = discovery.preferred_candidate_id or discovery.candidates[0].id

    injector = GrokSandboxInjector(session_id)
    executor = Executor(
        injector,
        application_launcher=launcher,
        poll_interval_ms=50,
    )
    launch = Action(
        id="launch-browser",
        op=ActionOp.LAUNCH_APPLICATION,
        value="browser",
        timeout_ms=10_000,
        application_candidate_id=candidate_id,
    )
    launch_result = await executor.execute(executor.prepare(launch, None))
    assert launch_result.success is True
    assert launch_result.evidence_element_id is not None

    structure = GrokSandboxStructure(session_id)
    elements = await structure.observe_structure()
    window = next(
        (
            element
            for element in elements
            if element.id == launch_result.evidence_element_id
        ),
        None,
    )
    assert window is not None
    assert window.bbox is not None

    page_title = "HPCU-Bootstrap-E2E"
    page_url = (
        "data:text/html,%3Ctitle%3EHPCU-Bootstrap-E2E%3C/title%3E"
        "%3Ch1%3EHPCU%20browser%20bootstrap%3C/h1%3E"
    )
    navigate = Action(
        id="navigate-bootstrap-page",
        op=ActionOp.NAVIGATE,
        value=page_url,
    )
    point = compute_safe_click_point(window.bbox)
    navigate_result = await executor.execute(
        executor.prepare(
            navigate,
            window.id,
            element=window,
            physical_point=point,
        )
    )
    assert navigate_result.success is True

    for _ in range(50):
        elements = await structure.observe_structure()
        if any(page_title in element.name for element in elements):
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("browser launched but visible page evidence never appeared")

    capture = GrokSandboxCapture(session_id)
    await capture.start()
    frame = await capture.grab()
    assert capture.store.get(frame.shm_id)[:8] == b"\x89PNG\r\n\x1a\n"
