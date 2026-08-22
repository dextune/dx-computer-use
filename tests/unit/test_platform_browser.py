"""Unit tests for the browser platform placeholders.

Every Phase 1 browser placeholder reports UNSUPPORTED capabilities and a
False probe, guaranteeing an honest "capability missing" signal rather than
a silent fallback.
"""

import pytest

from hpcu.capture.backend import CaptureCapabilities
from hpcu.input.injector import InputCapabilities
from hpcu.observation.structure_observer import StructureCapabilities
from hpcu.platform.browser.capture import (
    BrowserCaptureBackend,
)
from hpcu.platform.browser.capture import (
    probe as capture_probe,
)
from hpcu.platform.browser.input import (
    BrowserInputInjector,
)
from hpcu.platform.browser.input import (
    probe as input_probe,
)
from hpcu.platform.browser.structure import (
    BrowserStructureObserver,
)
from hpcu.platform.browser.structure import (
    probe as structure_probe,
)
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import CoordinateSpace, ScreenPoint
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.ui_element import UIElement


@pytest.mark.unit
def test_browser_capture_probe_is_false():
    # Given the browser capture placeholder
    # When probe() is read
    # Then it reports unavailable
    assert capture_probe() is False


@pytest.mark.unit
def test_browser_capture_all_capabilities_unsupported():
    # Given a browser capture backend
    backend = BrowserCaptureBackend("test-session")

    # When its capabilities are read
    caps: CaptureCapabilities = backend.capabilities()

    # Then every capability is explicitly UNSUPPORTED
    assert caps.pixel_grab is Capability.UNSUPPORTED
    assert caps.dirty_rects is Capability.UNSUPPORTED
    assert caps.cursor_separate is Capability.UNSUPPORTED
    assert caps.max_fps == 0


@pytest.mark.unit
async def test_browser_capture_grab_raises_placeholder_error():
    # Given a browser capture placeholder
    backend = BrowserCaptureBackend("test-session")

    # When grab() is attempted despite the unsupported capability
    # Then a clear placeholder error is raised (never a fabricated frame)
    with pytest.raises(RuntimeError):
        await backend.grab()


@pytest.mark.unit
def test_browser_structure_probe_is_false():
    # Given the browser structure placeholder
    # When probe() is read
    # Then it reports unavailable
    assert structure_probe() is False


@pytest.mark.unit
def test_browser_structure_capabilities_unsupported():
    # Given a browser structure observer
    observer = BrowserStructureObserver("test-session")

    # When its capabilities are read
    caps: StructureCapabilities = observer.capabilities()

    # Then every capability is explicitly UNSUPPORTED
    assert caps.tree is Capability.UNSUPPORTED
    assert caps.live_events is Capability.UNSUPPORTED


@pytest.mark.unit
async def test_browser_structure_observe_returns_empty_tree():
    # Given a browser structure placeholder
    observer = BrowserStructureObserver("test-session")

    # When observe_structure() is called
    elements = await observer.observe_structure()

    # Then an empty tree is returned (STRUCTURE_TREE_EMPTY path, not a crash)
    assert elements == ()


@pytest.mark.unit
def test_browser_input_probe_is_false():
    # Given the browser input placeholder
    # When probe() is read
    # Then it reports unavailable
    assert input_probe() is False


@pytest.mark.unit
def test_browser_input_capabilities_unsupported():
    # Given a browser input injector
    injector = BrowserInputInjector("test-session")

    # When its capabilities are read
    caps: InputCapabilities = injector.capabilities()

    # Then every capability is explicitly UNSUPPORTED
    assert caps.semantic_invoke is Capability.UNSUPPORTED
    assert caps.physical_pointer is Capability.UNSUPPORTED
    assert caps.physical_keyboard is Capability.UNSUPPORTED
    assert caps.global_hotkey is Capability.UNSUPPORTED
    assert caps.background_input is Capability.UNSUPPORTED


@pytest.mark.unit
async def test_browser_input_semantic_returns_unsupported_code():
    # Given a browser input placeholder and a target element
    injector = BrowserInputInjector("test-session")
    element = UIElement(id="btn_1", scene_version=1)

    # When a semantic action is attempted
    result = await injector.semantic(element, "invoke")

    # Then it fails explicitly with the semantic-unsupported code
    assert result.success is False
    assert result.mode == "semantic"
    assert result.failure_code == FailureCode.INPUT_SEMANTIC_UNSUPPORTED.value


@pytest.mark.unit
async def test_browser_input_physical_returns_unsupported_code():
    # Given a browser input placeholder and a physical point
    injector = BrowserInputInjector("test-session")
    point = ScreenPoint(space=CoordinateSpace.SCREEN_PHYSICAL_PX, x=1, y=1)

    # When a physical action is attempted
    result = await injector.physical(point, "click")

    # Then it fails explicitly with the physical-unsupported code
    assert result.success is False
    assert result.mode == "physical"
    assert result.failure_code == FailureCode.INPUT_PHYSICAL_UNSUPPORTED.value


@pytest.mark.unit
def test_browser_backends_share_session_id_shape():
    # Given all three placeholders for the same screen
    capture = BrowserCaptureBackend("screen-1")
    structure = BrowserStructureObserver("screen-1")
    injector = BrowserInputInjector("screen-1")

    # When their session ids are read
    # Then they all target the same screen session
    assert (capture.session_id, structure.session_id, injector.session_id) == (
        "screen-1",
        "screen-1",
        "screen-1",
    )
