"""Unit tests for PluginRegistry."""

import pytest

from hpcu.observation.registry import PluginRegistry

pytestmark = pytest.mark.unit


def test_register_and_get():
    registry = PluginRegistry()
    registry.register("test", lambda: "hello")
    assert registry.get("test") == "hello"


def test_get_unregistered_raises():
    registry = PluginRegistry()
    try:
        registry.get("missing")
        assert False, "should have raised"
    except KeyError:
        pass


def test_has():
    registry = PluginRegistry()
    registry.register("a", lambda: 1)
    assert registry.has("a")
    assert not registry.has("b")


def test_remove():
    registry = PluginRegistry()
    registry.register("a", lambda: 1)
    registry.remove("a")
    assert not registry.has("a")


def test_remove_missing_no_error():
    registry = PluginRegistry()
    registry.remove("nope")  # should not raise


def test_list():
    registry = PluginRegistry()
    registry.register("a", lambda: 1)
    registry.register("b", lambda: 2)
    names = registry.list()
    assert "a" in names
    assert "b" in names
    assert len(names) == 2


def test_register_with_args():
    registry = PluginRegistry()
    registry.register("adder", lambda a, b: a + b)
    assert registry.get("adder", 3, 4) == 7


def test_double_register_overwrites():
    registry = PluginRegistry()
    registry.register("x", lambda: "first")
    registry.register("x", lambda: "second")
    assert registry.get("x") == "second"


def test_remove_does_not_crash_control_loop():
    """After removal, core should handle KeyError gracefully."""
    registry = PluginRegistry()
    registry.register("capture", lambda: "cap")
    registry.remove("capture")
    try:
        registry.get("capture")
        assert False
    except KeyError:
        pass  # expected — core must handle this